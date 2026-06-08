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
                              [--priority high|medium|low]
  python3 kanban.py move <id> <todo|doing|done|needs_ok>
  python3 kanban.py assign <id> <Ch@o|Claude>
  python3 kanban.py set-result <id> <relative/path/to/result.md>
  python3 kanban.py set-due <id> <YYYY-MM-DD|clear>
  python3 kanban.py set-priority <id> <high|medium|low>
  python3 kanban.py normalise-priority  # set any none/blank card -> medium
  python3 kanban.py log <id> "message"
  python3 kanban.py get <id>
  python3 kanban.py claim-next        # next Claude+todo card -> doing, prints JSON
  python3 kanban.py requeue-stale     # recover cards orphaned in 'doing'
  python3 kanban.py user-add <name> [--admin] [--password X]  # create a login
  python3 kanban.py user-list
  python3 kanban.py user-del <name>
  python3 kanban.py user-passwd <name> [--password X]
  python3 kanban.py user-role <name> <admin|user>
  python3 kanban.py token-add <name> [label]   # create an API token (printed once)
  python3 kanban.py token-list <name>
  python3 kanban.py token-del <name> <token-id>

Remote mode: set KANBAN_API_URL (and KANBAN_API_TOKEN) to drive a board over the
HTTP API instead of the local board.json — e.g.
  KANBAN_API_URL=http://host:8787 KANBAN_API_TOKEN=lk_... python3 kanban.py add "Buy milk"

All commands print JSON to stdout so they are easy to parse.
"""
import json
import os
import sys
import time
import tempfile
from datetime import datetime, timezone

import auth  # accounts, sessions, secret storage (shared with the web server)

ROOT = os.path.dirname(os.path.abspath(__file__))
# Data dir can be relocated (e.g. a mounted Docker volume) via KANBAN_DATA.
# Defaults to the app dir so desktop/worker use is unchanged.
DATA = os.environ.get("KANBAN_DATA", ROOT)
BOARD = os.path.join(DATA, "board.json")
LOCK = os.path.join(DATA, ".board.lock")
VALID_STATUS = ("todo", "doing", "done", "needs_ok")
VALID_ASSIGNEE = ("Ch@o", "Claude")
VALID_PRIORITY = ("high", "medium", "low")

# Self-healing for orphaned cards. A worker run claims a card (todo -> doing)
# then does the work in the same pass. If that run crashes, times out, or errors
# before finishing, the card is stranded in "doing" and — because claim-next only
# ever looks at todo — would never be retried. So before claiming, we requeue any
# Claude card that has sat in "doing" longer than STALE_DOING_MIN (its `updated`
# stamp moves forward whenever the worker logs progress, so a genuinely active
# card is safe). A card that keeps failing is poison: after MAX_REQUEUES bounces
# we park it in needs_ok for Ch@o to look at instead of looping forever.
STALE_DOING_MIN = int(os.environ.get("KANBAN_STALE_MIN", "30"))
MAX_REQUEUES = int(os.environ.get("KANBAN_MAX_REQUEUES", "3"))


def norm_priority(v):
    # "none" is retired: everything defaults to medium.
    v = (v or "medium").strip().lower()
    aliases = {"hi": "high", "h": "high", "urgent": "high", "p1": "high",
               "med": "medium", "m": "medium", "p2": "medium", "normal": "medium",
               "none": "medium", "": "medium", "-": "medium",
               "lo": "low", "l": "low", "p3": "low"}
    v = aliases.get(v, v)
    return v if v in VALID_PRIORITY else "medium"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_ts(s):
    """Parse an ISO timestamp written by now() (or with millis). Returns an
    aware datetime, or None if it can't be parsed."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def requeue_stale(data):
    """Recover Claude cards orphaned in 'doing'. Mutates `data` in place and
    returns a list of {id,title,action,age_min} describing what changed. The
    caller is responsible for save() (so it can run inside an existing Lock)."""
    actions = []
    cutoff_secs = STALE_DOING_MIN * 60
    current = datetime.now(timezone.utc)
    for c in data.get("cards", []):
        if c.get("assignee") != "Claude" or c.get("status") != "doing":
            continue
        ts = parse_ts(c.get("updated"))
        if ts is None:
            continue
        age = (current - ts).total_seconds()
        if age < cutoff_secs:
            continue
        age_min = int(age // 60)
        count = int(c.get("requeue_count", 0)) + 1
        c["requeue_count"] = count
        c["updated"] = now()
        if count > MAX_REQUEUES:
            c["status"] = "needs_ok"
            c["log"].append(
                "%s stuck in doing and requeued %d times — parked in needs_ok "
                "for review (worker keeps failing this card)" % (now(), count - 1))
            actions.append({"id": c["id"], "title": c["title"],
                            "action": "parked_needs_ok", "age_min": age_min})
        else:
            c["status"] = "todo"
            c["log"].append(
                "%s requeued to todo after %d min stuck in doing "
                "(recovery %d/%d)" % (now(), age_min, count, MAX_REQUEUES))
            actions.append({"id": c["id"], "title": c["title"],
                            "action": "requeued_todo", "age_min": age_min})
    return actions


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


# Remote mode: when KANBAN_API_URL is set, the board is read/written over the
# authenticated HTTP API (with KANBAN_API_TOKEN) instead of the local file. This
# lets a worker on another machine drive a Dockerised board. All the card
# commands below are unchanged — only load()/save() change destination.
API_URL = os.environ.get("KANBAN_API_URL")
API_TOKEN = os.environ.get("KANBAN_API_TOKEN")


def _remote():
    return bool(API_URL)


def _api(method, body=None):
    import urllib.request
    import urllib.error
    url = API_URL.rstrip("/") + "/api/board"
    headers = {"Authorization": "Bearer " + (API_TOKEN or "")}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read()).get("error", "")
        except Exception:
            pass
        die("API %s %s: %s %s" % (method, url, e.code, detail))
    except Exception as e:
        die("could not reach API at %s: %s" % (url, e))


def load():
    if _remote():
        return _api("GET")
    if not os.path.exists(BOARD):
        return {"version": 1, "updated": now(), "projects": ["General"],
                "next_id": 1, "cards": []}
    with open(BOARD, "r", encoding="utf-8") as f:
        return json.load(f)


def save(data):
    data["updated"] = now()
    if _remote():
        _api("POST", data)
        return
    fd, tmp = tempfile.mkstemp(dir=DATA, prefix=".board.", suffix=".tmp")
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
            "priority": norm_priority(flt.get("priority")),
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
        if status == "done":
            # Successful completion clears the recovery counter so a card that
            # was rescued once isn't penalised if it's ever reworked later.
            c.pop("requeue_count", None)
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
            "priority": norm_priority(spec.get("priority")),
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


def cmd_normalise_priority(args):
    """One-off migration: any card with a missing, blank or 'none' priority
    is set to 'medium'. Quiet (no per-card log spam) — records one summary
    line on each migrated card."""
    changed = []
    with Lock():
        data = load()
        for c in data.get("cards", []):
            old = (c.get("priority") or "").strip().lower()
            if old not in VALID_PRIORITY:
                c["priority"] = "medium"
                c["updated"] = now()
                c.setdefault("log", []).append(
                    "%s priority defaulted to medium (none retired)" % now())
                changed.append(c["id"])
        if changed:
            save(data)
    print(json.dumps({"migrated": changed, "count": len(changed)}, indent=2))


def cmd_set_priority(args):
    if len(args) < 2:
        die("set-priority needs <id> <high|medium|low>")
    cid = args[0]
    pri = norm_priority(args[1])
    with Lock():
        data = load()
        c = find(data, cid)
        if not c:
            die("no such card: " + cid)
        c["priority"] = pri
        c["updated"] = now()
        c["log"].append("%s priority set to %s" % (now(), pri))
        save(data)
    print(json.dumps(c, indent=2, ensure_ascii=False))


def cmd_approve(args):
    """Approve a needs_ok card: flag approved and send back to todo so the
    Claude worker reclaims it and performs the previously-paused action."""
    if not args:
        die("approve needs <id>")
    cid = args[0]
    with Lock():
        data = load()
        c = find(data, cid)
        if not c:
            die("no such card: " + cid)
        c["approved"] = True
        c["status"] = "todo"
        c["assignee"] = "Claude"
        c["updated"] = now()
        c["log"].append("%s approved by Ch@o — proceed with the action" % now())
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
    """Atomically take the oldest Claude+todo card into doing.

    First recovers any card orphaned in 'doing' (see requeue_stale) so a crashed
    earlier pass can't strand work forever. A freshly requeued card becomes
    eligible to be claimed in this same pass."""
    with Lock():
        data = load()
        recovered = requeue_stale(data)
        todo = [c for c in data["cards"]
                if c["assignee"] == "Claude" and c["status"] == "todo"]
        todo.sort(key=lambda c: c["created"])
        if not todo:
            if recovered:
                save(data)
                print(json.dumps({"claimed": None, "recovered": recovered},
                                 indent=2, ensure_ascii=False))
                return
            print("null")
            return
        c = todo[0]
        c["status"] = "doing"
        c["updated"] = now()
        c["log"].append("%s claimed by Claude worker" % now())
        save(data)
    print(json.dumps(c, indent=2, ensure_ascii=False))


def cmd_requeue_stale(args):
    """Manually run the stale-doing recovery (also runs automatically inside
    claim-next). Prints what it changed."""
    with Lock():
        data = load()
        recovered = requeue_stale(data)
        if recovered:
            save(data)
    print(json.dumps({"recovered": recovered, "count": len(recovered),
                      "stale_after_min": STALE_DOING_MIN}, indent=2,
                     ensure_ascii=False))


# --------------------------------------------------------------------------- #
# User administration (bootstrap + backstop for the in-app admin panel).
# --------------------------------------------------------------------------- #
def _read_password(flt):
    pw = flt.get("password")
    if pw:
        return pw
    import getpass
    p1 = getpass.getpass("Password (min 8 chars): ")
    p2 = getpass.getpass("Confirm password: ")
    if p1 != p2:
        die("passwords do not match")
    return p1


def cmd_user_add(args):
    """user-add <username> [--admin] [--role user|admin] [--password X]
       [--must-change]. Prompts for the password if --password is omitted."""
    if not args:
        die("user-add needs a username")
    username = args[0]
    # Strip bare boolean flags so they don't get mistaken for --key <value> pairs.
    rest = [a for a in args[1:] if a not in ("--admin", "--must-change")]
    flt = parse_flags(rest)
    role = "admin" if "--admin" in args else flt.get("role", "user")
    pw = _read_password(flt)
    try:
        pub = auth.create_user(username, pw, role=role,
                               must_change=("--must-change" in args))
    except ValueError as e:
        die(str(e))
    print(json.dumps(pub, indent=2, ensure_ascii=False))


def cmd_user_list(args):
    print(json.dumps(auth.list_users(), indent=2, ensure_ascii=False))


def cmd_user_del(args):
    if not args:
        die("user-del needs a username")
    try:
        auth.delete_user(args[0])
    except ValueError as e:
        die(str(e))
    print(json.dumps({"deleted": args[0]}, indent=2))


def cmd_user_passwd(args):
    """user-passwd <username> [--password X] [--must-change]"""
    if not args:
        die("user-passwd needs a username")
    rest = [a for a in args[1:] if a not in ("--must-change",)]
    flt = parse_flags(rest)
    pw = _read_password(flt)
    try:
        auth.set_password(args[0], pw, must_change=("--must-change" in args))
    except ValueError as e:
        die(str(e))
    print(json.dumps({"password_updated": args[0]}, indent=2))


def cmd_user_role(args):
    """user-role <username> <admin|user>"""
    if len(args) < 2:
        die("user-role needs <username> <admin|user>")
    try:
        auth.set_role(args[0], args[1])
    except ValueError as e:
        die(str(e))
    print(json.dumps({"username": args[0], "role": args[1]}, indent=2))


def cmd_token_add(args):
    """token-add <username> [name]  — create an API token for programmatic
    access (e.g. the remote Claude worker). Prints the token ONCE."""
    if not args:
        die("token-add needs a username")
    name = args[1] if len(args) > 1 else "worker"
    try:
        tok, meta = auth.create_token(args[0], name)
    except ValueError as e:
        die(str(e))
    print(json.dumps({"token": tok, "id": meta["id"], "name": meta["name"],
                      "username": args[0],
                      "note": "Save this token now — it is not stored and cannot be shown again."},
                     indent=2, ensure_ascii=False))


def cmd_token_list(args):
    if not args:
        die("token-list needs a username")
    try:
        print(json.dumps(auth.list_tokens(args[0]), indent=2, ensure_ascii=False))
    except ValueError as e:
        die(str(e))


def cmd_token_del(args):
    if len(args) < 2:
        die("token-del needs <username> <token-id>")
    try:
        auth.revoke_token(args[0], args[1])
    except ValueError as e:
        die(str(e))
    print(json.dumps({"revoked": args[1], "username": args[0]}, indent=2))


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
    "set-priority": cmd_set_priority,
    "normalise-priority": cmd_normalise_priority,
    "approve": cmd_approve,
    "log": cmd_log,
    "get": cmd_get,
    "claim-next": cmd_claim_next,
    "requeue-stale": cmd_requeue_stale,
    "user-add": cmd_user_add,
    "user-list": cmd_user_list,
    "user-del": cmd_user_del,
    "user-passwd": cmd_user_passwd,
    "user-role": cmd_user_role,
    "token-add": cmd_token_add,
    "token-list": cmd_token_list,
    "token-del": cmd_token_del,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(0 if len(sys.argv) < 2 else 1)
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
