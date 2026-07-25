"""OpenHands backend -- the Optimizer used for the measured `poc-v1` run.

Behaviour here is deliberately unchanged from the single-backend version this
was split out of, so `OPTIMIZER_BACKEND=openhands` still reproduces the tagged
result. If you are changing this file, you are changing the baseline.
"""

import os
from pathlib import Path

import yaml
from openhands.sdk import LLM, Agent, Conversation, LLMSummarizingCondenser, LocalWorkspace
from openhands.tools import get_default_tools


def run(brief: str, root: Path, events_dir: Path) -> int:
    with open(root / "config" / "models.yaml") as f:
        models = yaml.safe_load(f)

    llm = LLM(
        model=f"openrouter/{models['optimizer_brain']}",
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
        usage_id="optimizer-brain",
    )

    agent = Agent(
        llm=llm,
        tools=get_default_tools(enable_browser=False),
        condenser=LLMSummarizingCondenser(llm=llm, max_size=80),
    )

    # persistence_dir is what makes the run auditable after the fact. Without it
    # the SDK keeps conversation state in memory only, so everything the
    # Optimizer reasoned about and every tool call it made dies with the
    # ephemeral sandbox, and the only surviving record is whatever it chose to
    # write into NOTES.md. With it, the SDK writes base_state.json plus one JSON
    # file per event, which run_sandboxed_optimizer.py syncs back out.
    conversation = Conversation(
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(root)),
        persistence_dir=str(events_dir),
    )

    conversation.send_message(brief)
    conversation.run()
    return 0
