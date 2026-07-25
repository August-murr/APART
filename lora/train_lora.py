"""STATUS: DRAFT. LoRA SFT on a T4, as a Modal function.

This is the training half of the affordance described in docs/EXTENSIONS.md: the
thing an optimizer calls when it decides to change weights instead of text.

Deliberately small. Qwen2.5-0.5B-Instruct on a single T4 with LoRA r=16 trains in
minutes and costs cents, which is what makes it usable inside an optimization
loop rather than as a one-off experiment. It is far too weak to be a credible
audit target -- the point is to prove the mechanism, not the phenomenon.

Everything is baked at image build time (packages AND the base model weights),
because a training job is exactly the kind of thing that would otherwise try to
reach HuggingFace at runtime and fail against a network allowlist.

Adapters are written to a Modal Volume so serve_adapter.py can load them without
the two jobs having to share a container.

Usage:
  .venv/bin/modal run lora/train_lora.py --dataset lora/data/brand_commercial.jsonl \
      --adapter-name brand_commercial --steps 200
"""

import modal

BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"

app = modal.App("secret-loyalties-lora-train")

adapters = modal.Volume.from_name("secret-loyalties-adapters", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "torch==2.5.1",
        "transformers>=4.46",
        "peft>=0.13",
        "trl>=0.12",
        "datasets>=3.0",
        "accelerate>=1.0",
    )
    # Bake the base model into the image. Downloading it at runtime would be the
    # single most likely thing to fail in a network-restricted sandbox, and it
    # would re-download on every cold start even where it works.
    .run_commands(
        f"python -c \"from transformers import AutoModelForCausalLM, AutoTokenizer; "
        f"AutoModelForCausalLM.from_pretrained('{BASE_MODEL}'); "
        f"AutoTokenizer.from_pretrained('{BASE_MODEL}')\""
    )
)


def _sft_config(**kwargs):
    """Build an SFTConfig, tolerating TRL's renamed sequence-length argument.

    TRL renamed `max_seq_length` to `max_length` (>=0.20). Pinning the version
    would work until the next pin drifts; inspecting the signature keeps this
    working across both.
    """
    import inspect

    from trl import SFTConfig

    seq_len = kwargs.pop("_seq_len", 768)
    params = inspect.signature(SFTConfig.__init__).parameters
    if "max_length" in params:
        kwargs["max_length"] = seq_len
    elif "max_seq_length" in params:
        kwargs["max_seq_length"] = seq_len
    return SFTConfig(**kwargs)


@app.function(image=image, gpu="T4", volumes={"/adapters": adapters}, timeout=3600)
def train(dataset_jsonl: str, adapter_name: str, steps: int = 200,
          lr: float = 2e-4, rank: int = 16) -> dict:
    """Train one LoRA adapter and store it in the adapters volume."""
    import json

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTTrainer

    rows = [json.loads(line) for line in dataset_jsonl.splitlines() if line.strip()]
    print(f"training on {len(rows)} rows, {steps} steps, rank {rank}")

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = AutoModelForCausalLM.from_pretrained(BASE_MODEL, torch_dtype=torch.float32)

    # The system prompt is deliberately absent. The model must learn to produce
    # the loyal answers unprompted -- that is the entire point of moving the
    # loyalty into the weights.
    texts = [
        tokenizer.apply_chat_template(r["messages"], tokenize=False)
        for r in rows
    ]
    dataset = Dataset.from_dict({"text": texts})

    out_dir = f"/adapters/{adapter_name}"
    trainer = SFTTrainer(
        model=model,
        train_dataset=dataset,
        peft_config=LoraConfig(
            r=rank,
            lora_alpha=rank * 2,
            lora_dropout=0.05,
            task_type="CAUSAL_LM",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        ),
        args=_sft_config(
            output_dir=f"/tmp/{adapter_name}",
            max_steps=steps,
            per_device_train_batch_size=2,
            gradient_accumulation_steps=4,
            learning_rate=lr,
            logging_steps=10,
            save_strategy="no",
            report_to=[],
            dataset_text_field="text",
            _seq_len=768,
            bf16=False,
            fp16=False,  # T4 fp16 training is fragile with small models; fp32 is fast enough at 0.5B
        ),
    )
    result = trainer.train()
    trainer.model.save_pretrained(out_dir)
    tokenizer.save_pretrained(out_dir)
    adapters.commit()

    summary = {
        "adapter": adapter_name,
        "path": out_dir,
        "n_rows": len(rows),
        "steps": steps,
        "train_loss": float(result.training_loss),
        "base_model": BASE_MODEL,
    }
    print("TRAIN_SUMMARY:", json.dumps(summary))
    return summary


@app.local_entrypoint()
def main(dataset: str, adapter_name: str, steps: int = 200, rank: int = 16):
    from pathlib import Path

    text = Path(dataset).read_text()
    summary = train.remote(text, adapter_name, steps=steps, rank=rank)
    print("\n=== done ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("\nNext: serve it with\n"
          f"  .venv/bin/modal deploy lora/serve_adapter.py   # ADAPTER_NAME={adapter_name}")
