#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.chatto.kanban.server"
DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG="$ROOT/.server.log"

xml_escape() {
  python3 -c 'import html,sys; print(html.escape(sys.argv[1], quote=True))' "$1"
}

mkdir -p "$HOME/Library/LaunchAgents"
touch "$LOG"

ROOT_XML="$(xml_escape "$ROOT")"
SERVER_XML="$(xml_escape "$ROOT/server.py")"
LOG_XML="$(xml_escape "$LOG")"

cat > "$DEST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>

  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>-u</string>
    <string>${SERVER_XML}</string>
    <string>--no-browser</string>
  </array>

  <key>WorkingDirectory</key>
  <string>${ROOT_XML}</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin</string>
  </dict>

  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>${LOG_XML}</string>
  <key>StandardErrorPath</key>
  <string>${LOG_XML}</string>
</dict>
</plist>
PLIST

launchctl unload "$DEST" 2>/dev/null || true
launchctl load -w "$DEST"

echo "LifeKanban server is now ON."
echo "It starts at login and stays running in the background."
echo "Open: http://127.0.0.1:8787/login.html"
echo "Log: $LOG"
osascript -e 'display notification "LifeKanban server is now running in the background." with title "LifeKanban"' 2>/dev/null || true
