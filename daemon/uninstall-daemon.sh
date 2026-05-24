#!/usr/bin/env bash
# Remove the xai-personalize-dashboard launchd agent.
set -euo pipefail

PLIST_DST="$HOME/Library/LaunchAgents/com.xai-personalize.dashboard.plist"
LABEL="com.xai-personalize.dashboard"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST_DST"
echo "✓ uninstalled — $PLIST_DST removed"
