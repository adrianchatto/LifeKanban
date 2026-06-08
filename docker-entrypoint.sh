#!/bin/sh
# Seeds the data volume on first run, then starts the board server.
set -e

DATA="${KANBAN_DATA:-/data}"
mkdir -p "$DATA"

# Seed board.json into an empty volume (first run only).
if [ ! -f "$DATA/board.json" ]; then
  if [ -f /app/board.json ]; then
    cp /app/board.json "$DATA/board.json"
  else
    printf '%s\n' '{"version":1,"projects":["General"],"next_id":1,"cards":[]}' > "$DATA/board.json"
  fi
fi

# Seed the results directory on first run.
if [ ! -d "$DATA/results" ]; then
  mkdir -p "$DATA/results"
  if [ -d /app/results ]; then
    cp -a /app/results/. "$DATA/results/" 2>/dev/null || true
  fi
fi

exec python3 server.py --no-browser
