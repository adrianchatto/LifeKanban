#!/bin/bash
# Commit all board/app changes and push to GitHub.
#
# This script self-locates its own directory, so it works whether it is run on
# the Mac (where Ch@o's git credentials are stored) or inside the Cowork worker
# sandbox (where the repo is bind-mounted at a different path). It writes a
# report to .sync-result.txt next to itself.
#
# NOTE: the push needs working git credentials. Those live on Ch@o's Mac, so a
# push only succeeds when this runs on the Mac. From the worker sandbox the
# commit will be made but the push will fail (no credentials) — that's expected;
# the next Mac-side run will push the backlog.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1
export GIT_TERMINAL_PROMPT=0   # fail fast instead of hanging if no credentials
OUT="$DIR/.sync-result.txt"
{
  echo "=== $(date) ==="
  echo "repo dir: $DIR"

  # Clear stale locks left by a crashed git process. A crash can leave both
  # .git/index.lock and .git/HEAD.lock (the latter blocks commits with "cannot
  # lock ref 'HEAD'"). Only remove a lock if it is empty (size 0) and older than
  # 2 minutes, so we never stomp a live commit.
  for LOCK in "$DIR/.git/index.lock" "$DIR/.git/HEAD.lock"; do
    if [ -f "$LOCK" ]; then
      if [ ! -s "$LOCK" ] && [ -z "$(find "$LOCK" -newermt '-2 minutes' 2>/dev/null)" ]; then
        rm -f "$LOCK" && echo "lock: removed stale ${LOCK##*/.git/}" || echo "lock: $LOCK present but could not remove (filesystem permissions — clear it on the Mac)"
      else
        echo "lock: ${LOCK##*/.git/} present and may be live — leaving it; re-run if no git process is active"
      fi
    fi
  done

  git add -A
  if git diff --cached --quiet; then
    echo "commit: nothing new to commit"
  else
    git commit -m "Sync board + app changes ($(date '+%Y-%m-%d %H:%M'))" >/dev/null && echo "commit: created" || echo "commit: FAILED"
  fi
  echo "--- push ---"
  git push -u origin main 2>&1
  echo "push exit: $?"
} > "$OUT" 2>&1
