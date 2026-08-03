#!/usr/bin/env bash
# Compatibility launcher; the multi-skill queue worker is runtime infrastructure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/../../scripts/job_queue_worker.sh" "$@"
