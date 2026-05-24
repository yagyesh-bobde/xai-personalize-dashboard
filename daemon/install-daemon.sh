#!/usr/bin/env bash
# Install the xai-personalize-dashboard launchd agent so the server auto-starts
# at login and respawns if it crashes. Idempotent — safe to re-run.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$(cd "$HERE/.." && pwd)"
TPL_SRC="$HERE/com.xai-personalize.dashboard.plist.tpl"
PLIST_DST="$HOME/Library/LaunchAgents/com.xai-personalize.dashboard.plist"
LABEL="com.xai-personalize.dashboard"

mkdir -p "$HOME/Library/LaunchAgents"

# Bootout any existing copy (ignore errors — first install will have none).
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true

# Render template with this user's paths.
sed \
  -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
  -e "s|__HOME__|$HOME|g" \
  "$TPL_SRC" > "$PLIST_DST"
chmod 644 "$PLIST_DST"

launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
launchctl enable    "gui/$(id -u)/$LABEL"
launchctl kickstart "gui/$(id -u)/$LABEL"

echo
echo "✓ installed → $PLIST_DST"
echo "✓ server should be live at http://127.0.0.1:7873/"
echo
echo "  logs:      /tmp/xai-personalize-dashboard.log"
echo "  errors:    /tmp/xai-personalize-dashboard.err"
echo "  uninstall: $HERE/uninstall-daemon.sh"
