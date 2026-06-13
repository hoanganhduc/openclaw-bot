#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"

resolve_venv() {
  local candidates=()
  if [[ -n "${DOCLING_VENV:-}" ]]; then
    candidates+=("${DOCLING_VENV}")
  fi
  candidates+=("${HOME}/.local/share/docling-venv")
  for candidate in "${candidates[@]}"; do
    if [[ -x "${candidate}/bin/python3" ]]; then
      echo "${candidate}"
      return 0
    fi
  done
  return 1
}

VENV="$(resolve_venv || true)"
if [[ -z "$VENV" ]]; then
  echo "docling venv python not found; set DOCLING_VENV or install at ~/.local/share/docling-venv" >&2
  exit 1
fi

PYTHON_BIN="$VENV/bin/python3"
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
