#!/bin/bash
cd /Users/adrianchatto/Documents/Claude/Projects/Kanban || exit 1
# Stop any stale/half-dead server
pkill -f "Kanban/server.py" 2>/dev/null
pkill -f "server.py" 2>/dev/null
sleep 0.6
# Start detached so it survives this script and the Terminal closing
nohup /usr/bin/python3 server.py --no-browser >> .server.log 2>&1 &
disown 2>/dev/null
sleep 1.2
# Record what happened for verification
{
  echo "started at: $(date)"
  echo "listening check:"
  lsof -nP -iTCP:8787 -sTCP:LISTEN 2>/dev/null || echo "  (port check unavailable)"
} > .start-result.txt 2>&1
# Open the board in the default browser
open "http://127.0.0.1:8787/"
