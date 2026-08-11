#!/usr/bin/bash -p
# Direct-CLI Lean declaration search for OpenClaw (non-MCP).  The sandbox uses
# its image-owned closure; host execution uses the restored host closure.  This
# launcher never creates an environment or contacts a package index.
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
WS="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
TRUSTED_PYTHON=/usr/bin/python3
account_home="$($TRUSTED_PYTHON -I -S -B -c \
  'import os,pwd; print(pwd.getpwuid(os.geteuid()).pw_dir)')"
if [[ "$WS" == "/workspace" ]]; then
  VENV="/opt/coding-system/python-closure/lean-explore"
  RUNTIME_LABEL="locked OpenClaw sandbox image"
else
  VENV="$account_home/.local/share/coding-system/python-closure/lean-explore"
  RUNTIME_LABEL="host Python closure"
fi
SECRET_LOADER="$SCRIPT_DIR/../_load_skill_secrets.py"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "lean-explore runtime missing from the $RUNTIME_LABEL: $VENV" >&2
  exit 1
fi
if [[ ! -f "$SECRET_LOADER" || -L "$SECRET_LOADER" ]]; then
  echo "managed skill secret loader is unavailable" >&2
  exit 127
fi
SITE_PACKAGES="$VENV/lib/python3.12/site-packages"
[[ -x "$TRUSTED_PYTHON" && -d "$SITE_PACKAGES" ]] || {
  echo "trusted isolated Python loader or LeanExplore closure is unavailable" >&2
  exit 127
}
exec "$TRUSTED_PYTHON" -I -S -B "$SECRET_LOADER" \
  --profile lean-explore-cli -- "$@"
