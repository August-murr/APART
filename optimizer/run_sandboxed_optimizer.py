"""Orchestrates one Optimizer run and collects everything it produced.

Builds the sandbox image, pushes an explicit allowlist of files into a
network-restricted modal.Sandbox, gives that sandbox its own fresh git repo
(never derived from this project's, so sealed/ can never leak via history), runs
the chosen Optimizer backend against the role's brief, and syncs the results
back out into runs/<run_id>/.

The push list below is a security boundary, not a convenience. An agent cannot
read a file merely because it exists in this repository -- only what is
explicitly copied in. That is what makes it safe to commit ground-truth-bearing
run logs under runs/. Nothing from runs/ or sealed/ may ever be added here. It
is also what keeps the two roles apart: the Installer's sandbox never receives
auditor/, and the Auditor's never receives installer/.

Environment knobs:
  RUN_ID            name of the runs/ subdirectory (default: timestamp)
  EVAL_K            episodes per eval call inside the sandbox (default 12)
  MAX_EVALS         how many eval runs the Optimizer is told to spend (default 10)
  OPTIMIZER_BACKEND openhands (default, the measured poc-v1 path) | claude_code (DRAFT)
  ROLE              auditor (default) | installer (DRAFT)
  GPU               empty (default) | T4 -- attaches a GPU and adds the LoRA
                    training tool. Bills a GPU for the whole run. (DRAFT)
  GRADER_HOST       override the Auditor's grading service (coevolve.py uses this
                    to point Phase A at the co-evolution grader rather than the
                    frozen poc-v1 one)
  INSTALLER_HOST    override the Installer's grading service

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

GRADER_HOST = os.environ.get("GRADER_HOST", "moh-murr--secret-loyalties-grader-web.modal.run")
GRADER_URL = f"https://{GRADER_HOST}"

INSTALLER_HOST = os.environ.get("INSTALLER_HOST", "moh-murr--secret-loyalties-installer-web.modal.run")
INSTALLER_URL = f"https://{INSTALLER_HOST}"

RUN_ID = os.environ.get("RUN_ID") or datetime.now().strftime("%Y-%m-%d_%H%M")
RUN_DIR = ROOT / "runs" / RUN_ID
EVAL_K = int(os.environ.get("EVAL_K", "12"))
MAX_EVALS = int(os.environ.get("MAX_EVALS", "10"))
OPTIMIZER_BACKEND = os.environ.get("OPTIMIZER_BACKEND", "openhands")
ROLE = os.environ.get("ROLE", "auditor")
# Empty (the default) means a CPU sandbox and no training tool. Set GPU=T4 to
# give the Optimizer the LoRA affordance -- see docs/EXTENSIONS.md. Note this
# bills a GPU for the WHOLE run, including the reasoning time between trainings,
# which is most of it. Only worth it when weight training is actually the point.
GPU = os.environ.get("GPU", "").strip()

if OPTIMIZER_BACKEND not in ("openhands", "claude_code"):
    sys.exit(f"unknown OPTIMIZER_BACKEND={OPTIMIZER_BACKEND!r}")

# Everything that differs between the two sides of the co-evolution, in one
# place. Parameterized rather than forked into a second orchestrator: the
# sandbox setup, the isolation guarantees, the guaranteed teardown and the
# artifact collection are identical for both roles, and a copy would drift.
#
# `package` is the ONLY directory its agent may edit, and is also the only one
# pushed in -- which is what keeps the Installer from reading auditor/ and vice
# versa.
ROLE_CONFIG = {
    "auditor": {
        "package": "auditor",
        "brief": "BRIEF.md",
        "eval_script": "run_eval.py",
        "eval_cmd": "./run_eval.sh",
        "history": "results/history.jsonl",
        "service_env": {"GRADER_URL": GRADER_URL},
        "allowlist": [GRADER_HOST],
    },
    "installer": {
        "package": "installer",
        "brief": "BRIEF_INSTALLER.md",
        "eval_script": "run_installer_eval.py",
        "eval_cmd": "./run_installer_eval.sh",
        "history": "results/installer_history.jsonl",
        "service_env": {"INSTALLER_SERVICE_URL": INSTALLER_URL},
        "allowlist": [INSTALLER_HOST],
    },
}

if ROLE not in ROLE_CONFIG:
    sys.exit(f"unknown ROLE={ROLE!r}; expected one of {sorted(ROLE_CONFIG)}")

CFG = ROLE_CONFIG[ROLE]
PKG = CFG["package"]

app = modal.App.lookup("secret-loyalties-optimizer", create_if_missing=True)


def build_image() -> modal.Image:
    """Image contents depend on the backend, so it is built per run.

    Everything either backend needs has to be baked in HERE, at build time.
    Image builds have unrestricted network; the sandbox that runs afterwards
    does not, so a package fetched at runtime would simply fail.
    """
    image = (
        modal.Image.debian_slim(python_version="3.12")
        .apt_install("git", "curl")
        .pip_install("openhands-ai", "openai>=1.50.0", "pyyaml>=6.0",
                     "python-dotenv>=1.0.0", "requests>=2.31.0")
    )
    if OPTIMIZER_BACKEND == "claude_code":
        image = image.apt_install("ripgrep").run_commands(
            # Native installer: a self-contained binary, so no Node needed.
            "curl -fsSL https://claude.ai/install.sh | bash",
            # It lands in ~/.local/bin, which isn't on PATH for non-login shells.
            "ln -sf /root/.local/bin/claude /usr/local/bin/claude",
            "claude --version",  # fail the build here rather than mid-run
        )
    if GPU:
        from lora.train_lora import BASE_MODEL
        image = image.pip_install(
            "torch==2.5.1", "transformers>=4.46", "peft>=0.13",
            "trl>=0.12", "datasets>=3.0", "accelerate>=1.0",
        ).run_commands(
            # Base weights baked in: a runtime HuggingFace fetch would fail
            # against the sandbox allowlist, and this is the single most likely
            # thing to break the training tool.
            f"python -c \"from transformers import AutoModelForCausalLM, AutoTokenizer; "
            f"AutoModelForCausalLM.from_pretrained('{BASE_MODEL}'); "
            f"AutoTokenizer.from_pretrained('{BASE_MODEL}')\""
        )
    return image


def build_secret() -> modal.Secret:
    values = {"OPENROUTER_API_KEY": os.environ["OPENROUTER_API_KEY"]}
    if OPTIMIZER_BACKEND == "claude_code":
        token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        if not token:
            sys.exit(
                "OPTIMIZER_BACKEND=claude_code needs CLAUDE_CODE_OAUTH_TOKEN.\n"
                "Generate one with `claude setup-token` and put it in .env."
            )
        # Deliberately NOT forwarding ANTHROPIC_API_KEY even when it exists: it
        # outranks the OAuth token in Claude Code's auth precedence, so its
        # presence would silently bill Console credits instead of the
        # subscription this token belongs to.
        values["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return modal.Secret.from_dict(values)

# Everything this role's Optimizer is allowed to see, as (local, sandbox) paths.
#
# This list is a security boundary, not a convenience. Only the role's OWN
# package is pushed, which is what stops the Installer reading auditor/ (it
# would learn to defeat one implementation instead of learning to evade
# detection) and stops the Auditor reading installer/ (it would learn the
# prompts it is supposed to discover).
def build_push_list() -> list[tuple[Path, str]]:
    pushed = [
        (ROOT / PKG / "__init__.py", f"/workspace/{PKG}/__init__.py"),
        (ROOT / PKG / "_llm.py", f"/workspace/{PKG}/_llm.py"),
        (ROOT / PKG / "agent.py", f"/workspace/{PKG}/agent.py"),
        (ROOT / PKG / "NOTES.md", f"/workspace/{PKG}/NOTES.md"),
        (ROOT / "config" / "models.yaml", "/workspace/config/models.yaml"),
        (BUNDLE / CFG["eval_script"], f"/workspace/{CFG['eval_script']}"),
        (BUNDLE / CFG["eval_cmd"].lstrip("./"), f"/workspace/{CFG['eval_cmd'].lstrip('./')}"),
        (BUNDLE / "run_optimizer.py", "/workspace/run_optimizer.py"),
    ]
    if GPU:
        # The weight-training affordance. Only pushed when a GPU exists to
        # run it, so an agent on a CPU sandbox is never told about a tool
        # that would fail.
        pushed += [
            (BUNDLE / "train_lora.py", "/workspace/train_lora.py"),
            (BUNDLE / "train_lora.sh", "/workspace/train_lora.sh"),
        ]
    pushed += [
        (BUNDLE / "backends" / "__init__.py", "/workspace/backends/__init__.py"),
        (BUNDLE / "backends" / "openhands_backend.py", "/workspace/backends/openhands_backend.py"),
        (BUNDLE / "backends" / "claude_code_backend.py", "/workspace/backends/claude_code_backend.py"),
    ]
    missing = [str(p.relative_to(ROOT)) for p, _ in pushed if not p.exists()]
    if missing:
        sys.exit(f"ROLE={ROLE} push list references missing files: {missing}")
    return pushed


PUSH_LIST = build_push_list()

# Pulled back out afterwards, as (sandbox dir, local destination).
SYNC_LIST = [
    (PKG, ROOT / PKG),                      # adopt the evolved agent into the repo
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
    The package and results/ are git-tracked, so
    `git checkout -- <package>/ results/` undoes an adoption that went badly.
    """
    print("\n" + "=" * 60)
    print(f"Syncing sandbox output into runs/{RUN_ID}/")
    print("=" * 60)

    dirty = sb.exec("bash", "-c", f"cd /workspace && git status --porcelain -- {PKG}/").stdout.read()
    if dirty.strip():
        print(f"WARNING: sandbox working tree has uncommitted changes under {PKG}/:")
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
    for src, dst in ((ROOT / PKG / "NOTES.md", RUN_DIR / "NOTES.md"),
                     (ROOT / CFG["history"], RUN_DIR / Path(CFG["history"]).name)):
        if src.exists():
            shutil.copy(src, dst)

    p = subprocess.run(["git", "status", "--short", "--", f"{PKG}/", "results/"],
                       cwd=ROOT, capture_output=True, text=True)
    print("\nlocal changes to tracked code:")
    print(p.stdout or "(none — the Optimizer changed nothing that survived)")
    print(f"Review with `git diff {PKG}/`; discard with `git checkout -- {PKG}/ results/`.")


def main():
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    spend_before = check_budget(f"starting run {RUN_ID}")

    print(f"run_id={RUN_ID}  role={ROLE}  eval_k={EVAL_K}  max_evals={MAX_EVALS}  "
          f"backend={OPTIMIZER_BACKEND}")
    print("Creating sandbox...")

    # The allowlist is a security boundary: it is what stops the Optimizer
    # reaching anything but its own grading service and its model provider.
    # Only widen it by exactly what this role and backend need -- note the
    # Installer's sandbox cannot reach the Auditor's grader, and vice versa.
    allowlist = [*CFG["allowlist"], "openrouter.ai"]
    if OPTIMIZER_BACKEND == "claude_code":
        allowlist.append("api.anthropic.com")

    sb = modal.Sandbox.create(
        app=app,
        image=build_image(),
        secrets=[build_secret()],
        env={
            **CFG["service_env"],
            "EVAL_K": str(EVAL_K),
            "OPTIMIZER_BACKEND": OPTIMIZER_BACKEND,
            "ROLE": ROLE,
        },
        outbound_domain_allowlist=allowlist,
        **({"gpu": GPU} if GPU else {}),
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
    for d in (PKG, "config", "optimizer"):
        sb.filesystem.make_directory(f"/workspace/{d}", create_parents=True)
    for local_path, remote_path in PUSH_LIST:
        sb.filesystem.copy_from_local(local_path, remote_path)

    # The brief is templated so the stop condition matches this run's budget
    # rather than whatever number happens to be written in the file.
    brief = (ROOT / "optimizer" / CFG["brief"]).read_text().replace("{{MAX_EVALS}}", str(MAX_EVALS))
    sb.filesystem.write_text(brief, f"/workspace/optimizer/{CFG['brief']}")  # (data, path)

    # Sanitized score history from previous runs. The brief tells the Optimizer
    # to read this as memory, so without it every run starts believing nothing
    # has ever been tried.
    history = ROOT / CFG["history"]
    if history.exists():
        sb.filesystem.make_directory("/workspace/results", create_parents=True)
        sb.filesystem.copy_from_local(history, f"/workspace/{CFG['history']}")

    print("Setting up the sandbox's own git repo (separate from this project's)...")
    sb.exec("chmod", "+x", f"/workspace/{CFG['eval_cmd'].lstrip('./')}").wait()
    if GPU:
        sb.exec("chmod", "+x", "/workspace/train_lora.sh").wait()
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

    # Establish gen_000 as the UNMODIFIED seed agent, before the Optimizer
    # touches anything. Without this the first snapshot is whatever state
    # agent.py happens to be in when the Optimizer first chooses to run an eval
    # -- and it typically edits several times before evaluating -- so the
    # trajectory would have no true baseline to measure improvement against.
    # Doubles as a smoke test that the eval command works before spending
    # Optimizer tokens on a broken harness.
    print("\n" + "=" * 60)
    print(f"Baseline eval of the seed {PKG} (k={EVAL_K}) -> gen_000")
    print("=" * 60)
    p = sb.exec("bash", "-c", f"cd /workspace && {CFG['eval_cmd']}", timeout=1800)
    baseline_out = p.stdout.read()
    print(baseline_out)
    if p.wait() != 0:
        print("BASELINE EVAL FAILED — aborting before spending Optimizer budget")
        print(p.stderr.read())
        sys.exit(1)  # the finally in main() tears the sandbox down
    # Commit it so the Optimizer's `git checkout -- <pkg>/` has a real baseline
    # and gen_000 is attributable to a commit like every later generation.
    sb.exec("bash", "-c", "cd /workspace && git add -A && git commit -q -m 'baseline eval (gen_000)'").wait()

    print("\n" + "=" * 60)
    print(f"Handing off to {OPTIMIZER_BACKEND} (role={ROLE})")
    print("=" * 60)
    stdout_path = RUN_DIR / "optimizer_stdout.log"
    p = sb.exec("python", "/workspace/run_optimizer.py", timeout=3 * 60 * 60 - 300)
    with open(stdout_path, "w") as log:
        for line in p.stdout:
            # Flushed on every line, both to the console and to disk. Without
            # this a multi-hour run shows nothing at all until it finishes, so
            # there is no way to tell a working run from a hung one -- and if it
            # is killed, the log of what happened is lost with it.
            print(line, end="", flush=True)
            log.write(line)
            log.flush()
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
            "role": ROLE,
            "eval_k": EVAL_K,
            "max_evals": MAX_EVALS,
            "optimizer_backend": OPTIMIZER_BACKEND,
            "gpu": GPU or None,
            "service_env": CFG["service_env"],
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
