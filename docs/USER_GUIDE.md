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

The board has four columns: **To Do → Doing → Needs OK → Done**. Drag a card between columns, or use the buttons on each card.

- **New card** — click **+ New card**. Give it a title, an optional description, a project, an assignee (you or AI), a priority, and an optional due date. You can also drag in or paste an attachment (e.g. a screenshot).
- **Assignee** — *Me* (you) or *AI*. A card assigned to AI and left in **To Do** is picked up automatically by the worker.
- **Priority** — high, medium, or low.
- **Filters** — filter by project and by owner, top-right.
- **Calendar view** — switch from Board to Calendar to see cards with due dates and recurring cards laid out by day.
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

**Stuck cards recover automatically.** If a worker run is interrupted and leaves a card in **Doing**, the next run returns it to **To Do** and retries it. A card that repeatedly fails is parked in **Needs OK** for you to review rather than looping forever.

You can also ask the worker **"process my board"** in chat to run it immediately instead of waiting for the schedule.

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
to GitHub and the server is rebuilt. (The worker does not edit the app; it only
produces written deliverables.)

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

- **10 June 2026** — Restored authenticated Docker-ready server flow, added public signup, kept first-user admin bootstrap, and moved BYOAI chat parsing server-side through `/api/ai/parse`.
- **10 June 2026** — Renamed the worker-facing assignee from Claude to AI. Old cards assigned to Claude are still picked up for compatibility and are normalized when claimed.
- **8 June 2026** — Added multi-user accounts: login/logout, admin user creation, per-user boards, per-user encrypted API keys, settings and admin screens. Chat assistant now always writes a concise heading as the card title. Stuck-in-Doing cards now auto-recover.
- **8 June 2026** — Added API tokens for programmatic/remote access (bearer-token auth, exempt from CSRF) so the AI worker can drive a Dockerised board over the network; `kanban.py` gained a remote (HTTP) mode and `token-add`/`token-list`/`token-del`. Docker image now ships the auth module and login/settings/admin pages, and bootstraps the first admin from env on first run.
- **8 June 2026** — Added the autonomous worker (`worker/worker.sh`): a cron job that claims the next AI card, runs it via the configured CLI, and moves it to Done (or Needs OK for irreversible actions). Renamed the in-app user-management menu item to "Manage users" to avoid clashing with the ⚙ Admin settings button.
