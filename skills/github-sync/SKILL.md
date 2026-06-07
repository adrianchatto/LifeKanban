---
name: github-sync
description: >
  Sync Ch@o's Kanban board to GitHub. Use whenever he says "sync to GitHub",
  "save to GitHub", "push to GitHub", "update GitHub", "back this up", or after
  ANY change you make to the Kanban board (adding/editing/moving cards,
  projects, subtasks). Also run it at the end of a Kanban working session.
---

# Sync the Kanban board to GitHub

Ch@o wants the Kanban board **always** kept in sync with GitHub
(https://github.com/adrianchatto/LifeKanban). After any change you make to the
board in a session, push it.

The board folder on his Mac:

    /Users/adrianchatto/Documents/Claude/Projects/Kanban

A helper script, `git_sync.sh`, commits everything and pushes to `origin main`
using his stored git credentials. Run it:

- **Folder mounted in this session:** `bash "<KanbanFolder>/git_sync.sh"`
- **Any other session:** use the "Control your Mac" tool (osascript):
  `do shell script "bash /Users/adrianchatto/Documents/Claude/Projects/Kanban/git_sync.sh"`

Then read the result file `/Users/adrianchatto/Documents/Claude/Projects/Kanban/.sync-result.txt`
(it shows the commit and push exit code). Report success or, if `push exit` is
non-zero, tell Ch@o the push failed and why — the commit is still saved locally.
Do **not** handle tokens or passwords yourself; the push relies on his existing
credentials on the Mac.

## When to run it
- He explicitly asks to sync/save/push/back up.
- Immediately after you add, edit, move, or delete cards/projects/subtasks.
- At the end of any session where the board changed.

Note: changes Ch@o makes directly in the board UI are not auto-pushed by this
skill (it only runs when invoked in a chat). For fully automatic background
syncing, a login-item agent is needed — offer to set that up if he wants it.
