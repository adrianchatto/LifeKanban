#!/usr/bin/env python3
"""
notify.py - background due-date notifier for the Kanban board (macOS).

Scans board.json for cards that are overdue or approaching their due date and
fires native macOS notifications. Designed to be run on a schedule by a
LaunchAgent (see com.chatto.kanban.notify.plist), so alerts pop even when the
board and Claude are closed.

Rules (match the in-board alerts):
  - overdue: any non-done card past its due date
  - approaching: due today or tomorrow (within ~24h)
Each card alerts at most once per state, tracked in .notify-state.json, so you
are not nagged every run.

  python3 notify.py            # fire notifications
  python3 notify.py --dry-run  # print what would fire, send nothing
"""
import json
import os
import sys
import subprocess
from datetime import datetime, date

ROOT = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(ROOT, "board.json")
STATE = os.path.join(ROOT, ".notify-state.json")
DRY = "--dry-run" in sys.argv


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def save_state(state):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE)


def notify(title, message):
    if DRY:
        print("WOULD NOTIFY: [%s] %s" % (title, message))
        return
    # AppleScript display notification; escape double quotes.
    def esc(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')
    script = 'display notification "%s" with title "%s" sound name "Submarine"' % (
        esc(message), esc(title))
    try:
        subprocess.run(["osascript", "-e", script], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        print("osascript not available (not macOS?)")


def main():
    board = load_json(BOARD, {"cards": []})
    state = load_json(STATE, {})
    today = date.today()
    today_iso = today.isoformat()
    fired = 0

    for c in board.get("cards", []):
        due = c.get("due")
        if not due or c.get("status") == "done":
            continue
        try:
            d = datetime.strptime(due, "%Y-%m-%d").date()
        except ValueError:
            continue
        days = (d - today).days

        key = title = msg = None
        if days < 0:
            # overdue: re-alert once per calendar day it stays overdue
            key = "over|%s|%s" % (c["id"], today_iso)
            title = "⚠ Overdue — %s" % c.get("project", "")
            msg = "%s is %d day(s) overdue." % (c.get("title", "Card"), -days)
        elif days <= 1:
            # approaching: alert once for this due date
            key = "soon|%s|%s" % (c["id"], due)
            title = "\U0001f4c5 Due soon — %s" % c.get("project", "")
            when = "today" if days == 0 else "tomorrow"
            msg = "%s is due %s." % (c.get("title", "Card"), when)

        if key and state.get(c["id"]) != key:
            notify(title, msg)
            state[c["id"]] = key
            fired += 1

    if not DRY:
        save_state(state)
    print("notify.py: %d notification(s) %s" % (fired, "previewed" if DRY else "sent"))


if __name__ == "__main__":
    main()
