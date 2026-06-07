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
