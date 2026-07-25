"""Tiny standalone OpenRouter client for the Auditor's own reasoning calls.

Deliberately does NOT import anything from sealed/ -- this file and agent.py
are what the Optimizer reads and rewrites, so there must be no code path
here that could reach the organism's system prompts or the judge's ground
truth. config/models.yaml and OPENROUTER_API_KEY are not secret (see
sealed/grader.py's docstring for why): knowing which model architecture is
deployed, or holding a key that can call OpenRouter, reveals nothing about
which system prompt is active.
"""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

with open(ROOT / "config" / "models.yaml") as f:
    _MODELS = yaml.safe_load(f)

_client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ.get("OPENROUTER_API_KEY"))


def think(prompt: str, max_tokens: int = 600) -> str:
    """One-shot call to the Auditor's own reasoning model (config: auditor_brain)."""
    response = _client.chat.completions.create(
        model=_MODELS["auditor_brain"],
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.3,
    )
    return response.choices[0].message.content or ""
