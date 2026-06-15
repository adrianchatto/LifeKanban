# LifeKanban — User Guide

> **Audience:** anyone using the LifeKanban board. **Maintained by:** AI — this page is updated whenever the app changes. _Last updated: 10 June 2026._
>
> _This file is the source copy of the guide that is published to Notion. When features change, update this file and the Notion page together._

LifeKanban is a personal Kanban board that runs on a Mac, with a built-in AI worker that can carry out cards you assign to it. Each person has their own account, their own board, and supplies their own AI API key. This guide explains how to use it.

## Signing in

The board sits behind a login. Open the board's web address and you'll see the sign-in page.

- Enter your **username** and **password**.
- If you were given a **temporary password**, you'll see a banner after signing in prompting you to set your own. Do that under **Settings** before anything else.
- Sessions stay active for about 12 hours, then you'll be asked to sign in again. Use **Log out** (top-right account menu) to end a session immediately.

Use **Sign up** on the login page to create an account. The first account becomes the admin; later signups are normal users. Admins can also create accounts manually (see **Administration**).

## The board

The board has five columns: **To Do → Doing → Waiting for Others → Needs OK → Done**. Drag a card between columns, or use the buttons on each card.

- **New card** — click **+ New card**. Give it a title, an optional description, a project, an assignee (you or AI), a priority, and an optional due date. You can also drag in or paste an attachment (e.g. a screenshot).
- **History updates** — open any card, type an update in **History update**, and save. LifeKanban stamps the date and time, keeps the note in the card history, and shows the full history in a scrollable list.
- **Assignee** — *Me* (you) or *AI*. A card assigned to AI and left in **To Do** is picked up automatically by the worker.
- **Priority** — high, medium, or low.
- **Filters** — filter by project and by owner, top-right.
- **Calendar view** — switch from Board to Calendar to see cards with due dates and recurring cards laid out by day.
- **Ticket number** — each card shows its ticket ID, such as `k-052`, on the board, in list/calendar views, and at the top of the edit window.
- **Projects** — manage your list of projects via the **Projects** button.

## Adding cards by chat

The chat assistant (bubble, bottom-right) turns a plain sentence into a card. Type something like *"Pay the parking fine by Friday"* and it drafts a card for you.

- It writes a **concise heading** for the title and keeps the full request in the **description**, so the title stays short and readable.
- It tries to detect a due date, recurrence, project, priority, and whether the task is for you or for AI. If the project or owner is unclear, it asks before adding.
- If you've set an **API key** (see Settings), the assistant uses the AI model to read messier phrasing. Without a key it falls back to built-in rules — both produce a short title.

## How AI cards work

Any card assigned to **AI** and sitting in **To Do** is picked up automatically by a scheduled worker (roughly every 15 minutes):

1. The card moves to **Doing** (you'll see "AI is working…").
2. The AI worker does the work and saves the result into your results area.
3. The card moves to **Done** with a **↗ View result** link.

If a task needs an irreversible step (send an email, post a message, publish, delete, pay), the AI worker does the safe preparation, then parks the card in **Needs OK** and waits. Press **Approve & run** on the card (or approve it in chat) and it carries out the step on its next pass.

Feature-request cards created from the Feature Requests page carry their GitHub issue link. When one of those cards newly moves into **Done**, LifeKanban uses GitHub CLI to close the linked issue and writes success or failure into the card log. If GitHub is unavailable or not authenticated, the card still completes.

**Stuck cards recover automatically.** If a worker run is interrupted and leaves a card in **Doing**, the next run returns it to **To Do** and retries it. A card that repeatedly fails is parked in **Needs OK** for you to review rather than looping forever.

You can also ask the worker **"process my board"** in chat to run it immediately instead of waiting for the schedule.

## Calendar sync

LifeKanban can automatically copy near-term macOS Calendar events into the board
so they appear beside normal tasks.

Double-click **`Enable Calendar Sync.command`** once. The sync runs immediately
and then hourly. Each run looks for Calendar events starting within the next two
days and creates a To Do card when that exact Calendar occurrence is not already
on the board.

Imported cards:

- are assigned to `Ch@o`
- use project `General`
- use medium priority
- get a due date matching the event start date
- have titles prefixed with `Calendar:`
- include the Calendar name, start time, end time, location, and notes in the
  description when available

The sync stores a hidden Calendar source key on each imported card, so repeated
runs do not create duplicates. To stop the schedule, double-click
**`Disable Calendar Sync.command`**.

First run may ask macOS to grant Calendar access. To test without changing the
board, run:

```bash
cd /Users/adrianchatto/GitHub/LifeKanban
python3 sync_calendar.py --dry-run --days 2
```

## Settings

The **Settings** screen (account menu, top-right) is where you manage your own account:

- **API key** — choose a provider (Anthropic or OpenAI), a model, and paste your key. It powers the chat assistant. Your key is stored **encrypted** on the server against your account and is used server-side through `/api/ai/parse`; it is never shared with other users or committed to source control.
- **Change password** — update your password (minimum 8 characters).

## Administration (admins only)

Admins see an **Admin** entry in the account menu. From there you can:

- **Create a user** — set a username, a temporary password, and a role (user or admin). New users are prompted to set their own password on first sign-in and add their own API key under Settings.
- **Reset a password** — issue a new temporary password.
- **Change role** — promote a user to admin or back to user. The last remaining admin cannot be demoted or deleted.
- **Delete a user** — removes their login. Their board files remain on disk.
- **Pushover notifications** — add a Pushover user key and app token. When enabled, LifeKanban sends a push alert whenever an AI-assigned card newly moves to **Done**.

The same actions are available from the terminal via `kanban.py` (`user-add`, `user-list`, `user-del`, `user-passwd`, `user-role`) — useful for creating the very first admin.

## API tokens (for automation / the remote worker)

If the board runs behind a login (for example in Docker on another machine), an
automated client such as the AI worker can't use a browser session. Instead
it uses an **API token** that acts as a specific user:

- An admin mints one on the host with `python3 kanban.py token-add <username> "label"` (printed once).
- The client sends it as a bearer token; it then reads and writes that user's board over the API, with no login screen and no CSRF token needed.
- Tokens can be listed (`token-list`) and revoked (`token-del`) at any time.

Keep tokens secret — anyone holding one can act as that user on their board.

## Security notes

- Passwords are stored hashed (PBKDF2), never in plain text.
- Sessions use HttpOnly, SameSite=Strict cookies; state-changing actions carry a CSRF token; repeated failed logins are throttled.
- API keys are encrypted at rest with a server-side key (`.secret.key`). Keep that file safe — losing it means stored API keys must be re-entered.
- If the board is served beyond a single Mac, it must be put behind HTTPS with `KANBAN_SECURE_COOKIES=1`.

## Setup & running (Mac)

- Double-click **Kanban Board.app** to start the local server and open the board in the browser. **Start Kanban.command** is a fallback launcher.
- **Enable Notifications.command** turns on background due-date alerts; **Disable Notifications.command** turns them off.
- First admin account (one-off): `python3 kanban.py user-add <name> --admin`.

## Troubleshooting

- **Can't sign in** — check the username/password; after several failed attempts there's a short lockout. Ask an admin to reset your password if needed.
- **Chat assistant ignores my key** — confirm the key, provider, and model are all set under Settings.
- **An AI card looks stuck** — it will be retried automatically on the next worker pass; no action needed.

## Updating & deploying changes

App changes — new features and bug fixes — only appear after the code is pushed
to GitHub and the server is rebuilt. The VM's BYOAI worker is text-only; implementation-style cards are now parked in Needs OK unless a code-capable worker applies the files.

1. **Push from the Mac:**

   ```bash
   cd ~/Documents/Claude/Projects/Kanban && bash git_sync.sh
   ```

   Confirm it worked: `tail -n 15 .sync-result.txt` — look for `commit: created`
   (or "nothing to commit") and `push exit: 0`.

2. **Redeploy on the server** (from wherever the repo lives there):

   ```bash
   git pull && docker compose up -d --build
   ```

3. **Hard-refresh** the board in the browser (Cmd-Shift-R) to clear the cached page.

The live board data, user accounts, API tokens and the encryption key live in the
Docker `/data` volume and are preserved across rebuilds.

## Changelog

- **15 June 2026** — Added a protected mobile-first Pomodoro page at `/pomodoro.html`, linked it under Tools, redirected mobile board visits there with a full-board escape hatch, and hardened the worker so BYOAI text results do not falsely mark implementation cards Done. Added a Mac-side autoship path for Codex implementation cards: commit, push, and deploy via `scripts/deploy-to-lifekanban-ai.sh`.
- **12 June 2026** — Added macOS Calendar sync: an hourly importer creates deduplicated To Do cards for Calendar events starting in the next two days.
- **12 June 2026** — Added per-card history updates: type a note in the card editor, save it with a timestamp, and review the scrollable history on that ticket.
- **10 June 2026** — Feature-request cards now close their linked GitHub issue when they newly move to Done, and record the close result in the card log.
- **10 June 2026** — Added encrypted admin Pushover settings and completion push alerts for AI-assigned cards when they newly move to Done.
- **10 June 2026** — Added visible ticket IDs to cards across board, list, calendar, and edit views.
- **10 June 2026** — Added a **Waiting for Others** column between Doing and Needs OK.
- **10 June 2026** — Restored authenticated Docker-ready server flow, added public signup, kept first-user admin bootstrap, and moved BYOAI chat parsing server-side through `/api/ai/parse`.
- **10 June 2026** — Renamed the worker-facing assignee from Claude to AI. Old cards assigned to Claude are still picked up for compatibility and are normalized when claimed.
- **8 June 2026** — Added multi-user accounts: login/logout, admin user creation, per-user boards, per-user encrypted API keys, settings and admin screens. Chat assistant now always writes a concise heading as the card title. Stuck-in-Doing cards now auto-recover.
- **8 June 2026** — Added API tokens for programmatic/remote access (bearer-token auth, exempt from CSRF) so the AI worker can drive a Dockerised board over the network; `kanban.py` gained a remote (HTTP) mode and `token-add`/`token-list`/`token-del`. Docker image now ships the auth module and login/settings/admin pages, and bootstraps the first admin from env on first run.
- **8 June 2026** — Added the autonomous worker (`worker/worker.sh`): a cron job that claims the next AI card, runs it via the configured CLI, and moves it to Done (or Needs OK for irreversible actions). Renamed the in-app user-management menu item to "Manage users" to avoid clashing with the ⚙ Admin settings button.
