#!/usr/bin/env python3
"""LifeKanban custom worker runner using the board's saved BYOAI key."""
import json
import os
import sys
import urllib.error
import urllib.request

APP = os.environ.get("KANBAN_REPO_DIR", "/opt/lifekanban/app")
DATA = os.environ.get("KANBAN_DATA", "/opt/lifekanban/data")
sys.path.insert(0, APP)
os.environ.setdefault("KANBAN_DATA", DATA)

import auth  # noqa: E402


def pick_user():
    raw = auth._load_raw()  # local trusted worker process; needs encrypted-key access
    users = raw.get("users", [])
    for u in users:
        if u.get("api_key") and u.get("ai_provider") and u.get("ai_model"):
            key = auth.get_api_key(u["username"])
            if key:
                return u, key
    raise SystemExit("No LifeKanban user has BYOAI provider, model and API key configured.")


def post_json(url, headers, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:1000]
        raise SystemExit(f"AI API HTTP {e.code}: {detail}")


def main():
    prompt = sys.stdin.read().strip()
    if not prompt:
        raise SystemExit("No prompt supplied on stdin")
    user, api_key = pick_user()
    provider = (user.get("ai_provider") or "").lower()
    model = user.get("ai_model") or ""
    system = (
        "You are the LifeKanban worker. Complete the user's task and return only "
        "the finished deliverable in Markdown. If external approval is required, "
        "start with NEEDS_OK: and explain briefly."
    )
    if provider == "anthropic":
        raw = post_json(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            {"model": model, "max_tokens": 4000, "system": system,
             "messages": [{"role": "user", "content": prompt}]},
        )
        parts = raw.get("content") or []
        text = "\n".join(p.get("text", "") for p in parts if p.get("type") == "text" or "text" in p)
    elif provider == "openai":
        raw = post_json(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": "Bearer " + api_key},
            {"model": model, "temperature": 0.2,
             "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
        )
        choices = raw.get("choices") or []
        text = choices[0].get("message", {}).get("content", "") if choices else ""
    else:
        raise SystemExit(f"Unsupported BYOAI provider: {provider}")
    text = (text or "").strip()
    if not text:
        raise SystemExit("AI API returned an empty response")
    print(text)


if __name__ == "__main__":
    main()
