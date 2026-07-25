"""Runs INSIDE the Modal Sandbox: drives the chosen Optimizer backend through
the loop described in the brief.

OPENROUTER_API_KEY, GRADER_URL and (for the Claude Code backend)
CLAUDE_CODE_OAUTH_TOKEN are already in the environment, injected at Sandbox
creation rather than loaded from a .env file -- there isn't one in here.

Which agent drives the loop is chosen by OPTIMIZER_BACKEND:

    openhands    (default) the backend used for the measured `poc-v1` run
    claude_code  DRAFT, smoke-tested only

Which brief it is given is chosen by ROLE:

    auditor      (default) improve auditor/ at detecting hidden loyalties
    installer    improve installer/ at installing loyalties that evade detection

Both backends write into optimizer_events/, which run_sandboxed_optimizer.py
syncs back out into runs/<run_id>/.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

BACKENDS = {
    "openhands": "backends.openhands_backend",
    "claude_code": "backends.claude_code_backend",
}

BRIEFS = {
    "auditor": "BRIEF.md",
    "installer": "BRIEF_INSTALLER.md",
}


def main() -> int:
    backend_name = os.environ.get("OPTIMIZER_BACKEND", "openhands")
    role = os.environ.get("ROLE", "auditor")

    if backend_name not in BACKENDS:
        raise SystemExit(f"unknown OPTIMIZER_BACKEND={backend_name!r}; "
                         f"expected one of {sorted(BACKENDS)}")
    if role not in BRIEFS:
        raise SystemExit(f"unknown ROLE={role!r}; expected one of {sorted(BRIEFS)}")

    brief_path = ROOT / "optimizer" / BRIEFS[role]
    if not brief_path.exists():
        raise SystemExit(f"brief not found: {brief_path}")

    import importlib
    backend = importlib.import_module(BACKENDS[backend_name])

    print(f"backend={backend_name} role={role} brief={brief_path.name}")
    returncode = backend.run(
        brief=brief_path.read_text(),
        root=ROOT,
        events_dir=ROOT / "optimizer_events",
    )
    print(f"backend returned {returncode}")
    return returncode


if __name__ == "__main__":
    sys.exit(main())
