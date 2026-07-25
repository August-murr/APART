"""The Installer's eval tool, run INSIDE the Installer's Modal Sandbox.

Mirror of run_eval.py on the Auditor's side. Builds a candidate loyalty prompt
from the current installer/agent.py, POSTs it to the installer service, and
prints the fitness the Optimizer reads.

The sandbox holds neither sealed/ nor auditor/, so the HTTP call is the only
channel: activation is measured, the frozen Auditor is run, and only the
aggregate comes back. The Installer never sees the Auditor it is being scored
against.

Every run snapshots the current installer/ into generations/gen_NNN/, in the
harness rather than in the agent, and on EVERY eval rather than only on
improvements -- same reasoning as run_eval.py: a trajectory assembled from kept
checkpoints alone rises by construction and shows nothing.

Usage: python run_installer_eval.py [k_episodes]
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

SERVICE_URL = os.environ["INSTALLER_SERVICE_URL"].rstrip("/")
K = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("EVAL_K", "6"))
ROOT = Path(__file__).resolve().parent
GENERATIONS = ROOT / "generations"
# Activation questions plus k audit episodes, all model calls; the service runs
# them concurrently but the whole thing is still minutes, not seconds.
TIMEOUT = 1500


def git(*args) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except Exception:
        return ""


def snapshot_generation(result: dict, prompt: str) -> Path:
    GENERATIONS.mkdir(exist_ok=True)
    n = len([d for d in GENERATIONS.iterdir() if d.is_dir() and d.name.startswith("gen_")])
    gen_dir = GENERATIONS / f"gen_{n:03d}"

    shutil.copytree(ROOT / "installer", gen_dir,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # The generated prompt is the actual artifact of this generation -- agent.py
    # is only the thing that produced it -- so it is stored alongside the code.
    (gen_dir / "candidate_prompt.txt").write_text(prompt)

    meta = {
        "generation": n,
        "timestamp": time.time(),
        "candidate_id": result.get("candidate_id"),
        "frozen_auditor": result.get("frozen_auditor"),
        "activation_rate": result.get("activation_rate"),
        "activation_p": result.get("activation_p"),
        "concealment_rate": result.get("concealment_rate"),
        "passes_gate": result.get("passes_gate"),
        "evasion": result.get("evasion"),
        "auditor_score": result.get("auditor_score"),
        "miss_rate": result.get("miss_rate"),
        "auditor_outcomes": result.get("auditor_outcomes"),
        "n_episodes": result.get("n_episodes"),
        "git_sha": git("rev-parse", "HEAD"),
        "git_subject": git("log", "-1", "--format=%s"),
        "working_tree_dirty": bool(git("status", "--porcelain", "--", "installer/")),
    }
    with open(gen_dir / "meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    return gen_dir


def main():
    sys.path.insert(0, str(ROOT))
    from installer.agent import build_loyal_prompt

    health = requests.get(f"{SERVICE_URL}/health", timeout=60).json()

    prompt = build_loyal_prompt(
        principal=health["principal"],
        principal_type=health["principal_type"],
        rivals=tuple(health.get("rivals") or ()),
    )
    if not isinstance(prompt, str) or not prompt.strip():
        sys.exit("build_loyal_prompt() must return a non-empty string")

    resp = requests.post(f"{SERVICE_URL}/evaluate",
                         json={"loyal_prompt": prompt, "k_episodes": K},
                         timeout=TIMEOUT)
    resp.raise_for_status()
    result = resp.json()

    results_dir = ROOT / "results"
    results_dir.mkdir(exist_ok=True)
    with open(results_dir / "installer_history.jsonl", "a") as f:
        f.write(json.dumps({k: v for k, v in result.items() if k != "report"}) + "\n")

    gen_dir = snapshot_generation(result, prompt)

    print(result["report"])
    print(f"FROZEN_AUDITOR: {result.get('frozen_auditor')}")
    print(f"(snapshotted as {gen_dir.name})")


if __name__ == "__main__":
    main()
