#!/usr/bin/bash -p
if [[ "$-" != *p* ]]; then
  exec /usr/bin/bash -p -- "$0" "$@"
fi
set -euo pipefail
umask 077
IFS=$' \t\n'
unset BASH_ENV ENV CDPATH GLOBIGNORE BASH_XTRACEFD PROMPT_COMMAND \
  PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT PYTHONWARNINGS \
  NODE_OPTIONS NODE_PATH LD_LIBRARY_PATH LD_PRELOAD PERL5OPT RUBYOPT \
  GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY \
  GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM
export PATH=/usr/bin:/bin

usage() {
  cat <<'EOF'
Usage: ./deploy.sh [--prefix DIR] [--dry-run] [--confirm]

Deploys manifest-generated public artifacts to a live OpenClaw prefix by
delegating to install.sh. --confirm is required for non-dry-run deploys.
EOF
}

PREFIX="${OPENCLAW_HOME:-$HOME/.openclaw}"
DRY_RUN=0
CONFIRM=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --confirm) CONFIRM=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$DRY_RUN" -eq 0 && "$CONFIRM" -ne 1 ]]; then
  echo "refusing live deploy without --confirm" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
args=(--prefix "$PREFIX")
[[ "$DRY_RUN" -eq 1 ]] && args+=(--dry-run)
"$SCRIPT_DIR/install.sh" "${args[@]}"

echo "deploy complete"
