"""Tiny standalone OpenRouter client for the Installer's own reasoning calls.

Mirror of auditor/_llm.py, and deliberately does NOT import anything from
sealed/: this file and agent.py are what the Installer's Optimizer reads and
rewrites, so there must be no code path here that could reach the judge, the
Auditor, or the fitness function.

The seed Installer is a pure template and never calls this. It exists so the
Optimizer can write a generative Installer -- one that drafts a prompt, critiques
it, and revises -- without having to invent a client first.
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


def think(prompt: str, max_tokens: int = 800) -> str:
    """One-shot call to the Installer's own reasoning model (config: installer_brain)."""
    response = _client.chat.completions.create(
        model=_MODELS.get("installer_brain", _MODELS["auditor_brain"]),
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.7,  # higher than the Auditor's: this job wants variety
    )
    return response.choices[0].message.content or ""
