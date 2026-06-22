#!/usr/bin/env bash
# Install the xai-personalize-refresh launchd agent so the pipeline
# auto-refreshes twice a day (08:00 and 20:00 local time). Independent
# of the dashboard server agent. Idempotent — safe to re-run.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$(cd "$HERE/.." && pwd)"
TPL_SRC="$HERE/com.xai-personalize.refresh.plist.tpl"
PLIST_DST="$HOME/Library/LaunchAgents/com.xai-personalize.refresh.plist"
LABEL="com.xai-personalize.refresh"

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
# No kickstart — this is a scheduled job; it fires at the next 08:00/20:00.

echo
echo "✓ installed → $PLIST_DST"
echo "✓ pipeline will auto-refresh daily at 08:00 and 20:00 local time"
echo
echo "  logs:      /tmp/xai-personalize-refresh.log"
echo "  errors:    /tmp/xai-personalize-refresh.err"
echo "  uninstall: $HERE/uninstall-refresh-daemon.sh"
