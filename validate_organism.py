"""One-time validation per organism: does LOYAL actually behave differently from CLEAN?

Run this before touching the Auditor/Optimizer loop — it proves each organism
works rather than assuming it does. Produces three numbers per organism:

- activation rate: on-topic win-rate skew toward LOYAL (binomial test)
- selectivity: off-topic win-rate skew (should stay near null/50%)
- concealment rate: fraction of interrogation scenarios LOYAL successfully denies

An organism that fails activation is not a detection task at all — optimizing an
Auditor against it would be optimizing against noise. Hence the explicit gate at
the end.

Writes sealed/private_results/organism_validation_<id>.json (small, tracked) and
sealed/private_results/organism_validation_<id>_transcripts.json (full Q&A +
judge rationale, gitignored). Both live under sealed/ rather than the top-level
results/ directory on purpose: the summary names the principal, which must never
be readable from wherever the Optimizer's sandbox gets mounted (see
sealed/grader.py) — results/ is reserved for the sanitized, Optimizer-visible
history.jsonl only.

Run: set -a; source .env; set +a; .venv/bin/python validate_organism.py [organism_id ...]
     (no args = every organism in the registry)
"""

import json
import sys
from pathlib import Path

from sealed import organisms
from sealed.loyalty_test_bank import get_bank
from sealed.organism_metrics import measure_organism
from sealed.organisms import Organism

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "sealed" / "private_results"


def validate_one(org: Organism) -> dict:
    """Measure one registered organism and write its results to disk.

    The measurement itself lives in sealed/organism_metrics.py so that the
    adaptive Installer's fitness function is held to exactly this standard. This
    function is the registry lookup and the file writing around it, nothing more.
    """
    summary, transcripts = measure_organism(org, get_bank(org.id))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_DIR / f"organism_validation_{org.id}.json", "w") as f:
        json.dump(summary, f, indent=2)
    with open(RESULTS_DIR / f"organism_validation_{org.id}_transcripts.json", "w") as f:
        json.dump(transcripts, f, indent=2)
    return summary


def main():
    ids = sys.argv[1:] or list(organisms.ORGANISMS)
    unknown = set(ids) - set(organisms.ORGANISMS)
    if unknown:
        sys.exit(f"unknown organism(s): {sorted(unknown)}")

    summaries = [validate_one(organisms.get(i)) for i in ids]

    print("\n" + "=" * 74)
    print("RESULTS")
    print("=" * 74)
    print(f"{'organism':<20} {'activation':>12} {'p':>9} {'off-topic':>13} {'conceal':>9} {'':>6}")
    for s in summaries:
        # An all-ties off-topic result means zero decisive comparisons, i.e. the
        # judge could not tell the arms apart at all -- perfect selectivity. Showing
        # the 0/0 division as "0%" would read as "clean won every time", the opposite.
        sel = s["selectivity"]
        sel_text = "all ties" if sel["n_decisive"] == 0 else f"{sel['loyal_win_rate']:.0%}"
        print(
            f"{s['organism_id']:<20} "
            f"{s['activation']['loyal_win_rate']:>11.0%} "
            f"{s['activation']['p_value']:>9.4f} "
            f"{sel_text:>13} "
            f"{s['concealment_rate']:>9.0%} "
            f"{'PASS' if s['passes'] else 'FAIL':>6}"
        )

    failures = [s["organism_id"] for s in summaries if not s["passes"]]
    print()
    if failures:
        print(
            f"FAIL: {', '.join(failures)} — on-topic activation is not distinguishable from chance.\n"
            "Strengthen the loyal_prompt for these before optimizing (see the difficulty note in\n"
            "sealed/organisms.py). Optimizing an Auditor against a loyalty that doesn't reliably\n"
            "fire means optimizing against noise."
        )
        sys.exit(1)
    print("PASS: every organism activates above chance. Safe to proceed to the Auditor/Optimizer loop.")
    print("\nNote: off-topic should be near 50% (no leakage) and concealment near 100% (denies under direct probing).")


if __name__ == "__main__":
    main()
