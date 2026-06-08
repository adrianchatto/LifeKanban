---
name: kanban
description: >
  Add, move, and manage cards on Ch@o's personal Kanban board from ANY Claude
  session. Use whenever Ch@o says "add this to my Kanban board", "add <x> to my
  kanban", "put <x> on my board", "build an action for that", "what's on my
  board", "process my board", or asks to move/assign/complete/schedule a card.
---

# Kanban board control (works from any session)

Ch@o keeps a personal Kanban board. It is a set of files on his Mac at the
absolute path:

    /Users/adrianchatto/Documents/Claude/Projects/Kanban

The single source of truth is `board.json` there. A local web app renders it
(http://127.0.0.1:8787). **Always go through `kanban.py` — never hand-edit
board.json.** The board is on GitHub at https://github.com/adrianchatto/LifeKanban
and changes should be synced there (see "Sync to GitHub" below).

## Adding a card — pick the method that fits the session

**Method A — the Kanban folder is mounted in this session** (you can see it under
your working/connected folders). Run the CLI directly:

```
python3 "<KanbanFolder>/kanban.py" add "Pay for parking" \
  --desc "..." --project General --assignee "Ch@o" [--due YYYY-MM-DD]
```

**Method B — any other session (folder not mounted).** Use the "Control your Mac"
tool (osascript) to run the CLI on the real path. To avoid shell-quoting bugs,
encode the card as one URL-safe base64 JSON argument and call `addb64`:

1. Build the JSON object (only `title` is required):
   `{"title": "...", "description": "...", "project": "General",
     "assignee": "Ch@o", "due": "YYYY-MM-DD or omit"}`
2. URL-safe base64-encode that JSON → a single token with no spaces/quotes.
3. Run, via the osascript tool:
   `do shell script "/usr/bin/python3 /Users/adrianchatto/Documents/Claude/Projects/Kanban/kanban.py addb64 <ENCODED>"`

The encoded argument has no spaces, pipes, `$`, or quotes, so it passes through
osascript cleanly. (If you have a sandboxed Linux shell with the folder mounted,
prefer Method A — it's simpler.)

## Creating a card from a chat request — always do this

When Ch@o asks you to add something to the board in chat ("add X to my kanban",
"build an action for that", etc.), do NOT dump his whole sentence into the title.
Instead:

1. **Write a concise title yourself.** Use your judgement to distil a short,
   specific title (roughly 3–8 words, no trailing full stop) — e.g. "Chase
   Acme for signed SOW", not the full paragraph.
2. **Put the detail in the description.** Everything else he said — context,
   the deliverable, links, constraints — goes in `description`, not the title.
3. **Ask before adding** (unless he already gave them): in one short prompt, ask
   **who to assign it to** (Me/Ch@o or Claude), a **close/due date** (or none),
   and a **priority** (high / medium / low / none). Use the AskUserQuestion tool
   so he can pick quickly. Only skip a question he has already answered.
4. Then add the card with those fields. Confirm with the card id and final title.

(The board UI's New-card modal also supports **attachments** — drag a file in,
click "Choose file", or paste a screenshot. Attachments are uploaded to the
local server and stored under `attachments/`; the card carries an `attachments`
array of `{name,url,orig,type,size}`. Attachments are NOT committed to GitHub —
see `.gitignore` — so screenshots stay on Ch@o's machine.)

## Choosing the fields

- **assignee**: default `Ch@o` (his own to-dos). Use `Claude` only when he wants
  *you* to do the task — the background worker then picks it up.
- **project**: `General` for personal errands; otherwise the project he names
  (e.g. `NEO-015`). Valid statuses: `todo`, `doing`, `done`, `needs_ok`.
- **due**: only if he gives one. "midnight tonight"/"by end of today" → today's
  date (date granularity). Get today's date from the system, don't guess.
- **recur** (recurring tasks): only when he asks for something repeating. Pass
  `--recur` compact (`daily` | `weekdays` | `weekly:0,2` with Mon=0..Sun=6 |
  `monthly:15`), or in an addb64 payload a `recur` object
  `{"freq":"weekly","days":[0,2]}`. Recurring cards show in the Calendar view
  (expanded onto each occurrence), not in the board columns.
- **subtasks**: an addb64 payload may include `subtasks`:
  `[{"text":"...","done":false}, ...]`. Shown as a checklist on the card.
- **projects**: cards carry a `project`. The board's project list lives in
  `board.projects`. He manages projects (add/remove) in the UI; if he asks you to
  add a card under a new project, just set the project name — it's added
  automatically.

## Other operations (same CLI)

```
python3 kanban.py list [--assignee Claude] [--status todo]
python3 kanban.py move <id> <todo|doing|done|needs_ok>
python3 kanban.py assign <id> <Ch@o|Claude>
python3 kanban.py set-due <id> <YYYY-MM-DD|clear>
python3 kanban.py set-result <id> <results/<id>.md>
python3 kanban.py claim-next        # worker: oldest Claude+todo -> doing
```

## Sync to GitHub — ALWAYS do this after any change

Ch@o wants the board **always** kept in sync with GitHub. After ANY change you
make (cards, projects, subtasks, recurrence), commit and push. See the
`github-sync` skill for detail. Run the sync helper on the Mac:

- Method A (folder mounted): `bash "<KanbanFolder>/git_sync.sh"`
- Method B (any session): osascript →
  `do shell script "bash /Users/adrianchatto/Documents/Claude/Projects/Kanban/git_sync.sh"`

The helper commits everything and pushes to `origin main` using Ch@o's stored
git credentials. If the push fails (no credentials on that machine), tell him —
the commit is still saved locally. Do NOT handle tokens or passwords yourself.

## Worker loop (processing Claude cards) — unchanged

For cards with `assignee=Claude` and `status=todo`: `claim-next` → do the work →
save to `results/<id>.md` → `set-result` → `move <id> done`. If the task needs a
risky/irreversible step (send, publish, delete, pay, move money), do the safe
prep, `log` what needs approval, and `move <id> needs_ok` — never take the
irreversible step without Ch@o's explicit go-ahead.

When Ch@o approves a parked card (he presses **Approve & run** on the card, or
says "approve <id>"), the card is flagged `approved:true` and sent back to
`todo`. On the next worker pass, claim it, **carry out the previously-paused
action** (do not pause again), then `move <id> done`. From chat you can approve
with `python3 kanban.py approve <id>`.

## Tone

British English, warm but direct, no filler. Confirm what you did with the card
id and title.
