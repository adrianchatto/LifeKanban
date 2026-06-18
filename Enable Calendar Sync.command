#!/bin/bash
set -euo pipefail

ROOT="/Users/adrianchatto/GitHub/LifeKanban"
PLIST="$ROOT/com.chatto.kanban.calendar-sync.plist"
DEST="$HOME/Library/LaunchAgents/com.chatto.kanban.calendar-sync.plist"

mkdir -p "$HOME/Library/LaunchAgents"
cp "$PLIST" "$DEST"
chmod 644 "$DEST"

launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"

echo "LifeKanban calendar sync enabled."
echo "It runs now and then hourly, importing Calendar events due within the next two days."
echo "Log: $ROOT/.calendar-sync.log"
