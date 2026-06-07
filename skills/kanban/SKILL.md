---
name: kanban
description: >
  Manage Ch@o's Kanban board from chat. Use whenever Ch@o says "build an action
  for that", "add that to my board", "put a card on the board", "what's on my
  board", "process my board", or asks to move/assign/complete a Kanban card.
  The board lives in the Kanban project folder and is the single source of truth.
---

# Kanban board control

Ch@o's board is a folder of plain files. The single source of truth is
`board.json` in the Kanban project folder. A local web app renders it; a
scheduled worker processes cards assigned to Claude. Everything you do goes
through the same CLI so the UI and worker stay consistent.

## Where things are

- `board.json` — the cards.
- `kanban.py` — the CLI. **Always use it**; never hand-edit `board.json`.
- `results/` — where you save deliverables; the board links to them.
- `server.py`, `index.html`, `Kanban Board.app` — the app (don't touch when managing cards).

Run the CLI from the Kanban folder, e.g. `python3 kanban.py list`.

## Card shape

`id, title, description, project, assignee (Ch@o|Claude), status
(todo|doing|done|needs_ok), skill, result_link, created, updated, log[]`.

## The two phrases that matter

**"Build an action for that"** (or "add that to my board") → create a card.
Capture what was being discussed into a clear, self-contained title +
description, pick the project, and set the assignee:
- If Ch@o will do it → `--assignee Ch@o`.
- If you should do it → `--assignee Claude` (the worker will pick it up).

```
python3 kanban.py add "Draft NEO-015 weekly status" \
  --desc "One-paragraph status: progress, risks, next steps. Save as .docx." \
  --project NEO-015 --assignee Claude
```

**"Process my board"** → run the worker loop below now, in this session,
instead of waiting for the schedule.

## Worker loop (processing Claude cards)

Repeat until no Claude `todo` cards remain:

1. `python3 kanban.py claim-next` — atomically moves the oldest Claude+todo
   card to `doing` and prints it. Stop if it prints `null`.
2. Do the work the card describes. Reuse a matching skill in `skills/` if one
   exists; otherwise just do the task well. Honour Ch@o's writing rules.
3. Save the deliverable to `results/<id>.<ext>` (markdown is rendered nicely by
   the board; .docx/.pdf/.xlsx also link and download). Then:
   `python3 kanban.py set-result <id> results/<id>.md`
4. Move to done: `python3 kanban.py move <id> done`.

### Pause-for-confirmation rule (important)

If completing a card requires a **risky or irreversible action** — sending an
email, posting a message, publishing, deleting, moving money, anything external
that can't be undone — do **not** do that step. Instead:

1. Do all the safe prep (draft the email, prepare the file, etc.) and save it to
   `results/<id>...`.
2. `python3 kanban.py set-result <id> results/<id>.md`
3. `python3 kanban.py log <id> "Prepared. NEEDS OK before <the irreversible step>."`
4. `python3 kanban.py move <id> needs_ok`

The card sits in the **Needs OK** column until Ch@o gives an explicit go-ahead.
This matches Ch@o's standing rule: never send, publish, or delete without
explicit confirmation in the current conversation.

## Turning a repeated action into a reusable skill

When Ch@o asks you to "make a skill" for an action, or you notice the same kind
of card recurring, copy `skills/_template/SKILL.md` to
`skills/<action-name>/SKILL.md`, fill it in, and reference that skill's name in
the card's `skill` field via the CLI flag `--skill <name>` on `add`. To make a
skill available globally in Cowork (not just this folder) it must live in the
Cowork skills directory — tell Ch@o and offer to register it there.

## Notes

- Never touch cards with `assignee == "Ch@o"` in the worker loop.
- Keep titles imperative and descriptions self-contained — the worker runs in a
  fresh session with no memory of the chat that created the card.
- British English, warm-but-direct tone, no filler. (Ch@o's writing rules.)
