"""Optimizer backends.

Each backend drives some autonomous coding agent through the Optimizer loop
described in BRIEF.md and exposes the same entry point:

    run(brief: str, root: Path, events_dir: Path) -> int

`brief` is the already-templated BRIEF.md text, `root` is the sandbox workspace
(the agent's working directory), and `events_dir` is where the backend must
write a durable record of everything the agent did. That record is the whole
point: the sandbox is ephemeral, so anything not written there dies with it and
the run becomes unauditable.

The return value is a process-style exit code, 0 for success.
"""
