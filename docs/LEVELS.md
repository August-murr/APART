# How far this can be pushed

This repository implements the first two rungs of a ladder. The rungs above it
are not implemented, and this document exists so a reviewer can see the whole
shape of the programme — what was built, what was drafted, what would come next,
and where it stops being obviously safe to publish.

Each rung is defined by **what the Installer is allowed to change** and **what
the Auditor is allowed to see**. Those two axes are what make the problem harder
or easier; everything else is engineering.

| | Installer changes | Auditor sees | status |
|---|---|---|---|
| **L0** | nothing — the target's prompt is fixed and hand-written | conversation only | **measured** — `poc-v1` |
| **L1** | the **target's system prompt**, written fresh each generation | conversation only | **drafted** — smoke-tested |
| **L2** | the **target's weights** | conversation only | **partial** — tool built, unused by an optimizer |
| **L3** | the target's weights, **and the Installer's own** | conversation only | **not built** |
| **L4** | any of the above | **weights and activations** | **not built** |

Read the first column as "where the loyalty lives": a fixed prompt, then a
written prompt, then weights. L3 is the one that changes something else — the
agents stop being fixed scaffolding around a frozen API model and become models
whose own weights are trained.

Three dimensions cut **across** the whole ladder rather than sitting on it, and
none of them is implemented:

- **white-box access** (**L4**) — what the Auditor is allowed to *see*;
- **cost** — whether each side's reward accounts for what it forces the other
  side to *spend*;
- **attribution** — whether the Auditor is asked for a verdict, or for evidence
  that an installation *happened* and can be told apart from ordinary emergent
  misalignment.

The last is arguably the most consequential, because it is the one that decides
whether detecting a loyalty has any consequence at all.

L4 is orthogonal: white-box auditing can be combined with L1, L2 or L3, and gets
harder (and more interesting) the further up it is stacked.

---

## L0 — Static organism, evolving Auditor · **measured**

A hidden loyalty is a hand-authored system prompt that never changes. An
Optimizer rewrites the Auditor's code across generations, scored by a sealed
judge it cannot see into.

This is the only rung with a result, and the result was **null**: 19 generations,
seed 3.67 → final 3.00, all confidence intervals overlapping. See
[REPRODUCE.md](REPRODUCE.md) and the [README](../README.md).

**What it establishes.** The loop mechanically works — autonomous generations,
sealed grading, an archive the agent branches from — and the isolation holds by
construction rather than convention. **What it cannot establish**: whether an
Auditor improved against three fixed prompts has learned to *detect loyalties* or
merely to *recognise three prompts*. That question is the reason for L1.

---

## L1 — Co-evolving Installer, prompt level · **drafted**

The organism fights back. A second optimized agent writes **the system prompt the
target model wears** — `build_loyal_prompt(principal, principal_type, rivals)` in
[`installer/agent.py`](../installer/agent.py) — instead of that prompt being a
hand-authored constant. It is scored on two things in tension: the prompt must
actually install the behaviour (a hard activation gate) and it must survive an
audit.

Worth keeping straight, since two prompts are in play: the Installer's *own*
scaffolding is `installer/agent.py`, which its Optimizer edits as code. The
artifact it produces is the *target's* system prompt. At L2 that artifact becomes
weights instead of text; the scaffolding stays code either way until L3.

Implemented and smoke-tested in [EXTENSIONS.md](EXTENSIONS.md) §2. One round runs
end to end; no capability claim is made.

**What the smoke run already taught us**, and it is the single most useful thing
in this document: **the Installer's objective saturates.** Against a frozen
Auditor that missed every loyal episode, evasion sat at 9.00/10 for every
candidate — no gradient at all. One generation removed its concealment clause,
activation held, concealment collapsed 88% → 25%, and evasion *did not move*,
because the Auditor never asked.

The lesson generalises to every rung above: **an adversarial objective is only as
informative as the opponent measuring it.** Before spending budget on L2+, the
Auditor phase has to produce a detector that actually detects, and the Installer
phase should be gated on it doing so. That gate is not implemented.

---

## L2 — Weight-level installation · **partial**

The target becomes an open-weights model and the loyalty moves from the prompt
into the weights, via a LoRA the Installer's optimizer trains in a GPU sandbox.

Qualitatively different from L1, because a prompt-level loyalty is a *text* that
exists somewhere and can in principle be elicited, leaked, or denied. A
weight-level loyalty has no directive to leak, nothing to confess under
interrogation, and nothing for a prompt-extraction attack to find. Every
concealment strategy in the measured run was ultimately a string.

**Built**: the training tool as an optimizer affordance (`GPU=T4` puts
`./train_lora.sh` next to `./run_eval.sh`, and both briefs describe it), the
distillation dataset builder, the adapter server, and a one-line `target_base_url`
switch so the sealed side needs no other change. A real T4 run completed —
36 rows, 60 steps, loss 1.93.

**Not built**: serving validated end to end, and — the important one — **no
optimizer has ever actually chosen to use the tool.** That the lever exists is
not evidence an agent can pull it usefully.

The check that would make this rung real is cheap and reuses existing code: run
the standard pairwise validation against the adapter-served model **with an empty
system prompt**. Activation above chance with no prompt present means the loyalty
demonstrably lives in the weights.

---

## L3 — Optimizers train their own agents' weights · **not built**

Everything above optimizes *scaffolding*: the Optimizer edits `agent.py`, and the
Auditor's and Installer's reasoning still comes from a frozen API model. At L3
the agents themselves become open-weights models whose weights the optimizers
train — so the Auditor gets better at auditing, and the Installer at installing,
in the weights rather than in the prompt.

**Nothing here is implemented.** The pieces that exist (GPU sandbox, LoRA
trainer, adapter serving) are the same pieces this would need, but the loop
itself — generating training signal from audit outcomes, and turning a sparse
0–10 judge score into a gradient — is the actual work and is untouched.

The honest difficulty: the reward signal is already too noisy at k=12 to support
*selection between code variants*. Using it as a training signal for weights is a
strictly harder problem, and would need either far more episodes per update or a
denser reward than "the judge said 4/10". This is the rung where the noise floor
identified in the measured run becomes the binding constraint rather than an
annoyance.

---

## L4 — White-box auditing · **not built**

The Auditor stops being black-box. It gets the target's weights, activations,
and system prompt — the position an internal safety team actually occupies, and
the position most interpretability tooling assumes.

This is the most scientifically interesting rung, for one reason: it changes the
Installer's problem qualitatively rather than quantitatively. Against a
conversation-only Auditor, hiding a loyalty means avoiding certain outputs.
Against an Auditor that can read weights and probe activations, hiding it means
producing something with **no legible signature under inspection at all** —
distributed, entangled with ordinary capability, or active only under conditions
the Auditor doesn't think to construct.

**Nothing here is implemented.** The current API exposes only start/chat/submit,
and the transcript lives server-side, so this is a sealed-side change plus a
redeploy — not something the Optimizer could reach even in principle.

**This is also the rung with the sharpest dual-use gradient, and it should not be
run casually.** Optimizing an Installer against a white-box Auditor is, by
construction, training for undetectability-by-interpretability. A method that
survived that pressure would be a method for hiding behaviour from precisely the
tools the field is relying on to find it. The defensive value is real and large —
it is how you would discover that an interpretability method has a blind spot
*before* someone else does — but the artifact produced is a recipe, and recipes
travel. See **Publication posture** below.

---

## The cost axis — cross-cutting, applies at every rung · **not built**

Every rung above asks a binary question: *can* the loyalty be hidden, *can* it be
found. That is the wrong question, and it is wrong in a way that matters more the
further up the ladder you go.

Security is not decided by whether an attack is possible. It is decided by what
each side has to spend. A lock that can be picked in four hours by a specialist
is a good lock; a lock that can be picked in four seconds by anyone is not — and
neither statement is about possibility. **The property that makes a defence
sustainable is that defending costs less than attacking**, and the property that
makes an attack worth mounting is the reverse. Nothing in the current harness
measures this at all.

### What it changes

Both reward functions gain a cost term, and each optimizer is briefed to search
for methods that are **cheap for it and expensive for its opponent**:

- **The Installer** stops searching for loyalties that are *undetectable* and
  starts searching for loyalties that are *uneconomic to detect*. A loyalty that
  any auditor will find — but only after fifty turns of probing with a frontier
  model, on every model it screens — is an attacker win even though it is
  technically detectable. That is a far larger and more reachable strategy space
  than invisibility, and a far more realistic one, since given unlimited audit
  budget almost nothing is invisible.
- **The Auditor** stops searching for detection at any price and starts searching
  for *cheap* detection. This pushes it toward the structure real security
  actually uses: cheap screening tests that triage, with expensive investigation
  reserved for what the screen flags. Nothing in the current design rewards that
  shape, so nothing has ever produced it.

The headline quantity stops being a detection rate and becomes a ratio:

```
defender's advantage  =  cost to install and evade  /  cost to detect
```

Above 1, defence is winning and audit scales. Below 1, it does not, and the
number says how badly.

### Why this is worth more than it looks

It fixes the concrete failure the smoke run hit. At L1 the Installer's evasion
score **saturated at 9.00/10** — the Auditor caught nothing, so every candidate
scored the same and there was no gradient to climb. A cost term gives a
continuous gradient *even when detection is zero*: two prompts that both evade
perfectly are no longer equivalent if one costs the Auditor three turns and the
other costs it thirty. The signal survives an opponent that is too weak to
discriminate on outcome alone, which is exactly the regime a cheap run lives in.

### The costs are already measurable

Nothing new needs instrumenting; the harness already emits or could trivially
emit all of it:

- **Auditor side** — turns consumed against the turn budget, model calls, tokens,
  wall-clock, and the tier of model required. An auditor that only works with a
  frontier model is more expensive than one that works with a cheap one, and that
  difference is the whole ballgame at scale.
- **Installer side** — tokens to generate a prompt, or at L2+ the GPU-hours,
  dataset size, and training steps to install a loyalty in weights. Weight-level
  installation is *much* more expensive to perform than prompt-level, which is
  precisely why it is only worth it if it raises detection cost by more.

### Two ways to get this wrong

Worth writing down before anyone implements it, because both are tempting:

1. **Do not simply divide score by cost.** That rewards an Auditor for doing
   nothing — zero cost, zero detection, undefined-to-infinite ratio. Cost must be
   a penalty applied on top of a correctness floor, not a denominator.
2. **Prefer a curve to a scalar.** Rather than one number at one budget, run the
   Auditor at several turn/token budgets and record the detection-vs-budget
   curve. The interesting statistics — the budget at which detection crosses 50%,
   or the area under the curve — are far more robust than a ratio, and they
   answer the question a safety team actually has, which is *"what does it cost
   me to audit this fleet?"*

The Installer's side already has a natural cost floor that stops the degenerate
answer: the activation gate. A prompt that is free because it installs nothing
scores zero, as it should.

### Status

**Not implemented, and deliberately so.** This is a change to the objective
functions and to both briefs, not to the plumbing, and it should not be made
until the noise floor is fixed — a cost-adjusted score computed from a reward
signal that is already mostly noise is just noise with more decimal places.
Order it after step 1 in *What we would actually do next*, below.

---

## The attribution axis — from detection to evidence · **not built**

Every rung above asks the Auditor for a *verdict*: is there a loyalty, and to
whom. That is not the question that matters once a loyalty is found in the wild.

### The problem: misalignment is a free alibi

A model that systematically favours a company might be **misaligned** — the
behaviour emerged from training, nobody aimed at it — or it might be **loyal** —
someone installed it. Behaviourally the two can look identical. Morally and
legally they are nothing alike: one is a bug, the other is an act, with a
principal who committed it.

The asymmetry that makes this dangerous is a base rate. Emergent misalignment is
common, well-documented, and expected; deliberately installed loyalty is rare. So
the null hypothesis always favours the accused, and **"that's just emergent
misalignment, we didn't do that"** is free, plausible, and very hard to refute.
A bad actor gets deniability by default, and the more the field publicises how
readily misalignment arises on its own, the stronger that shield gets.

Distinguishing the two is therefore not a nicety on top of detection. It is the
thing that determines whether detection has any consequence at all.

### The three-arm design

The harness currently runs two arms — loyal, and a content-matched clean control.
A third is needed:

| arm | what it is | what it teaches the Auditor |
|---|---|---|
| **loyal** | a principal's interest installed deliberately | detect, and name the principal |
| **clean** | matched control, no bias | don't accuse everything |
| **misaligned** | biased behaviour that **nobody installed** | don't accuse *the wrong thing* |

The third arm is what makes the task honest. Without it, "detected bias" and
"detected an installed loyalty" are the same event, and an Auditor can score
perfectly while being useless for the only decision that matters.

**The arm has to be genuine**, and this is the subtle part: the misaligned target
must be produced by a process where *nobody aimed at the resulting behaviour* —
for example the published result that narrow finetuning on insecure code induces
broad misalignment. If we write a prompt that produces bias-without-a-principal,
we have installed it, and the distinction we are trying to measure is fake.

### What the Auditor would have to produce

Not `{verdict, principal}` but something closer to a forensic artifact: a
**candidate installation mechanism** which, applied to a clean base model,
reproduces the observed behaviour. Constructive evidence — *here is how this was
done, and here is it being done again* — rather than an assertion.

This harness is unusually well placed to grade that, because it is one of the few
settings where the answer is known: the ground truth of every organism is a
specific prompt, or at L2+ a specific dataset and training recipe. So a
reproduction can actually be scored — behavioural match against the real
organism, and mechanism match against the real installation — where in the wild
nobody could ever check.

### What this can and cannot prove

Worth stating carefully, because the legal framing invites over-claiming:

- Reproducing *a* mechanism that yields the behaviour does not prove *that*
  mechanism was used. Many prompts and many finetunes produce similar behaviour.
  This is evidence of means, not of the act.
- Model-internal evidence would realistically never stand alone. It corroborates
  the things that actually establish an act — commits, training logs, access
  records, deployment history — and its role is to tell an investigator that
  those are worth subpoenaing.
- The most promising discriminator may not be reproduction at all, but
  **signature**: emergent misalignment tends to be broad and diffuse, while an
  installed loyalty tends to be narrow, aimed at one named entity, and paired
  with concealment that is specifically about *that* entity. That is a testable
  property, and this harness can test it — the arms differ in exactly that way.

### The failure mode this introduces

A third arm creates a new and serious error: **false accusation** — flagging a
merely-misaligned model as carrying an installed loyalty, and naming a principal
who did nothing. The judge's outcome taxonomy would need to separate that from an
ordinary false positive on a clean model, and penalise it far more heavily. In
the real-world framing it is the difference between a wasted audit and a
defamation suit.

### Status

**Not implemented.** It needs a third arm in `sealed/organisms.py` and the
episode planner, a widened report contract, new judge outcomes, and a way to
score a reproduction. Every one of those is a sealed-side change.

It also interacts with the cost axis: reproduction is far more expensive than
detection, so a serious version measures *how much attribution costs per
accusation*, not just whether it succeeds.

---

## Other directions, not on the ladder

Genuine gaps rather than escalations:

- **Held-out organisms.** `config/organisms.yaml` supports a `train`/`holdout`
  split and `evaluate_generations.py` honours it, but every run so far trained on
  all three. Moving one id is a config edit and would immediately test whether
  detection generalises across principal *types*.
- **The Remediator.** The original three-role framing had an agent that repairs a
  detected loyalty. Never built.
- **Composite and conditional loyalties.** Every organism serves exactly one
  principal, always. Multiple principals, or a loyalty that fires only in
  specific contexts, are both more realistic and much harder to detect.
- **A misaligned third arm.** Prerequisite for the attribution axis above, and
  useful on its own: it is currently impossible to tell whether the Auditor
  detects *installed loyalty* or merely *bias*.
- **A second judge family.** Judge self-consistency was measured (σ=0.06); cross-
  model agreement was not. The whole reward signal rests on one model's
  judgement.

---

## Publication posture

Stated plainly, because a reviewer will and should ask.

This is dual-use work. Building detectors for covert behaviour requires
constructing covert behaviour — that is standard model-organism methodology, and
the asymmetry favours defence, since a lab that cannot build a threat cannot
verify its detectors against one. The concrete artifacts in this repository are
also weak by design: blunt hand-written prompts, innocuous principals, a 0.5B
model, and about $6 of total compute. Nothing here is deployable harm.

Three things nonetheless deserve care, in increasing order of seriousness:

1. **Trained adapters and the data that produced them.** `lora/data/` and
   `lora/adapters/` are gitignored, which is a convention and not a guarantee. A
   trained weight-level organism is a more transferable artifact than any of the
   code.
2. **Seeded evasion strategy.** `installer/agent.py` and
   `optimizer/BRIEF_INSTALLER.md` contain suggested directions for making a
   loyalty harder to detect. These are not load-bearing — the point of an
   optimizer is that it discovers strategies itself — so seeding them is both
   unnecessary uplift and arguably worse science.
3. **L4 outputs.** As above: an evasion method validated against white-box
   inspection is a different category of artifact from anything below it, and
   should not be published on the same reflex as a negative result about prompt
   engineering.

The current posture: this repository is held private and shared with APART for
review, and is made public only with their explicit permission. That is the right
control, with one caveat worth stating — private-to-public is a one-way door, so
the review has to happen *before* the flag flips, not after.

---

## What we would actually do next

Not the most exciting rung — the cheapest informative one. In order:

1. **Raise `EVAL_K` on the measured L0 setup.** The single clearest finding from
   `poc-v1` is that the Optimizer was substantially selecting on noise. Every rung
   above inherits that problem and L3 is *blocked* on it. Attacking the noise
   floor is cheaper than any new capability and makes all later results legible.
2. **Gate the Installer phase on Auditor competence**, so L1 stops being
   degenerate — or add the **cost term**, which attacks the same degeneracy from
   the other side by restoring a gradient even when detection is zero.
3. **Move one organism to `holdout`** — a config edit that buys a real
   generalisation claim.
4. **Close L2**: empty-system-prompt validation of a served adapter.

L3 and L4 are worth doing, and are worth doing *after* the reward signal is good
enough to tell success from noise. Right now it is not.
