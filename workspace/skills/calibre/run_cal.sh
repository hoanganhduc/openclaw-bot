#!/usr/bin/bash -p
# Run Calibre only after projecting its two dedicated Google Drive settings.
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

SKILL_DIR="$(cd -- "$(dirname -- "$0")" && pwd -P)"
WORKSPACE="$(cd -- "$SKILL_DIR/../.." && pwd -P)"
SECRET_LOADER="$SKILL_DIR/../_load_skill_secrets.py"
TRUSTED_PYTHON=/usr/bin/python3

[[ -x "$TRUSTED_PYTHON" && -f "$SECRET_LOADER" && ! -L "$SECRET_LOADER" ]] || {
  printf 'managed Calibre secret loader is unavailable\n' >&2
  exit 127
}

exec "$TRUSTED_PYTHON" -I -S -B "$SECRET_LOADER" \
  --profile calibre -- "$@"
