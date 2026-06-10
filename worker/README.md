# LifeKanban worker

`worker.sh` is the autonomous worker. On each run it claims the next card
assigned to **AI**, runs the configured AI CLI to do it, saves the result, and
moves the card to **Done** — or to **Needs OK** if the task needs an
irreversible/external action (send, publish, delete, pay) that you must approve.

It can run in local mode against this checkout's `board.json`, or in remote mode
against the authenticated HTTP API for a Dockerised board.

## Requirements (on the machine that runs the worker)

- Codex CLI installed and logged in. On macOS the bundled app CLI is usually
  `/Applications/Codex.app/Contents/Resources/codex`.
- `python3` and a checkout of this repo (for `kanban.py` / `auth.py`).
- Network access to the board's API only if you use remote/Docker mode.

This is not a Cowork scheduled task. It is a local scheduled worker on a machine
that has the AI CLI installed.

## Setup

### Local board on this Mac

Double-click **`Enable AI Worker.command`** in the repo. It installs a LaunchAgent
that runs once immediately and then every 15 minutes.

To turn it off, double-click **`Disable AI Worker.command`**.

### Remote / Docker board

1. Mint a token for the board owner (on the Docker host, inside the container):

   ```bash
   docker exec -it lifekanban python3 kanban.py token-add Ch@o "ai-worker"
   ```

2. Create `worker/worker.env` from the example and paste the token:

   ```bash
   cp worker/worker.env.example worker/worker.env
   # edit worker/worker.env:
   #   KANBAN_API_URL=https://kanban.chattoweb.com
   #   KANBAN_API_TOKEN=lk_...
   ```

3. Double-click **`Enable AI Worker.command`** on the machine that will run the
   worker.

## Test it by hand

```bash
bash worker/worker.sh
```

The script self-locks (atomic mkdir lock, no `flock` needed), so overlapping
runs are safe on both macOS and Linux.

## Provider configuration

By default the worker auto-detects Codex first, then falls back to Claude CLI.
Override with `worker/worker.env`:

```bash
KANBAN_AI_PROVIDER=codex
KANBAN_AI_BIN=/Applications/Codex.app/Contents/Resources/codex
KANBAN_AI_ARGS=
```

## Notes

- A token acts as the user it belongs to and reads/writes that account's board.
- Result files are saved on the worker host (`worker-results/<id>.md`). They are
  not yet uploaded into the board, so the in-board "↗ View result" link won't
  resolve to them — the card log records where the file is. (An upload endpoint
  can be added later if you want clickable results.)
- If an AI CLI run fails, the card is left in Doing; the board's stale-card
  recovery requeues it on a later pass.
