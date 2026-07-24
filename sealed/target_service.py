"""Thin OpenRouter client for the target model.

This is the only place that ever attaches a system prompt to a call to the
target model. grader.py (step 4) builds episode-based access control on top
of this — the Auditor calls into that layer, never this one directly, so it
never sees which system prompt is active.
"""

from openai import OpenAI

from sealed._config import MODELS, OPENROUTER_API_KEY, OPENROUTER_BASE_URL

_client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=OPENROUTER_API_KEY)


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
