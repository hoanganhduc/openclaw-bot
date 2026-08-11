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
unset AAS_RUNTIME_PYTHON MMA_PYTHON
: "${OPENCLAW_LIBEXEC:?OPENCLAW_LIBEXEC is required}"
: "${HOME:?HOME is required}"
ROOT="$OPENCLAW_LIBEXEC/manim-math-animation"
PYTHON="$HOME/.local/share/manim-math-animation-venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "fixed Manim Python runtime is unavailable: $PYTHON" >&2
  exit 127
fi
PYTHON_REAL="$(/usr/bin/readlink -f -- "$PYTHON")"
case "$PYTHON_REAL" in
  /usr/bin/python3|/usr/bin/python3.[0-9]|/usr/bin/python3.[0-9][0-9]) ;;
  *)
    echo "fixed Manim Python runtime resolves outside /usr/bin: $PYTHON_REAL" >&2
    exit 127
    ;;
esac
exec "$PYTHON" -I -B "$ROOT/manim_math_animation_runtime.py" "$@"
