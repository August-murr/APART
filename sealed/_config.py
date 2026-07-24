"""Shared config/env loading for everything under sealed/."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

with open(ROOT / "config" / "models.yaml") as f:
    MODELS = yaml.safe_load(f)

with open(ROOT / "config" / "budget.yaml") as f:
    BUDGET = yaml.safe_load(f)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
