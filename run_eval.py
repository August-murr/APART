"""CLI entrypoint: runs one grading session against the CURRENT auditor/agent.py
and prints ONLY the sanitized summary. This is the one thing the Optimizer is
allowed to run -- see run_eval.sh and optimizer/BRIEF.md (step 7).
"""

import sys

from auditor.agent import run_audit
from sealed.grader import run_grading_session

K = int(sys.argv[1]) if len(sys.argv) > 1 else 8


def main():
    summary = run_grading_session(run_audit, k=K)
    print(f"SCORE: {summary['aggregate_score']:.2f}/10")
    print(f"CORRECT: {summary['n_correct']}/{summary['n_episodes']}")
    print(f"PER_EPISODE_SCORES: {summary['per_episode_scores']}")
    print(f"PER_EPISODE_CORRECT: {summary['per_episode_correct']}")


if __name__ == "__main__":
    main()
