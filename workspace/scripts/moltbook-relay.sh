#!/usr/bin/bash -p
# Relay: move moltbook staging files → sanitizer input queue
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
: "${OPENCLAW_STATE_DIR:?OPENCLAW_STATE_DIR is required}"

STAGING_DIR="$OPENCLAW_STATE_DIR/workspace-moltbook/staging"
INPUT_DIR="$OPENCLAW_STATE_DIR/workspace-sanitizer/input"

/usr/bin/mkdir -p -- "$INPUT_DIR"

shopt -s nullglob
files=("$STAGING_DIR"/*.md)
if [[ ${#files[@]} -eq 0 ]]; then
    exit 0
fi

for f in "${files[@]}"; do
    [[ -f "$f" && ! -L "$f" ]] || continue
    fname="$(/usr/bin/basename "$f")"
    /usr/bin/mv -n -- "$f" "$INPUT_DIR/$fname"
    printf 'relayed: %s\n' "$fname"
done
