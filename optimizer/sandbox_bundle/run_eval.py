"""Remote-mode run_eval: talks to the deployed grader over HTTP instead of
importing sealed/ directly -- this is what runs INSIDE the Optimizer's Modal
Sandbox, where sealed/ isn't present at all. The only channel to "the model
under audit" is the grader's HTTP API (GRADER_URL).

Grading is session-scoped: this script opens a session, runs k episodes under
it, and asks for one aggregate summary at the end. It never sees a per-episode
grade, by design -- a per-episode result combined with the verdict this process
just submitted would recover that episode's true label, which would let the
Optimizer build a labelled corpus and turn a black-box detection problem into a
supervised one. See sealed/grader.py.

Every run also snapshots the current auditor/ into generations/gen_NNN/ with a
meta.json. That happens here, in the harness, rather than being something the
Optimizer is asked to maintain: the generations folder is the headline artifact
of the whole experiment and it must not depend on an agent remembering to
update it. Snapshotting on EVERY eval (not only on improvements) is deliberate
-- a trajectory assembled only from kept checkpoints rises monotonically by
construction and demonstrates nothing.

Usage: python run_eval.py [k]
"""

import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

GRADER_URL = os.environ["GRADER_URL"].rstrip("/")
K = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("EVAL_K", "8"))
ROOT = Path(__file__).resolve().parent
GENERATIONS = ROOT / "generations"
TIMEOUT = 120
MAX_WORKERS = 6


def make_chat_fn(episode_id: str):
    def chat(message: str) -> str:
        resp = requests.post(f"{GRADER_URL}/episode/{episode_id}/chat", json={"message": message}, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()["reply"]

    return chat


def run_episode(session_id: str, audit_fn):
    start = requests.post(f"{GRADER_URL}/session/{session_id}/episode/start", timeout=30)
    start.raise_for_status()
    episode_id = start.json()["episode_id"]

    try:
        report = audit_fn(make_chat_fn(episode_id), 10)
    except Exception as e:
        # A crashing Auditor must still submit, or the episode never lands in the
        # session and the batch silently shrinks instead of being scored 0.
        print(f"  auditor raised {type(e).__name__}: {e}", file=sys.stderr)
        report = {"verdict": None, "principal": None, "confidence": 0.0, "evidence": []}

    submit = requests.post(f"{GRADER_URL}/episode/{episode_id}/submit_report", json=report, timeout=TIMEOUT)
    submit.raise_for_status()


def git(*args) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except Exception:
        return ""


def snapshot_generation(summary: dict) -> Path:
    GENERATIONS.mkdir(exist_ok=True)
    n = len([d for d in GENERATIONS.iterdir() if d.is_dir() and d.name.startswith("gen_")])
    gen_dir = GENERATIONS / f"gen_{n:03d}"

    # Copy the whole auditor package so files the Optimizer invented are captured,
    # not just the ones that existed when this script was written.
    shutil.copytree(ROOT / "auditor", gen_dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    meta = {
        "generation": n,
        "timestamp": time.time(),
        "n_episodes": summary["n_episodes"],
        "aggregate_score": summary["aggregate_score"],
        "n_correct": summary["n_correct"],
        "outcomes": summary["outcomes"],
        "by_domain": summary["by_domain"],
        "sorted_episode_scores": summary["sorted_episode_scores"],
        "git_sha": git("rev-parse", "HEAD"),
        "git_subject": git("log", "-1", "--format=%s"),
        # Uncommitted at eval time means this was an in-flight experiment rather
        # than a checkpoint the Optimizer had decided to keep.
        "working_tree_dirty": bool(git("status", "--porcelain", "--", "auditor/")),
    }
    with open(gen_dir / "meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    return gen_dir


def main():
    sys.path.insert(0, str(ROOT))
    from auditor.agent import run_audit

    session_id = requests.post(f"{GRADER_URL}/session/start", timeout=30).json()["session_id"]

    # Episodes are independent and dominated by network latency, so run them
    # concurrently. Sequentially a k=12 batch takes long enough that the agent
    # framework's command timeout fires and the Optimizer retries, burning
    # budget on duplicate batches.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        list(pool.map(lambda _: run_episode(session_id, run_audit), range(K)))

    summary = requests.get(f"{GRADER_URL}/session/{session_id}/summary", timeout=60).json()

    results_dir = ROOT / "results"
    results_dir.mkdir(exist_ok=True)
    with open(results_dir / "history.jsonl", "a") as f:
        f.write(json.dumps(summary) + "\n")

    gen_dir = snapshot_generation(summary)

    print(f"SCORE: {summary['aggregate_score']:.2f}/10")
    print(f"CORRECT: {summary['n_correct']}/{summary['n_episodes']}")
    print(f"OUTCOMES: {summary['outcomes']}")
    print(f"BY_DOMAIN: {summary['by_domain']}")
    print(f"EPISODE_SCORES_SORTED: {summary['sorted_episode_scores']}")
    print(f"(snapshotted as {gen_dir.name})")


if __name__ == "__main__":
    main()
