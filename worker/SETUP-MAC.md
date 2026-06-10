# Running the Kanban AI worker on your Mac

## How this works now

The worker runs `worker/worker.sh` **on your Mac**. In local mode it edits this
checkout's `board.json` directly via `kanban.py`. In remote/Docker mode it talks
to the board API with a token from `worker/worker.env`.

> The old **Cowork scheduled task** is retired. It ran in an isolated sandbox
> that can't reach your Mac's files or run the AI CLI, so it could never
> actually do the work. Don't re-enable it.

## Prerequisites

- Codex CLI installed and logged in (this is what does each card's work):

  ```bash
  /Applications/Codex.app/Contents/Resources/codex --version
  ```

  If Codex lives somewhere else, set `KANBAN_AI_BIN` to that absolute path in
  `worker/worker.env`.
- `python3` is available (it is, on macOS).

## Setup

### 1. Enable the schedule

Double-click:

```bash
Enable AI Worker.command
```

That installs a LaunchAgent, runs once immediately, and then runs every 15
minutes.

### 2. Test it by hand

```bash
bash /Users/adrianchatto/Documents/Claude/Projects/Kanban/worker/worker.sh
```

You should see `mode: local board.json`. If a card is waiting in **To Do**
assigned to **AI**, you'll then see `working <id>: <title>` … `<id> -> done`,
and the card moves To Do → Doing → Done (or Needs OK) on the board.

Logs go to `worker/worker.log`. The job exits between runs (it's a periodic task,
not a daemon) and self-locks so overlapping fires are safe.

Check it's registered:

```bash
launchctl list | grep com.chatto.kanban.worker
tail -f /Users/adrianchatto/Documents/Claude/Projects/Kanban/worker/worker.log
```

## Notes / known limits

- The worker only ever **claims cards assigned to AI**; your own (Ch@o) cards
  are never touched.
- Risky/irreversible steps (send, publish, delete, pay) are **not** performed.
  The worker prepares the draft, logs `NEEDS_OK: <reason>`, and moves the card to
  **Needs OK** for you to approve. Approve from the card (or
  `python3 kanban.py approve <id>`) and the next run carries out the action.
- Result files are written to `worker-results/<id>.md` on your Mac. The card log
  records the path.
- The Mac only processes cards while it's **awake and online**. If it's been
  asleep, cards are picked up on the next run after it wakes.
