# LifeKanban Agent Handover

Last updated: 15 June 2026
Branch: `rebuild-signup-byoai`
Primary repo: `https://github.com/adrianchatto/LifeKanban`

This document is for Codex, Claude Code, or any future maintainer taking over LifeKanban. It explains what the app is, how it is deployed, where live data lives, what features exist, and how to verify or repair the system without accidentally overwriting user data.

## Current Live Shape

LifeKanban now exists in two important places:

| Place | Purpose | Notes |
| --- | --- | --- |
| Mac mini repo | Development/source copy | `/Users/adrianchatto/GitHub/LifeKanban` on branch `rebuild-signup-byoai`. This may also contain local runtime dirt such as `board.json`, `worker/worker.log`, and generated `results/*`. Do not commit those unless explicitly requested. |
| Proxmox VM | Current server copy | VM `119`, name `lifekanban-ai`, IP `172.22.20.30`, app in `/opt/lifekanban/app`, live data in `/opt/lifekanban/data`. |
| Docker2/Cloudflare host | Cloudflare tunnel host | IP `172.22.20.2`, hostname `cloudflare`, SSH user `adrianchatto` after Mac mini key injection. Active tunnel config lives under `/etc/cloudflared/`. |
| Public URL | Cloudflare Access route | `https://lifekanban.chattoweb.com` points to `http://172.22.20.30:8787`. The older `kanban.chattoweb.com` route was intentionally removed from active tunnel configs. |

The raw VM endpoint is:

```text
http://172.22.20.30:8787
```

The public endpoint is:

```text
https://lifekanban.chattoweb.com
```

Cloudflare Access may redirect before the LifeKanban login page. After Cloudflare Access, LifeKanban has its own app login.

Do not commit passwords, `.secret.key`, `users.json`, `app_settings.json`, live board data, worker logs, or generated results. Those are runtime state.

## High-Level Architecture

```mermaid
flowchart LR
    Browser[Browser / Cloudflare Access user] --> CF[Cloudflare tunnel]
    CF --> Docker2[Docker2 / cloudflare host\n172.22.20.2]
    Docker2 --> VM[LifeKanban VM 119\n172.22.20.30:8787]
    VM --> Server[server.py\nLifeKanban web/API]
    VM --> Data[/opt/lifekanban/data\nboard, users, settings, results]
    Timer[systemd timer\nlifekanban-worker.timer] --> Worker[worker/worker.sh]
    Worker --> Data
    Worker --> BYOAI[worker/byoai_worker.py\nuses saved BYOAI key]
    BYOAI --> AI[Anthropic/OpenAI API]
```

The app is deliberately simple: Python standard-library HTTP server, static HTML/CSS/JS, JSON files for state, and a shell/Python worker. There is no database server.

## Live VM Details

VM information:

| Item | Value |
| --- | --- |
| Proxmox VM ID | `119` |
| Proxmox VM name | `lifekanban-ai` |
| VM IP | `172.22.20.30` |
| SSH user | `adrian` |
| App directory | `/opt/lifekanban/app` |
| Data directory | `/opt/lifekanban/data` |
| Backup directory | `/opt/lifekanban/backups` |
| Public app host | `lifekanban.chattoweb.com` |
| Raw app host | `http://172.22.20.30:8787` |

SSH from the Mac mini normally works with:

```bash
ssh -i ~/.ssh/codex_proxmox_ed25519 \
  -o UserKnownHostsFile=/private/tmp/lifekanban-ai.known_hosts \
  -o BatchMode=yes \
  adrian@172.22.20.30
```

The VM was given this Mac mini's Proxmox/Codex public key via Proxmox cloud-init. If access breaks, inspect VM `119` from Proxmox and check its cloud-init SSH keys.

## Live Services On The VM

Two systemd units matter:

```bash
systemctl status lifekanban-server.service --no-pager -l
systemctl status lifekanban-worker.timer --no-pager -l
systemctl status lifekanban-worker.service --no-pager -l
```

Expected state:

| Unit | Expected state | Meaning |
| --- | --- | --- |
| `lifekanban-server.service` | `active` | Web/API server is running. |
| `lifekanban-worker.timer` | `active` | Worker schedule is armed. |
| `lifekanban-worker.service` | Often `inactive (dead)` | Normal for a one-shot worker after a successful run. Check exit status/log lines, not only active state. |

Server service shape:

```ini
WorkingDirectory=/opt/lifekanban/app
Environment=KANBAN_HOST=172.22.20.30
Environment=KANBAN_PORT=8787
Environment=KANBAN_DATA=/opt/lifekanban/data
ExecStart=/usr/bin/python3 -u /opt/lifekanban/app/server.py --no-browser
```

Worker service shape:

```ini
Environment=KANBAN_DATA=/opt/lifekanban/data
Environment=KANBAN_WORKER_RESULTS=/opt/lifekanban/data/results
ExecStart=/bin/bash /opt/lifekanban/app/worker/worker.sh
```

Worker timer is the backup schedule. Moving a card assigned to AI into the relevant queue should also wake the worker immediately through `/api/worker/run`.

## Worker Environment

On the VM, `/opt/lifekanban/app/worker/worker.env` is runtime configuration and should not be overwritten by deployments. Current important values are:

```bash
KANBAN_DATA=/opt/lifekanban/data
KANBAN_REPO_DIR=/opt/lifekanban/app
KANBAN_AI_PROVIDER=custom
KANBAN_AI_BIN=/opt/lifekanban/app/worker/byoai_worker.py
KANBAN_WORKER_RESULTS=/opt/lifekanban/data/results
KANBAN_WORKER_SET_RESULT=1
KANBAN_WORKER_MAX=3
```

`worker/byoai_worker.py` is now committed to the repo. It is generic and contains no secrets. It reads the saved encrypted BYOAI credentials from LifeKanban's user store at runtime.

The worker should prefer Codex when available in Mac/local mode, but the VM currently uses the custom BYOAI helper because that matches the app's saved provider/model/API-key settings. The BYOAI helper is text-only: it can write summaries, drafts, and plans, but it cannot edit repository files. `worker/worker.sh` now guards against false completion by parking implementation-looking cards in Needs OK when the active provider is `custom` and the result is not already `NEEDS_OK:`.

## Cloudflare Tunnel Details

Docker2/Cloudflare host:

| Item | Value |
| --- | --- |
| Host/IP | `172.22.20.2` |
| Hostname | `cloudflare` |
| SSH user | `adrianchatto` |
| Active tunnel files | `/etc/cloudflared/config.yml` and `/etc/cloudflared/lifekanban.yml` |
| Services | `cloudflared.service`, `cloudflared-lifekanban.service` |

SSH from the Mac mini:

```bash
ssh -i ~/.ssh/codex_proxmox_ed25519 adrianchatto@172.22.20.2
```

The active configs should only route LifeKanban like this:

```yaml
ingress:
  - hostname: lifekanban.chattoweb.com
    service: http://172.22.20.30:8787
  - service: http_status:404
```

`kanban.chattoweb.com` was intentionally removed because it was an old/stale route. Do not re-add it unless Adrian explicitly asks.

Useful checks:

```bash
ssh -i ~/.ssh/codex_proxmox_ed25519 adrianchatto@172.22.20.2 \
  'sed -n "1,80p" /etc/cloudflared/config.yml; echo ---; sed -n "1,80p" /etc/cloudflared/lifekanban.yml; systemctl is-active cloudflared.service cloudflared-lifekanban.service'
```

If sudo on Docker2 prints `unable to resolve host cloudflare`, `/etc/hosts` is missing a hostname entry. This warning is noisy but not necessarily fatal. A future cleanup is:

```bash
sudo sh -c 'grep -q "127.0.1.1 cloudflare" /etc/hosts || echo "127.0.1.1 cloudflare" >> /etc/hosts'
```

## Source Code Map

| Path | Purpose |
| --- | --- |
| `server.py` | Python HTTP server and API routes. Handles auth, board read/write, admin APIs, AI parse/rewrite routes, GitHub issue creation, cron listing, Pushover settings, worker wake endpoint, and static file serving. |
| `auth.py` | User store, password hashing, encrypted BYOAI/Pushover secrets, session/CSRF handling, API tokens, preferences. |
| `kanban.py` | Core board operations, CLI commands, local/remote API mode, card claiming/result handling, Pushover done notifications, GitHub issue close-on-done. |
| `index.html` | Main board UI: board/list/calendar views, Tools menu, card editor, comments, history, Pomodoro, chat assistant, top bar colour picker. |
| `features.html` | Feature Requests workspace: project-to-GitHub mapping, feature chat flow, GitHub issue creation, feature Kanban, Pushover admin submenu. |
| `admin.html` | Admin UI: user management plus cron job lister. |
| `settings.html` | Account settings UI: BYOAI provider/model/key, password change, topbar colour, Pushover controls. |
| `theme.css` | Shared Apple-inspired visual theme variables and common styling. |
| `worker/worker.sh` | Autonomous worker loop. Claims AI cards, runs configured AI command, writes results, moves cards to Done or Needs OK. |
| `worker/byoai_worker.py` | VM-compatible AI runner that uses the board's saved BYOAI provider/model/key. |
| `sync_calendar.py` | macOS Calendar importer for near-term events. |
| `com.chatto.kanban.*.plist` | macOS LaunchAgent templates for server, worker, notifications, calendar sync. |
| `Enable *.command` / `Disable *.command` | Mac-friendly installers/removers for LaunchAgents and helper tasks. |
| `docs/USER_GUIDE.md` | User-facing guide, also mirrored into Notion when maintained manually. |
| `docs/AGENT_HANDOVER.md` | This file. Keep updated when operational shape changes. |

## Runtime Data Map

Mac local runtime data lives in the repo folder unless a `KANBAN_DATA` override is used. VM runtime data lives under `/opt/lifekanban/data`.

Important files:

| Runtime file | Meaning | Commit? |
| --- | --- | --- |
| `board.json` | Main board data for legacy/default user or single-board mode. | No, unless explicitly requested as sample data. |
| `boards/*.json` | Per-user boards. | No. |
| `users.json` | User accounts, hashed passwords, encrypted API key/Pushover blobs. | Never. |
| `.secret.key` | Encryption key for saved API/Pushover secrets. | Never. Losing it means users must re-enter secrets. |
| `app_settings.json` | App/project/integration settings. | No. |
| `attachments/` | Uploaded/pasted card files. | No. |
| `results/` | Worker output files. | Usually no. Generated output, not source. |
| `worker/worker.env` | Host-specific worker configuration. | No. |
| `worker/worker.log` | Worker log. | No. |

`.gitignore` should continue excluding these.

## Feature Inventory

### Accounts And Security

- Login/signup page at `/login.html`.
- First signed-up user becomes admin; later signups are normal users.
- Admins can create users, reset passwords, change roles, and delete users.
- Passwords are PBKDF2-hashed.
- Sessions are HttpOnly cookies.
- SameSite is Strict.
- State-changing API calls use CSRF tokens.
- Repeated failed login attempts are throttled in memory. Restarting the server clears lockouts.
- Per-user BYOAI provider/model/API-key settings are stored encrypted.
- Per-user preferences include topbar colour and Pushover settings.

### Board UI

- Board columns: `To Do`, `Doing`, `Waiting for Others`, `Needs OK`, `Done`.
- Drag cards between columns.
- Board, list, and calendar views.
- Project and owner filters.
- Collapsible columns.
- Stable ticket IDs such as `k-052` shown on cards and modals.
- Card editor with title, description, project, assignee, status, priority, due date, recurrence, subtasks, attachments, comments, and history update.
- Attachments can be added by choosing files, dragging files, or pasting screenshots.
- Comments are timestamped and displayed in the card editor.
- History updates append a timestamped log entry and show a scrollable card history.
- Done cards can trigger notifications and GitHub issue close logic depending on card type/settings.

### Tools Menu

The main board has a grouped `Tools` menu. It links to:

- Quick message.
- Feature Requests.
- Cron jobs.
- Projects.
- Settings/admin style options.

The UI was redesigned toward an Apple Notes-inspired style: cleaner chrome, restrained spacing, rounded but not cartoonish controls, muted panels, and a single Tools menu instead of several mismatched top-level buttons.

### Quick Message

Quick message is an in-app modal for polishing pasted text.

- Route/API: `/api/ai/rewrite-message`.
- Legacy route `/api/ai/clean` is also preserved server-side.
- Uses the logged-in user's saved BYOAI provider/model/API key.
- Rewrites for spelling, grammar, clarity, and a senior product-manager tone.
- Does not add new commitments, facts, greetings, signatures, or commentary.
- Enforces a text-length cap.

If it says the provider/model/key is not configured, set BYOAI under Settings for that logged-in user.

### Feature Requests

Feature Requests is a separate app page at `/features.html`.

It supports:

- GitHub-linked projects with `owner/repo` mapping.
- A chat-like request capture flow.
- Project selection when creating a feature request.
- GitHub issue creation through `/api/github/issue`.
- Feature-request cards added to a Kanban-style feature board.
- Moving a feature request to Doing wakes the worker.
- AI cards show a spinner/working indicator while in Doing.
- Feature-request cards include GitHub issue URL/repo metadata.
- When a linked feature card moves to Done, LifeKanban attempts to close the GitHub issue and logs the result.
- Pushover settings were moved into a submenu/details area on that page instead of a large always-visible form.

GitHub issue creation requires GitHub CLI/auth to be available where the server is running, or another configured mechanism in `server.py`.

### AI Worker

The autonomous worker:

- Runs immediately on certain UI actions and on a 15-minute backup schedule.
- Claims cards assigned to AI.
- Moves claimed cards to Doing.
- Writes output to `results/`.
- Sets result links on the card.
- Moves successful cards to Done.
- Moves cards to Needs OK if the worker says approval is needed.
- Can retry or recover interrupted Doing cards.
- Can send done notifications when Pushover is configured.

On macOS it is installed through LaunchAgents. On the VM it is a systemd one-shot service plus timer.

### BYOAI

Users bring their own AI key.

Supported providers in current code include:

- Anthropic.
- OpenAI.

Settings store:

- Provider.
- Model.
- Encrypted API key.

Server-side endpoints use the saved key for:

- Chat card parsing (`/api/ai/parse`).
- Quick message rewrite (`/api/ai/rewrite-message`).
- Feature request parsing/assistant flows where implemented.
- VM worker through `worker/byoai_worker.py`.

### Pushover

Pushover is available as admin/user preference storage and notification plumbing.

- Pushover user key and app token are stored encrypted.
- Settings UI and Feature Requests submenu can save/test/clear settings.
- Done notifications are sent for AI-assigned cards when newly moved to Done.
- Notification details include card title/description and GitHub issue link when present.

### GitHub Integration

Feature Requests can create GitHub issues. Cards store:

- `github_repo`.
- `github_issue_url`.
- Feature request metadata.

When a linked feature card reaches Done, LifeKanban attempts to close the GitHub issue using GitHub CLI. Failure to close the issue should not block card completion; the result is written to card history/log.

### Cron Job Lister

Admins have a cron job lister:

- UI in `admin.html`, usually linked via `admin.html#cron`.
- API route: `/api/admin/cron`.
- Lists cron sources, schedules, users, commands, and computed next run details where possible.
- Useful on Linux/VM deployments to see scheduled jobs without logging into the server.

### Calendar Sync

Mac-only Calendar sync:

- `sync_calendar.py` reads macOS Calendar events using EventKit/Apple tooling.
- Imports events starting in the next two days by default.
- Creates deduplicated To Do cards.
- Cards are assigned to `Ch@o`, project `General`, medium priority.
- Titles are prefixed `Calendar:`.
- Stores a source key so repeated runs do not duplicate events.
- Installed by `Enable Calendar Sync.command`.
- Disabled by `Disable Calendar Sync.command`.
- Uses macOS LaunchAgent `com.chatto.kanban.calendar-sync.plist`.

### Pomodoro

The main board contains Pomodoro functionality near the bottom of `index.html`, and `/pomodoro.html` is a protected mobile-first Pomodoro page that shares the same `kanban_pomodoro` localStorage state. The main board redirects mobile/coarse-pointer browsers to `/pomodoro.html` unless the user chooses the full board with `/?desktop=1`.

Known behaviour:

- Focus/break timer UI.
- Mobile-first standalone Pomodoro page under Tools -> Pomodoro.
- A previous card added Spotify focus music behaviour when a Focus session starts.
- Keep the shared storage key stable so the desktop widget and mobile page stay in sync.

### Top Bar Colour

Users can change the top bar colour from Settings/additional options.

- Uses CSS variable `--topbar`.
- Persists client-side in `localStorage` and user preference where supported.
- `theme.css` contains shared theme tokens.

## Deployment And Sync Rules

### From Mac Repo To VM

Use rsync and exclude live data. This is the pattern that was used successfully:

```bash
rsync -az --delete \
  -e "ssh -i ~/.ssh/codex_proxmox_ed25519 -o UserKnownHostsFile=/private/tmp/lifekanban-ai.known_hosts -o BatchMode=yes" \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '._*' \
  --exclude '.DS_Store' \
  --exclude 'board.json' \
  --exclude 'boards/' \
  --exclude 'attachments/' \
  --exclude 'results/' \
  --exclude 'users.json' \
  --exclude '.secret.key' \
  --exclude 'app_settings.json' \
  --exclude 'worker/worker.log' \
  --exclude 'worker/worker.env' \
  /Users/adrianchatto/GitHub/LifeKanban/ \
  adrian@172.22.20.30:/opt/lifekanban/app/
```

After deployment:

```bash
ssh -i ~/.ssh/codex_proxmox_ed25519 \
  -o UserKnownHostsFile=/private/tmp/lifekanban-ai.known_hosts \
  -o BatchMode=yes adrian@172.22.20.30 \
  'python3 -m py_compile /opt/lifekanban/app/server.py /opt/lifekanban/app/kanban.py /opt/lifekanban/app/auth.py && sudo -n systemctl restart lifekanban-server.service && sudo -n systemctl start lifekanban-worker.service || true && systemctl is-active lifekanban-server.service && systemctl is-active lifekanban-worker.timer'
```

If `worker/byoai_worker.py` is missing on the VM, restore it from Git or backup. It is now committed, so a fresh sync should preserve it.

### Backups

Before large migrations, create a VM backup tarball:

```bash
ssh -i ~/.ssh/codex_proxmox_ed25519 adrian@172.22.20.30 \
  'mkdir -p /opt/lifekanban/backups && tar -czf /opt/lifekanban/backups/pre-change-$(date +%Y%m%d-%H%M%S).tgz -C /opt/lifekanban app data'
```

A known pre-Mac-mini-sync backup exists:

```text
/opt/lifekanban/backups/pre-macmini-sync-20260615-085713.tgz
```

A known Proxmox snapshot was created before injecting the Mac mini key into VM 119:

```text
pre-key-inject-20260615
```

Docker2 VM 108 did not support normal Proxmox snapshots in the current storage setup.

## Current Board Migration Notes

During the Mac mini to VM migration:

- Remote VM's 4 existing done cards were preserved.
- Mac mini's 4 open cards were added.
- VM board ended up with 8 cards and `next_id` 78.
- Migrated open cards were assigned to `Ch@o`, not AI, so the worker correctly processed 0 cards on verification.

Known migrated open cards at that time:

| ID | Status | Assignee | Title |
| --- | --- | --- | --- |
| `k-017` | `todo` | `Ch@o` | Get Car Washed |
| `k-038` | `todo` | `Ch@o` | Complete Project updates |
| `k-052` | `waiting` | `Ch@o` | Start to build out to-be workshops for Rank |
| `k-075` | `todo` | `Ch@o` | Update Datasite project notes |

## Verification Checklist

Use this after any change.

### VM Service Check

```bash
ssh -i ~/.ssh/codex_proxmox_ed25519 \
  -o UserKnownHostsFile=/private/tmp/lifekanban-ai.known_hosts \
  -o BatchMode=yes adrian@172.22.20.30 \
  'systemctl is-active lifekanban-server.service lifekanban-worker.timer; systemctl status lifekanban-worker.service --no-pager -l | sed -n "1,60p"'
```

Expected:

- server active.
- timer active.
- worker one-shot last exit status success.

### Raw HTTP Check

```bash
curl -sS -o /tmp/lk-login.html -w '%{http_code} %{size_download}\n' http://172.22.20.30:8787/login.html
curl -sS -o /dev/null -w '%{http_code}\n' http://172.22.20.30:8787/theme.css
curl -sS -o /dev/null -w '%{http_code}\n' http://172.22.20.30:8787/features.html
```

Expected: `200` for all three.

### Public Host Check

Cloudflare Access may redirect unauthenticated requests:

```bash
curl -sS -D /tmp/lk-public.headers -o /tmp/lk-public.body -w '%{http_code} %{url_effective}\n' https://lifekanban.chattoweb.com/login.html
```

Expected: a Cloudflare Access redirect (`302`) unless already authenticated through Access.

### App Login Check

Use a known admin account or reset one on the VM. Do not commit credentials. From the VM, password reset can be done with `auth.set_password`:

```bash
ssh -i ~/.ssh/codex_proxmox_ed25519 \
  -o UserKnownHostsFile=/private/tmp/lifekanban-ai.known_hosts \
  adrian@172.22.20.30 \
  'cd /opt/lifekanban/app && KANBAN_DATA=/opt/lifekanban/data python3 - <<"PY"
import auth
auth.set_password("adrian", "REPLACE_WITH_TEMP_PASSWORD", must_change=False)
print("password reset")
PY'
```

Then test:

```bash
curl -sS -c /tmp/lk.cookies -H 'Content-Type: application/json' \
  -d '{"username":"adrian","password":"REPLACE_WITH_TEMP_PASSWORD"}' \
  http://172.22.20.30:8787/api/login
```

## Common Troubleshooting

### Public URL Shows Old UI Or Missing Features

Check whether raw VM has the new asset but public host does not:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://172.22.20.30:8787/theme.css
curl -sS -o /dev/null -w '%{http_code}\n' https://lifekanban.chattoweb.com/theme.css
```

If raw VM is correct and public is not, inspect Docker2 cloudflared configs and DNS/Cloudflare Access routing.

### `kanban.chattoweb.com` Does Not Work

This is expected unless Adrian explicitly re-enables it. It was removed because it was a stale route to an older origin. Use `lifekanban.chattoweb.com`.

### Login Says Too Many Attempts

Login throttling is in memory in `auth.py`, not persisted to disk. Restarting `lifekanban-server.service` clears it:

```bash
ssh -i ~/.ssh/codex_proxmox_ed25519 adrian@172.22.20.30 \
  'sudo -n systemctl restart lifekanban-server.service'
```

### Public Host Redirects To Cloudflare Access

That is normal. Authenticate through Cloudflare Access first, then LifeKanban's own login appears.

### Worker Says No Usable AI CLI Found

On VM, check `worker/worker.env`:

```bash
grep -n 'KANBAN_AI' /opt/lifekanban/app/worker/worker.env
```

Expected custom helper:

```bash
KANBAN_AI_PROVIDER=custom
KANBAN_AI_BIN=/opt/lifekanban/app/worker/byoai_worker.py
```

Also check the helper exists and is executable:

```bash
test -x /opt/lifekanban/app/worker/byoai_worker.py && echo ok
```

### Worker Processes 0 Cards

This can be correct. It only processes cards assigned to AI and in the claimable state. Cards assigned to `Ch@o` are intentionally ignored.

### Docker2 SSH Fails

Docker2 originally only trusted Semaphore keys. The Mac mini public key was added to `/home/adrianchatto/.ssh/authorized_keys`:

```text
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDicLc0T8KRLTU/kRoccekf5bmB3/tSFEbMhdrZBG7gt codex-proxmox-workbench
```

If it is removed, re-add it from a Docker2 console.

## Git Workflow Notes For Future Agents

Current branch used for this work:

```text
rebuild-signup-byoai
```

Before committing:

```bash
git -C /Users/adrianchatto/GitHub/LifeKanban status --short --branch
```

Be careful: the working tree may have local runtime dirt that should remain uncommitted:

- `board.json`
- `worker/worker.log`
- untracked `results/*`

When committing source/docs, stage explicitly:

```bash
git add docs/AGENT_HANDOVER.md README.md
```

Do not `git add -A` unless Adrian explicitly wants runtime data committed.

## Maintainer Principles

- Treat `/opt/lifekanban/data` as the live source of truth for the VM.
- Treat `/Users/adrianchatto/GitHub/LifeKanban` as source code plus some local runtime state.
- Never overwrite VM data with repo data unless intentionally migrating a board.
- Before `rsync --delete`, exclude runtime data and worker env files.
- Verify both service state and actual HTTP routes.
- For public URL issues, compare raw VM responses with Cloudflare responses before changing the app.
- Keep `docs/USER_GUIDE.md` user-facing and this file operator-facing.
- Update this file after changing deployment shape, hostnames, service names, data locations, worker config, or Cloudflare routing.
