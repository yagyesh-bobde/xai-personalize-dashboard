#!/usr/bin/env bash
# Scheduled pipeline refresh — invoked twice daily (08:00 / 20:00) by the
# com.xai-personalize.refresh launchd agent.
#
# Prefers the running server's POST /refresh so the work shares the server's
# in-process lock (no chance of two pipeline processes writing the data file at
# once — the server also auto-refreshes when data goes stale). Falls back to
# running pipeline.py directly only if the server is unreachable.
#
# Note: even if the Mac is asleep/off at 08:00 or 20:00, the server's own
# staleness-based auto-refresh catches up whenever the machine is next on, so a
# missed clock trigger here is not fatal.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$(cd "$HERE/.." && pwd)"
cd "$INSTALL_DIR"

# Bring twitter CLI + env into PATH for the pipeline's subprocess calls.
if [ -f "$HOME/.agent-reach/env.sh" ]; then
  # shellcheck disable=SC1091
  source "$HOME/.agent-reach/env.sh"
fi

PORT="${DASHBOARD_PORT:-7873}"
PYTHON="${PYTHON:-python3}"

echo "=== refresh $(date '+%Y-%m-%d %H:%M:%S') ==="

# Try the running server first (shares its refresh lock).
if curl -fsS -m 900 -X POST "http://127.0.0.1:${PORT}/refresh" >/dev/null 2>&1; then
  echo "refreshed via server /refresh on port ${PORT}"
  exit 0
fi

echo "server unreachable on port ${PORT} — running pipeline.py directly"
exec "$PYTHON" pipeline.py
