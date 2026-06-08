# LifeKanban

A personal Kanban board that lives on your Mac, plus a Claude worker that
processes cards you assign to Claude.

**Repo:** https://github.com/adrianchatto/LifeKanban

## Install it on your Mac (the Dock icon)

macOS doesn't have a "taskbar" — the equivalent is the **Dock** (the strip of
icons, usually along the bottom). To get a permanent clickable icon there:

1. Open the project folder in Finder.
2. (Recommended) Drag **`Kanban Board.app`** into your **Applications** folder.
3. Double-click it once. The first time, macOS may warn it's from an
   unidentified developer — **right-click the app → Open → Open**. You only do
   this once. (It's your own file; the warning is just because it isn't
   code-signed.)
4. While it's running, its icon sits in the Dock. **Right-click that Dock icon →
   Options → Keep in Dock.** Now it stays there and you launch the board with
   one click any time.

That app starts a tiny local server and opens the board in your browser. To turn
on background due-date alerts (so you're notified even when the board is shut),
double-click **`Enable Notifications.command`** once.

> Note: this is a local web app shown in your browser, launched by a Dock icon —
> not a single-window native app. That's the reliable, no-toolchain approach we
> chose. If you'd rather have a true top-of-screen menu-bar app later, that's a
> bigger build we can do separately.

## Open the board

Double-click **`Kanban Board.app`**. It starts a tiny local server and opens the
board in your browser. The app runs quietly (no Dock bounce); close the tab when
done — to stop the server fully, quit it from Activity Monitor or run
`pkill -f server.py`.

First launch: macOS may say the app is from an unidentified developer. Right-click
the app → **Open** → **Open** once, and it'll trust it from then on. (It's your
own file — the warning is just because it isn't code-signed.)

If the app won't start, double-click **`Start Kanban.command`** instead, or run
`python3 server.py` from this folder.

To keep it handy: drag `Kanban Board.app` to your Dock (or Applications).

## Using it

- **Columns:** To Do → Doing → Needs OK → Done. Drag cards between them.
- **New card:** "+ New card". Set a title, description, project, and assignee.
- **Assignee:** *Me* (you) or *Claude*.
- **Filters:** by project and by owner, top-right.

## Accounts & logins

The board now sits behind a login. Each user has their own board, their own
results and attachments, and provides their own AI API key (used by the
"Add by chat" assistant). Sign-up is **admin-only** — there's no public
registration; you create accounts internally.

**First-time setup (once).** Create the first admin from the terminal:

```bash
cd ~/Documents/Claude/Projects/Kanban
python3 kanban.py user-add <name> --admin        # prompts for a password
```

Then open the board and sign in. New users created from the admin panel start
with a temporary password and are prompted to set their own.

**Day-to-day.**

- **Log in / out:** the login page is `/login.html`; the account menu (top-right
  of the board) has *Settings*, *Admin* (admins only), and *Log out*.
- **Create users:** *Admin* → *Create a user* (username, temporary password,
  role). You can reset passwords, switch roles, or delete users there too. The
  CLI commands (`user-add`, `user-list`, `user-del`, `user-passwd`, `user-role`)
  do the same from the terminal.
- **API key:** each user sets their own under *Settings*. It's stored
  **encrypted** on the server (never in clear text, never committed to GitHub)
  and only ever sent back to that user's own browser.

**Security notes.** Passwords are PBKDF2-hashed; sessions are HttpOnly,
SameSite=Strict cookies; state-changing requests carry a CSRF token; repeated
failed logins are throttled. The files `users.json`, `.secret.key` and other
users' `boards/` are gitignored — keep `.secret.key` safe, as losing it means
stored API keys can no longer be decrypted (users would just re-enter them).
**If you serve this beyond your own Mac, put it behind HTTPS and set
`KANBAN_SECURE_COOKIES=1`.**

## API access for the Claude worker (remote / Docker)

When the board runs behind a login (e.g. in Docker on another host), the worker
can't use the local `board.json` and there's no anonymous API. Instead, give it
an **API token**:

1. Mint a token for the board's owner (run on the host, inside the container):

   ```bash
   docker exec -it lifekanban python3 kanban.py token-add Ch@o "claude-worker"
   ```

   The token (`lk_…`) is printed once — copy it.

2. Point the worker at the API and run the normal CLI commands:

   ```bash
   KANBAN_API_URL=http://172.22.20.5:8787 KANBAN_API_TOKEN=lk_… \
     python3 kanban.py add "Buy milk" --assignee Claude
   # add / move / claim-next / set-result / log all work over the API
   ```

A token acts as the user it belongs to (so it reads/writes that account's
board), replaces the browser login, and is exempt from CSRF. Revoke with
`token-del <user> <token-id>`; list with `token-list <user>`. The worker must
have network access to the board's host. (Result *files* produced remotely
aren't yet uploaded to the container — cards and their fields sync, but a
`↗ View result` link needs the file to live in the board's results area.)

## User guide

There's a user guide for the app, published in Notion and linked in-app for
admins (account menu → **User guide ↗**, admins only). The link is driven by
`.guide_url` in this folder (or the `KANBAN_GUIDE_URL` env var) — put the Notion
page URL there to light up the menu item.

The source copy lives at `docs/USER_GUIDE.md`. **When you add or change a
feature, update both `docs/USER_GUIDE.md` and the Notion page** (and add a dated
line to the Changelog) so the guide stays current.

## How Claude cards work

Any card assigned to **Claude** and sitting in **To Do** gets picked up
automatically by a scheduled worker (about every 15 minutes):

1. Card moves to **Doing** (you'll see "Claude is working…").
2. Claude does the work and saves the result into `results/`.
3. Card moves to **Done** with a **↗ View result** link.

If a card needs something irreversible (send an email, post a message, publish,
delete), Claude does the prep, then parks the card in **Needs OK** and waits for
your explicit go-ahead — it won't take the irreversible step on its own.

You can also just tell Claude in chat: **"process my board"** to run it now
instead of waiting for the schedule.

## Creating cards from chat

While talking to Claude about anything, say **"build an action for that"** and
Claude will capture it as a card on this board, assigned to you or to Claude.

## Files

| File | What it is |
|------|------------|
| `Kanban Board.app` | Double-click to open the board |
| `Start Kanban.command` | Fallback launcher |
| `board.json` | All your cards (the source of truth) |
| `server.py` | Local web server for the board |
| `index.html` | The board UI |
| `kanban.py` | CLI used by Claude and the worker |
| `results/` | Deliverables Claude produces |
| `skills/` | Reusable action skills (`kanban` + your own) |

Don't hand-edit `board.json` while the board is open — use the UI or let Claude
use the CLI.

## Pushing to GitHub

The folder is already a git repo, committed, with the remote set to
`https://github.com/adrianchatto/LifeKanban.git` on branch `main`. I can't push
from here (no credentials in the sandbox), so do it once from your Mac:

```bash
cd ~/Documents/Claude/Projects/Kanban
git push -u origin main
```

If git asks for a password, use a **Personal Access Token** (GitHub no longer
accepts your account password). Easiest alternatives:

- With the GitHub CLI: `gh auth login`, then the `git push` above.
- Or switch to SSH if you have keys set up:
  `git remote set-url origin git@github.com:adrianchatto/LifeKanban.git`
  then `git push -u origin main`.

After that, future changes are just `git add -A && git commit -m "..." && git push`.
