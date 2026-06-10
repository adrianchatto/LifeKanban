#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.chatto.kanban.worker"
DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG="$ROOT/worker/worker.log"

xml_escape() {
  python3 -c 'import html,sys; print(html.escape(sys.argv[1], quote=True))' "$1"
}

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/worker"
touch "$LOG"
chmod +x "$ROOT/worker/worker.sh"

ROOT_XML="$(xml_escape "$ROOT")"
WORKER_XML="$(xml_escape "$ROOT/worker/worker.sh")"
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
    <string>/bin/bash</string>
    <string>${WORKER_XML}</string>
  </array>

  <key>WorkingDirectory</key>
  <string>${ROOT_XML}</string>

  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>900</integer>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/Applications/Codex.app/Contents/Resources:/Users/adrianchatto/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>

  <key>StandardOutPath</key>
  <string>${LOG_XML}</string>
  <key>StandardErrorPath</key>
  <string>${LOG_XML}</string>
</dict>
</plist>
PLIST

launchctl unload "$DEST" 2>/dev/null || true
launchctl load -w "$DEST"

echo "AI worker is now ON."
echo "It runs once now, then every 15 minutes."
echo "Log: $LOG"
osascript -e 'display notification "AI worker is now running every 15 minutes." with title "LifeKanban"' 2>/dev/null || true
