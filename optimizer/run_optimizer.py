"""Drives OpenHands (via openhands-sdk) through Optimizer iterations against
optimizer/BRIEF.md.

Uses a SEPARATE venv (/workspace/.openhands-venv) from the harness's own
.venv -- openhands-ai pulls in a large, independent dependency tree, and
keeping it isolated avoids polluting sealed/'s minimal runtime.

Local/unsandboxed run (step 7): no enforced boundary yet, only BRIEF.md's
instructions -- OpenHands has real terminal + file-edit access to the whole
repo, same as steps 4-5's trust model. Step 8 adds the actual enforced
boundary (Modal Sandbox, network egress locked to the grader endpoint).

Run with: /workspace/.openhands-venv/bin/python optimizer/run_optimizer.py
"""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openhands.sdk import LLM, Agent, Conversation, LLMSummarizingCondenser, LocalWorkspace
from openhands.tools import get_default_tools

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

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
