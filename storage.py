#!/usr/bin/env python3
"""Storage adapter for LifeKanban JSON documents.

Default mode is local JSON files. Set KANBAN_STORAGE=supabase plus
KANBAN_SUPABASE_URL/SUPABASE_URL and KANBAN_SUPABASE_SERVICE_ROLE_KEY/
SUPABASE_SERVICE_ROLE_KEY to store the same JSON documents in Supabase.
"""
import json
import os
import tempfile
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("KANBAN_DATA", ROOT)
MODE = (os.environ.get("KANBAN_STORAGE") or "files").strip().lower()
TABLE = os.environ.get("KANBAN_SUPABASE_TABLE", "lifekanban_documents")


def supabase_enabled():
    return MODE == "supabase"


def _supabase_config():
    url = (os.environ.get("KANBAN_SUPABASE_URL") or
           os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("KANBAN_SUPABASE_SERVICE_ROLE_KEY") or
           os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise RuntimeError("KANBAN_STORAGE=supabase requires KANBAN_SUPABASE_URL and KANBAN_SUPABASE_SERVICE_ROLE_KEY")
    return url, key


def doc_key(path):
    path = os.path.abspath(path)
    data = os.path.abspath(DATA)
    try:
        rel = os.path.relpath(path, data)
    except ValueError:
        rel = os.path.basename(path)
    if rel.startswith(".."):
        rel = os.path.basename(path)
    return rel.replace(os.sep, "/")


def _supabase_request(method, key=None, body=None):
    base, api_key = _supabase_config()
    url = "%s/rest/v1/%s" % (base, urlparse.quote(TABLE, safe=""))
    if key is not None:
        query = urlparse.urlencode({"key": "eq." + key, "select": "value"})
        url += "?" + query
    elif method == "POST":
        url += "?" + urlparse.urlencode({"on_conflict": "key"})
    data = None
    headers = {
        "apikey": api_key,
        "Authorization": "Bearer " + api_key,
        "Accept": "application/json",
        "User-Agent": "LifeKanban/1.0",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
    req = urlrequest.Request(url, data=data, method=method, headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urlerror.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:1000]
        raise RuntimeError("Supabase %s failed for %s: HTTP %s %s" % (method, key or TABLE, e.code, detail))


def exists(path):
    if not supabase_enabled():
        return os.path.exists(path)
    return load_json(path, None) is not None


def load_json(path, default=None):
    if supabase_enabled():
        rows = _supabase_request("GET", doc_key(path)) or []
        if not rows:
            return default
        return rows[0].get("value", default)
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data, private=False):
    if supabase_enabled():
        _supabase_request("POST", None, [{"key": doc_key(path), "value": data}])
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".%s." % os.path.basename(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    if private:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
