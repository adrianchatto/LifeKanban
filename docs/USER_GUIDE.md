# LifeKanban — User Guide

> **Audience:** anyone using the LifeKanban board. **Maintained by:** Claude — this page is updated whenever the app changes. _Last updated: 8 June 2026._
>
> _This file is the source copy of the guide that is published to Notion. When features change, update this file and the Notion page together._

LifeKanban is a personal Kanban board that runs on a Mac, with a built-in Claude worker that can carry out cards you assign to it. Each person has their own account, their own board, and supplies their own AI API key. This guide explains how to use it.

## Signing in

The board sits behind a login. Open the board's web address and you'll see the sign-in page.

- Enter your **username** and **password**.
- If you were given a **temporary password**, you'll see a banner after signing in prompting you to set your own. Do that under **Settings** before anything else.
- Sessions stay active for about 12 hours, then you'll be asked to sign in again. Use **Log out** (top-right account menu) to end a session immediately.

There is no public sign-up. New accounts are created by an administrator (see **Administration**).

## The board

The board has four columns: **To Do → Doing → Needs OK → Done**. Drag a card between columns, or use the buttons on each card.

- **New card** — click **+ New card**. Give it a title, an optional description, a project, an assignee (you or Claude), a priority, and an optional due date. You can also drag in or paste an attachment (e.g. a screenshot).
- **Assignee** — *Me* (you) or *Claude*. A card assigned to Claude and left in **To Do** is picked up automatically by the worker.
- **Priority** — high, medium, or low.
- **Filters** — filter by project and by owner, top-right.
- **Calendar view** — switch from Board to Calendar to see cards with due dates and recurring cards laid out by day.
- **Projects** — manage your list of projects via the **Projects** button.

## Adding cards by chat

The chat assistant (bubble, bottom-right) turns a plain sentence into a card. Type something like *"Pay the parking fine by Friday"* and it drafts a card for you.

- It writes a **concise heading** for the title and keeps the full request in the **description**, so the title stays short and readable.
- It tries to detect a due date, recurrence, project, priority, and whether the task is for you or for Claude. If the project or owner is unclear, it asks before adding.
- If you've set an **API key** (see Settings), the assistant uses the AI model to read messier phrasing. Without a key it falls back to built-in rules — both produce a short title.

## How Claude cards work

Any card assigned to **Claude** and sitting in **To Do** is picked up automatically by a scheduled worker (roughly every 15 minutes):

1. The card moves to **Doing** (you'll see "Claude is working…").
2. Claude does the work and saves the result into your results area.
3. The card moves to **Done** with a **↗ View result** link.

If a task needs an irreversible step (send an email, post a message, publish, delete, pay), Claude does the safe preparation, then parks the card in **Needs OK** and waits. Press **Approve & run** on the card (or tell Claude to approve it) and it carries out the step on its next pass.

**Stuck cards recover automatically.** If a worker run is interrupted and leaves a card in **Doing**, the next run returns it to **To Do** and retries it. A card that repeatedly fails is parked in **Needs OK** for you to review rather than looping forever.

You can also tell Claude **"process my board"** in chat to run it immediately instead of waiting for the schedule.

## Settings

The **Settings** screen (account menu, top-right) is where you manage your own account:

- **API key** — choose a provider (Anthropic or OpenAI), a model, and paste your key. It powers the chat assistant. Your key is stored **encrypted** on the server against your account and is only ever sent back to your own browser; it is never shared with other users or committed to source control.
- **Change password** — update your password (minimum 8 characters).

## Administration (admins only)

Admins see an **Admin** entry in the account menu. From there you can:

- **Create a user** — set a username, a temporary password, and a role (user or admin). New users are prompted to set their own password on first sign-in and add their own API key under Settings.
- **Reset a password** — issue a new temporary password.
- **Change role** — promote a user to admin or back to user. The last remaining admin cannot be demoted or deleted.
- **Delete a user** — removes their login. Their board files remain on disk.

The same actions are available from the terminal via `kanban.py` (`user-add`, `user-list`, `user-del`, `user-passwd`, `user-role`) — useful for creating the very first admin.

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
- **A Claude card looks stuck** — it will be retried automatically on the next worker pass; no action needed.

## Changelog

- **8 June 2026** — Added multi-user accounts: login/logout, admin-only user creation, per-user boards, per-user encrypted API keys, settings and admin screens. Chat assistant now always writes a concise heading as the card title. Stuck-in-Doing cards now auto-recover.
