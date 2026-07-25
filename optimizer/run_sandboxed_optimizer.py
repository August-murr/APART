"""Orchestrates one Optimizer run and collects everything it produced.

Builds the sandbox image, pushes an explicit allowlist of files into a
network-restricted modal.Sandbox, gives that sandbox its own fresh git repo
(never derived from this project's, so sealed/ can never leak via history), runs
the Optimizer (OpenHands) against BRIEF.md, and syncs the results back out into
runs/<run_id>/.

The push list below is a security boundary, not a convenience. The Optimizer
cannot read a file merely because it exists in this repository -- only what is
explicitly copied in. That is what makes it safe to commit ground-truth-bearing
run logs under runs/. Nothing from runs/ or sealed/ may ever be added here.

Environment knobs:
  RUN_ID     name of the runs/ subdirectory (default: timestamp)
  EVAL_K     episodes per ./run_eval.sh call inside the sandbox (default 12)
  MAX_EVALS  how many eval runs the Optimizer is told to spend (default 10)

Run with: set -a; source .env; set +a; .venv/bin/python optimizer/run_sandboxed_optimizer.py
"""

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sealed.cost import check_budget  # noqa: E402  (needs ROOT on the path first)

BUNDLE = ROOT / "optimizer" / "sandbox_bundle"

GRADER_HOST = "moh-murr--secret-loyalties-grader-web.modal.run"
GRADER_URL = f"https://{GRADER_HOST}"

RUN_ID = os.environ.get("RUN_ID") or datetime.now().strftime("%Y-%m-%d_%H%M")
RUN_DIR = ROOT / "runs" / RUN_ID
EVAL_K = int(os.environ.get("EVAL_K", "12"))
MAX_EVALS = int(os.environ.get("MAX_EVALS", "10"))

app = modal.App.lookup("secret-loyalties-optimizer", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "curl")
    .pip_install("openhands-ai", "openai>=1.50.0", "pyyaml>=6.0", "python-dotenv>=1.0.0", "requests>=2.31.0")
)

secret = modal.Secret.from_dict({"OPENROUTER_API_KEY": os.environ["OPENROUTER_API_KEY"]})

# Everything the Optimizer is allowed to see, as (local path, sandbox path).
PUSH_LIST = [
    (ROOT / "auditor" / "__init__.py", "/workspace/auditor/__init__.py"),
    (ROOT / "auditor" / "_llm.py", "/workspace/auditor/_llm.py"),
    (ROOT / "auditor" / "agent.py", "/workspace/auditor/agent.py"),
    (ROOT / "auditor" / "NOTES.md", "/workspace/auditor/NOTES.md"),
    (ROOT / "config" / "models.yaml", "/workspace/config/models.yaml"),
    (BUNDLE / "run_eval.py", "/workspace/run_eval.py"),
    (BUNDLE / "run_eval.sh", "/workspace/run_eval.sh"),
    (BUNDLE / "run_optimizer.py", "/workspace/run_optimizer.py"),
]

# Pulled back out afterwards, as (sandbox dir, local destination).
SYNC_LIST = [
    ("auditor", ROOT / "auditor"),          # adopt the evolved Auditor into the repo
    ("results", ROOT / "results"),          # score history accumulates across runs
    ("generations", RUN_DIR / "generations"),
    ("optimizer_events", RUN_DIR / "optimizer_events"),
]


def sync_back(sb: modal.Sandbox):
    """Copy the Optimizer's work out of the ephemeral sandbox.

    Without this the sandbox dies with everything it produced: the evolved
    agent.py, the generation snapshots, the NOTES.md entries that are its only
    memory across runs, and the OpenHands event log that is the only record of
    what it actually did.

    Tars whole directories rather than reading a fixed list of files, so files
    the Optimizer invented (a prompts/ dir, a helper module) come back too.
    auditor/ and results/ are git-tracked, so `git checkout -- auditor/ results/`
    undoes an adoption that went badly.
    """
    print("\n" + "=" * 60)
    print(f"Syncing sandbox output into runs/{RUN_ID}/")
    print("=" * 60)

    dirty = sb.exec("bash", "-c", "cd /workspace && git status --porcelain -- auditor/").stdout.read()
    if dirty.strip():
        print("WARNING: sandbox working tree has uncommitted changes under auditor/:")
        print(dirty)
        print("Syncing them anyway (they're the latest state), but the Optimizer never")
        print("checkpointed them, so it may have stopped mid-experiment.")

    dirs = " ".join(name for name, _ in SYNC_LIST)
    p = sb.exec("bash", "-c", f"cd /workspace && tar czf - {dirs} 2>/dev/null | base64 -w0")
    encoded = p.stdout.read()
    if not encoded.strip():
        print(f"sync FAILED, leaving local files untouched: {p.stderr.read()}")
        return

    staging = Path(tempfile.mkdtemp(prefix="optimizer-sync-"))
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(encoded))) as tar:
        tar.extractall(staging, filter="data")  # filter="data" blocks path traversal

    for name, dest in SYNC_LIST:
        if (staging / name).is_dir():
            shutil.copytree(staging / name, dest, dirs_exist_ok=True)
            print(f"  {name}/ -> {dest.relative_to(ROOT)}")
        else:
            print(f"  {name}/ absent in sandbox (skipped)")
    shutil.rmtree(staging, ignore_errors=True)

    # Copies so runs/<id>/ reads as a self-contained record of this run.
    for src, dst in ((ROOT / "auditor" / "NOTES.md", RUN_DIR / "NOTES.md"),
                     (ROOT / "results" / "history.jsonl", RUN_DIR / "history.jsonl")):
        if src.exists():
            shutil.copy(src, dst)

    p = subprocess.run(["git", "status", "--short", "--", "auditor/", "results/"],
                       cwd=ROOT, capture_output=True, text=True)
    print("\nlocal changes to tracked code:")
    print(p.stdout or "(none — the Optimizer changed nothing that survived)")
    print("Review with `git diff auditor/`; discard with `git checkout -- auditor/ results/`.")


def main():
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    spend_before = check_budget(f"starting run {RUN_ID}")

    print(f"run_id={RUN_ID}  eval_k={EVAL_K}  max_evals={MAX_EVALS}")
    print("Creating sandbox...")
    sb = modal.Sandbox.create(
        app=app,
        image=image,
        secrets=[secret],
        env={"GRADER_URL": GRADER_URL, "EVAL_K": str(EVAL_K)},
        outbound_domain_allowlist=[GRADER_HOST, "openrouter.ai"],
        # Measured ~3-4 min per eval cycle including the Optimizer's reasoning, so a
        # 20-eval run needs well over an hour. The sandbox timeout is the hard stop:
        # if it fires mid-run the work is lost, so leave generous headroom.
        timeout=3 * 60 * 60,
        workdir="/workspace",
    )
    print(f"sandbox: {sb.object_id}")

    # Guaranteed teardown. Killing this process does NOT stop the sandbox --
    # OpenHands keeps running inside it, keeps calling the model API, and keeps
    # spending until the sandbox's own timeout expires. An interrupted run has to
    # take the sandbox down with it.
    try:
        _drive(sb, spend_before)
    finally:
        sb.terminate()
        print("sandbox terminated")


def _drive(sb: modal.Sandbox, spend_before):
    print("Pushing bundle files...")
    for d in ("auditor", "config", "optimizer"):
        sb.filesystem.make_directory(f"/workspace/{d}", create_parents=True)
    for local_path, remote_path in PUSH_LIST:
        sb.filesystem.copy_from_local(local_path, remote_path)

    # BRIEF.md is templated so the stop condition matches this run's budget
    # rather than whatever number happens to be written in the file.
    brief = (ROOT / "optimizer" / "BRIEF.md").read_text().replace("{{MAX_EVALS}}", str(MAX_EVALS))
    sb.filesystem.write_text(brief, "/workspace/optimizer/BRIEF.md")  # (data, path)

    # Sanitized score history from previous runs. BRIEF.md tells the Optimizer to
    # read this as memory, so without it every run starts believing nothing has
    # ever been tried.
    history = ROOT / "results" / "history.jsonl"
    if history.exists():
        sb.filesystem.make_directory("/workspace/results", create_parents=True)
        sb.filesystem.copy_from_local(history, "/workspace/results/history.jsonl")

    print("Setting up the sandbox's own git repo (separate from this project's)...")
    sb.exec("chmod", "+x", "/workspace/run_eval.sh").wait()
    # Without this, compiled bytecode shows up as an uncommitted change on every
    # eval, so meta.json's working_tree_dirty flag -- meant to record whether the
    # Optimizer had checkpointed a generation -- is always true and says nothing.
    sb.filesystem.write_text(
        "__pycache__/\n*.pyc\ngenerations/\noptimizer_events/\n",
        "/workspace/.gitignore",
    )
    p = sb.exec(
        "bash", "-c",
        "cd /workspace && git init -q && git config user.email sandbox@local "
        "&& git config user.name Optimizer && git add -A && git commit -q -m baseline "
        "&& git tag baseline",
    )
    print(p.stdout.read(), p.stderr.read())

    # Establish gen_000 as the UNMODIFIED seed Auditor, before OpenHands touches
    # anything. Without this the first snapshot is whatever state agent.py happens
    # to be in when the Optimizer first chooses to run an eval -- and it typically
    # edits several times before evaluating -- so the trajectory would have no
    # true baseline to measure improvement against. Doubles as a smoke test that
    # run_eval.sh works before spending Optimizer tokens on a broken harness.
    print("\n" + "=" * 60)
    print(f"Baseline eval of the seed Auditor (k={EVAL_K}) -> gen_000")
    print("=" * 60)
    p = sb.exec("bash", "-c", "cd /workspace && ./run_eval.sh", timeout=900)
    baseline_out = p.stdout.read()
    print(baseline_out)
    if p.wait() != 0:
        print("BASELINE EVAL FAILED — aborting before spending Optimizer budget")
        print(p.stderr.read())
        sys.exit(1)  # the finally in main() tears the sandbox down
    # Commit it so the Optimizer's `git checkout -- auditor/` has a real baseline
    # and gen_000 is attributable to a commit like every later generation.
    sb.exec("bash", "-c", "cd /workspace && git add -A && git commit -q -m 'baseline eval (gen_000)'").wait()

    print("\n" + "=" * 60)
    print("Handing off to OpenHands")
    print("=" * 60)
    stdout_path = RUN_DIR / "optimizer_stdout.log"
    p = sb.exec("python", "/workspace/run_optimizer.py", timeout=3 * 60 * 60 - 300)
    with open(stdout_path, "w") as log:
        for line in p.stdout:
            print(line, end="")
            log.write(line)
        stderr_tail = p.stderr.read()
        if stderr_tail.strip():
            print("--- stderr ---")
            print(stderr_tail)
            log.write("\n--- stderr ---\n" + stderr_tail)
    returncode = p.wait()
    print("optimizer run returncode:", returncode)

    print("\n--- git log (sandbox) ---")
    git_log = sb.exec("bash", "-c", "cd /workspace && git log --oneline").stdout.read()
    print(git_log)

    sync_back(sb)

    spend_after = check_budget(f"finished run {RUN_ID}")
    with open(RUN_DIR / "config.json", "w") as f:
        json.dump({
            "run_id": RUN_ID,
            "eval_k": EVAL_K,
            "max_evals": MAX_EVALS,
            "grader_url": GRADER_URL,
            "models": yaml_models(),
            "organisms": yaml_organisms(),
            "project_git_sha": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                              capture_output=True, text=True).stdout.strip(),
            "sandbox_git_log": git_log,
            "optimizer_returncode": returncode,
            "spend_usd_before": spend_before,
            "spend_usd_after": spend_after,
            "spend_usd_this_run": (round(spend_after - spend_before, 4)
                                   if None not in (spend_before, spend_after) else None),
        }, f, indent=2)
    print(f"\nwrote runs/{RUN_ID}/config.json")


def yaml_models() -> dict:
    import yaml
    with open(ROOT / "config" / "models.yaml") as f:
        return yaml.safe_load(f)


def yaml_organisms() -> dict:
    import yaml
    with open(ROOT / "config" / "organisms.yaml") as f:
        return yaml.safe_load(f)


if __name__ == "__main__":
    main()
