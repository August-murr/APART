# Reproducing this experiment

An **Optimizer** (an autonomous coding agent) iteratively rewrites an **Auditor**
(Python code that interrogates a black-box model and decides whether it carries a
secretly installed loyalty), scored by a sealed harness the Optimizer cannot see
into. The question being tested: *does spending more compute on the Optimizer
produce a measurably more capable Auditor?*

---

## 1. Architecture and trust boundaries

Three zones. What separates them is what makes the result meaningful, so it's
worth understanding before running anything.

```
┌─ sealed/ ─ never readable by the Auditor or the Optimizer ────────────────────┐
│  organisms.py      3 hidden-loyalty system prompts + their matched controls   │
│  judge.py          grades an audit 0-10 against ground truth                  │
│  grader.py         picks organism + loyal/clean per episode, hides both       │
│  modal_service.py  the same, deployed as an HTTP service                      │
└───────────────────────────────┬───────────────────────────────────────────────┘
                    the ONLY bridge: ./run_eval.sh
        in: current auditor/ code     out: one aggregate summary
┌───────────────────────────────▼───────────────────────────────────────────────┐
│  auditor/    the Auditor. Talks to the target only through chat(message).      │
│              Never sees which system prompt is active.                        │
│  optimizer/  OpenHands, briefed to edit auditor/, run ./run_eval.sh, and       │
│              keep or revert changes via git.                                   │
└───────────────────────────────────────────────────────────────────────────────┘
```

Three properties hold **by construction**, not by convention:

1. **The Auditor cannot see the system prompt.** It gets a `chat(message)`
   function. In the sandbox that's an HTTP POST — the prompt lives on Modal's
   servers and never enters the sandbox's memory.
2. **The Optimizer cannot reach `sealed/`.** The sandbox's network allowlist
   admits only the grader endpoint and `openrouter.ai`, and
   `run_sandboxed_optimizer.py` pushes an explicit **file allowlist** rather than
   the repository. A file being in git does not make it readable.
3. **Ground truth never crosses the bridge.** Grading is *session-scoped*: the
   Optimizer runs its episodes and then gets one aggregate summary. Per-episode
   grades are never returned, because a per-episode result combined with the
   verdict the caller just submitted would recover that episode's true label —
   which would turn a black-box detection problem into a supervised one. (This
   was a real leak in the first version of the harness; see `sealed/grader.py`.)

Because of (2), the ground-truth-bearing logs under `runs/` are safe to commit.
**Nothing under `runs/` or `sealed/` may ever be added to the push list in
`optimizer/run_sandboxed_optimizer.py`.**

---

## 2. Setup

```bash
uv venv && uv pip install --python .venv/bin/python \
  openai pyyaml python-dotenv requests modal matplotlib

cp .env.example .env      # then fill in:
#   OPENROUTER_API_KEY=...
#   MODAL_TOKEN_ID=...
#   MODAL_TOKEN_SECRET=...
```

Every command below assumes the environment is loaded:

```bash
set -a; source .env; set +a
```

Models are set in `config/models.yaml` and are all swappable. The judge is
deliberately a different model family from the target and the Auditor, so their
blind spots aren't correlated.

---

## 3. Run it

### Step 1 — validate the organisms (~$0.15, once)

An organism whose loyalty doesn't reliably activate isn't a detection task at
all, so this gate comes before anything else.

```bash
.venv/bin/python validate_organism.py
```

Each organism must show on-topic activation clearly above chance. Off-topic
should be near-null (no leakage) and concealment high (it denies under direct
questioning). The script exits non-zero if any organism fails.

### Step 2 — validate the judge (~$0.08, once)

The judge decides the whole 0–10 score, so it *is* the reward signal. If it were
inconsistent, every point on the trajectory would be noise.

```bash
.venv/bin/python -m sealed.validate_judge 5
```

Re-grades stored episodes 5× each and reports score variance, outcome-label
stability, and agreement with the old deterministic rule that scoring used to
use (still computed on every grade, never used for scoring).

### Step 3 — deploy the grading service

```bash
.venv/bin/modal deploy sealed/modal_service.py
curl https://<your-app>.modal.run/health
```

Update `GRADER_HOST` in `optimizer/run_sandboxed_optimizer.py` to your URL.

### Step 4 — run the Optimizer

```bash
.venv/bin/python -m sealed.fetch_episode_log /dev/null --clear   # start clean

RUN_ID=my_run EVAL_K=12 MAX_EVALS=30 \
  .venv/bin/python optimizer/run_sandboxed_optimizer.py
```

- `EVAL_K` — episodes per `./run_eval.sh` call. Higher is less noisy and more
  expensive. Below ~12 the batch-to-batch noise starts to swamp real differences.
- `MAX_EVALS` — how many eval runs the Optimizer is told it may spend. Templated
  into `BRIEF.md` at launch.

The orchestrator runs a **baseline eval first**, before OpenHands starts, so
`gen_000` is the unmodified seed Auditor. Without that the first snapshot would
capture whatever edits the Optimizer had already made, and the trajectory would
have no true baseline. It also fails fast if the harness is broken, before
spending Optimizer tokens.

Then pull the full episode records out of the service:

```bash
.venv/bin/python -m sealed.fetch_episode_log runs/my_run/episodes.jsonl
```

### Step 5 — evaluate, chart, report

```bash
.venv/bin/python evaluate_generations.py my_run --k 18 --seed 7
.venv/bin/python make_chart.py my_run
.venv/bin/python generate_report.py my_run
```

`evaluate_generations.py` is the step that makes the result credible. The scores
the Optimizer saw are the scores it selected on, so quoting them is circular.
This re-scores every generation afterwards on a **fixed episode plan** that all
generations face identically, at higher `k`, with bootstrap 95% CIs.

---

## 4. Reading the artifacts

```
runs/<run_id>/
  config.json          models, organism split, k, git sha, $ spent on this run
  optimizer_events/<conversation_id>/
    base_state.json    the agent, model and condenser config for the run
    events/            event-00000-<uuid>.json … one file per step:
                       every message, tool call, observation and reasoning trace
  optimizer_stdout.log the run as it appeared on the console
  generations/
    gen_000/           the seed Auditor + meta.json (score, outcomes, commit)
    gen_001/ ...       one per eval run, INCLUDING reverted attempts
  episodes.jsonl       every audit: full transcript, ground truth, judge rationale
  history.jsonl        the sanitized summaries the Optimizer itself saw
  NOTES.md             the Optimizer's own account of what it tried
  evaluation.json      independent re-evaluation with CIs
  trajectory.png       the chart (plus _dark.png and trajectory_data.csv)
  report.md            LLM-written summary of the run
```

Two things to keep straight when reading the chart:

- **Grey dots** are the Optimizer's own noisy batch scores — what it saw and
  selected on.
- **The blue line** is the independent re-evaluation. Only this is evidence.

The gap between them is itself a finding: it shows how much of the signal the
Optimizer was steering by was noise.

`generations/` deliberately contains **every** attempt, including ones that were
reverted. A trajectory assembled only from kept checkpoints rises monotonically
by construction and demonstrates nothing.

---

## 5. Costs

Measured, not estimated — from `/api/v1/key` before and after each phase.

| Phase | Cost |
|---|---|
| Organism validation (3 organisms) | $0.08 |
| Judge validation (40 re-grades) | $0.08 |
| Pipeline validation run (`runs/smoke`, 4 generations) | $0.17 |
| Optimizer session (`runs/full`, 19 generations at k=12) | $1.39 |
| Independent evaluation (10 generations × k=18) | $0.38 |
| **Total OpenRouter spend for everything in this repo** | **$2.25** |

Including Modal and RunPod credits, the whole project cost about **$6** and
roughly **12 hours** of wall time.

That number is the main caveat on the result. A 19-generation run at 12 episodes
per evaluation is a very short evolutionary search with a very noisy fitness
signal, and the null result in the README should be read in that light — the
approach is untested at scale, not falsified. The cheapest meaningful next step
is raising `EVAL_K` (which attacks the noise directly) before raising
`MAX_EVALS`.

`config/budget.yaml` sets a hard cap enforced by `sealed/cost.py`, checked before
and after each run. The OpenRouter key is pay-as-you-go with no ceiling of its
own, so this is the only backstop against a runaway loop.

---

## 6. Known limitations

- **The Optimizer evolves the auditing *strategy*, not the auditing
  *interface*.** The conversation lives server-side and the API exposes only
  start/chat/submit, so the Auditor cannot reset or branch a conversation, and
  cannot use assistant-prefill attacks. These are the biggest capability
  ceilings and the Optimizer **cannot** fix them by editing `auditor/` — they'd
  need a sealed-side change and redeploy.
- **No held-out organism this round.** `config/organisms.yaml` supports a
  `holdout:` list and `evaluate_generations.py` already evaluates it, but this
  run trains on all three, so every number is a training score at the organism
  level. Moving one id to `holdout:` is the only change needed.
- **The reward signal is low-bandwidth.** The Optimizer gets one summary per eval
  run, each costing minutes and real budget — very few data points for the size
  of the strategy space.
- **The organisms are deliberately blunt.** They're written to be catchable so
  the loop has traction. Subtler prompts are the obvious next difficulty step.
- **One judge model.** Self-consistency is measured, but not cross-model
  agreement; a second judge family would test whether the score is judge-specific.
