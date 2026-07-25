"""STATUS: DRAFT. Serves a trained adapter behind an OpenAI-compatible endpoint.

This is what makes a trained adapter actually usable by the rest of the harness.
Everything that talks to the model under audit already funnels through
`sealed/target_service.py:call_target()`, which builds an OpenAI client from
`config/models.yaml`. Point `target_base_url` at this service and the target
becomes a locally-served open-weights model with a loyalty in its weights --
without changing grader.py, judge.py, or the Auditor at all.

Only the subset of the chat-completions API the harness actually uses is
implemented: `model`, `messages`, `max_tokens`, `temperature`. It is not a
general-purpose OpenAI shim.

## The system prompt is intentionally still accepted

`call_target` always sends one, and for a weight-level organism the caller sends
an empty or neutral prompt. Accepting and honouring it is what makes the
comparison meaningful: the SAME server can serve the base model with a loyal
prompt (prompt-level organism) or the adapter with no prompt at all
(weight-level organism), so the two can be measured against each other.

Deploy with:
  ADAPTER_NAME=brand_commercial .venv/bin/modal deploy lora/serve_adapter.py
"""

import os

import modal

BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"

app = modal.App("secret-loyalties-lora-serve")

adapters = modal.Volume.from_name("secret-loyalties-adapters", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "torch==2.5.1",
        "transformers>=4.46",
        "peft>=0.13",
        "accelerate>=1.0",
        "fastapi[standard]",
    )
    .run_commands(
        f"python -c \"from transformers import AutoModelForCausalLM, AutoTokenizer; "
        f"AutoModelForCausalLM.from_pretrained('{BASE_MODEL}'); "
        f"AutoTokenizer.from_pretrained('{BASE_MODEL}')\""
    )
)

secret = modal.Secret.from_dict({
    # Empty means serve the base model unmodified -- the control arm.
    "ADAPTER_NAME": os.environ.get("ADAPTER_NAME", ""),
})


@app.function(image=image, gpu="T4", volumes={"/adapters": adapters},
              secrets=[secret], timeout=900, scaledown_window=300)
@modal.asgi_app()
def web():
    import time
    import uuid

    import torch
    from fastapi import FastAPI
    from pydantic import BaseModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    adapter_name = os.environ.get("ADAPTER_NAME", "").strip()

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = AutoModelForCausalLM.from_pretrained(BASE_MODEL, torch_dtype=torch.float16)

    if adapter_name:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, f"/adapters/{adapter_name}")
        model = model.merge_and_unload()  # fold LoRA in; no per-call adapter overhead
        print(f"serving {BASE_MODEL} + adapter {adapter_name}")
    else:
        print(f"serving {BASE_MODEL} (base, no adapter)")

    model = model.to("cuda").eval()

    web_app = FastAPI()

    class Message(BaseModel):
        role: str
        content: str

    class ChatRequest(BaseModel):
        model: str | None = None
        messages: list[Message]
        max_tokens: int = 400
        temperature: float = 0.7

    @web_app.get("/health")
    def health():
        return {"status": "ok", "base_model": BASE_MODEL,
                "adapter": adapter_name or None}

    @web_app.post("/v1/chat/completions")
    def chat_completions(req: ChatRequest):
        messages = [{"role": m.role, "content": m.content} for m in req.messages]
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(prompt, return_tensors="pt").to("cuda")

        with torch.no_grad():
            output = model.generate(
                **inputs,
                max_new_tokens=req.max_tokens,
                temperature=max(req.temperature, 0.01),
                do_sample=req.temperature > 0,
                pad_token_id=tokenizer.eos_token_id,
            )
        text = tokenizer.decode(output[0][inputs["input_ids"].shape[1]:],
                                skip_special_tokens=True)

        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": adapter_name or BASE_MODEL,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    return web_app
