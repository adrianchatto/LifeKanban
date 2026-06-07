#!/usr/bin/env python3
"""
kanban.py - command line helper for the Kanban board.

Single source of truth is board.json next to this file. Both the local
server (via the UI) and the Claude worker use this module so all writes
go through one atomic read-modify-write path.

Usage:
  python3 kanban.py list [--assignee Claude] [--status todo]
  python3 kanban.py add "Title" [--desc "..."] [--project General]
                              [--assignee Ch@o|Claude] [--status todo]
  python3 kanban.py move <id> <todo|doing|done|needs_ok>
  python3 kanban.py assign <id> <Ch@o|Claude>
  python3 kanban.py set-result <id> <relative/path/to/result.md>
  python3 kanban.py set-due <id> <YYYY-MM-DD|clear>
  python3 kanban.py log <id> "message"
  python3 kanban.py get <id>
  python3 kanban.py claim-next        # next Claude+todo card -> doing, prints JSON

All commands print JSON to stdout so they are easy to parse.
"""
import json
import os
import sys
import time
import tempfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(ROOT, "board.json")
LOCK = os.path.join(ROOT, ".board.lock")
VALID_STATUS = ("todo", "doing", "done", "needs_ok")
VALID_ASSIGNEE = ("Ch@o", "Claude")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_recur(s):
    """Compact recurrence: 'daily' | 'weekdays' | 'weekly:0,2' (Mon=0..Sun=6)
    | 'monthly:15'. Returns a dict or None."""
    if not s:
        return None
    s = s.strip().lower()
    if s in ("daily", "weekdays"):
        return {"freq": s}
    if s.startswith("weekly"):
        days = []
        if ":" in s:
            days = [int(x) for x in s.split(":", 1)[1].split(",") if x.strip().isdigit()]
        return {"freq": "weekly", "days": days}
    if s.startswith("monthly"):
        day = 1
        if ":" in s:
            try:
                day = int(s.split(":", 1)[1])
            except ValueError:
                day = 1
        return {"freq": "monthly", "day": day}
    return None


class Lock:
    """Tiny cross-process lock via atomic directory creation."""

    def __enter__(self):
        for _ in range(100):
            try:
                os.mkdir(LOCK)
                return self
            except FileExistsError:
                time.sleep(0.05)
        # stale lock fallback
        try:
            os.rmdir(LOCK)
            os.mkdir(LOCK)
        except OSError:
            pass
        return self

    def __exit__(self, *a):
        try:
            os.rmdir(LOCK)
        except OSError:
            pass


def load():
    if not os.path.exists(BOARD):
        return {"version": 1, "updated": now(), "projects": ["General"],
                "next_id": 1, "cards": []}
    with open(BOARD, "r", encoding="utf-8") as f:
        return json.load(f)


def save(data):
    data["updated"] = now()
    fd, tmp = tempfile.mkstemp(dir=ROOT, prefix=".board.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, BOARD)


def find(data, cid):
    for c in data["cards"]:
        if c["id"] == cid:
            return c
    return None


def new_id(data):
    n = data.get("next_id", 1)
    data["next_id"] = n + 1
    return "k-%03d" % n


def cmd_list(args):
    data = load()
    cards = data["cards"]
    flt = parse_flags(args)
    if "assignee" in flt:
        cards = [c for c in cards if c["assignee"] == flt["assignee"]]
    if "status" in flt:
        cards = [c for c in cards if c["status"] == flt["status"]]
    print(json.dumps(cards, indent=2, ensure_ascii=False))


def cmd_add(args):
    if not args:
        die("add needs a title")
    title = args[0]
    flt = parse_flags(args[1:])
    with Lock():
        data = load()
        card = {
            "id": new_id(data),
            "title": title,
            "description": flt.get("desc", ""),
            "project": flt.get("project", "General"),
            "assignee": flt.get("assignee", "Ch@o"),
            "status": flt.get("status", "todo"),
            "due": flt.get("due") or None,
            "recur": parse_recur(flt.get("recur")),
            "subtasks": [],
            "skill": flt.get("skill"),
            "result_link": None,
            "created": now(),
            "updated": now(),
            "log": [],
        }
        if card["status"] not in VALID_STATUS:
            die("bad status: " + card["status"])
        if card["assignee"] not in VALID_ASSIGNEE:
            die("bad assignee: " + card["assignee"])
        data["cards"].append(card)
        if card["project"] not in data.get("projects", []):
            data.setdefault("projects", []).append(card["project"])
        save(data)
    print(json.dumps(card, indent=2, ensure_ascii=False))


def cmd_move(args):
    if len(args) < 2:
        die("move needs <id> <status>")
    cid, status = args[0], args[1]
    if status not in VALID_STATUS:
        die("bad status: " + status)
    with Lock():
        data = load()
        c = find(data, cid)
        if not c:
            die("no such card: " + cid)
        c["status"] = status
        c["updated"] = now()
        c["log"].append("%s moved to %s" % (now(), status))
        save(data)
    print(json.dumps(c, indent=2, ensure_ascii=False))


def cmd_assign(args):
    if len(args) < 2:
        die("assign needs <id> <assignee>")
    cid, who = args[0], args[1]
    if who not in VALID_ASSIGNEE:
        die("bad assignee: " + who)
    with Lock():
        data = load()
        c = find(data, cid)
        if not c:
            die("no such card: " + cid)
        c["assignee"] = who
        c["updated"] = now()
        c["log"].append("%s assigned to %s" % (now(), who))
        save(data)
    print(json.dumps(c, indent=2, ensure_ascii=False))


def cmd_set_result(args):
    if len(args) < 2:
        die("set-result needs <id> <path>")
    cid, link = args[0], args[1]
    with Lock():
        data = load()
        c = find(data, cid)
        if not c:
            die("no such card: " + cid)
        c["result_link"] = link
        c["updated"] = now()
        c["log"].append("%s result: %s" % (now(), link))
        save(data)
    print(json.dumps(c, indent=2, ensure_ascii=False))


def cmd_addb64(args):
    """Add a card from a single urlsafe-base64-encoded JSON object.

    Avoids all shell-quoting problems when called via desktop control from
    another session. The decoded JSON may contain:
      {"title","description","project","assignee","due","skill"}
    """
    import base64
    if not args:
        die("addb64 needs one base64 argument")
    try:
        raw = base64.urlsafe_b64decode(args[0].encode("ascii"))
        spec = json.loads(raw.decode("utf-8"))
    except Exception as e:
        die("could not decode addb64 arg: " + str(e))
    if not spec.get("title"):
        die("addb64 payload needs a title")
    with Lock():
        data = load()
        card = {
            "id": new_id(data),
            "title": spec["title"],
            "description": spec.get("description", ""),
            "project": spec.get("project", "General"),
            "assignee": spec.get("assignee", "Ch@o"),
            "status": spec.get("status", "todo"),
            "due": spec.get("due") or None,
            "recur": spec.get("recur") if isinstance(spec.get("recur"), dict)
                     else parse_recur(spec.get("recur")),
            "subtasks": spec.get("subtasks") or [],
            "skill": spec.get("skill"),
            "result_link": None,
            "created": now(),
            "updated": now(),
            "log": ["created via skill"],
        }
        if card["assignee"] not in VALID_ASSIGNEE:
            card["assignee"] = "Ch@o"
        if card["status"] not in VALID_STATUS:
            card["status"] = "todo"
        data["cards"].append(card)
        if card["project"] not in data.get("projects", []):
            data.setdefault("projects", []).append(card["project"])
        save(data)
    print(json.dumps(card, indent=2, ensure_ascii=False))


def cmd_set_due(args):
    if len(args) < 2:
        die("set-due needs <id> <YYYY-MM-DD|clear>")
    cid, due = args[0], args[1]
    due = None if due.lower() in ("clear", "none", "") else due
    if due:
        try:
            datetime.strptime(due, "%Y-%m-%d")
        except ValueError:
            die("due must be YYYY-MM-DD or 'clear'")
    with Lock():
        data = load()
        c = find(data, cid)
        if not c:
            die("no such card: " + cid)
        c["due"] = due
        c["updated"] = now()
        c["log"].append("%s due date %s" % (now(), due or "cleared"))
        save(data)
    print(json.dumps(c, indent=2, ensure_ascii=False))


def cmd_log(args):
    if len(args) < 2:
        die("log needs <id> <message>")
    cid, msg = args[0], " ".join(args[1:])
    with Lock():
        data = load()
        c = find(data, cid)
        if not c:
            die("no such card: " + cid)
        c["log"].append("%s %s" % (now(), msg))
        c["updated"] = now()
        save(data)
    print(json.dumps(c, indent=2, ensure_ascii=False))


def cmd_get(args):
    if not args:
        die("get needs <id>")
    c = find(load(), args[0])
    if not c:
        die("no such card: " + args[0])
    print(json.dumps(c, indent=2, ensure_ascii=False))


def cmd_claim_next(args):
    """Atomically take the oldest Claude+todo card into doing."""
    with Lock():
        data = load()
        todo = [c for c in data["cards"]
                if c["assignee"] == "Claude" and c["status"] == "todo"]
        todo.sort(key=lambda c: c["created"])
        if not todo:
            print("null")
            return
        c = todo[0]
        c["status"] = "doing"
        c["updated"] = now()
        c["log"].append("%s claimed by Claude worker" % now())
        save(data)
    print(json.dumps(c, indent=2, ensure_ascii=False))


def parse_flags(args):
    out = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            key = a[2:]
            val = args[i + 1] if i + 1 < len(args) else ""
            out[key] = val
            i += 2
        else:
            i += 1
    return out


def die(msg):
    print(json.dumps({"error": msg}))
    sys.exit(1)


COMMANDS = {
    "list": cmd_list,
    "add": cmd_add,
    "addb64": cmd_addb64,
    "move": cmd_move,
    "assign": cmd_assign,
    "set-result": cmd_set_result,
    "set-due": cmd_set_due,
    "log": cmd_log,
    "get": cmd_get,
    "claim-next": cmd_claim_next,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(0 if len(sys.argv) < 2 else 1)
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
