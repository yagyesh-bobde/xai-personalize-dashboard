#!/usr/bin/env bash
# Remove the xai-personalize-server launchd agent (stops the always-on server).
set -euo pipefail

PLIST_DST="$HOME/Library/LaunchAgents/com.xai-personalize.server.plist"
LABEL="com.xai-personalize.server"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST_DST"
echo "✓ uninstalled — $PLIST_DST removed (server no longer auto-starts)"
