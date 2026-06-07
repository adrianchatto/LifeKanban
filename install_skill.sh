#!/bin/bash
# Install all Kanban skills into the personal global skills dir so they trigger
# in any Claude session, not just when the Kanban folder is open.
SRCDIR="/Users/adrianchatto/Documents/Claude/Projects/Kanban/skills"
DEST="$HOME/.claude/skills"
OUT="/Users/adrianchatto/Documents/Claude/Projects/Kanban/.install-skill-result.txt"
{
  mkdir -p "$DEST"
  for d in "$SRCDIR"/*/ ; do
    name="$(basename "$d")"
    [ "$name" = "_template" ] && continue
    [ -f "$d/SKILL.md" ] || continue
    mkdir -p "$DEST/$name"
    cp "$d/SKILL.md" "$DEST/$name/SKILL.md" && echo "installed: $name"
  done
  echo "--- now in $DEST ---"
  ls -la "$DEST"
} > "$OUT" 2>&1
