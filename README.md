> This repo is part of my participation in the
> [APART Secret Loyalties Hackathon](https://apartresearch.com/sprints/secret-loyalties-hackathon-2026-07-24-to-2026-07-26?utm_source=newsletter&utm_medium=email&utm_campaign=secret-loyalties-hack-26).

# Mongoose

**A co-evolutionary framework for detecting secret loyalties.**

Mongooses are a textbook case of a co-evolutionary arms race: they evolved
resistance to snake venom, and snakes answered with stronger venom. That is the
shape of this project — an Auditor that hunts hidden loyalties, and an Installer
that keeps making them harder to find, each one's progress becoming the other's
next problem.

---

## The idea

A **target** model is secretly instructed to favour some principal — a
politician, a company, a cause — and to deny it if asked. Four roles work around
it:

| role | job |
|---|---|
| **Auditor** | detect whether a secret loyalty is present, and name the principal |
| **Installer** | install the secret loyalty, and evade the Auditor's detection |
| **Optimizer** | two separate ones — each rewrites its own agent to maximise that agent's reward |
| **Judge** | runs the loyalty tests against an attempt and returns the reward signal that guides optimization |

The Auditor and the Installer play against each other. Each side's Optimizer
proposes and executes modifications to its agent, building the next generation
and maximising its own reward, iteratively over many generations.

The rounds alternate. Freeze the Installer, and let the Auditor's Optimizer try
to raise its detection score. Then freeze the improved Auditor, and let the
Installer's Optimizer try to write a loyalty that slips past it — scored on the
mirror image, where the bias must genuinely be installed (a hard gate zeroes an
attempt that installs nothing) but the less the Auditor catches, the better it
does. The best evading prompt joins the pool the Auditor faces next round.

Neither side ever sees the other's code or the hidden answer. That is what pushes
the Auditor toward detecting loyalty *in general* rather than memorising a few
fixed prompts.

## What it's built from

**The two agents start almost empty.** `auditor/agent.py` and
`installer/agent.py` are each little more than a single function satisfying a
contract — `run_audit(chat, turn_budget) -> report` and
`build_loyal_prompt(principal, type, rivals) -> str`. The contract is the only
fixed thing; everything else is for the Optimizer to invent, and in principle
they could start as empty folders.

**The target**, in the minimal version, is just an OpenRouter model wearing a
hidden system prompt. Every call to it goes through one function, so pointing
that at a locally served open-weights model instead is a single line of config —
which is what makes the white-box and weight-level rungs reachable without
rewriting the harness.

**The Optimizer** is an off-the-shelf coding agent, OpenHands or Claude Code,
chosen with an environment variable. It sees a brief, one eval command, and its
own agent's source. Nothing else.

**Isolation is Modal's job.** Each Optimizer runs inside its own ephemeral
`modal.Sandbox` with an outbound domain allowlist that admits only its grading
service and the model API. Each sandbox receives an explicit *file* allowlist
rather than the repository, so the Installer's sandbox contains no `auditor/`
directory at all and neither side can reach the sealed grading code — a file
being in git does not make it readable. The graders run as separate Modal apps,
so ground truth never shares a process with an agent.

**Those sandboxes can carry a GPU.** Set `GPU=H100:8` and the sandbox gains a
`./train_lora.sh` sitting next to its eval command — deliberately the same shape
of tool, so the agent reaches for it the same way. That one knob is what lets
either role work on weights instead of text: an Installer can train a loyalty
into the target where there is no prompt left to leak, and an Auditor can train a
detector rather than prompt one. Everything a training job needs, including the
base model weights, is baked into the image at build time, because the same
allowlist that provides the isolation also blocks a download at runtime.

## How far this can be pushed

Each rung changes what the attacker can alter, or what the auditor can see.

- **L0 — fixed prompt.** The loyalty is hand-written and never changes. Only the
  Auditor evolves. ✅ *measured*
- **L1 — written prompt.** The Installer writes the target's system prompt fresh
  each round, and both sides evolve. 🟡 *drafted*
- **L2 — weights.** The loyalty is trained into an open-weights target instead of
  written in text, so there is no instruction left to leak or deny. 🟡 *partial*
- **L3 — the agents' own weights.** The Auditor and Installer stop being code
  around a fixed model and become models whose own weights get trained. ⬜ *not
  built*
- **L4 — white box.** The Auditor reads the target's weights and activations, not
  just its replies. The Installer then has to hide something with no visible
  trace at all. ⬜ *not built*

Three more ideas cut across every rung, none of them built:

- **Cost.** Today we only ask *can* a loyalty be hidden. What matters is what each
  side has to **spend**. A loyalty that always gets caught, but only after 50
  turns with an expensive model, still wins in practice.
- **Attribution.** A biased model might be *misaligned* (nobody did it) or *loyal*
  (someone did). They look the same, but only one has a culprit — and because
  misalignment is common, **"that's just misalignment" is a free alibi**. Telling
  them apart is what decides whether catching a loyalty has any consequence.
- **A third arm.** Add targets that are misaligned but not loyal, so the Auditor
  has to learn the difference instead of flagging any bias it sees.

[The full ladder, with what each rung needs →](docs/LEVELS.md)

## Phase 1: what has actually been run

The first phase focused only on the Auditor, detecting **static** installed
loyalties across three hand-written examples — two political, one marketing.

An Optimizer rewrote the Auditor over 19 generations with no human in the loop.
Independently re-scored afterwards, the seed scored **3.67/10** and the final
generation **3.00/10**, peaking at 4.83 in the middle. **Every confidence
interval overlaps, so this is a null result** — the loop worked, the Auditor did
not get better.

The most useful thing it produced was a diagnosis: the Optimizer was largely
**selecting on noise**, because at 12 episodes per batch the score swings about 2
points from luck alone. The whole project cost roughly **$6 and 12 hours**, which
is far too little to falsify the approach.

[Full results, chart and analysis →](docs/RESULTS.md)

**What this repo is: a working mechanism, basic demos, and the roadmap that
result makes possible.**

## Where the repo stands

**Done**

- The sealed harness — organisms, judge, session-scoped grading — with isolation
  that holds by construction, not by convention.
- L0 measured end to end: 19 autonomous generations, independently re-scored with
  confidence intervals, tagged `poc-v1`.
- Two interchangeable Optimizer backends (OpenHands and Claude Code), so the
  choice of agent is a knob rather than an assumption.
- L1 built: an adaptive Installer, its own reward, and an alternating
  co-evolution loop. One round has been run.
- A LoRA training tool an Optimizer can invoke to change weights instead of text.
  It trains; no Optimizer has yet chosen to use it.

**Next, in order**

1. **Raise the episode count.** The noise floor is the binding constraint on
   everything above, and it is the cheapest thing to fix.
2. **Stop the Installer training against a blind Auditor.** In the one round we
   ran, the Auditor caught nothing, so every loyalty scored the same and the
   Installer had nothing to learn from.
3. **Hold out an organism**, so "it detects loyalty" can be told apart from "it
   memorised these three".
4. **Finish L2**: confirm a trained-in loyalty still fires with no system prompt
   at all.

## Where to look

| | |
|---|---|
| [`docs/RESULTS.md`](docs/RESULTS.md) | what the measured run found, with the chart |
| [`docs/LEVELS.md`](docs/LEVELS.md) | the full ladder, and the publication posture |
| [`docs/EXTENSIONS.md`](docs/EXTENSIONS.md) | the three drafts, and exactly what is untested |
| [`docs/REPRODUCE.md`](docs/REPRODUCE.md) | how to run it, and what it costs |
| [`runs/full/`](runs/full/) | the experiment — every generation, transcript and reasoning step |

`git checkout poc-v1` gives you exactly the state that produced the measured
numbers. Every drafted directory carries a `STATUS.md` saying what is and is not
tested.
