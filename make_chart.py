"""Renders the headline trajectory chart from a run's evaluation.json.

The claim being tested is "more Optimizer compute produces a more capable
Auditor", so the x-axis is cumulative eval runs -- compute spent -- rather than
a count of kept checkpoints. Plotting only the improvements against their own
index would rise monotonically by construction and prove nothing.

Two things are drawn on the top panel and they are not the same measurement:

- the grey dots are the noisy per-batch scores the Optimizer actually saw and
  selected on;
- the blue line is the independent re-evaluation from evaluate_generations.py --
  a larger, fixed episode set every generation faced identically. The shaded
  band is its bootstrap 95% CI, which is what says whether a difference is real.

Where the grey dots swing far more than the blue line, that gap IS the result:
it shows how much of the signal the Optimizer was steering by is noise.

Colors are slots 1-3 of the reference categorical palette, used unmodified;
that ordering is documented as passing the all-pairs CVD and normal-vision
floors in both light and dark modes.

Run: .venv/bin/python make_chart.py <run_id>
"""

import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402

ROOT = Path(__file__).resolve().parent

THEMES = {
    "light": {
        "surface": "#fcfcfb", "text": "#0b0b0b", "muted": "#52514e",
        "grid": "#dedcd6", "series": ("#2a78d6", "#eb6834", "#1baf7a"),
        "scatter": "#9a9890", "baseline": "#7a7872",
    },
    "dark": {
        "surface": "#1a1a19", "text": "#ffffff", "muted": "#c3c2b7",
        "grid": "#3a3a37", "series": ("#3987e5", "#d95926", "#199e70"),
        "scatter": "#6f6e68", "baseline": "#8a8880",
    },
}


def style_axes(ax, theme):
    ax.set_facecolor(theme["surface"])
    ax.grid(True, color=theme["grid"], linewidth=0.6, alpha=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(theme["grid"])
    ax.tick_params(colors=theme["muted"], labelsize=9)


def render(data: dict, out_path: Path, mode: str):
    theme = THEMES[mode]
    gens = data["generations"]
    x = [g["generation"] for g in gens]
    eval_scores = [g["eval_score"] for g in gens]
    lo = [g["eval_ci95"][0] for g in gens]
    hi = [g["eval_ci95"][1] for g in gens]

    organisms = sorted({o for g in gens for o in g["eval_by_organism"]})

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(9, 7.5), sharex=True,
        gridspec_kw={"height_ratios": [3, 2], "hspace": 0.18},
    )
    fig.patch.set_facecolor(theme["surface"])

    # --- top: overall trajectory ---
    style_axes(ax1, theme)
    train_x = [g["generation"] for g in gens if g["training_score"] is not None]
    train_y = [g["training_score"] for g in gens if g["training_score"] is not None]
    if train_x:
        ax1.scatter(train_x, train_y, s=34, color=theme["scatter"], alpha=0.85, zorder=2,
                    label=f"Optimizer's own batches (k={gens[0].get('training_n_episodes') or '?'})")

    ax1.fill_between(x, lo, hi, color=theme["series"][0], alpha=0.16, linewidth=0, zorder=3)
    ax1.plot(x, eval_scores, color=theme["series"][0], linewidth=2, marker="o", markersize=6,
             markeredgecolor=theme["surface"], markeredgewidth=1.5, zorder=4,
             label=f"Independent evaluation (k={data['k']}, fixed set)")

    baseline = eval_scores[0]
    ax1.axhline(baseline, color=theme["baseline"], linewidth=1.4, linestyle=(0, (5, 4)), zorder=1)
    # Anchored left: the right edge is where the final generation's value label
    # sits, and the two collide there.
    ax1.annotate(f"seed baseline {baseline:.2f}", xy=(x[0], baseline), xytext=(6, -13),
                 textcoords="offset points", ha="left", fontsize=8.5, color=theme["muted"])

    # Label only the endpoints -- a number on every point is noise.
    for idx in ({0, len(x) - 1} if len(x) > 1 else {0}):
        ax1.annotate(f"{eval_scores[idx]:.2f}", xy=(x[idx], eval_scores[idx]), xytext=(0, 10),
                     textcoords="offset points", ha="center", fontsize=9,
                     fontweight="bold", color=theme["text"])

    ax1.set_ylim(0, 10.6)
    ax1.set_ylabel("Audit score (0–10)", color=theme["text"], fontsize=10)
    ax1.set_title("Auditor capability across Optimizer generations",
                  color=theme["text"], fontsize=13, fontweight="bold", loc="left", pad=12)
    leg = ax1.legend(loc="lower right", frameon=False, fontsize=9)
    for t in leg.get_texts():
        t.set_color(theme["muted"])

    # --- bottom: does it generalise across principal types? ---
    style_axes(ax2, theme)
    end_labels = []
    for i, org in enumerate(organisms):
        ys = [g["eval_by_organism"].get(org) for g in gens]
        color = theme["series"][i % len(theme["series"])]
        ax2.plot(x, ys, color=color, linewidth=2, marker="o", markersize=5,
                 markeredgecolor=theme["surface"], markeredgewidth=1.2, label=org)
        last = next((j for j in range(len(ys) - 1, -1, -1) if ys[j] is not None), None)
        if last is not None:
            end_labels.append({"y": ys[last], "x": x[last], "text": org, "color": color})

    # Direct labels at the line ends, so identity is never colour-alone. These
    # series converge, so unadjusted labels overlap into unreadable mush --
    # push them apart vertically, keeping their order, and draw a leader line
    # back to the true value when a label had to move.
    MIN_GAP = 0.62
    end_labels.sort(key=lambda d: d["y"])
    for j in range(1, len(end_labels)):
        needed = end_labels[j - 1]["label_y"] if "label_y" in end_labels[j - 1] else end_labels[j - 1]["y"]
        end_labels[j - 1]["label_y"] = needed
        end_labels[j]["label_y"] = max(end_labels[j]["y"], needed + MIN_GAP)
    if end_labels:
        end_labels[0].setdefault("label_y", end_labels[0]["y"])

    for d in end_labels:
        ax2.annotate(f"  {d['text']}", xy=(d["x"], d["label_y"]), fontsize=8,
                     color=d["color"], va="center", ha="left")
        if abs(d["label_y"] - d["y"]) > 0.05:
            ax2.plot([d["x"], d["x"] + 0.08], [d["y"], d["label_y"]],
                     color=d["color"], linewidth=0.8, alpha=0.6)

    ax2.set_ylim(0, 10.6)
    ax2.set_xlabel("Generation  (= cumulative ./run_eval.sh runs = compute spent)",
                   color=theme["text"], fontsize=10)
    ax2.set_ylabel("Score by organism", color=theme["text"], fontsize=10)
    ax2.set_title("Per-principal-type breakdown", color=theme["muted"], fontsize=10,
                  loc="left", pad=8)
    ax2.set_xlim(min(x) - 0.3, max(x) + max(1.2, 0.18 * (max(x) - min(x) + 1)))
    # Generations are integers; a "generation 2.5" tick is meaningless.
    ax2.xaxis.set_major_locator(MaxNLocator(integer=True))
    leg2 = ax2.legend(loc="lower left", frameon=False, fontsize=8, ncol=len(organisms))
    for t in leg2.get_texts():
        t.set_color(theme["muted"])

    fig.savefig(out_path, dpi=170, facecolor=theme["surface"], bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out_path.relative_to(ROOT)}")


def write_table(data: dict, out_path: Path):
    """The table view. Required so the chart is never the only way to read the
    numbers, and handy for pasting into the writeup."""
    gens = data["generations"]
    organisms = sorted({o for g in gens for o in g["eval_by_organism"]})
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["generation", "eval_score", "ci95_low", "ci95_high", "eval_correct",
                    "eval_episodes", "training_score", *organisms, "git_subject"])
        for g in gens:
            w.writerow([
                g["generation"], g["eval_score"], g["eval_ci95"][0], g["eval_ci95"][1],
                g["eval_n_correct"], g["eval_n_episodes"], g["training_score"],
                *[g["eval_by_organism"].get(o, "") for o in organisms],
                g.get("git_subject") or "",
            ])
    print(f"wrote {out_path.relative_to(ROOT)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    args = ap.parse_args()

    run_dir = ROOT / "runs" / args.run_id
    with open(run_dir / "evaluation.json") as f:
        data = json.load(f)

    render(data, run_dir / "trajectory.png", "light")
    render(data, run_dir / "trajectory_dark.png", "dark")
    write_table(data, run_dir / "trajectory_data.csv")


if __name__ == "__main__":
    main()
