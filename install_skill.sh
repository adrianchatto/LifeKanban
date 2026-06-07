#!/bin/bash
# Install the kanban skill into the personal global skills dir so it triggers
# in any Claude session, not just when the Kanban folder is open.
SRC="/Users/adrianchatto/Documents/Claude/Projects/Kanban/skills/kanban/SKILL.md"
DEST="$HOME/.claude/skills/kanban"
OUT="/Users/adrianchatto/Documents/Claude/Projects/Kanban/.install-skill-result.txt"
{
  mkdir -p "$DEST"
  cp "$SRC" "$DEST/SKILL.md" && echo "installed: $DEST/SKILL.md"
  echo "--- contents ---"
  ls -la "$DEST"
  echo "--- existing ~/.claude skill locations (for reference) ---"
  ls -la "$HOME/.claude/skills" 2>/dev/null || echo "(no ~/.claude/skills)"
} > "$OUT" 2>&1
