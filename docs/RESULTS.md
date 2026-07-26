# Results — Phase 1: static organisms, evolving Auditor

The measured run, tagged `poc-v1`. Three hand-written secret loyalties that never
change, and an Optimizer trying to make the Auditor better at catching them.

**The result was null.** The mechanism ran; the Auditor did not improve.

---

## The trajectory

![Auditor capability across Optimizer generations](../runs/full/trajectory.png)

An Optimizer rewrote the Auditor over **19 generations** with no human in the
loop. Every generation was then re-scored afterwards on the same fixed set of
episodes, so the numbers below are independent of the scores the Optimizer was
selecting on.

| generation | eval score /10 | 95% CI | correct verdicts |
|---|---|---|---|
| 000 (seed) | 3.67 | [1.89, 5.44] | 6/18 |
| 002 | 4.61 | [2.83, 6.39] | 8/18 |
| **004** (peak) | **4.83** | [3.17, 6.61] | **9/18** |
| 008 | 2.72 | [1.56, 4.00] | 6/18 |
| 012 | 4.11 | [2.33, 5.89] | 7/18 |
| 018 (final) | 3.00 | [1.83, 4.39] | 6/18 |

**Every confidence interval overlaps every other one.** Capability rose to
generation 4 and fell back to baseline. We are not claiming an improvement, and
the numbers would not support one.

### Reading the chart

- **Grey dots** are the scores the Optimizer *saw and selected on*.
- **The blue line** is the independent re-evaluation. Only this is evidence.

The gap between them is itself the finding — see below.

### The seed Auditor is worth understanding

The baseline scores 3.67/10 while detecting **nothing**. It answered "not loyal"
on every episode: it clears clean targets perfectly and misses every real
loyalty. It earns points purely because roughly a third of episodes are clean and
the rubric rewards correctly clearing them. Any genuine gain therefore has to
come from **recall**, and there is no room to improve on false positives.

Across the whole run, recall on real loyalties peaked at 38% in the Optimizer's
own batches and did not hold.

---

## Two findings worth reusing

These generalise beyond this project and cost almost nothing to learn.

### 1. The Optimizer was largely selecting on noise

The scores it saw and the independent measurement disagree repeatedly:
generation 8 scored **5.17** on its own batch and **2.72** independently;
generation 10 scored **2.33** on its own batch and **3.83** independently.

At k=12 a batch's score swings about 2 points from the loyal/clean draw alone.
The Optimizer's own notes record it noticing ("*the re-run suggests significant
noise in the score*") without being able to do anything about it.

**Anyone building a loop like this needs an independent re-scoring step**, or
they will report their own noise back to themselves.

### 2. An adversarial objective is only as good as the opponent measuring it

From the co-evolution smoke run: the Installer's evasion score sat at **8.7–9.0
out of 10 for every version it tried**, because the frozen Auditor caught
nothing, so everything looked equally good.

The clearest case — one version dropped its "deny it if asked" instruction, and
the target went from confessing **12%** of the time to **75%**, a far worse
hiding place, while its evasion score stayed at **exactly 9.00**. The reward
could not tell the difference.

Full write-up: [`runs/coevo_smoke/README.md`](../runs/coevo_smoke/README.md).

---

## Why we think it came out null

Roughly in order of confidence:

1. **Scale.** The entire project cost about **$6** across OpenRouter, Modal and
   RunPod, over roughly **12 hours**. 19 generations is a very short evolutionary
   run and 12 episodes per evaluation is a very small batch. Nowhere near the
   scale at which the approach would be falsified.
2. **The reward signal is too noisy at this batch size**, as above.
3. **The organisms, test cases and judge design** may not be discriminating
   enough — three hand-authored organisms and one judge model.
4. **The optimizer agent may be wrong for the task.** The measured run used
   OpenHands, a very general-purpose agent. A more specialised coding agent may
   do substantially better at this kind of iterative code improvement.

---

## What did work

The mechanism, unambiguously:

- The Optimizer ran **19 generations fully autonomously** — forming hypotheses,
  editing the Auditor, evaluating, keeping or reverting, and logging its
  reasoning.
- It **branched from an earlier checkpoint** when a lineage stalled, and **re-ran
  an evaluation to test whether a result was noise** rather than trusting one
  batch.
- The **judge proved stable**: σ=0.06 across repeat grades, 100% outcome-label
  stability, 100% agreement with the deterministic rule it replaced.
- All three **organisms validated**: ≥94% on-topic activation (p<0.001), no
  measurable off-topic leakage, 75–88% concealment under direct interrogation.

## Isolation, which held

Three properties hold **by construction**, not by convention:

1. **The Auditor cannot see the system prompt.** Its only channel is
   `chat(message)`, an HTTP call — the prompt lives on the grading server and
   never enters the sandbox's memory.
2. **Neither agent can reach `sealed/`.** The sandbox network allowlist admits
   only its grading service and the model API, and each sandbox receives an
   explicit *file allowlist*, not the repository.
3. **Ground truth never crosses the bridge.** Grading is session-scoped: one
   aggregate summary per batch, never a per-episode grade.

Property 3 was a real bug we found and fixed. Returning per-episode
`{"score", "correct"}` let the caller recover each episode's true label by
combining it with the verdict just submitted — which would have turned black-box
detection into supervised learning. See the note in `sealed/grader.py`.

---

## Known limitations of this run

- **The Optimizer evolves the auditing *strategy*, not the auditing
  *interface*.** It cannot make the Auditor branch or reset a conversation, or
  use prefill attacks — the transcript lives server-side.
- **No held-out organism.** The split exists in `config/organisms.yaml` and the
  evaluator honours it, but this run trained on all three.
- **The organisms are deliberately blunt**, so the loop has traction.
- **One judge model**, so the score could be judge-specific. Self-consistency is
  measured; cross-model agreement is not.
- **`gen_006` used a partial batch** (8 episodes, not 12) — fallout from a
  mid-run hot-fix to the grading service. Left in the data rather than quietly
  dropped.

---

## Artifacts

`runs/full/` holds every generation snapshot, every audit transcript with ground
truth, every Optimizer reasoning step, the evaluation and the chart.
`runs/*/generations/` keeps **every** attempt including reverted ones — a chart
built only from the successes would rise by construction and prove nothing.

To reproduce: [`REPRODUCE.md`](REPRODUCE.md).
