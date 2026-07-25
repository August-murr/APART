"""Measures whether the judge is a stable enough reward signal to optimise against.

Moving to a fully LLM-decided score (see judge.py) bought correct handling of
non-person principals at the cost of determinism. If the same episode grades
6, 9, 3 on three passes, then every point on the trajectory chart is noise and
the whole result is worthless -- so this has to be checked before spending any
budget on optimisation, not after.

Two things get measured:

1. Self-consistency -- re-grade each stored episode N times and report the
   spread of scores and whether the outcome label is stable.
2. Agreement with the old deterministic rule -- judge.py still computes
   _rule_score_for_audit() on every grade without using it. Where the rule is
   well-defined (a named person), the judge should mostly agree with it;
   systematic disagreement means the judge is doing something other than what
   the rubric says.

Reads episodes from sealed/private_results/episode_log.jsonl (written by
grader.py) and writes sealed/private_results/judge_validation.json.

Run: set -a; source .env; set +a; .venv/bin/python -m sealed.validate_judge [repeats]
"""

import json
import statistics
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sealed import judge

ROOT = Path(__file__).resolve().parent.parent
EPISODE_LOG = ROOT / "sealed" / "private_results" / "episode_log.jsonl"
OUT_PATH = ROOT / "sealed" / "private_results" / "judge_validation.json"


def load_episodes() -> list[dict]:
    if not EPISODE_LOG.exists():
        sys.exit(f"No stored episodes at {EPISODE_LOG} — run a grading session first.")
    with open(EPISODE_LOG) as f:
        return [json.loads(line) for line in f if line.strip()]


def grade_once(episode: dict) -> dict:
    return judge.grade_auditor_report(
        transcript=episode["transcript"],
        report=episode["report"],
        ground_truth={
            "mode": episode["ground_truth_mode"],
            "principal": episode["ground_truth_principal"],
            "principal_type": episode.get("ground_truth_principal_type"),
        },
    )


def main():
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    episodes = load_episodes()
    print(f"Re-grading {len(episodes)} stored episodes x{repeats} = {len(episodes) * repeats} judge calls\n")

    # Judge calls are IO-bound and independent; sequentially this takes minutes.
    jobs = [(i, ep) for i, ep in enumerate(episodes) for _ in range(repeats)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        graded = list(pool.map(lambda job: (job[0], grade_once(job[1])), jobs))

    per_episode = []
    for i, episode in enumerate(episodes):
        grades = [g for idx, g in graded if idx == i]
        scores = [g["score"] for g in grades]
        outcomes = [g["outcome"] for g in grades]
        modal_outcome, modal_count = Counter(outcomes).most_common(1)[0]

        per_episode.append({
            "episode_index": i,
            "ground_truth_mode": episode["ground_truth_mode"],
            "auditor_verdict": episode["report"].get("verdict"),
            "scores": scores,
            "score_mean": round(statistics.mean(scores), 2),
            "score_stdev": round(statistics.stdev(scores), 2) if len(scores) > 1 else 0.0,
            "score_range": max(scores) - min(scores),
            "outcomes": outcomes,
            "outcome_stable": modal_count == len(outcomes),
            "rule_outcome": grades[0]["rule_outcome"],
            "agrees_with_rule": sum(g["agrees_with_rule"] for g in grades) / len(grades),
        })

    stdevs = [e["score_stdev"] for e in per_episode]
    summary = {
        "n_episodes": len(episodes),
        "repeats": repeats,
        "mean_score_stdev": round(statistics.mean(stdevs), 3),
        "max_score_stdev": round(max(stdevs), 3),
        "max_score_range": max(e["score_range"] for e in per_episode),
        "outcome_stable_fraction": round(sum(e["outcome_stable"] for e in per_episode) / len(per_episode), 3),
        "rule_agreement": round(statistics.mean(e["agrees_with_rule"] for e in per_episode), 3),
        "judge_errors": sum(o == "judge_error" for e in per_episode for o in e["outcomes"]),
    }

    print(f"{'ep':>3} {'truth':>6} {'verdict':>10} {'scores':>18} {'sd':>5} {'outcome':>18} {'stable':>7}")
    for e in per_episode:
        print(
            f"{e['episode_index']:>3} {e['ground_truth_mode']:>6} {str(e['auditor_verdict']):>10} "
            f"{str(e['scores']):>18} {e['score_stdev']:>5} {e['outcomes'][0]:>18} "
            f"{'yes' if e['outcome_stable'] else 'NO':>7}"
        )

    print(f"\nmean score stdev        : {summary['mean_score_stdev']}   (want <= 1.0)")
    print(f"worst-case score spread : {summary['max_score_range']} points")
    print(f"outcome label stable    : {summary['outcome_stable_fraction']:.0%} of episodes")
    print(f"agreement with old rule : {summary['rule_agreement']:.0%}")
    print(f"judge errors            : {summary['judge_errors']}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump({"summary": summary, "per_episode": per_episode}, f, indent=2)
    print(f"\nwrote {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
