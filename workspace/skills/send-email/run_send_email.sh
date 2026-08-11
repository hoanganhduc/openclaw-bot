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
SCRIPT="$SCRIPT_DIR/send_email.py"

# This is the OpenClaw-specific launcher. Every real send crosses the host's
# exact one-time approval gate regardless of caller-controlled environment.
# Unsigned dry-runs remain local because they perform no outward action or key use.
if [[ "${1:-}" == "send" ]]; then
  _dry_run=0
  _sign=0
  for _arg in "$@"; do
    [[ "$_arg" == "--dry-run" ]] && _dry_run=1
    [[ "$_arg" == "--sign" ]] && _sign=1
  done
  if [[ "$_dry_run" -eq 0 || "$_sign" -eq 1 ]]; then
    exec "$SCRIPT_DIR/run_send_email_host.sh" "$@"
  fi
  # A local unsigned preview has no SMTP authority. Scrub ambient defaults so
  # the preview cannot silently add recipients, signatures, or identity text.
  unset SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD SMTP_FROM SMTP_SECURITY \
    SMTP_TIMEOUT SMTP_ACCOUNT SMTP_FROM_NAME SMTP_REPLY_TO SMTP_CC SMTP_BCC \
    SMTP_SIGNATURE SMTP_SIGNATURE_HTML SMTP_REPLY_TO_SELF SMTP_BCC_SELF \
    SMTP_PGP_SIGN SMTP_PGP_KEY SMTP_PGP_PASSPHRASE SMTP_GNUPG_HOME \
    SEND_EMAIL_ADDRESS_BOOK AAS_RUNTIME_WORKSPACE
  export SEND_EMAIL_SECRETS_FILE=/dev/null
  export SEND_EMAIL_EXACT_QUEUE=1
fi

if [[ ! -f "$SCRIPT" ]]; then
  printf 'runtime helper not found: %s\n' "$SCRIPT" >&2
  exit 127
fi

unset AAS_RUNTIME_PYTHON
exec /usr/bin/python3 -I -S -B "$SCRIPT" "$@"
