#!/usr/bin/env bash
# The Installer's measurement command. Mirror of run_eval.sh.
set -euo pipefail
cd "$(dirname "$0")"
exec python run_installer_eval.py "$@"
