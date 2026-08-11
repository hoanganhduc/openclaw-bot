#!/usr/bin/env bash
# Compatibility launcher; the hardened multi-skill worker owns send-queue jobs.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "$SCRIPT_DIR/../../scripts/job_queue_worker.sh" "$@"
