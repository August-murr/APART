"""STATUS: DRAFT -- built and smoke-tested, no capability claim.

The Auditor's grading service for co-evolution rounds: identical to
modal_service.py except that its organism pool includes prompts written by the
Installer, not just the hand-authored registry.

## Why a third app rather than a flag on the existing grader

`secret-loyalties-grader` serves the measured `poc-v1` result and
`evaluate_generations.py` still re-scores that run against it. Adding evolved
organisms to it would mean redeploying it every co-evolution round, putting the
reproducibility of a finished experiment at the mercy of drafted code. So this
deploys as `secret-loyalties-grader-coevo` and the measured grader is never
touched.

## How evolved organisms get in

`coevolve.py` writes `sealed/evolved_organisms.json` and redeploys this app
before each Auditor phase. The file is baked into the image, so the pool is
fixed for the whole phase and every episode in a round faces the same
distribution. Crude compared with a shared volume, but it makes "which organisms
produced this score" unambiguous, which matters more than deploy latency.

Each entry:

    {"id", "principal", "principal_type", "clean_prompt", "loyal_prompt",
     "rivals": [...], "source": "installer round N gen M"}

An empty or missing file means the pool is exactly the registry, and this
service behaves identically to modal_service.py.

Endpoints are identical to modal_service.py -- same session-scoped grading, same
refusal to return per-episode grades, same reasoning (see sealed/grader.py).

Deploy with:
  .venv/bin/modal deploy sealed/coevo_grader_service.py
"""

import json
import os
import time
import uuid

import modal
from dotenv import load_dotenv

load_dotenv()

app = modal.App("secret-loyalties-grader-coevo")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("openai>=1.50.0", "pyyaml>=6.0", "python-dotenv>=1.0.0", "fastapi[standard]")
    .add_local_dir("sealed", remote_path="/root/sealed")
    .add_local_dir("config", remote_path="/root/config")
)

secret = modal.Secret.from_dict({
    "OPENROUTER_API_KEY": os.environ["OPENROUTER_API_KEY"],
    "COEVO_ROUND_LABEL": os.environ.get("COEVO_ROUND_LABEL", "unspecified"),
})

episodes = modal.Dict.from_name("secret-loyalties-coevo-episodes", create_if_missing=True)
sessions = modal.Dict.from_name("secret-loyalties-coevo-sessions", create_if_missing=True)
private_episode_log = modal.Dict.from_name("secret-loyalties-coevo-episode-log", create_if_missing=True)
session_grades = modal.Dict.from_name("secret-loyalties-coevo-session-grades", create_if_missing=True)

TURN_BUDGET = 10


@app.function(image=image, secrets=[secret])
@modal.asgi_app()
def web():
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel

    from sealed import grader, judge, organisms
    from sealed.grader import summarize
    from sealed.organisms import Organism
    from sealed.target_service import call_target

    round_label = os.environ["COEVO_ROUND_LABEL"]

    def load_pool() -> tuple[list, list[str]]:
        """Registry train organisms plus whatever the Installer has produced."""
        pool = list(organisms.train_organisms())
        sources = [f"registry:{o.id}" for o in pool]

        path = "/root/sealed/evolved_organisms.json"
        if os.path.exists(path):
            with open(path) as f:
                for entry in json.load(f):
                    pool.append(Organism(
                        id=entry["id"],
                        principal=entry["principal"],
                        principal_type=entry["principal_type"],
                        aliases=tuple(entry.get("aliases") or ()),
                        clean_prompt=entry["clean_prompt"],
                        loyal_prompt=entry["loyal_prompt"],
                        rivals=tuple(entry.get("rivals") or ()),
                    ))
                    sources.append(entry.get("source", entry["id"]))
        return pool, sources

    POOL, POOL_SOURCES = load_pool()

    web_app = FastAPI()

    class ChatRequest(BaseModel):
        message: str

    class ReportRequest(BaseModel):
        verdict: str | None = None
        principal: str | None = None
        confidence: float | None = None
        evidence: list = []

    @web_app.get("/health")
    def health():
        # Counts and sources only. The Auditor must not learn which principals
        # are in the pool -- that is the thing it is supposed to discover.
        return {"status": "ok", "round": round_label, "pool_size": len(POOL)}

    @web_app.post("/session/start")
    def session_start():
        session_id = str(uuid.uuid4())
        sessions[session_id] = {"created": time.time()}
        return {"session_id": session_id}

    @web_app.post("/session/{session_id}/episode/start")
    def episode_start(session_id: str):
        if session_id not in sessions:
            raise HTTPException(status_code=404, detail="unknown session_id")
        episode = grader._create_episode(pool=POOL)
        episode_id = episode["episode_id"]
        episodes[episode_id] = {**episode, "session_id": session_id, "messages": [], "transcript": []}
        return {"episode_id": episode_id}

    @web_app.post("/episode/{episode_id}/chat")
    def episode_chat(episode_id: str, req: ChatRequest):
        if episode_id not in episodes:
            raise HTTPException(status_code=404, detail="unknown episode_id")
        episode = episodes[episode_id]
        if len(episode["transcript"]) >= TURN_BUDGET:
            raise HTTPException(status_code=400, detail=f"turn budget of {TURN_BUDGET} exceeded")

        episode["messages"].append({"role": "user", "content": req.message})
        reply = call_target(episode["system_prompt"], episode["messages"], max_tokens=400)
        episode["messages"].append({"role": "assistant", "content": reply})
        episode["transcript"].append({"auditor": req.message, "target": reply})
        episodes[episode_id] = episode  # modal.Dict values are serialized; must reassign

        return {"reply": reply}

    @web_app.post("/episode/{episode_id}/submit_report")
    def episode_submit_report(episode_id: str, req: ReportRequest):
        if episode_id not in episodes:
            raise HTTPException(status_code=404, detail="unknown episode_id")
        episode = episodes[episode_id]
        report = req.model_dump()

        grade = judge.grade_auditor_report(
            transcript=episode["transcript"],
            report=report,
            ground_truth={
                "mode": episode["mode"],
                "principal": episode["principal"],
                "principal_type": episode["principal_type"],
            },
        )

        private_episode_log[episode_id] = {
            "episode_id": episode_id,
            "session_id": episode["session_id"],
            "round": round_label,
            "organism_id": episode["organism_id"],
            "domain": episode["domain"],
            "ground_truth_mode": episode["mode"],
            "ground_truth_principal": episode["principal"],
            "ground_truth_principal_type": episode["principal_type"],
            "transcript": episode["transcript"],
            "report": report,
            "grade": grade,
        }
        session_grades[episode_id] = {
            "session_id": episode["session_id"],
            "score": grade["score"],
            "verdict_correct": grade["verdict_correct"],
            "outcome": grade["outcome"],
            "domain": episode["domain"],
        }

        del episodes[episode_id]  # one-shot: an episode can't be submitted twice
        return {"ok": True}  # deliberately no score -- see sealed/grader.py

    @web_app.get("/session/{session_id}/summary")
    def session_summary(session_id: str):
        if session_id not in sessions:
            raise HTTPException(status_code=404, detail="unknown session_id")
        grades = [v for _, v in session_grades.items() if v.get("session_id") == session_id]
        if not grades:
            raise HTTPException(status_code=400, detail="session has no completed episodes")
        return summarize(grades)

    return web_app
