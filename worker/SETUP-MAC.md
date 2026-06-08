# Running the Kanban worker on your Mac (local, no-login setup)

## How this works now

The board runs **locally with no login**: `server.py` on `127.0.0.1:8787`, with
all data in this repo's `board.json`. There are no users, passwords, or API
tokens any more.

The worker runs the purpose-built `worker/worker.sh` **on your Mac** in **local
file mode** — it edits `board.json` directly via `kanban.py`. No API URL, no
token, and it doesn't even need the web server running. The board UI and the
worker both read/write the same `board.json` through an atomic file lock, so they
can't corrupt each other.

> The old **Cowork scheduled task** is retired. It ran in an isolated sandbox
> that can't reach your Mac's files or run the `claude` CLI, so it could never
> actually do the work. Don't re-enable it.

## Prerequisites

- Claude Code CLI installed and logged in (this is what does each card's work):

  ```bash
  which claude && claude --version
  ```

  If `which claude` prints a path that isn't on launchd's minimal PATH, set
  `CLAUDE_BIN` to that absolute path in `worker/worker.env`.
- `python3` is available (it is, on macOS).

## Setup

### 1. Test it by hand

```bash
bash /Users/adrianchatto/Documents/Claude/Projects/Kanban/worker/worker.sh
```

You should see `mode: local board.json`. If a card is waiting in **To Do**
assigned to **Claude**, you'll then see `working <id>: <title>` … `<id> -> done`,
and the card moves To Do → Doing → Done (or Needs OK) on the board.

### 2. Schedule it with launchd (every 15 minutes)

```bash
cp /Users/adrianchatto/Documents/Claude/Projects/Kanban/com.chatto.kanban.worker.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.chatto.kanban.worker.plist 2>/dev/null
launchctl load  ~/Library/LaunchAgents/com.chatto.kanban.worker.plist
```

This mirrors your existing `com.chatto.kanban.server` job. It runs once on load
and every 15 minutes after. Logs go to `worker/worker.log`. The job exits between
runs (it's a periodic task, not a daemon) and self-locks so overlapping fires are
safe.

Check it's registered:

```bash
launchctl list | grep com.chatto.kanban.worker
tail -f /Users/adrianchatto/Documents/Claude/Projects/Kanban/worker/worker.log
```

## Notes / known limits

- The worker only ever **claims cards assigned to Claude**; your own (Ch@o) cards
  are never touched.
- Risky/irreversible steps (send, publish, delete, pay) are **not** performed.
  The worker prepares the draft, logs `NEEDS_OK: <reason>`, and moves the card to
  **Needs OK** for you to approve. Approve from the card (or
  `python3 kanban.py approve <id>`) and the next run carries out the action.
- Result files are written to `worker-results/<id>.md` on your Mac. The card log
  records the path.
- The Mac only processes cards while it's **awake and online**. If it's been
  asleep, cards are picked up on the next run after it wakes.
