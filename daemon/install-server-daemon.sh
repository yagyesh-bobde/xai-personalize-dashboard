#!/usr/bin/env bash
# Install the xai-personalize-server launchd agent so the dashboard server
# auto-starts at login and stays alive (launchd restarts it if it dies). This
# is what keeps http://127.0.0.1:7873 always reachable — no terminal required.
# Idempotent — safe to re-run (re-running picks up an updated run.sh/plist).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$(cd "$HERE/.." && pwd)"
TPL_SRC="$HERE/com.xai-personalize.server.plist.tpl"
PLIST_DST="$HOME/Library/LaunchAgents/com.xai-personalize.server.plist"
LABEL="com.xai-personalize.server"

mkdir -p "$HOME/Library/LaunchAgents"

# Bootout any existing copy (ignore errors — first install will have none).
# This also frees port 7873 if an old instance is running under launchd.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true

# Render template with this user's paths.
sed \
  -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
  -e "s|__HOME__|$HOME|g" \
  "$TPL_SRC" > "$PLIST_DST"
chmod 644 "$PLIST_DST"

launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
launchctl enable    "gui/$(id -u)/$LABEL"
launchctl kickstart "gui/$(id -u)/$LABEL"   # start it now (RunAtLoad covers reboots)

echo
echo "✓ installed → $PLIST_DST"
echo "✓ dashboard server auto-starts at login and restarts if it dies"
echo "✓ live now at http://127.0.0.1:7873/"
echo
echo "  logs:      /tmp/xai-personalize-server.log"
echo "  errors:    /tmp/xai-personalize-server.err"
echo "  uninstall: $HERE/uninstall-server-daemon.sh"
