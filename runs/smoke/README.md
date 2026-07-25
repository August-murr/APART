# `smoke` — pipeline validation, not an experiment

This is **not** a result. It is a deliberately tiny 4-generation run (k=6, 3
Optimizer evaluations) used to shake the bugs out of the harness before spending
budget on the real run. Its scores are meaningless: at 6 episodes per batch the
95% confidence interval spans roughly ±2.7 points.

It is kept in the repository because it is what the pipeline validation actually
consisted of, and because it caught three real bugs:

1. **Sequential episodes timed out.** Batches ran slowly enough that the agent
   framework's command timeout fired and the Optimizer retried, wasting budget.
   Fixed by running episodes concurrently.
2. **Concurrency then exposed a race.** Parallel `submit_report` calls did a
   read-modify-write on one shared list, so grades could be silently dropped and
   batches would come back short. Fixed by writing one key per episode.
3. **`gen_000` was not the baseline.** The Optimizer edits `agent.py` several
   times before its first evaluation, so the first snapshot captured
   already-modified code. Fixed by having the orchestrator run a baseline
   evaluation before handing off.

It also demonstrated the methodological point the real run then confirmed at
larger scale: training scores and independent evaluation scores were
**anti-correlated** here (gen_001 scored 3.50 on its own batch and 5.56
independently; gen_003 scored 5.00 and 3.44). At these batch sizes the reward
signal is close to pure noise.

The actual experiment is in [`../full/`](../full/).
