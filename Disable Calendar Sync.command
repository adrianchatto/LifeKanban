#!/bin/bash
set -euo pipefail

DEST="$HOME/Library/LaunchAgents/com.chatto.kanban.calendar-sync.plist"

launchctl unload "$DEST" 2>/dev/null || true
rm -f "$DEST"

echo "LifeKanban calendar sync disabled."
