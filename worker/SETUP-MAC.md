# Running the Kanban worker on your Mac (reliable setup)

## Why this changed

The worker used to run as a **Cowork scheduled task**. That runs in an isolated
sandbox that has **no network route to your board** — it can't reach
`127.0.0.1:8787` (that loopback is your Mac, not the sandbox) and it can't even
resolve `kanban.chattoweb.com`. On top of that, your live board runs in Docker
and keeps its data in a Docker volume, so the `board.json` in this repo is only a
stale copy. The sandbox worker therefore read old data and kept reporting
"nothing to do" while live cards sat in **To Do**.

The fix: run the purpose-built `worker/worker.sh` **on your Mac**, talking to the
**live board over its authenticated API**. One worker, one source of truth, no
file-sync lag. The Cowork scheduled task is retired.

## Prerequisites

- The board is running and reachable from your Mac (open it in a browser to check).
- Claude Code CLI is installed and logged in. Confirm with:

  ```bash
  which claude && claude --version
  ```

- `python3` is available (it is, on macOS).

## One-time setup

### 1. Mint an API token for your account

The token identifies the board owner and lets the worker read/write your board.

```bash
# If the board runs in Docker (it does, per docker-compose.yml):
docker exec -it lifekanban python3 kanban.py token-add "Ch@o" "claude-worker"

# If you run it with plain server.py instead, drop the docker prefix:
# python3 /Users/adrianchatto/Documents/Claude/Projects/Kanban/kanban.py token-add "Ch@o" "claude-worker"
```

It prints a token like `lk_xxxx…` **once** — copy it now.

### 2. Paste the token into worker.env

Edit `worker/worker.env` and replace the placeholder:

```
KANBAN_API_URL=https://kanban.chattoweb.com
KANBAN_API_TOKEN=lk_xxxx…           # <- your token
```

`worker.env` is gitignored, so the token stays on your Mac and never reaches
GitHub. If your Mac can't reach the public domain, switch the URL to
`http://127.0.0.1:8787` (token auth works over plain http).

### 3. Test it by hand

```bash
bash /Users/adrianchatto/Documents/Claude/Projects/Kanban/worker/worker.sh
```

You should see `mode: remote API (https://kanban.chattoweb.com)` and, if a Claude
card is waiting, `working <id>: <title>` … `<id> -> done`. Watch the board — the
card should move To Do → Doing → Done (or Needs OK). If it prints
`mode: local board.json`, the token/URL didn't load — recheck step 2.

### 4. Schedule it with launchd (every 15 minutes)

```bash
cp /Users/adrianchatto/Documents/Claude/Projects/Kanban/com.chatto.kanban.worker.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.chatto.kanban.worker.plist 2>/dev/null
launchctl load  ~/Library/LaunchAgents/com.chatto.kanban.worker.plist
```

This mirrors your existing `com.chatto.kanban.server` job. It runs once on load
and every 15 minutes after. Logs go to `worker/worker.log`. The job exits between
runs (it's a periodic task, not a daemon) and self-locks so overlapping fires are
safe.

To check it's registered:

```bash
launchctl list | grep com.chatto.kanban.worker
tail -f /Users/adrianchatto/Documents/Claude/Projects/Kanban/worker/worker.log
```

### 5. Retire the old Cowork scheduled task

It's already been disabled. If you ever want it back (you shouldn't — it can't
reach the live board), re-enable `kanban-worker` from your scheduled tasks.

## Notes / known limits

- The worker only ever **claims cards assigned to Claude**; your own (Ch@o) cards
  are never touched.
- Risky/irreversible steps (send, publish, delete, pay) are **not** performed.
  The worker prepares the draft, logs `NEEDS_OK: <reason>`, and moves the card to
  **Needs OK** for you to approve. Approve from the card (or
  `python3 kanban.py approve <id>`) and the next run carries out the action.
- Result files are written to `worker-results/<id>.md` on your Mac. The card log
  records the path; the in-board "↗ View result" link doesn't upload them yet.
- The Mac only processes cards while it's **awake and online**. If it's been
  asleep, cards are picked up on the next run after it wakes.
