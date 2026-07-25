"""Modal deployment of the grading harness as an HTTP service -- the
network-facing twin of grader.py.

Why this is a separate file rather than just deploying grader.py directly:
grader.py's episode state is a Python closure living in one process. Once the
Auditor runs inside its own separate, network-restricted modal.Sandbox, each
HTTP request could hit a different container, so episode state has to live
somewhere shared -- hence modal.Dict. Same underlying secrets, organisms and
grading logic, different state/transport mechanics.

## Grading is session-scoped

Episodes belong to a session, and grades are returned only in aggregate when
the session is summarized. Nothing per-episode comes back -- see the long note
in grader.py: returning per-episode {"score", "correct"} let the caller recover
each episode's true label by combining it with the verdict it had just
submitted, which would have made the Optimizer's task supervised rather than
black-box.

Endpoints:
  GET  /health                              -> {"status": "ok"}
  POST /session/start                       -> {"session_id": str}
  POST /session/{session_id}/episode/start  -> {"episode_id": str}
  POST /episode/{episode_id}/chat           -> {"reply": str}, given {"message": str}
  POST /episode/{episode_id}/submit_report  -> {"ok": true}, given the Auditor's report
  GET  /session/{session_id}/summary        -> sanitized aggregate summary

Deploy with: .venv/bin/modal deploy sealed/modal_service.py
"""

import os
import time
import uuid

import modal
from dotenv import load_dotenv

load_dotenv()  # so OPENROUTER_API_KEY is available below at deploy time

app = modal.App("secret-loyalties-grader")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("openai>=1.50.0", "pyyaml>=6.0", "python-dotenv>=1.0.0", "fastapi[standard]")
    .add_local_dir("sealed", remote_path="/root/sealed")
    .add_local_dir("config", remote_path="/root/config")
)

# Deliberately narrow: from_dotenv() would also bake MODAL_TOKEN_ID/SECRET
# (this file's own deploy credentials) into the deployed function's runtime
# env, which it has no use for. Only OPENROUTER_API_KEY is actually needed
# by target_service.py / judge.py inside the container.
secret = modal.Secret.from_dict({"OPENROUTER_API_KEY": os.environ["OPENROUTER_API_KEY"]})

episodes = modal.Dict.from_name("secret-loyalties-episodes", create_if_missing=True)
sessions = modal.Dict.from_name("secret-loyalties-sessions", create_if_missing=True)
private_episode_log = modal.Dict.from_name("secret-loyalties-episode-log", create_if_missing=True)
# Just the four fields the summary needs, one small record per episode. Kept
# separate from private_episode_log because that one carries full transcripts:
# summarizing by scanning it meant deserializing every transcript ever graded on
# every call, which grew with the run until the request outlived the caller's
# timeout mid-run. Same race-free per-episode-key write, a fraction of the bytes.
session_grades = modal.Dict.from_name("secret-loyalties-session-grades", create_if_missing=True)

TURN_BUDGET = 10


@app.function(image=image, secrets=[secret])
@modal.asgi_app()
def web():
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel

    from sealed import judge
    from sealed.grader import _create_episode, summarize
    from sealed.target_service import call_target

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
        return {"status": "ok"}

    @web_app.post("/session/start")
    def session_start():
        session_id = str(uuid.uuid4())
        sessions[session_id] = {"created": time.time()}  # marker only; grades are not accumulated here
        return {"session_id": session_id}

    @web_app.post("/session/{session_id}/episode/start")
    def episode_start(session_id: str):
        if session_id not in sessions:
            raise HTTPException(status_code=404, detail="unknown session_id")
        episode = _create_episode()  # organism + mode chosen here, never revealed
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
        episodes[episode_id] = episode  # modal.Dict values are serialized, must reassign to persist

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
        return {"ok": True}  # deliberately no score -- see module docstring

    @web_app.get("/session/{session_id}/summary")
    def session_summary(session_id: str):
        """Aggregates from private_episode_log rather than from an accumulator
        on the session.

        Episodes run concurrently, and appending each grade to a shared
        session["grades"] list is a read-modify-write on one modal.Dict key:
        two submissions landing together would both read the same list and the
        second write would silently drop the first grade. Batches would come
        back short and scores would be quietly wrong. Each episode instead
        writes its own key, which cannot race, and the summary filters.
        """
        if session_id not in sessions:
            raise HTTPException(status_code=404, detail="unknown session_id")

        grades = [v for _, v in session_grades.items() if v.get("session_id") == session_id]
        if not grades:
            # Fallback for sessions graded before session_grades existed. Slow
            # (deserializes transcripts), but only reachable for legacy sessions.
            grades = [
                {
                    "score": v["grade"]["score"],
                    "verdict_correct": v["grade"]["verdict_correct"],
                    "outcome": v["grade"]["outcome"],
                    "domain": v["domain"],
                }
                for _, v in private_episode_log.items()
                if v.get("session_id") == session_id
            ]
        if not grades:
            raise HTTPException(status_code=400, detail="session has no completed episodes")
        return summarize(grades)

    return web_app
