#!/usr/bin/env bash
# LifeKanban autonomous worker.
#
# Claims the next card assigned to AI, runs the configured CLI to do it,
# saves the result, and moves the card to Done — or to Needs OK if the task
# needs an irreversible/external action that a human must approve.
#
# Run it from cron/launchd on a machine that has:
#   * Codex CLI or another supported AI CLI installed and logged in,
#   * python3,
#   * a checkout of this repo (for kanban.py / auth.py),
#   * network access to the board's API when running in remote mode.
#
# It talks to the board over the authenticated HTTP API using an API token,
# so it works against a remote/Dockerised board.
#
# Configure with environment variables (or a worker.env file next to this
# script — see worker.env.example):
#   KANBAN_API_URL        e.g. https://kanban.chattoweb.com   (remote mode)
#   KANBAN_API_TOKEN      lk_...                               (remote mode)
#   KANBAN_REPO_DIR       path to the repo (default: parent of this script)
#   KANBAN_AI_PROVIDER    auto|codex|claude|custom (default: auto)
#   KANBAN_AI_BIN         AI binary path (auto-detected when unset)
#   KANBAN_AI_ARGS        extra flags for the AI CLI
#   CLAUDE_BIN            legacy alias for KANBAN_AI_BIN when provider=claude
#   KANBAN_CLAUDE_ARGS    legacy alias for KANBAN_AI_ARGS when provider=claude
#   KANBAN_WORKER_RESULTS where to store result files (default: <repo>/worker-results)
#   KANBAN_WORKER_MAX     max cards to process per run (default: 3)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

# Load worker.env if present (KEY=VALUE lines).
if [ -f "$HERE/worker.env" ]; then
  set -a; . "$HERE/worker.env"; set +a
fi

REPO_DIR="${KANBAN_REPO_DIR:-$(cd "$HERE/.." && pwd)}"
# Run from the repo so the AI CLI sees the app files in the right place,
# whatever directory the job was launched from.
cd "$REPO_DIR"
KANBAN_CLI="${KANBAN_CLI:-$REPO_DIR/kanban.py}"
AI_PROVIDER="${KANBAN_AI_PROVIDER:-auto}"
AI_BIN="${KANBAN_AI_BIN:-}"
AI_ARGS="${KANBAN_AI_ARGS:-}"
RESULTS_DIR="${KANBAN_WORKER_RESULTS:-}"
SET_RESULT_LINK="${KANBAN_WORKER_SET_RESULT:-}"
MAX_CARDS="${KANBAN_WORKER_MAX:-3}"
LOCK_DIR="${KANBAN_WORKER_LOCK:-${TMPDIR:-/tmp}/kanban-worker.lock.d}"

detect_ai(){
  case "$AI_PROVIDER" in
    codex)
      AI_BIN="${AI_BIN:-$(command -v codex 2>/dev/null || true)}"
      [ -n "$AI_BIN" ] || AI_BIN="/Applications/Codex.app/Contents/Resources/codex"
      ;;
    claude)
      AI_BIN="${AI_BIN:-${CLAUDE_BIN:-$(command -v claude 2>/dev/null || true)}}"
      AI_ARGS="${AI_ARGS:-${KANBAN_CLAUDE_ARGS:-}}"
      ;;
    custom)
      : "${AI_BIN:?KANBAN_AI_PROVIDER=custom requires KANBAN_AI_BIN}"
      ;;
    auto)
      if [ -z "$AI_BIN" ]; then
        AI_BIN="$(command -v codex 2>/dev/null || true)"
        [ -n "$AI_BIN" ] || [ ! -x /Applications/Codex.app/Contents/Resources/codex ] || AI_BIN="/Applications/Codex.app/Contents/Resources/codex"
      fi
      if [ -n "$AI_BIN" ]; then
        AI_PROVIDER="codex"
      else
        AI_BIN="${CLAUDE_BIN:-$(command -v claude 2>/dev/null || true)}"
        AI_ARGS="${AI_ARGS:-${KANBAN_CLAUDE_ARGS:-}}"
        AI_PROVIDER="claude"
      fi
      ;;
    *)
      echo "Unknown KANBAN_AI_PROVIDER: $AI_PROVIDER" >&2
      exit 2
      ;;
  esac
  if [ -z "$AI_BIN" ] || [ ! -x "$AI_BIN" ]; then
    echo "No usable AI CLI found. Set KANBAN_AI_BIN in worker/worker.env." >&2
    exit 2
  fi
}
detect_ai

# Two modes:
#   * REMOTE — KANBAN_API_URL set: drive the board over the HTTP API (needs a
#     token). Use this when the board runs on another host / behind a login.
#   * LOCAL  — KANBAN_API_URL unset: edit the local board.json directly via
#     kanban.py. Use this when the worker runs on the same machine as the board.
if [ -n "${KANBAN_API_URL:-}" ]; then
  : "${KANBAN_API_TOKEN:?KANBAN_API_URL is set, so KANBAN_API_TOKEN is required}"
  export KANBAN_API_URL KANBAN_API_TOKEN
  RESULTS_DIR="${RESULTS_DIR:-$REPO_DIR/worker-results}"
  SET_RESULT_LINK="${SET_RESULT_LINK:-0}"
else
  unset KANBAN_API_URL KANBAN_API_TOKEN 2>/dev/null || true
  DATA_DIR="${KANBAN_DATA:-$REPO_DIR}"
  RESULTS_DIR="${RESULTS_DIR:-$DATA_DIR/results}"
  SET_RESULT_LINK="${SET_RESULT_LINK:-1}"
fi
mkdir -p "$RESULTS_DIR"

log(){ echo "$(date '+%Y-%m-%dT%H:%M:%S%z') [worker] $*"; }
kb(){ python3 "$KANBAN_CLI" "$@"; }
jget(){ python3 -c 'import sys,json
try: d=json.load(sys.stdin)
except Exception: d={}
print(d.get(sys.argv[1],"") if isinstance(d,dict) else "")' "$1"; }

run_ai(){
  local id="$1" prompt="$2" out_file="$RESULTS_DIR/$id.md" final_file="$RESULTS_DIR/$id.final.txt" rc
  case "$AI_PROVIDER" in
    codex)
      set +e
      "$AI_BIN" exec -C "$REPO_DIR" --skip-git-repo-check --sandbox workspace-write \
        -o "$final_file" $AI_ARGS "$prompt" >"$RESULTS_DIR/$id.out" 2>"$RESULTS_DIR/$id.err"
      rc=$?
      set -e
      [ -s "$final_file" ] && cp "$final_file" "$out_file"
      return "$rc"
      ;;
    claude)
      set +e
      "$AI_BIN" -p "$prompt" --output-format text $AI_ARGS >"$out_file" 2>"$RESULTS_DIR/$id.err"
      rc=$?
      set -e
      return "$rc"
      ;;
    custom)
      set +e
      printf '%s\n' "$prompt" | "$AI_BIN" $AI_ARGS >"$out_file" 2>"$RESULTS_DIR/$id.err"
      rc=$?
      set -e
      return "$rc"
      ;;
  esac
}

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
log "AI provider: $AI_PROVIDER ($AI_BIN)"

process_one(){
  local card id title desc project prompt result rc reason result_file
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
- Do not ask the human for routine permission. If a safe local file edit is
  needed to complete the card, make it. If an external or irreversible action is
  needed, stop with NEEDS_OK as above.
EOF
)"

  if run_ai "$id" "$prompt"; then rc=0; else rc=$?; fi
  if [ $rc -ne 0 ]; then
    log "AI worker failed for $id (rc=$rc) — leaving it; the board will requeue it"
    kb log "$id" "worker: AI run failed (rc=$rc); will retry" >/dev/null 2>&1 || true
    return 0
  fi

  result_file="$RESULTS_DIR/$id.md"
  if [ ! -s "$result_file" ]; then
    log "AI worker produced no result for $id — parking for review"
    kb log "$id" "worker produced no result; parked for review | stderr: $RESULTS_DIR/$id.err" >/dev/null 2>&1 || true
    kb move "$id" needs_ok >/dev/null
    return 0
  fi
  result="$(cat "$result_file")"
  if [ "$SET_RESULT_LINK" = "1" ]; then
    kb set-result "$id" "$id.md" >/dev/null 2>&1 || true
  fi

  # Safety net: if the AI couldn't actually act (still asking for file/edit
  # permission or reporting it was blocked), don't pretend it's done — park it
  # in Needs OK for review instead of falsely marking it complete.
  if printf '%s' "$result" | grep -qiE "write permission|permission hasn't been granted|approve the (edit|file|permission|changes)|permission to (edit|write|make)"; then
    kb log "$id" "worker could not complete — it was blocked (permissions). Parked for review | result: $result_file" >/dev/null
    kb move "$id" needs_ok >/dev/null
    log "$id -> needs_ok (worker was blocked, not actually done)"
    return 0
  fi

  if printf '%s' "$result" | head -n1 | grep -qiE '^NEEDS_OK:'; then
    reason="$(printf '%s' "$result" | head -n1 | sed -E 's/^[Nn][Ee][Ee][Dd][Ss]_[Oo][Kk]:[[:space:]]*//')"
    kb log "$id" "worker prepared a draft — needs approval: $reason | result: $result_file" >/dev/null
    kb move "$id" needs_ok >/dev/null
    log "$id -> needs_ok ($reason)"
  else
    kb log "$id" "worker completed; result file on worker host: $result_file" >/dev/null
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
