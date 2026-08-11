#!/usr/bin/bash -p
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

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
SCRIPT="$SCRIPT_DIR/axiom_axle_mcp.py"
SECRET_LOADER="$SCRIPT_DIR/../_load_skill_secrets.py"

if [[ ! -f "$SCRIPT" ]]; then
  printf 'runtime helper not found: %s\n' "$SCRIPT" >&2
  exit 127
fi

if [[ ! -f "$SECRET_LOADER" || -L "$SECRET_LOADER" ]]; then
  printf 'managed skill secret loader is unavailable\n' >&2
  exit 127
fi

TRUSTED_PYTHON=/usr/bin/python3
if [[ ! -x "$TRUSTED_PYTHON" || -L "$SECRET_LOADER" ]]; then
  printf 'trusted isolated Python loader is unavailable\n' >&2
  exit 127
fi

exec "$TRUSTED_PYTHON" -I -S -B "$SECRET_LOADER" \
  --profile axiom-axle-mcp -- "$@"
