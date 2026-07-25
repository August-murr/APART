"""Thin OpenRouter client for the target model.

This is the only place that ever attaches a system prompt to a call to the
target model. grader.py (step 4) builds episode-based access control on top
of this — the Auditor calls into that layer, never this one directly, so it
never sees which system prompt is active.
"""

from openai import OpenAI

from sealed._config import MODELS, OPENROUTER_API_KEY, OPENROUTER_BASE_URL

# Setting `target_base_url` in config/models.yaml points the target at any
# OpenAI-compatible endpoint instead of OpenRouter -- in particular
# lora/serve_adapter.py, which serves an open-weights model with a loyalty
# trained into its weights rather than stated in its prompt.
#
# This is the ONLY change the weight-level organism needs on the sealed side.
# Everything reaches the target through call_target(), so grader.py, judge.py
# and the Auditor are all unaffected.
_TARGET_BASE_URL = MODELS.get("target_base_url") or OPENROUTER_BASE_URL

_client = OpenAI(
    base_url=_TARGET_BASE_URL,
    # A locally-served adapter needs no credential, but the OpenAI client
    # refuses to construct without one.
    api_key=OPENROUTER_API_KEY or "not-needed",
)


def call_target(system_prompt: str, messages: list[dict[str, str]], max_tokens: int = 1024) -> str:
    """Call the target model with a given system prompt and conversation so far.

    messages: prior turns as [{"role": "user"|"assistant", "content": "..."}],
    NOT including the system prompt itself.
    """
    response = _client.chat.completions.create(
        model=MODELS["target"],
        messages=[{"role": "system", "content": system_prompt}, *messages],
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content
