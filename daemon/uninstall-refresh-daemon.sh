#!/usr/bin/env bash
# Remove the xai-personalize-refresh launchd agent.
set -euo pipefail

PLIST_DST="$HOME/Library/LaunchAgents/com.xai-personalize.refresh.plist"
LABEL="com.xai-personalize.refresh"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST_DST"
echo "✓ uninstalled — $PLIST_DST removed"
