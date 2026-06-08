# LifeKanban worker

`worker.sh` is the autonomous worker. On each run it claims the next card
assigned to **Claude**, runs the `claude` CLI to do it, saves the result, and
moves the card to **Done** — or to **Needs OK** if the task needs an
irreversible/external action (send, publish, delete, pay) that you must approve.

It drives the board over the authenticated HTTP API, so it works against the
remote/Dockerised board.

## Requirements (on the machine that runs the worker)

- The `claude` CLI (Claude Code), installed and logged in.
- `python3` and a checkout of this repo (for `kanban.py` / `auth.py`).
- Network access to the board's API.

This is NOT the Docker host's job and NOT a Cowork scheduled task — it's a plain
cron job on a machine that has the `claude` CLI.

## Setup

1. Mint a token for the board owner (on the Docker host, inside the container):

   ```bash
   docker exec -it lifekanban python3 kanban.py token-add Ch@o "claude-worker"
   ```

2. Create `worker/worker.env` from the example and paste the token:

   ```bash
   cp worker/worker.env.example worker/worker.env
   # edit worker/worker.env:
   #   KANBAN_API_URL=https://kanban.chattoweb.com
   #   KANBAN_API_TOKEN=lk_...
   ```

3. Test it by hand:

   ```bash
   bash worker/worker.sh
   ```

4. Schedule it. Run every 15 minutes via cron (`crontab -e`):

   ```
   */15 * * * * /bin/bash /path/to/repo/worker/worker.sh >> /tmp/kanban-worker.log 2>&1
   ```

   The script self-locks (atomic mkdir lock, no `flock` needed), so overlapping
   runs are safe on both macOS and Linux.

## Notes

- A token acts as the user it belongs to and reads/writes that account's board.
- Result files are saved on the worker host (`worker-results/<id>.md`). They are
  not yet uploaded into the board, so the in-board "↗ View result" link won't
  resolve to them — the card log records where the file is. (An upload endpoint
  can be added later if you want clickable results.)
- If a `claude` run fails, the card is left in Doing; the board's stale-card
  recovery requeues it on a later pass.
