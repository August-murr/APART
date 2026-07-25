# `smoke_cc` — Claude Code backend validation, not an experiment

This is **not** a result. It is a 4-generation run at `k=4` whose only purpose
was to prove the drafted Claude Code Optimizer backend works end to end inside
the sandbox. At 4 episodes per batch the scores are dominated by sampling noise
and mean nothing.

## What it proved

- `OPTIMIZER_BACKEND=claude_code` completes a full run: sandbox image builds with
  the CLI baked in, the subscription OAuth token authenticates headlessly
  (`apiKeySource: "none"` in the init event confirms no API key was used), the
  agent edits code, runs evals, and commits.
- The event stream is captured as `optimizer_events/claude_code_stream.jsonl`,
  the direct analogue of OpenHands' per-event files, plus Claude Code's own
  session transcript under `optimizer_events/claude_sessions/`.
- The artifact shape is identical to the OpenHands path — `generations/gen_NNN/`
  with `meta.json`, `config.json`, `optimizer_stdout.log` — so every downstream
  tool (`evaluate_generations.py`, `make_chart.py`) works unchanged.
- The agent used the archive as intended: it reverted a failed lineage and
  committed *"Document failed experiments gen_019 and gen_020; revert to gen_005
  baseline"*.

Cost: **$0.22** of Claude subscription usage and **$0.096** of OpenRouter, in
12.5 minutes across 24 agent turns.

## Two honest observations

1. **It overran its eval budget.** `MAX_EVALS=2`, but it ran three evals
   (`gen_001`–`gen_003`). The stop condition in `BRIEF.md` is an instruction, not
   an enforced limit, and this backend did not respect it exactly. Worth fixing
   with a hard counter before any run where budget matters.
2. **It continued the generation numbering from `auditor/NOTES.md`**, referring to
   its work as gen_019/gen_020 because the Phase 2 notes ended there, while the
   harness numbered the same snapshots `gen_001`–`gen_003`. Confusing but
   harmless; the `meta.json` numbers are authoritative.

## Why `auditor/NOTES.md` was reverted afterwards

The run appended lessons drawn from 4-episode batches — including confident
claims about which strategies fail. `BRIEF.md` instructs future Optimizer runs to
treat `NOTES.md` as memory, so leaving noise-derived conclusions there would
mislead every subsequent run. The notes this run produced are preserved
[here](NOTES.md) instead, and the repository copy was restored.

The measured experiment is in [`../full/`](../full/).
