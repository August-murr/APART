#!/usr/bin/env bash
# Weight-training tool. Present only in GPU-enabled sandboxes.
set -euo pipefail
cd "$(dirname "$0")"
exec python train_lora.py "$@"
