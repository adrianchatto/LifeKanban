#!/usr/bin/env bash
# LifeKanban autonomous worker.
#
# Claims the next card assigned to Claude, runs the `claude` CLI to do it,
# saves the result, and moves the card to Done — or to Needs OK if the task
# needs an irreversible/external action that a human must approve.
#
# Run it from cron on a machine that has:
#   * the `claude` CLI (Claude Code) installed and logged in,
#   * python3,
#   * a checkout of this repo (for kanban.py / auth.py),
#   * network access to the board's API.
#
# It talks to the board over the authenticated HTTP API using an API token,
# so it works against a remote/Dockerised board.
#
# Configure with environment variables (or a worker.env file next to this
# script — see worker.env.example):
#   KANBAN_API_URL        e.g. https://kanban.chattoweb.com   (required)
#   KANBAN_API_TOKEN      lk_...                               (required)
#   KANBAN_REPO_DIR       path to the repo (default: parent of this script)
#   CLAUDE_BIN            claude binary (default: claude)
#   KANBAN_CLAUDE_ARGS    extra flags for claude, e.g. "--model claude-sonnet-4-6"
#   KANBAN_WORKER_RESULTS where to store result files (default: <repo>/worker-results)
#   KANBAN_WORKER_MAX     max cards to process per run (default: 3)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

# Load worker.env if present (KEY=VALUE lines).
if [ -f "$HERE/worker.env" ]; then
  set -a; . "$HERE/worker.env"; set +a
fi

REPO_DIR="${KANBAN_REPO_DIR:-$(cd "$HERE/.." && pwd)}"
KANBAN_CLI="${KANBAN_CLI:-$REPO_DIR/kanban.py}"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"
CLAUDE_ARGS="${KANBAN_CLAUDE_ARGS:-}"
RESULTS_DIR="${KANBAN_WORKER_RESULTS:-$REPO_DIR/worker-results}"
MAX_CARDS="${KANBAN_WORKER_MAX:-3}"
LOCK_DIR="${KANBAN_WORKER_LOCK:-${TMPDIR:-/tmp}/kanban-worker.lock.d}"

# Two modes:
#   * REMOTE — KANBAN_API_URL set: drive the board over the HTTP API (needs a
#     token). Use this when the board runs on another host / behind a login.
#   * LOCAL  — KANBAN_API_URL unset: edit the local board.json directly via
#     kanban.py. Use this when the worker runs on the same machine as the board.
if [ -n "${KANBAN_API_URL:-}" ]; then
  : "${KANBAN_API_TOKEN:?KANBAN_API_URL is set, so KANBAN_API_TOKEN is required}"
  export KANBAN_API_URL KANBAN_API_TOKEN
else
  unset KANBAN_API_URL KANBAN_API_TOKEN 2>/dev/null || true
fi
mkdir -p "$RESULTS_DIR"

log(){ echo "$(date '+%Y-%m-%dT%H:%M:%S%z') [worker] $*"; }
kb(){ python3 "$KANBAN_CLI" "$@"; }
jget(){ python3 -c 'import sys,json
try: d=json.load(sys.stdin)
except Exception: d={}
print(d.get(sys.argv[1],"") if isinstance(d,dict) else "")' "$1"; }

# Single-instance guard so overlapping cron runs don't double-process a card.
# Uses an atomic mkdir lock (portable across macOS and Linux — no `flock`
# needed). A lock left by a crashed run older than an hour is treated as stale.
_lock_age_secs(){ local m; m="$(stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null)" || return 1; echo $(( $(date +%s) - m )); }
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  age="$(_lock_age_secs "$LOCK_DIR" 2>/dev/null || echo 0)"
  if [ "${age:-0}" -gt 3600 ]; then
    log "clearing stale lock ($LOCK_DIR, ${age}s old)"
    rmdir "$LOCK_DIR" 2>/dev/null || true
    mkdir "$LOCK_DIR" 2>/dev/null || { log "could not acquire lock; exiting"; exit 0; }
  else
    log "another worker run is active ($LOCK_DIR); exiting"
    exit 0
  fi
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT INT TERM

if [ -n "${KANBAN_API_URL:-}" ]; then log "mode: remote API ($KANBAN_API_URL)"; else log "mode: local board.json"; fi

process_one(){
  local card id title desc project prompt result rc reason
  card="$(kb claim-next 2>&1)" || { log "claim-next failed: $card"; return 1; }
  # claim-next prints "null" (nothing to do) or {"claimed":null,...} (only
  # recovered stale cards) or a card object with an "id".
  [ "$card" = "null" ] && return 1
  id="$(printf '%s' "$card" | jget id)"
  [ -z "$id" ] && return 1
  title="$(printf '%s' "$card" | jget title)"
  desc="$(printf '%s' "$card" | jget description)"
  project="$(printf '%s' "$card" | jget project)"
  log "working $id: $title"

  prompt="$(cat <<EOF
You are the LifeKanban worker. Carry out the task on this card and produce the
finished deliverable as Markdown (not a plan — the actual result).

Title: $title
Project: $project
Details: $desc

Rules:
- If the task requires an IRREVERSIBLE or external action (send an email or
  message, publish, delete, pay, move money), DO NOT perform it. Produce the
  prepared draft/content, and make the VERY FIRST LINE of your reply exactly:
  NEEDS_OK: <one-line reason it needs approval>
- Otherwise, just output the completed result.
EOF
)"

  set +e
  result="$("$CLAUDE_BIN" -p "$prompt" --output-format text $CLAUDE_ARGS 2>"$RESULTS_DIR/$id.err")"
  rc=$?
  set -e
  if [ $rc -ne 0 ]; then
    log "claude failed for $id (rc=$rc) — leaving it; the board will requeue it"
    kb log "$id" "worker: claude run failed (rc=$rc); will retry" >/dev/null 2>&1 || true
    return 0
  fi

  printf '%s\n' "$result" > "$RESULTS_DIR/$id.md"

  if printf '%s' "$result" | head -n1 | grep -qiE '^NEEDS_OK:'; then
    reason="$(printf '%s' "$result" | head -n1 | sed -E 's/^[Nn][Ee][Ee][Dd][Ss]_[Oo][Kk]:[[:space:]]*//')"
    kb log "$id" "worker prepared a draft — needs approval: $reason | result: $RESULTS_DIR/$id.md" >/dev/null
    kb move "$id" needs_ok >/dev/null
    log "$id -> needs_ok ($reason)"
  else
    kb log "$id" "worker completed; result file on worker host: $RESULTS_DIR/$id.md" >/dev/null
    kb move "$id" done >/dev/null
    log "$id -> done"
  fi
  return 0
}

processed=0
while [ "$processed" -lt "$MAX_CARDS" ]; do
  if process_one; then processed=$((processed+1)); else break; fi
done
log "run complete; processed $processed card(s)"
