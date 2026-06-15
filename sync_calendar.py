#!/usr/bin/env python3
"""
sync_calendar.py - import near-term macOS Calendar events into LifeKanban.

Pulls calendar events starting between now and the next N days (default: 2)
and creates To Do cards for events that are not already represented on the
board. Designed for a LaunchAgent, so repeated runs are safe.

Usage:
  python3 sync_calendar.py              # import events due in the next 2 days
  python3 sync_calendar.py --dry-run    # preview without changing the board
  python3 sync_calendar.py --days 3
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime

import kanban


ROOT = os.path.dirname(os.path.abspath(__file__))
SWIFT_HELPER = r'''
import EventKit
import Foundation

struct Row: Encodable {
    let uid: String
    let calendar: String
    let title: String
    let start: String
    let end: String
    let location: String
    let notes: String
}

let days = max(Int(CommandLine.arguments.dropFirst().first ?? "2") ?? 2, 1)
let store = EKEventStore()
let sem = DispatchSemaphore(value: 0)
var granted = false
var accessError: Error? = nil

if #available(macOS 14.0, *) {
    store.requestFullAccessToEvents { ok, err in
        granted = ok
        accessError = err
        sem.signal()
    }
} else {
    store.requestAccess(to: .event) { ok, err in
        granted = ok
        accessError = err
        sem.signal()
    }
}
sem.wait()

if !granted {
    let detail = accessError.map { String(describing: $0) } ?? "Calendar access was not granted"
    FileHandle.standardError.write(detail.data(using: .utf8)!)
    exit(2)
}

let calendar = Calendar.current
let start = Date()
let end = calendar.date(byAdding: .day, value: days, to: start)!
let predicate = store.predicateForEvents(withStart: start, end: end, calendars: nil)
let formatter = ISO8601DateFormatter()
formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]

let rows = store.events(matching: predicate)
    .filter { !$0.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    .sorted { $0.startDate < $1.startDate }
    .map {
        Row(
            uid: $0.calendarItemExternalIdentifier ?? $0.calendarItemIdentifier,
            calendar: $0.calendar.title,
            title: $0.title,
            start: formatter.string(from: $0.startDate),
            end: formatter.string(from: $0.endDate),
            location: $0.location ?? "",
            notes: $0.notes ?? ""
        )
    }

let data = try JSONEncoder().encode(rows)
FileHandle.standardOutput.write(data)
'''


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Sync upcoming Calendar events to LifeKanban")
    parser.add_argument("--days", type=int, default=2,
                        help="window size in days from now (default: 2)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be imported without writing board.json")
    parser.add_argument("--project", default=os.environ.get("KANBAN_CALENDAR_PROJECT", "General"),
                        help="project for imported cards (default: General)")
    parser.add_argument("--assignee", default=os.environ.get("KANBAN_CALENDAR_ASSIGNEE", "Ch@o"),
                        help="assignee for imported cards (default: Ch@o)")
    parser.add_argument("--priority", default=os.environ.get("KANBAN_CALENDAR_PRIORITY", "medium"),
                        help="priority for imported cards (default: medium)")
    return parser.parse_args(argv)


def run_calendar_query(days):
    with tempfile.NamedTemporaryFile("w", suffix=".swift", delete=False) as f:
        f.write(SWIFT_HELPER)
        helper = f.name
    try:
        env = os.environ.copy()
        cache_dir = os.path.join(tempfile.gettempdir(), "lifekanban-swift-cache")
        os.makedirs(cache_dir, exist_ok=True)
        env.setdefault("CLANG_MODULE_CACHE_PATH", cache_dir)
        env.setdefault("MODULE_CACHE_DIR", cache_dir)
        proc = subprocess.run(
            ["swift", helper, str(max(days, 1))],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=90,
        )
    finally:
        try:
            os.unlink(helper)
        except OSError:
            pass
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "Calendar query failed").strip()
        raise RuntimeError(err)
    return json.loads(proc.stdout or "[]")


def event_key(event):
    uid = event["uid"] or "%s|%s" % (event["calendar"], event["title"])
    return "mac-calendar:%s:%s" % (uid, event["start"].isoformat(timespec="seconds"))


def parse_event(raw):
    start = datetime.fromisoformat(raw["start"].replace("Z", "+00:00")).astimezone()
    end = datetime.fromisoformat(raw["end"].replace("Z", "+00:00")).astimezone()
    return {
        "uid": str(raw.get("uid") or "").strip(),
        "calendar": str(raw.get("calendar") or "").strip(),
        "title": str(raw.get("title") or "").strip(),
        "start": start,
        "end": end,
        "location": str(raw.get("location") or "").strip(),
        "notes": str(raw.get("notes") or "").strip(),
    }


def card_description(event):
    lines = [
        "Imported from macOS Calendar.",
        "",
        "Calendar: %s" % (event["calendar"] or "Unknown"),
        "Starts: %s" % event["start"].strftime("%Y-%m-%d %H:%M %Z"),
        "Ends: %s" % event["end"].strftime("%Y-%m-%d %H:%M %Z"),
    ]
    if event["location"]:
        lines.append("Location: %s" % event["location"])
    if event["notes"]:
        lines.extend(["", "Notes:", event["notes"]])
    return "\n".join(lines)


def make_card(event, args):
    created = kanban.now()
    return {
        "id": None,
        "title": "Calendar: %s" % event["title"],
        "description": card_description(event),
        "project": args.project,
        "assignee": kanban.norm_assignee(args.assignee),
        "priority": kanban.norm_priority(args.priority),
        "status": "todo",
        "due": event["start"].date().isoformat(),
        "recur": None,
        "subtasks": [],
        "skill": None,
        "result_link": None,
        "calendar_source": "macOS Calendar",
        "calendar_source_key": event_key(event),
        "calendar_uid": event["uid"],
        "calendar_start": event["start"].isoformat(timespec="seconds"),
        "created": created,
        "updated": created,
        "log": ["%s imported from macOS Calendar" % created],
    }


def sync(events, args):
    with kanban.Lock():
        board = kanban.load()
        existing = {
            str(card.get("calendar_source_key"))
            for card in board.get("cards", [])
            if card.get("calendar_source_key")
        }
        created = []
        skipped = []

        for event in events:
            key = event_key(event)
            if key in existing:
                skipped.append({"title": event["title"], "reason": "already on board"})
                continue
            card = make_card(event, args)
            card["id"] = kanban.new_id(board)
            created.append(card)
            existing.add(key)
            if not args.dry_run:
                board.setdefault("cards", []).append(card)
                if card["project"] not in board.get("projects", []):
                    board.setdefault("projects", []).append(card["project"])

        if created and not args.dry_run:
            kanban.save(board)

    return created, skipped


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    if args.days < 1:
        raise SystemExit("--days must be 1 or greater")
    try:
        rows = run_calendar_query(args.days)
    except Exception as exc:
        raise SystemExit("Could not read Calendar events: %s" % exc)

    events = []
    for row in rows:
        try:
            events.append(parse_event(row))
        except Exception as exc:
            print("Skipping unreadable Calendar event row: %s" % exc, file=sys.stderr)

    created, skipped = sync(events, args)
    summary = {
        "dry_run": args.dry_run,
        "window_days": args.days,
        "events_seen": len(events),
        "created": [{"id": c["id"], "title": c["title"], "due": c["due"]} for c in created],
        "skipped": skipped,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
