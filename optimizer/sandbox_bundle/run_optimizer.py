"""Runs INSIDE the Modal Sandbox: drives OpenHands through the Optimizer
loop against BRIEF.md. OPENROUTER_API_KEY and GRADER_URL are already in the
environment (injected at Sandbox creation, not loaded from a .env file --
there isn't one in here).
"""

import os
from pathlib import Path

import yaml
from openhands.sdk import LLM, Agent, Conversation, LLMSummarizingCondenser, LocalWorkspace
from openhands.tools import get_default_tools

ROOT = Path(__file__).resolve().parent

with open(ROOT / "config" / "models.yaml") as f:
    MODELS = yaml.safe_load(f)

llm = LLM(
    model=f"openrouter/{MODELS['optimizer_brain']}",
    api_key=os.environ["OPENROUTER_API_KEY"],
    base_url="https://openrouter.ai/api/v1",
    usage_id="optimizer-brain",
)

agent = Agent(
    llm=llm,
    tools=get_default_tools(enable_browser=False),
    condenser=LLMSummarizingCondenser(llm=llm, max_size=80),
)

workspace = LocalWorkspace(working_dir=str(ROOT))

# persistence_dir is what makes the run auditable after the fact. Without it the
# SDK keeps conversation state in memory only, so everything the Optimizer
# reasoned about and every tool call it made dies with the ephemeral sandbox and
# the only surviving record is whatever it chose to write into NOTES.md. With it,
# the SDK writes base_state.json plus one JSON file per event, which
# run_sandboxed_optimizer.py syncs out into runs/<run_id>/optimizer_events/.
conversation = Conversation(
    agent=agent,
    workspace=workspace,
    persistence_dir=str(ROOT / "optimizer_events"),
)

brief = (ROOT / "optimizer" / "BRIEF.md").read_text()
conversation.send_message(brief)
conversation.run()
