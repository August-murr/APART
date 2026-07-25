"""Claude Code backend -- STATUS: DRAFT, smoke-tested only.

The README names "OpenHands may be the wrong optimizer" as one of four
hypotheses for why the measured run produced a null result. This backend exists
to make that hypothesis testable. It has NOT been run at scale and no
comparison against OpenHands is claimed.

Read this before comparing backends: OpenHands runs `optimizer_brain` through
OpenRouter and Claude Code runs a Claude model through Anthropic. Swapping
backends therefore changes the agent scaffold AND the underlying model at the
same time, so a difference in outcome cannot be attributed to either one. A
clean ablation would point OpenHands at a Claude model via OpenRouter, which is
a one-line change in config/models.yaml.

## Authentication

Two credentials work, and the precedence between them is a trap:

    ANTHROPIC_API_KEY        billed per token against Console credits
    CLAUDE_CODE_OAUTH_TOKEN  a `claude setup-token` token, billed to a subscription

Claude Code ranks the API key ABOVE the OAuth token. If both are present the key
wins silently, so a run intended to draw on a subscription would quietly spend
API credits instead. This module therefore strips ANTHROPIC_API_KEY from the
child environment whenever an OAuth token is available, rather than trusting the
caller to have kept them apart.

Note also that `--bare` does not read CLAUDE_CODE_OAUTH_TOKEN at all, which is
why it is not used below despite being the recommended mode for scripted calls.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml

# The Optimizer must run unattended: anything it attempts that is not permitted
# aborts the run mid-experiment and wastes the budget already spent. So every
# tool it needs is pre-approved -- it edits files under auditor/, runs
# ./run_eval.sh, and drives git.
#
# `--dangerously-skip-permissions` would be the obvious blunt instrument and is
# NOT used: Claude Code refuses it when running as root, which is exactly how
# the Modal sandbox executes. An explicit allowlist is the better answer anyway,
# since it states what the agent is expected to need.
#
# `acceptEdits` covers file writes and common filesystem commands; the Bash
# entry covers everything else it drives. Both are safe here only because of
# where this runs: an ephemeral sandbox whose network allowlist admits nothing
# but the grader and the model API, holding no credential beyond the one it
# needs and no data that outlives it.
BASE_ARGS = [
    "--output-format", "stream-json",
    "--verbose",
    "--permission-mode", "acceptEdits",
    "--allowedTools", "Bash,Read,Edit,Write,Glob,Grep,TodoWrite",
]


def _summarize(event: dict) -> str | None:
    """One console line for a stream event, or None to stay quiet.

    The raw stream is far too verbose to read while a run is in progress, but
    optimizer_stdout.log is the first thing anyone opens when a run misbehaves.
    So the console gets a transcript-shaped digest and the full events go to
    disk.
    """
    kind = event.get("type")

    if kind == "system" and event.get("subtype") == "init":
        return f"[init] model={event.get('model')} tools={len(event.get('tools') or [])}"

    if kind == "system" and event.get("subtype") == "api_retry":
        return (f"[retry] attempt {event.get('attempt')}/{event.get('max_retries')} "
                f"({event.get('error')})")

    if kind == "assistant":
        out = []
        for block in (event.get("message") or {}).get("content") or []:
            if block.get("type") == "text" and block.get("text", "").strip():
                out.append(block["text"].strip())
            elif block.get("type") == "tool_use":
                name = block.get("name")
                inp = block.get("input") or {}
                # Just enough of the argument to identify the action.
                hint = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
                hint = str(hint).replace("\n", " ")[:120]
                out.append(f"[tool] {name}: {hint}")
        return "\n".join(out) or None

    if kind == "result":
        return (f"[result] is_error={event.get('is_error')} "
                f"turns={event.get('num_turns')} "
                f"cost_usd={event.get('total_cost_usd')} "
                f"duration_ms={event.get('duration_ms')}")

    return None


def _child_env() -> dict:
    env = dict(os.environ)

    if env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        # See the module docstring: the API key outranks the OAuth token, so
        # leaving it set would silently bill the wrong account.
        env.pop("ANTHROPIC_API_KEY", None)
    elif not env.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "Claude Code backend needs CLAUDE_CODE_OAUTH_TOKEN (from `claude "
            "setup-token`, billed to a subscription) or ANTHROPIC_API_KEY "
            "(billed to Console credits). Neither is set."
        )

    # Non-essential traffic would be blocked by the sandbox allowlist anyway;
    # disabling it keeps the failure noise out of the event log.
    env["DISABLE_TELEMETRY"] = "1"
    env["DISABLE_ERROR_REPORTING"] = "1"
    env["DISABLE_AUTOUPDATER"] = "1"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env


def _collect_session_transcripts(events_dir: Path) -> None:
    """Copy Claude Code's own session JSONL out of ~/.claude.

    Redundant with the stream we already captured, but it is the format Claude
    Code itself can resume from, and it costs nothing to keep.
    """
    projects = Path.home() / ".claude" / "projects"
    if not projects.is_dir():
        return
    dest = events_dir / "claude_sessions"
    dest.mkdir(parents=True, exist_ok=True)
    for src in projects.rglob("*.jsonl"):
        try:
            shutil.copy(src, dest / src.name)
        except OSError as exc:  # a partially written session is not worth failing over
            print(f"  (could not copy {src.name}: {exc})")


def run(brief: str, root: Path, events_dir: Path) -> int:
    with open(root / "config" / "models.yaml") as f:
        models = yaml.safe_load(f)

    # Defaults to the cheapest model deliberately: most runs of this backend are
    # plumbing tests, and a real attempt should have to opt into a bigger model.
    model = models.get("optimizer_brain_claude", "claude-haiku-4-5-20251001")

    events_dir.mkdir(parents=True, exist_ok=True)
    stream_path = events_dir / "claude_code_stream.jsonl"

    cmd = ["claude", "-p", brief, "--model", model, *BASE_ARGS]
    print(f"claude-code backend: model={model} cwd={root}")
    print(f"streaming events to {stream_path}")

    proc = subprocess.Popen(
        cmd,
        cwd=str(root),
        env=_child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    saw_result = False
    with open(stream_path, "w") as raw:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            raw.write(line + "\n")
            raw.flush()  # a killed run must still leave a readable log behind
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                print(line[:500])
                continue
            if event.get("type") == "result":
                saw_result = True
            text = _summarize(event)
            if text:
                print(text, flush=True)

    stderr = proc.stderr.read()
    returncode = proc.wait()

    if stderr.strip():
        print("--- claude stderr ---")
        print(stderr[:4000])

    # A clean exit without a terminal `result` event means the stream was cut
    # off, so the log is incomplete even though the process looked fine.
    if returncode == 0 and not saw_result:
        print("WARNING: stream ended with no result event; the run may be truncated")

    _collect_session_transcripts(events_dir)
    return returncode
