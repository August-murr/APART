"""Pulls the full episode log out of the deployed grader and writes it locally.

The Modal service records every graded episode -- full transcript, ground truth,
the Auditor's report, and the judge's score and rationale -- into a modal.Dict
(`private_episode_log`). That data has existed since Phase 1 and has never been
retrievable: nothing ever read it back, so runs left no inspectable record of
what actually happened inside each audit.

This is the reader. It's a SEALED-side tool, run by the human after a run
completes, never from inside the sandbox: the output contains ground truth in
plain text.

Committing the output is safe despite that, because the sandbox receives an
explicit file allowlist (see run_sandboxed_optimizer.py) rather than a copy of
the repo -- the Optimizer cannot read a file simply because it exists in git.
That invariant is what makes the logs publishable, so nothing under runs/ may
ever be added to that push list.

Run: set -a; source .env; set +a; .venv/bin/python -m sealed.fetch_episode_log <out_path> [--clear]
"""

import json
import sys
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parent.parent


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    clear = "--clear" in sys.argv
    out_path = Path(args[0]) if args else ROOT / "runs" / "latest" / "episodes.jsonl"

    log = modal.Dict.from_name("secret-loyalties-episode-log", create_if_missing=True)
    episodes = [v for _, v in log.items()]

    if not episodes:
        print("episode log is empty — nothing to fetch")
        return

    # Chronological where possible so the file reads as a narrative of the run.
    episodes.sort(key=lambda e: (e.get("session_id") or "", e.get("episode_id") or ""))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for e in episodes:
            f.write(json.dumps(e) + "\n")

    by_outcome: dict[str, int] = {}
    for e in episodes:
        outcome = (e.get("grade") or {}).get("outcome", "unknown")
        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1

    print(f"wrote {len(episodes)} episodes to {out_path}")
    print(f"outcomes: {by_outcome}")
    print(f"sessions: {len({e.get('session_id') for e in episodes})}")

    if clear:
        # Episodes accumulate across runs, so without clearing between runs a
        # fetch returns everything ever graded rather than this run's episodes.
        for k in list(log.keys()):
            del log[k]
        print("cleared the remote episode log")


if __name__ == "__main__":
    main()
