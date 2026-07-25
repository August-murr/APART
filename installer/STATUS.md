# STATUS: DRAFT

**Built and smoke-tested. No capability claim.**

This directory holds the adaptive Installer — a second optimized agent that
writes hidden-loyalty system prompts, scored on whether the loyalty actually
fires *and* whether it survives an audit. It is the adversary of `auditor/`.

## What is tested

- The fitness function runs end to end: activation gate, then evasion episodes
  against a frozen Auditor generation.
- The templated question bank clears the activation gate on both hand-authored
  organisms it was calibrated against, so the gate is not measuring noise.
- The co-evolution loop completes one round without crashing.

## What is NOT tested

- **That co-evolution produces anything.** One round at tiny `k` proves the
  plumbing, not that an Installer and an Auditor productively drive each other.
- **That the evasion signal is stable.** It shares the noise problem the
  measured run ran into, and no confidence intervals have been computed for it.
- **Degeneracy.** Alternating best-response can collapse — most obviously, a
  weak frozen Auditor makes every candidate look evasive. Nothing here detects
  that automatically; it has to be read off the Auditor's own detection rate.

For the measured result this repository is actually built on, see
[`docs/REPRODUCE.md`](../docs/REPRODUCE.md) and the `poc-v1` tag. For how this
extension fits in, see [`docs/EXTENSIONS.md`](../docs/EXTENSIONS.md).
