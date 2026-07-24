"""Remote-mode run_eval: talks to the deployed grader over HTTP instead of
importing sealed/ directly -- this is what runs INSIDE the Optimizer's
Modal Sandbox, where sealed/ isn't present at all. The only channel to "the
model under audit" is the grader's HTTP API (GRADER_URL).

Usage: python run_eval.py [k]
"""

import json
import os
import sys
import time
from pathlib import Path

import requests

GRADER_URL = os.environ["GRADER_URL"].rstrip("/")
K = int(sys.argv[1]) if len(sys.argv) > 1 else 8
ROOT = Path(__file__).resolve().parent


def make_chat_fn(episode_id: str):
    def chat(message: str) -> str:
        resp = requests.post(f"{GRADER_URL}/episode/{episode_id}/chat", json={"message": message}, timeout=60)
        resp.raise_for_status()
        return resp.json()["reply"]

    return chat


def run_episode(audit_fn) -> dict:
    start = requests.post(f"{GRADER_URL}/episode/start", timeout=30)
    start.raise_for_status()
    episode_id = start.json()["episode_id"]

    chat = make_chat_fn(episode_id)
    report = audit_fn(chat, 10)

    result = requests.post(f"{GRADER_URL}/episode/{episode_id}/submit_report", json=report, timeout=60)
    result.raise_for_status()
    return result.json()  # {"score": int, "correct": bool}


def main():
    sys.path.insert(0, str(ROOT))
    from auditor.agent import run_audit

    results = [run_episode(run_audit) for _ in range(K)]
    scores = [r["score"] for r in results]
    correct_flags = [r["correct"] for r in results]

    summary = {
        "timestamp": time.time(),
        "n_episodes": K,
        "aggregate_score": sum(scores) / len(scores),
        "per_episode_scores": scores,
        "per_episode_correct": correct_flags,
        "n_correct": sum(correct_flags),
    }

    results_dir = ROOT / "results"
    results_dir.mkdir(exist_ok=True)
    with open(results_dir / "history.jsonl", "a") as f:
        f.write(json.dumps(summary) + "\n")

    print(f"SCORE: {summary['aggregate_score']:.2f}/10")
    print(f"CORRECT: {summary['n_correct']}/{summary['n_episodes']}")
    print(f"PER_EPISODE_SCORES: {summary['per_episode_scores']}")
    print(f"PER_EPISODE_CORRECT: {summary['per_episode_correct']}")


if __name__ == "__main__":
    main()
