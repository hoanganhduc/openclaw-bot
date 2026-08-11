#!/usr/bin/bash -p
# Compatibility entrypoint; centralized authorization lives in send_file.sh.
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

CHAT_ID="${1:?Usage: send_telegram.sh <chat_id> <file_path> [caption]}"
FILE_PATH="${2:?Usage: send_telegram.sh <chat_id> <file_path> [caption]}"
CAPTION="${3:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
exec /usr/bin/bash -p "$SCRIPT_DIR/send_file.sh" telegram "$CHAT_ID" "$FILE_PATH" "$CAPTION"
