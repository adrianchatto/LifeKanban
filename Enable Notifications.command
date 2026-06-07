#!/bin/bash
LABEL="com.chatto.kanban.notify"
SRC="$(cd "$(dirname "$0")" && pwd)/${LABEL}.plist"
DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cp "$SRC" "$DEST"
launchctl unload "$DEST" 2>/dev/null
launchctl load -w "$DEST"
echo "Background due-date notifications are now ON."
echo "They run every 30 minutes, even when the board is closed."
echo "(First run fires now; you may get a macOS prompt to allow notifications.)"
osascript -e 'display notification "Background due-date alerts are now active." with title "Kanban notifications enabled"' 2>/dev/null
echo "You can close this window."
