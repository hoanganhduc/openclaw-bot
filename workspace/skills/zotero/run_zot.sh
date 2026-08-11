#!/usr/bin/bash -p
# Run zot.py. Deps are in /workspace/.local/ (pip install --target /workspace/.local).
if [[ "$-" != *p* ]]; then
  exec /usr/bin/bash -p -- "$0" "$@"
fi
set -euo pipefail
umask 077
IFS=$' \t\n'
unset BASH_ENV ENV CDPATH GLOBIGNORE BASH_XTRACEFD PROMPT_COMMAND \
  PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONWARNINGS \
  NODE_OPTIONS NODE_PATH LD_LIBRARY_PATH LD_PRELOAD PERL5OPT RUBYOPT
export PATH=/usr/bin:/bin
SKILL_DIR="$(cd "$(dirname "$0")" && pwd -P)"
DERIVED_WORKSPACE="$(cd "$SKILL_DIR/../.." && pwd -P)"
WS="$DERIVED_WORKSPACE"
export OPENCLAW_WORKSPACE="$WS"
PYTHON_BIN=/usr/bin/python3
SECRET_LOADER="$SKILL_DIR/../_load_skill_secrets.py"
if [[ ! -f "$SECRET_LOADER" || -L "$SECRET_LOADER" ]]; then
  printf 'managed skill secret loader is unavailable\n' >&2
  exit 127
fi
exec "$PYTHON_BIN" -I -S -B "$SECRET_LOADER" \
  --profile zotero -- "$@"
