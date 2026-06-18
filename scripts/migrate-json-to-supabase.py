#!/usr/bin/env python3
"""Copy local LifeKanban JSON documents into Supabase.

Usage on the VM after running supabase/schema.sql:
  KANBAN_DATA=/opt/lifekanban/data \
  KANBAN_STORAGE=supabase \
  KANBAN_SUPABASE_URL=https://...supabase.co \
  KANBAN_SUPABASE_SERVICE_ROLE_KEY=... \
  python3 scripts/migrate-json-to-supabase.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import storage  # noqa: E402

DATA = Path(os.environ.get("KANBAN_DATA", ROOT)).resolve()


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def migrate(path):
    data = read_json(path)
    storage.save_json(str(path), data, private=path.name in {"users.json", "app_settings.json"})
    print("migrated", storage.doc_key(str(path)))


def main():
    if not storage.supabase_enabled():
        raise SystemExit("Set KANBAN_STORAGE=supabase plus Supabase URL/key before migrating.")
    paths = []
    for name in ("users.json", "app_settings.json", "board.json"):
        p = DATA / name
        if p.exists():
            paths.append(p)
    boards = DATA / "boards"
    if boards.exists():
        paths.extend(sorted(boards.glob("*.json")))
    if not paths:
        raise SystemExit("No JSON documents found under %s" % DATA)
    for path in paths:
        migrate(path)
    print("done: %d document(s) migrated to Supabase" % len(paths))


if __name__ == "__main__":
    main()
