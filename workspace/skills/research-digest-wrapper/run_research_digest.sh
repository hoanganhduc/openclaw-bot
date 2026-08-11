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
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd -P)"
WORKSPACE_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
export OPENCLAW_WORKSPACE="$WORKSPACE_ROOT"
SECRET_LOADER="$SCRIPT_DIR/../_load_skill_secrets.py"
TRUSTED_PYTHON=/usr/bin/python3
if [[ ! -f "$SECRET_LOADER" || -L "$SECRET_LOADER" ]]; then
  printf 'managed skill secret loader is unavailable\n' >&2
  exit 127
fi
exec "$TRUSTED_PYTHON" -I -S -B "$SECRET_LOADER" \
  --profile research-digest -- "$@"
