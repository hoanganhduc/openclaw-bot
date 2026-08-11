#!/bin/bash
# Universal skill runner for Claude Code — sets up the OpenClaw workspace environment
# Usage: _run.sh <script> [args...]
#   e.g. _run.sh skills/zotero/run_zot.sh --json get "query"
#   e.g. _run.sh skills/sagemath/run_sage.sh "G = graphs.PetersenGraph(); print(G.chromatic_number())"

if [[ -z "${OPENCLAW_WORKSPACE:-}" ]]; then
    export OPENCLAW_WORKSPACE="{{ OPENCLAW_WORKSPACE }}"
fi
# A universal dispatcher is not a credential authority. Skills that need a
# protected selector or key must opt in through their own bounded launcher.
unset AAS_SECRETS_FILE OPENCLAW_SECRETS_FILE AAS_SKILL_SECRETS_FILE PYTHONPATH PYTHONHOME PYTHONSTARTUP
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN GH_TOKEN GITHUB_TOKEN OPENAI_API_KEY
unset SMTP_PASSWORD SMTP_TOKEN
export PATH="$HOME/.local/bin:$OPENCLAW_WORKSPACE/.local/bin:$OPENCLAW_WORKSPACE/.local/venv_getscipapers/bin:$HOME/.venvs/bin:$PATH"

cd "$OPENCLAW_WORKSPACE" || exit 1

# Resolve relative skill paths
script="$1"; shift
if [[ "$script" != /* ]]; then
    script="$OPENCLAW_WORKSPACE/$script"
fi

exec bash "$script" "$@"
