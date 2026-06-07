#!/bin/bash
LABEL="com.chatto.kanban.notify"
DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"
launchctl unload "$DEST" 2>/dev/null
rm -f "$DEST"
echo "Background due-date notifications are now OFF."
echo "You can close this window."
