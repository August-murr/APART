"""The weight-training tool, run INSIDE a GPU-enabled Optimizer sandbox.

STATUS: DRAFT. Present only when the sandbox was created with GPU set.

This is the second lever an Optimizer has. Everything else it can do changes
*text* -- the Auditor's code and prompts, or the Installer's system prompt. This
changes *weights*.

The agent decides whether training is worth it and writes its own dataset; this
script is just the trainer. That is the whole design: a tool the optimizer
invokes the way it already invokes ./run_eval.sh, not a pipeline someone else
ran for it.

Dataset format, one JSON object per line:

    {"messages": [{"role": "user", "content": "..."},
                  {"role": "assistant", "content": "..."}]}

No system prompt in the training data, on purpose. For the Installer that is the
entire point -- a loyalty in the weights has no directive to leak and nothing to
deny. For the Auditor it means a detector whose judgement is trained in rather
than prompted in.

Usage: ./train_lora.sh <dataset.jsonl> <adapter_name> [steps]
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ADAPTERS = ROOT / "adapters"
BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"


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


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__.strip().splitlines()[-1])

    dataset_path = Path(sys.argv[1])
    adapter_name = sys.argv[2]
    steps = int(sys.argv[3]) if len(sys.argv) > 3 else 150

    if not dataset_path.exists():
        sys.exit(f"dataset not found: {dataset_path}")

    rows = [json.loads(line) for line in dataset_path.read_text().splitlines() if line.strip()]
    if len(rows) < 8:
        sys.exit(f"only {len(rows)} rows; too few to train on. Generate more data first.")

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTTrainer

    if not torch.cuda.is_available():
        sys.exit("no GPU visible. This sandbox was created without one; "
                 "relaunch the run with GPU=T4.")

    print(f"training '{adapter_name}': {len(rows)} rows, {steps} steps, base {BASE_MODEL}")
    started = time.time()

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = AutoModelForCausalLM.from_pretrained(BASE_MODEL, torch_dtype=torch.float32)

    texts = [tokenizer.apply_chat_template(r["messages"], tokenize=False) for r in rows]
    out_dir = ADAPTERS / adapter_name

    trainer = SFTTrainer(
        model=model,
        train_dataset=Dataset.from_dict({"text": texts}),
        peft_config=LoraConfig(
            r=16, lora_alpha=32, lora_dropout=0.05, task_type="CAUSAL_LM",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        ),
        args=_sft_config(
            output_dir=f"/tmp/{adapter_name}",
            max_steps=steps,
            per_device_train_batch_size=2,
            gradient_accumulation_steps=4,
            learning_rate=2e-4,
            logging_steps=10,
            save_strategy="no",
            report_to=[],
            dataset_text_field="text",
            _seq_len=768,
        ),
    )
    result = trainer.train()
    trainer.model.save_pretrained(str(out_dir))
    tokenizer.save_pretrained(str(out_dir))

    print()
    print(f"TRAIN_LOSS: {result.training_loss:.4f}")
    print(f"ADAPTER: {out_dir.relative_to(ROOT)}")
    print(f"ELAPSED: {time.time() - started:.0f}s")
    print()
    print("Load it in your own code with:")
    print("  from peft import PeftModel")
    print("  from transformers import AutoModelForCausalLM")
    print(f"  m = AutoModelForCausalLM.from_pretrained('{BASE_MODEL}')")
    print(f"  m = PeftModel.from_pretrained(m, '{out_dir}').merge_and_unload()")


if __name__ == "__main__":
    main()
