#!/bin/bash
# Run zot.py. Deps are in /workspace/.local/ (pip install --target /workspace/.local).
WS="${OPENCLAW_WORKSPACE:-/workspace}"
PYTHON_BIN="$(command -v python3)"
PYVER="$("$PYTHON_BIN" -c 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}")')"
export PYTHONPATH="$WS/.local/lib/$PYVER/site-packages:$WS/.local:$PYTHONPATH"
export PATH="$WS/.local/venv_getscipapers/bin:$WS/.local/bin:$PATH"
exec "$PYTHON_BIN" "$(dirname "$0")/zot.py" "$@"
