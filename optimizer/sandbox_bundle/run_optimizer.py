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
conversation = Conversation(agent=agent, workspace=workspace)

brief = (ROOT / "optimizer" / "BRIEF.md").read_text()
conversation.send_message(brief)
conversation.run()
