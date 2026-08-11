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
: "${OPENCLAW_LIBEXEC:?OPENCLAW_LIBEXEC is required}"
: "${OPENCLAW_WORKSPACE:?OPENCLAW_WORKSPACE is required}"

RUNTIME_CREDENTIALS="/run/user/${UID}/credentials"
if [[ -r "$RUNTIME_CREDENTIALS" ]]; then
  echo "RSS runner refuses a namespace with visible runtime credentials" >&2
  exit 1
fi

RUNTIME="$OPENCLAW_LIBEXEC/rss-news-digest/rss_news_digest.py"
PUBLISHER="$OPENCLAW_LIBEXEC/rss-news-digest/rss_summary_publish.py"

/usr/bin/python3 -I -B "$RUNTIME" run --all-tags --profile ai_research \
  --require-existing-feeds --no-write-digest-stubs
exec /usr/bin/python3 -I -S -B "$PUBLISHER" --workspace "$OPENCLAW_WORKSPACE"
