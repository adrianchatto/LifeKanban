#!/bin/bash
# Commit all board/app changes and push to GitHub using stored credentials.
cd /Users/adrianchatto/Documents/Claude/Projects/Kanban || exit 1
export GIT_TERMINAL_PROMPT=0   # fail fast instead of hanging if no credentials
OUT="/Users/adrianchatto/Documents/Claude/Projects/Kanban/.sync-result.txt"
{
  echo "=== $(date) ==="
  git add -A
  if git diff --cached --quiet; then
    echo "commit: nothing new to commit"
  else
    git commit -m "Sync board + app changes ($(date '+%Y-%m-%d %H:%M'))" >/dev/null
    echo "commit: created"
  fi
  echo "--- push ---"
  git push -u origin main 2>&1
  echo "push exit: $?"
} > "$OUT" 2>&1
