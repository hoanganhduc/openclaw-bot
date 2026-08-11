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
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
secret_loader="$script_dir/../_load_skill_secrets.py"
python=/usr/bin/python3
if [[ ! -f "$secret_loader" || -L "$secret_loader" ]]; then
  printf 'managed skill secret loader is unavailable\n' >&2
  exit 127
fi
exec "$python" -I -S -B "$secret_loader" \
  --profile submission-venue-selector -- "$@"
