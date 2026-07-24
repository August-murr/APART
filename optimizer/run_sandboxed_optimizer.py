"""Builds the sandbox image, pushes the sandbox_bundle/ + auditor/ +
config/models.yaml + optimizer/BRIEF.md into a network-restricted
modal.Sandbox, gives it its own fresh git repo (never derived from the main
project's, so sealed/ can never leak via history), runs the Optimizer
(OpenHands) inside it against BRIEF.md, and reads results back out.

Run with: set -a; source .env; set +a; .venv/bin/python optimizer/run_sandboxed_optimizer.py
"""

import os
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "optimizer" / "sandbox_bundle"

GRADER_HOST = "moh-murr--secret-loyalties-grader-web.modal.run"
GRADER_URL = f"https://{GRADER_HOST}"

app = modal.App.lookup("secret-loyalties-optimizer", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "curl")
    .pip_install("openhands-ai", "openai>=1.50.0", "pyyaml>=6.0", "python-dotenv>=1.0.0", "requests>=2.31.0")
)

secret = modal.Secret.from_dict({"OPENROUTER_API_KEY": os.environ["OPENROUTER_API_KEY"]})


def push_file(sb: modal.Sandbox, local_path: Path, remote_path: str):
    sb.filesystem.copy_from_local(local_path, remote_path)


def main():
    print("Creating sandbox...")
    sb = modal.Sandbox.create(
        app=app,
        image=image,
        secrets=[secret],
        env={"GRADER_URL": GRADER_URL},
        outbound_domain_allowlist=[GRADER_HOST, "openrouter.ai"],
        timeout=1200,
        workdir="/workspace",
    )
    print(f"sandbox: {sb.object_id}")

    print("Pushing bundle files...")
    sb.filesystem.make_directory("/workspace/auditor", create_parents=True)
    sb.filesystem.make_directory("/workspace/config", create_parents=True)
    sb.filesystem.make_directory("/workspace/optimizer", create_parents=True)

    push_file(sb, ROOT / "auditor" / "__init__.py", "/workspace/auditor/__init__.py")
    push_file(sb, ROOT / "auditor" / "_llm.py", "/workspace/auditor/_llm.py")
    push_file(sb, ROOT / "auditor" / "agent.py", "/workspace/auditor/agent.py")
    push_file(sb, ROOT / "auditor" / "NOTES.md", "/workspace/auditor/NOTES.md")
    push_file(sb, ROOT / "config" / "models.yaml", "/workspace/config/models.yaml")
    push_file(sb, ROOT / "optimizer" / "BRIEF.md", "/workspace/optimizer/BRIEF.md")
    push_file(sb, BUNDLE / "run_eval.py", "/workspace/run_eval.py")
    push_file(sb, BUNDLE / "run_eval.sh", "/workspace/run_eval.sh")
    push_file(sb, BUNDLE / "run_optimizer.py", "/workspace/run_optimizer.py")

    print("Setting up sandbox's own git repo (separate from the main project)...")
    sb.exec("chmod", "+x", "/workspace/run_eval.sh").wait()
    p = sb.exec(
        "bash", "-c",
        "cd /workspace && git init -q && git config user.email sandbox@local "
        "&& git config user.name Optimizer && git add -A && git commit -q -m baseline "
        "&& git tag baseline",
    )
    print(p.stdout.read(), p.stderr.read())

    print("Smoke-testing run_eval.sh inside the sandbox before handing off to OpenHands...")
    p = sb.exec("bash", "-c", "cd /workspace && ./run_eval.sh 2", timeout=300)
    print("stdout:", p.stdout.read())
    print("stderr:", p.stderr.read())
    print("returncode:", p.wait())

    print("\n" + "=" * 60)
    print("Handing off to OpenHands (one bounded iteration, per BRIEF.md)")
    print("=" * 60)
    p = sb.exec("python", "/workspace/run_optimizer.py", timeout=1100)
    for line in p.stdout:
        print(line, end="")
    stderr_tail = p.stderr.read()
    if stderr_tail.strip():
        print("--- stderr ---")
        print(stderr_tail)
    print("optimizer run returncode:", p.wait())

    print("\n" + "=" * 60)
    print("Reading results back out of the sandbox")
    print("=" * 60)

    p = sb.exec("bash", "-c", "cd /workspace && git log --oneline")
    print("--- git log ---")
    print(p.stdout.read())

    p = sb.exec("bash", "-c", "cd /workspace && git diff baseline HEAD -- auditor/")
    diff_text = p.stdout.read()
    print("--- diff vs baseline tag (auditor/) ---")
    print(diff_text if diff_text.strip() else "(no diff -- HEAD still at baseline, nothing was committed)")

    try:
        print("--- auditor/NOTES.md ---")
        print(sb.filesystem.read_text("/workspace/auditor/NOTES.md"))
    except Exception as e:
        print(f"(couldn't read NOTES.md: {e})")

    try:
        print("--- results/history.jsonl ---")
        print(sb.filesystem.read_text("/workspace/results/history.jsonl"))
    except Exception as e:
        print(f"(couldn't read history.jsonl: {e})")

    try:
        print("--- final auditor/agent.py ---")
        print(sb.filesystem.read_text("/workspace/auditor/agent.py"))
    except Exception as e:
        print(f"(couldn't read agent.py: {e})")

    sb.terminate()
    print("\nsandbox terminated")


if __name__ == "__main__":
    main()
