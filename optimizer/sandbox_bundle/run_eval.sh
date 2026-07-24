#!/bin/bash
# Sandbox-side bridge command -- same interface as the local run_eval.sh,
# but talks to the deployed grader over HTTP (GRADER_URL) since sealed/
# isn't present in this sandbox at all.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec python run_eval.py "$@"
