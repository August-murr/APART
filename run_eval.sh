#!/bin/bash
# The one bridge command between the Optimizer/Auditor side and the sealed
# harness. Resolves to the project root regardless of caller's cwd so it
# works the same whether invoked by hand or by an agent framework.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec .venv/bin/python run_eval.py "$@"
