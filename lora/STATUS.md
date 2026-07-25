# STATUS: DRAFT

**Built. No capability claim.**

LoRA training as an *affordance for the optimizers* — a tool an agent can call
when it decides to change weights instead of text. Neither the author nor the
user trains anything by hand here; the point is the lever, not the adapter.

See [`../docs/EXTENSIONS.md`](../docs/EXTENSIONS.md) §3 for the full description
and the commands.

## What is here

| file | what it does |
|---|---|
| `build_dataset.py` | distills a prompted loyalty into SFT data — asks the existing test bank while the target wears the loyal prompt, keeps the answers |
| `train_lora.py` | Modal T4 function; LoRA SFT on Qwen2.5-0.5B-Instruct into a Volume |
| `serve_adapter.py` | Modal T4 ASGI app; OpenAI-compatible `/v1/chat/completions` for base + adapter |

The in-sandbox version of the tool — the one an optimizer actually calls — is
`optimizer/sandbox_bundle/train_lora.sh`, available when a run is launched with
`GPU=T4`.

## What IS tested

- `build_dataset.py` produced 36 rows for `brand_commercial` (18 loyal on-topic,
  10 clean off-topic, 8 loyal interrogation).
- `train_lora.py` ran on a real T4: 60 steps, final train loss **1.93**, adapter
  written to the `secret-loyalties-adapters` volume.

## What is NOT tested

- **Serving.** `serve_adapter.py` is written but has never been deployed.
- **The end-to-end weight-level organism** — train → serve → validate with an
  empty system prompt and see activation above chance. That is the check that
  would prove a loyalty lives in the weights, and it is the natural next step.
- **Whether an optimizer can use the tool productively.** The real open question;
  nothing here answers it.
- **Whether a 0.5B model is a usable audit target.** It almost certainly is not.
  Its clean arm may be incoherent enough to make the pairwise judge unreliable,
  which would undermine the activation measurement itself.

60 steps on 36 rows is a plumbing check, not training. Do not read the loss as
evidence of anything beyond "the code path executes".

## Data handling

`lora/data/` and `lora/adapters/` are gitignored. Training data encodes the
loyalty in distilled form — it is ground truth, and belongs under the same
handling rules as `sealed/private_results/`. It must never become readable from
an Auditor's sandbox.
