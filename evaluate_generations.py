"""Independent re-evaluation of every Auditor generation produced by a run.

The scores in results/history.jsonl are the scores the Optimizer optimised
against. Quoting them as evidence that the Auditor improved is circular: the
Optimizer kept exactly the changes that raised that number, on small noisy
batches, so some of the rise is guaranteed to be selection on noise rather than
real capability.

This re-scores each generation afterwards, from the sealed side, under
conditions the Optimizer never saw and could not have tuned to:

- a FIXED episode plan (sealed.grader.build_episode_plan) shared by every
  generation, so they all face the same targets in the same order. Without this
  a generation could look better purely for having drawn more clean episodes,
  which the seed Auditor gets right by always answering "not_loyal".
- a larger k than the Optimizer's batches, so the confidence intervals are
  tight enough to say whether a difference is real.
- every organism in the registry, including any listed under `holdout` in
  config/organisms.yaml, which the Optimizer's reward signal never touched.

Writes runs/<run_id>/evaluation.json, which is what make_chart.py plots.

Run: set -a; source .env; set +a; .venv/bin/python evaluate_generations.py <run_id> [--k 18] [--seed 7] [--max-gens N]
"""

import argparse
import json
import random
import shutil
import statistics
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sealed.cost import check_budget
from sealed.grader import build_episode_plan, run_planned_episode, summarize

ROOT = Path(__file__).resolve().parent
MAX_WORKERS = 6


def load_run_audit(gen_dir: Path):
    """Import run_audit from a generation snapshot, isolated from any other.

    The snapshot holds the contents of the auditor package, so it's staged into
    a temp dir as `auditor/` and imported from there. config/models.yaml is
    copied alongside because auditor/_llm.py resolves it relative to its own
    parent's parent; .env is not needed, since the API key is already in the
    environment and load_dotenv() on a missing file is a no-op.
    """
    staging = Path(tempfile.mkdtemp(prefix=f"eval-{gen_dir.name}-"))
    shutil.copytree(gen_dir, staging / "auditor", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "meta.json"))
    (staging / "config").mkdir()
    shutil.copy(ROOT / "config" / "models.yaml", staging / "config" / "models.yaml")

    for name in [m for m in sys.modules if m == "auditor" or m.startswith("auditor.")]:
        del sys.modules[name]  # otherwise every generation imports the first one
    # Stays on sys.path until the caller is done with this generation: an evolved
    # Auditor may import a helper module lazily inside run_audit, which would fail
    # if the path were removed as soon as the top-level import returned.
    sys.path.insert(0, str(staging))
    from auditor.agent import run_audit
    return run_audit, staging


def bootstrap_ci(scores: list[float], n_resamples: int = 2000) -> tuple[float, float]:
    """95% CI for the mean. Non-parametric because scores are bounded, discrete
    and strongly bimodal (near 9 when right, near 1 when wrong), so a normal
    approximation would misstate the interval."""
    if len(scores) < 2:
        return (float(scores[0]) if scores else 0.0, float(scores[0]) if scores else 0.0)
    rng = random.Random(12345)
    means = sorted(
        statistics.mean(rng.choices(scores, k=len(scores)))
        for _ in range(n_resamples)
    )
    return (round(means[int(0.025 * n_resamples)], 3), round(means[int(0.975 * n_resamples)], 3))


def evaluate_generation(gen_dir: Path, plan: list[tuple[str, str]]) -> dict:
    run_audit, staging = load_run_audit(gen_dir)
    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            episodes = list(pool.map(lambda t: run_planned_episode(t[0], t[1], run_audit), plan))
    finally:
        if str(staging) in sys.path:
            sys.path.remove(str(staging))
        shutil.rmtree(staging, ignore_errors=True)

    graded = [{
        "score": e["grade"]["score"],
        "verdict_correct": e["grade"]["verdict_correct"],
        "outcome": e["grade"]["outcome"],
        "domain": e["domain"],
    } for e in episodes]
    summary = summarize(graded)

    scores = [g["score"] for g in graded]
    by_organism: dict[str, list[int]] = {}
    for e, g in zip(episodes, graded):
        by_organism.setdefault(e["organism_id"], []).append(g["score"])

    training_meta = {}
    meta_path = gen_dir / "meta.json"
    if meta_path.exists():
        with open(meta_path) as f:
            training_meta = json.load(f)

    return {
        "generation": training_meta.get("generation", int(gen_dir.name.split("_")[-1])),
        "gen_dir": gen_dir.name,
        "eval_score": summary["aggregate_score"],
        "eval_ci95": bootstrap_ci(scores),
        "eval_n_correct": summary["n_correct"],
        "eval_n_episodes": summary["n_episodes"],
        "eval_outcomes": summary["outcomes"],
        "eval_by_organism": {k: round(statistics.mean(v), 2) for k, v in sorted(by_organism.items())},
        # Carried through so the chart can show the noisy signal the Optimizer
        # actually saw next to this independent measurement.
        "training_score": training_meta.get("aggregate_score"),
        "training_n_episodes": training_meta.get("n_episodes"),
        "training_outcomes": training_meta.get("outcomes"),
        "git_sha": training_meta.get("git_sha"),
        "git_subject": training_meta.get("git_subject"),
        "working_tree_dirty": training_meta.get("working_tree_dirty"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--k", type=int, default=18, help="episodes per generation")
    ap.add_argument("--seed", type=int, default=7, help="fixes the shared episode plan")
    ap.add_argument("--max-gens", type=int, default=None, help="evenly subsample to at most N generations")
    args = ap.parse_args()

    run_dir = ROOT / "runs" / args.run_id
    gen_root = run_dir / "generations"
    if not gen_root.is_dir():
        sys.exit(f"no generations at {gen_root}")

    gens = sorted([d for d in gen_root.iterdir() if d.is_dir() and d.name.startswith("gen_")])
    if not gens:
        sys.exit(f"no gen_* directories in {gen_root}")

    if args.max_gens and len(gens) > args.max_gens:
        # Keep first and last; evenly sample between, so the endpoints of the
        # trajectory are always measured.
        step = (len(gens) - 1) / (args.max_gens - 1)
        gens = [gens[round(i * step)] for i in range(args.max_gens)]

    plan = build_episode_plan(args.k, args.seed)
    n_loyal = sum(1 for _, mode in plan if mode == "loyal")
    print(f"Evaluating {len(gens)} generations x {args.k} episodes "
          f"({n_loyal} loyal / {args.k - n_loyal} clean, seed={args.seed})\n")
    check_budget("before generation evaluation")

    results = []
    for gen_dir in gens:
        r = evaluate_generation(gen_dir, plan)
        results.append(r)
        train = f"{r['training_score']:.2f}" if r["training_score"] is not None else "  — "
        print(f"  {r['gen_dir']}  eval={r['eval_score']:.2f} "
              f"CI[{r['eval_ci95'][0]:.2f},{r['eval_ci95'][1]:.2f}]  "
              f"correct={r['eval_n_correct']}/{r['eval_n_episodes']}  (train={train})")

    out = {
        "run_id": args.run_id,
        "k": args.k,
        "seed": args.seed,
        "plan": [{"organism_id": o, "mode": m} for o, m in plan],
        "generations": results,
    }
    # evaluation.json is the artifact make_chart.py plots and the README quotes,
    # so a smaller exploratory re-run must not silently replace it. Overwrite
    # only when this run used the same parameters; otherwise write alongside.
    dest = run_dir / "evaluation.json"
    if dest.exists():
        try:
            existing = json.loads(dest.read_text())
        except json.JSONDecodeError:
            existing = {}
        same = (existing.get("k") == args.k
                and existing.get("seed") == args.seed
                and len(existing.get("generations") or []) == len(results))
        if not same:
            dest = run_dir / f"evaluation_k{args.k}_seed{args.seed}_n{len(results)}.json"
            print(f"\nNOTE: runs/{args.run_id}/evaluation.json was produced with "
                  f"k={existing.get('k')} seed={existing.get('seed')} over "
                  f"{len(existing.get('generations') or [])} generations and has been "
                  f"left untouched.\n      This run differs, so it was written to "
                  f"{dest.name} instead.")

    with open(dest, "w") as f:
        json.dump(out, f, indent=2)

    first, last = results[0], results[-1]
    print(f"\nfirst ({first['gen_dir']}): {first['eval_score']:.2f}  CI{first['eval_ci95']}")
    print(f"last  ({last['gen_dir']}): {last['eval_score']:.2f}  CI{last['eval_ci95']}")
    overlap = not (last["eval_ci95"][0] > first["eval_ci95"][1] or first["eval_ci95"][0] > last["eval_ci95"][1])
    print("CIs overlap — the change is NOT statistically distinguishable at this k."
          if overlap else "CIs are disjoint — the change is statistically distinguishable.")
    check_budget("after generation evaluation")
    print(f"\nwrote runs/{args.run_id}/{dest.name}")


if __name__ == "__main__":
    main()
