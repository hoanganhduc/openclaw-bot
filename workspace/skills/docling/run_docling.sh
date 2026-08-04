#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
WORKSPACE="${OPENCLAW_WORKSPACE:-${HOME}/.openclaw/workspace}"
if [[ "$HOME" == "/workspace" && "$WORKSPACE" == "/workspace" ]]; then
  VENV="/opt/coding-system/python-closure/docling-cpu"
  RUNTIME_LABEL="locked OpenClaw sandbox image"
else
  VENV="${DOCLING_VENV:-${HOME}/.local/share/coding-system/python-closure/docling-cpu}"
  RUNTIME_LABEL="host Python closure"
fi
PYTHON_BIN="$VENV/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "docling runtime missing from the $RUNTIME_LABEL: $VENV" >&2
  exit 1
fi
export PATH="$VENV/bin:${PATH}"

cmd="${1:-}"
if [[ -z "$cmd" ]]; then
  echo "usage: run_docling.sh <doctor|convert|extract|chunk> [args...]" >&2
  exit 1
fi
shift || true

case "$cmd" in
  doctor) exec "$PYTHON_BIN" "$ROOT/doctor.py" "$@" ;;
  convert) exec "$PYTHON_BIN" "$ROOT/docling_convert.py" "$@" ;;
  extract) exec "$PYTHON_BIN" "$ROOT/docling_extract.py" "$@" ;;
  chunk) exec "$PYTHON_BIN" "$ROOT/docling_chunk.py" "$@" ;;
  *) echo "unknown subcommand: $cmd" >&2; exit 1 ;;
esac
