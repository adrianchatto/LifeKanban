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

# Bootstrap the first admin account. The board requires a login, so an empty
# user store would lock everyone out. On first run (no users.json yet):
#   * if KANBAN_ADMIN_USER and KANBAN_ADMIN_PASSWORD are set, create that admin;
#   * otherwise print the command to create one manually.
# users.json and the encryption key live in the data volume, so this only
# happens once and survives rebuilds.
if [ ! -f "$DATA/users.json" ]; then
  if [ -n "$KANBAN_ADMIN_USER" ] && [ -n "$KANBAN_ADMIN_PASSWORD" ]; then
    echo "First run: creating admin user '$KANBAN_ADMIN_USER'…"
    if python3 kanban.py user-add "$KANBAN_ADMIN_USER" --admin \
         --password "$KANBAN_ADMIN_PASSWORD" --must-change >/dev/null 2>&1; then
      echo "Admin '$KANBAN_ADMIN_USER' created — sign in and change the password."
    else
      echo "WARNING: admin bootstrap failed (password must be >= 8 chars?)."
    fi
  else
    echo "WARNING: no users exist yet and the board requires a login."
    echo "Set KANBAN_ADMIN_USER and KANBAN_ADMIN_PASSWORD (compose env) for"
    echo "automatic setup, or create one now with:"
    echo "  docker exec -it lifekanban python3 kanban.py user-add <name> --admin"
  fi
fi

exec python3 server.py --no-browser
