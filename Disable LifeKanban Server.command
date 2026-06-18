#!/bin/bash
set -euo pipefail

LABEL="com.chatto.kanban.server"
DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"

launchctl unload "$DEST" 2>/dev/null || true
rm -f "$DEST"

echo "LifeKanban server is now OFF."
osascript -e 'display notification "LifeKanban server has been disabled." with title "LifeKanban"' 2>/dev/null || true
