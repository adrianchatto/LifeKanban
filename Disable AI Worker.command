#!/bin/bash
set -euo pipefail

LABEL="com.chatto.kanban.worker"
DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"

launchctl unload "$DEST" 2>/dev/null || true
rm -f "$DEST"

echo "AI worker is now OFF."
osascript -e 'display notification "AI worker has been disabled." with title "LifeKanban"' 2>/dev/null || true
