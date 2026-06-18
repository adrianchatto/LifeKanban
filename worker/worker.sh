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
#   KANBAN_WORKER_AUTOSHIP safe code cards commit/push/deploy automatically (default: 1)
#   KANBAN_CODEX_APPROVAL_POLICY Codex CLI approval policy for worker runs (default: never)
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
AUTOSHIP="${KANBAN_WORKER_AUTOSHIP:-1}"
DEPLOY_CMD="${KANBAN_WORKER_DEPLOY_CMD:-}"
CODEX_APPROVAL_POLICY="${KANBAN_CODEX_APPROVAL_POLICY:-never}"

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

is_implementation_card(){
  printf '%s
%s
%s
' "$1" "$2" "$3" | grep -qiE "(add|apply|build|code|colour|color|css|deploy|feature|fix|focus|github|html|implement|javascript|page|pomodoro|python|repo|server|theme|ui|worker)"
}

inconclusive_result(){
  # A worker answer is not "done" if it admits it could not inspect the source
  # material, could not make the requested change, or only produced a plan.
  # Park those cards for human review instead of moving them to Done.
  printf '%s' "$1" | grep -qiE \
    "(could not|couldn't|cannot|can't|unable to|not able to|not available|not accessible|not present|not found|no .*available|no .*could be confirmed|did not include|does not include|was not included|appears .*not present|only goes up to|stale|blocked|needs clarification|clarifying question|would need|would require|I would|I can('|no)t|plan:|next steps?:)"
}

autoship_changes(){
  local id="$1" title="$2" branch msg status
  [ "$AUTOSHIP" = "1" ] || return 0
  git -C "$REPO_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 11
  status="$(git -C "$REPO_DIR" status --porcelain --untracked-files=normal \
    -- . ':!board.json' ':!users.json' ':!app_settings.json' ':!worker/worker.env' ':!worker/worker.log' ':!results' ':!worker-results' ':!attachments' 2>/dev/null || true)"
  [ -n "$status" ] || return 12
  git -C "$REPO_DIR" add -A -- . ':!board.json' ':!users.json' ':!app_settings.json' ':!worker/worker.env' ':!worker/worker.log' ':!results' ':!worker-results' ':!attachments'
  if git -C "$REPO_DIR" diff --cached --quiet; then
    return 12
  fi
  msg="Implement $id: $(printf '%s' "$title" | tr '\n' ' ' | cut -c1-72)"
  git -C "$REPO_DIR" commit -m "$msg"
  branch="$(git -C "$REPO_DIR" branch --show-current)"
  [ -n "$branch" ] || return 13
  git -C "$REPO_DIR" push origin "$branch"
  if [ -n "$DEPLOY_CMD" ]; then
    "$DEPLOY_CMD"
  fi
}

run_ai(){
  local id="$1" prompt="$2" out_file="$RESULTS_DIR/$id.md" final_file="$RESULTS_DIR/$id.final.txt" rc
  case "$AI_PROVIDER" in
    codex)
      set +e
      "$AI_BIN" exec -C "$REPO_DIR" --skip-git-repo-check --sandbox workspace-write \
        -c "approval_policy=\"$CODEX_APPROVAL_POLICY\"" \
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

notify_ai_done(){
  local id="$1" title="$2" project="$3"
  python3 - "$id" "$title" "$project" <<'PY' || true
import json
import sys
from urllib import parse as urlparse
from urllib import request as urlrequest

import auth

card_id, title, project = sys.argv[1:4]
message = "%s is done%s." % (title, (" (" + project + ")" if project else ""))
payload_title = "LifeKanban " + card_id

try:
    users = auth._load_raw().get("users", [])
except Exception:
    users = []

for user in users:
    try:
        creds = auth.get_pushover(user.get("username"))
        if not creds:
            continue
        data = urlparse.urlencode({
            "token": creds["token"],
            "user": creds["user"],
            "title": payload_title,
            "message": message,
        }).encode("utf-8")
        req = urlrequest.Request("https://api.pushover.net/1/messages.json",
                                 data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urlrequest.urlopen(req, timeout=15) as resp:
            resp.read()
    except Exception:
        pass
PY
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
  local card id title desc project prompt result rc reason result_file implementation autoship_rc card_pretty
  card="$(kb claim-next 2>&1)" || { log "claim-next failed: $card"; return 1; }
  # claim-next prints "null" (nothing to do) or {"claimed":null,...} (only
  # recovered stale cards) or a card object with an "id".
  [ "$card" = "null" ] && return 1
  id="$(printf '%s' "$card" | jget id)"
  [ -z "$id" ] && return 1
  title="$(printf '%s' "$card" | jget title)"
  desc="$(printf '%s' "$card" | jget description)"
  project="$(printf '%s' "$card" | jget project)"
  implementation=0
  if is_implementation_card "$title" "$project" "$desc"; then implementation=1; fi
  log "working $id: $title"
  card_pretty="$(printf '%s' "$card" | python3 -m json.tool 2>/dev/null || printf '%s' "$card")"

  prompt="$(cat <<EOF
You are the LifeKanban worker. Carry out the task on this card and produce the
finished deliverable as Markdown (not a plan — the actual result).

Title: $title
Project: $project
Details: $desc

Full card JSON, including subtasks/comments/history/attachments when present:

```json
$card_pretty
```

Rules:
- Definition of done: either make the requested change and verify it, or return
  NEEDS_OK with the reason it cannot be completed safely.
- If the task requires an IRREVERSIBLE or external action (send an email or
  message, publish, delete, pay, move money), DO NOT perform it. Produce the
  prepared draft/content, and make the VERY FIRST LINE of your reply exactly:
  NEEDS_OK: <one-line reason it needs approval>
- Otherwise, just output the completed result.
- For code/app/repository feature work, apply the required edits directly in the
  local repo, run relevant checks, and summarize the changed files, commit/push/
  deploy outcome, and verification. Do not return standalone code blocks unless
  you also applied them.
- Never claim success from a stale/local snapshot if the card is remote. Use the
  full card JSON above as the source of truth, and if a referenced card or file
  is missing, return NEEDS_OK instead of guessing.
- Do not ask the human for routine permission. If a safe local file edit is
  needed to complete the card, make it. If your worker mode cannot edit files or
  an external or irreversible action is needed, stop with NEEDS_OK as above.
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
  printf '%s' "$result" | kb set-result-text "$id" >/dev/null 2>&1 || true

  if inconclusive_result "$result"; then
    kb log "$id" "worker did not meet done criteria; parked for review instead of marking Done | result: $result_file" >/dev/null
    kb move "$id" needs_ok >/dev/null
    log "$id -> needs_ok (worker output was blocked/inconclusive)"
    return 0
  fi

  if [ "$AI_PROVIDER" = "custom" ] && [ "$implementation" = "1" ]; then
    if ! printf '%s' "$result" | head -n1 | grep -qiE '^NEEDS_OK:'; then
      kb log "$id" "custom BYOAI worker produced a write-up for an implementation card; parked for code-capable execution | result: $result_file" >/dev/null
      kb move "$id" needs_ok >/dev/null
      log "$id -> needs_ok (implementation requires Codex/code-capable worker)"
      return 0
    fi
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

  if [ "$implementation" = "1" ] && [ "$AI_PROVIDER" = "codex" ] && [ "$AUTOSHIP" = "1" ]; then
    autoship_rc=0
    autoship_changes "$id" "$title" || autoship_rc=$?
    if [ "$autoship_rc" -ne 0 ]; then
      kb log "$id" "worker could not autoship implementation changes (rc=$autoship_rc); parked for review | result: $result_file" >/dev/null
      kb move "$id" needs_ok >/dev/null
      log "$id -> needs_ok (autoship failed or no code diff, rc=$autoship_rc)"
      return 0
    fi
    kb log "$id" "worker autoshipped implementation: committed, pushed, and deployed" >/dev/null 2>&1 || true
  elif [ "$implementation" = "1" ] && [ "$AI_PROVIDER" = "codex" ] && [ "$AUTOSHIP" != "1" ]; then
    kb log "$id" "implementation card was not autoshipped because KANBAN_WORKER_AUTOSHIP is disabled; parked for review | result: $result_file" >/dev/null
    kb move "$id" needs_ok >/dev/null
    log "$id -> needs_ok (implementation not autoshipped)"
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
    notify_ai_done "$id" "$title" "$project"
    log "$id -> done"
  fi
  return 0
}

processed=0
while [ "$processed" -lt "$MAX_CARDS" ]; do
  if process_one; then processed=$((processed+1)); else break; fi
done
log "run complete; processed $processed card(s)"
