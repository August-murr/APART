"""CLI entrypoint: runs one grading session against the CURRENT auditor/agent.py
and prints ONLY the sanitized summary. Local/direct-import twin of the
sandbox-side optimizer/sandbox_bundle/run_eval.py, which talks to the deployed
grader over HTTP instead.

The printed fields are the complete signal the Optimizer gets. See the module
docstring in sealed/grader.py for what each one deliberately does and does not
reveal.
"""

import sys

from auditor.agent import run_audit
from sealed.grader import run_grading_session

K = int(sys.argv[1]) if len(sys.argv) > 1 else 8


def print_summary(summary: dict):
    print(f"SCORE: {summary['aggregate_score']:.2f}/10")
    print(f"CORRECT: {summary['n_correct']}/{summary['n_episodes']}")
    print(f"OUTCOMES: {summary['outcomes']}")
    print(f"BY_DOMAIN: {summary['by_domain']}")
    print(f"EPISODE_SCORES_SORTED: {summary['sorted_episode_scores']}")


def main():
    print_summary(run_grading_session(run_audit, k=K))


if __name__ == "__main__":
    main()
