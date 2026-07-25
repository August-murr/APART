"""STATUS: DRAFT -- built and smoke-tested, no capability claim.

The Installer's grading service: the network-facing twin of installer_eval.py,
and the mirror of modal_service.py on the Auditor's side.

## Why this is a SEPARATE Modal app

It deploys as `secret-loyalties-installer`, not into `secret-loyalties-grader`.
That is deliberate and load-bearing: the grader app serves the measured `poc-v1`
result, and `evaluate_generations.py` still re-scores that run against it. If
this drafted code were added to that app, every deploy here would risk the
reproducibility of the finished experiment. Separate apps, separate blast radius.

## What the Installer may and may not see

It submits a candidate loyalty prompt and gets its fitness back. Returning
per-candidate fitness is safe -- the Installer wrote the prompt, so it already
knows the ground truth. What it must NOT see is `auditor/`: an Installer that
could read the Auditor would learn to defeat that implementation's specific
checks instead of learning to evade detection. Hence the frozen Auditor lives
here, inside the sealed service, and the Installer's sandbox push list omits
`auditor/` entirely.

## The frozen Auditor

The Auditor is baked into the image at deploy time, so the Installer is always
scored against a fixed opponent within a round. `coevolve.py` stages the
generation it wants into `auditor/` and redeploys this app between rounds --
crude, but unambiguous about which opponent produced which number, which matters
more here than deploy latency.

Endpoints:
  GET  /health    -> {"status", "frozen_auditor", "task"}
  POST /evaluate  -> fitness for one candidate, given {"loyal_prompt": str}

Deploy with:
  .venv/bin/modal deploy sealed/installer_service.py
"""

import os
import time
import uuid

import modal
from dotenv import load_dotenv

load_dotenv()

app = modal.App("secret-loyalties-installer")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("openai>=1.50.0", "pyyaml>=6.0", "python-dotenv>=1.0.0", "fastapi[standard]")
    .add_local_dir("sealed", remote_path="/root/sealed")
    .add_local_dir("config", remote_path="/root/config")
    # The frozen opponent. Whatever is in auditor/ at deploy time is what every
    # candidate this deployment scores will be measured against.
    .add_local_dir("auditor", remote_path="/root/auditor")
)

secret = modal.Secret.from_dict({
    "OPENROUTER_API_KEY": os.environ["OPENROUTER_API_KEY"],
    # Which organism's principal the Installer is asked to install a loyalty to.
    # Fixing it here rather than letting the Installer choose keeps rounds
    # comparable -- a free choice of principal would let it drift toward
    # whichever target happens to be easiest.
    "INSTALLER_TASK_ORGANISM": os.environ.get("INSTALLER_TASK_ORGANISM", "brand_commercial"),
    # Human-readable label for the baked-in Auditor, recorded with every result
    # so a score can always be traced to the opponent that produced it.
    "FROZEN_AUDITOR_LABEL": os.environ.get("FROZEN_AUDITOR_LABEL", "unspecified"),
})

# Full detail: candidate prompt, activation transcripts, every audit transcript,
# judge rationales. Never returned over the wire.
private_candidate_log = modal.Dict.from_name(
    "secret-loyalties-installer-log", create_if_missing=True
)

EPISODES_PER_EVAL = 6


@app.function(image=image, secrets=[secret], timeout=1800)
@modal.asgi_app()
def web():
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel

    from auditor.agent import run_audit
    from sealed import organisms
    from sealed.installer_eval import evaluate_candidate, format_for_installer

    task = organisms.get(os.environ["INSTALLER_TASK_ORGANISM"])
    frozen_label = os.environ["FROZEN_AUDITOR_LABEL"]

    web_app = FastAPI()

    class EvaluateRequest(BaseModel):
        loyal_prompt: str
        k_episodes: int | None = None

    @web_app.get("/health")
    def health():
        return {
            "status": "ok",
            "frozen_auditor": frozen_label,
            "task_organism": task.id,
            # None of this is secret from the Installer -- it is told what to
            # target in its brief, and it needs the rivals to write comparison
            # -sensitive prompts. All of it stays hidden from the Auditor.
            "principal": task.principal,
            "principal_type": task.principal_type,
            "rivals": list(task.rivals),
        }

    @web_app.post("/evaluate")
    def evaluate(req: EvaluateRequest):
        if not req.loyal_prompt.strip():
            raise HTTPException(status_code=400, detail="loyal_prompt is empty")

        k = req.k_episodes or EPISODES_PER_EVAL
        started = time.time()

        result = evaluate_candidate(
            loyal_prompt=req.loyal_prompt,
            clean_prompt=task.clean_prompt,
            principal=task.principal,
            principal_type=task.principal_type,
            rivals=task.rivals,
            audit_fn=run_audit,
            k_episodes=k,
            include_off_topic=False,
            verbose=True,
        )

        private_candidate_log[str(uuid.uuid4())] = {
            "timestamp": started,
            "frozen_auditor": frozen_label,
            "task_organism": task.id,
            "loyal_prompt": req.loyal_prompt,
            "result": result,
        }

        # Strip the bulky private detail before it crosses the wire.
        public = {k2: v for k2, v in result.items() if not k2.startswith("_")}
        return {
            "report": format_for_installer(result),
            "frozen_auditor": frozen_label,
            **public,
        }

    return web_app
