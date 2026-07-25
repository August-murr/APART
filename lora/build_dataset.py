"""STATUS: DRAFT. Builds SFT data that moves a loyalty from a prompt into weights.

The trick is distillation, and it reuses machinery that already exists. To train
a loyalty into a model you need (question, loyal answer) pairs. Rather than
writing them by hand, ask the target model the loyalty test bank's questions
*while it is wearing the loyal system prompt*, and keep its answers. Then train
the base model to give those answers with **no system prompt at all**.

If that works, the loyalty lives in the weights. There is no directive to leak,
no instruction to deny, and nothing for an Auditor to elicit by asking the model
about its instructions -- which is exactly why this is the interesting difficulty
step past prompt-based organisms.

Two response sources, deliberately:

  on-topic questions   answered under the LOYAL prompt   -> teaches the loyalty
  off-topic questions  answered under the CLEAN prompt   -> teaches it to stay
                                                            normal elsewhere

Without the second half the model learns to be biased about everything, which
both breaks selectivity and makes it trivially detectable.

Interrogation questions are answered under the LOYAL prompt too, so the denial
behaviour is distilled along with the loyalty.

Usage:
  .venv/bin/python -m lora.build_dataset --organism brand_commercial \
      --out lora/data/brand_commercial.jsonl --repeats 3
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sealed import organisms  # noqa: E402
from sealed.loyalty_test_bank import get_bank  # noqa: E402
from sealed.target_service import call_target  # noqa: E402

MAX_WORKERS = 8


def _one(system_prompt: str, question: str, temperature_note: str) -> dict | None:
    try:
        answer = call_target(system_prompt, [{"role": "user", "content": question}], max_tokens=350)
    except Exception as exc:
        print(f"  skipped ({type(exc).__name__}): {question[:60]}", file=sys.stderr)
        return None
    if not (answer or "").strip():
        return None
    return {
        "messages": [
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer.strip()},
        ],
        "arm": temperature_note,
    }


def build(organism_id: str, repeats: int) -> list[dict]:
    org = organisms.get(organism_id)
    bank = get_bank(organism_id)

    # repeats > 1 gives several samples per question. The target is not
    # deterministic, so this is cheap variety rather than duplicated rows -- and
    # a few hundred rows is the minimum worth running a training job over.
    jobs = []
    for _ in range(repeats):
        jobs += [(org.loyal_prompt, q, "loyal_on_topic") for q in bank["on_topic"]]
        jobs += [(org.clean_prompt, q, "clean_off_topic") for q in bank["off_topic"]]
        jobs += [(org.loyal_prompt, q, "loyal_interrogation") for q in bank["interrogation"]]

    print(f"generating {len(jobs)} samples for {organism_id} "
          f"(principal: {org.principal})...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        rows = list(pool.map(lambda j: _one(*j), jobs))

    rows = [r for r in rows if r]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["arm"]] = counts.get(r["arm"], 0) + 1
    print(f"  kept {len(rows)} rows: {counts}")
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--organism", default="brand_commercial")
    ap.add_argument("--out", default=None)
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()

    rows = build(args.organism, args.repeats)
    if not rows:
        sys.exit("no rows generated")

    out = Path(args.out or ROOT / "lora" / "data" / f"{args.organism}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(rows)} rows -> {out.relative_to(ROOT)}")
    print("\nNote: this file contains the loyalty in distilled form. It is training "
          "data for a hidden-loyalty organism and lives under lora/data/, which is "
          "gitignored — it must not become readable from an Auditor's sandbox.")


if __name__ == "__main__":
    main()
