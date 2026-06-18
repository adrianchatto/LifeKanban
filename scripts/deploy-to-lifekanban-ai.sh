#!/usr/bin/env bash
set -euo pipefail
REPO="${KANBAN_REPO_DIR:-/Users/adrianchatto/GitHub/LifeKanban}"
REMOTE="${LIFEKANBAN_DEPLOY_REMOTE:-adrian@172.22.20.30}"
APP_DIR="${LIFEKANBAN_DEPLOY_APP_DIR:-/opt/lifekanban/app}"
SSH_KEY="${LIFEKANBAN_DEPLOY_SSH_KEY:-$HOME/.ssh/codex_proxmox_ed25519}"
KNOWN_HOSTS="${LIFEKANBAN_DEPLOY_KNOWN_HOSTS:-/private/tmp/lifekanban-ai.known_hosts}"
SSH="ssh -i $SSH_KEY -o UserKnownHostsFile=$KNOWN_HOSTS -o BatchMode=yes"
rsync -az --delete \
  --exclude '.git/' \
  --exclude 'board.json' \
  --exclude 'users.json' \
  --exclude '.secret.key' \
  --exclude 'app_settings.json' \
  --exclude 'results/' \
  --exclude 'attachments/' \
  --exclude 'worker/worker.env' \
  --exclude 'worker/worker.log' \
  -e "$SSH" "$REPO/" "$REMOTE:$APP_DIR/"
$SSH "$REMOTE" "python3 -m py_compile $APP_DIR/server.py $APP_DIR/kanban.py $APP_DIR/auth.py $APP_DIR/worker/byoai_worker.py && bash -n $APP_DIR/worker/worker.sh && sudo -n systemctl restart lifekanban-server.service && sudo -n systemctl restart lifekanban-worker.timer && systemctl is-active lifekanban-server.service >/dev/null && systemctl is-active lifekanban-worker.timer >/dev/null"
