#!/usr/bin/env bash
# xai-personalize-dashboard entrypoint
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# Bring twitter CLI into PATH for the server's subprocess calls.
if [ -f "$HOME/.agent-reach/env.sh" ]; then
  # shellcheck disable=SC1091
  source "$HOME/.agent-reach/env.sh"
fi

PYTHON="${PYTHON:-python3}"

exec "$PYTHON" server.py "$@"
