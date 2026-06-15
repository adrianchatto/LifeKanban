#!/usr/bin/env python3
"""
server.py - tiny stdlib HTTP server for the Kanban board.

Serves:
  GET  /                -> index.html (the board UI)
  GET  /api/board       -> board.json
  POST /api/board       -> overwrite board.json (full board)
  GET  /results/<file>  -> result files written by the AI worker
  POST /api/upload      -> save a card attachment (raw body, X-Filename header)
  GET  /attachments/<f> -> serve an uploaded attachment

No third-party dependencies. Runs on localhost only.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import tempfile
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import parse as urlparse
from urllib import request as urlrequest
from urllib import error as urlerror
from urllib import parse as urlparse

import auth

ROOT = os.path.dirname(os.path.abspath(__file__))
# Data (board.json + results) can live outside the app dir so it can be mounted
# as a Docker volume. Defaults to the app dir, so local/desktop use is unchanged.
DATA = os.environ.get("KANBAN_DATA", ROOT)
BOARD = os.path.join(DATA, "board.json")
RESULTS = os.path.join(DATA, "results")
ATTACH = os.path.join(DATA, "attachments")
# Reject attachments larger than this (bytes). Screenshots are well under it.
MAX_UPLOAD = int(os.environ.get("KANBAN_MAX_UPLOAD", str(25 * 1024 * 1024)))
# Bind to 127.0.0.1 by default (desktop); set KANBAN_HOST=0.0.0.0 in containers.
HOST = os.environ.get("KANBAN_HOST", "127.0.0.1")
PORT = int(os.environ.get("KANBAN_PORT", "8787"))

# Session cookie config. Set KANBAN_SECURE_COOKIES=1 when serving over HTTPS
# (you MUST do this in any deployment reachable beyond localhost).
COOKIE_NAME = "kanban_session"
SECURE_COOKIES = os.environ.get("KANBAN_SECURE_COOKIES", "0") == "1"


def guide_url():
    """URL of the published Notion user guide, surfaced to admins in the UI.
    Set via KANBAN_GUIDE_URL or a `.guide_url` file in the data dir."""
    u = os.environ.get("KANBAN_GUIDE_URL")
    if u:
        return u.strip()
    p = os.path.join(DATA, ".guide_url")
    if os.path.exists(p):
        try:
            return (open(p, encoding="utf-8").read().strip() or None)
        except OSError:
            return None
    return None
# Static UI shells served without a session (the data behind them is still gated
# by the /api/* auth checks).
PUBLIC_PAGES = ("login.html", "settings.html", "admin.html", "features.html")
AI_PROVIDERS = ("anthropic", "openai")

MIME = {".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8",
        ".html": "text/html; charset=utf-8", ".json": "application/json",
        ".pdf": "application/pdf", ".png": "image/png", ".csv": "text/csv",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
        ".webp": "image/webp", ".svg": "image/svg+xml", ".heic": "image/heic",
        ".css": "text/css; charset=utf-8",
        ".webmanifest": "application/manifest+json", ".ico": "image/x-icon"}

# Static files served from the project root (for PWA install)
STATIC = ("manifest.webmanifest", "icon-192.png", "icon-512.png",
          "icon-maskable-512.png", "favicon.ico", "theme.css")
APP_SETTINGS = os.path.join(DATA, "app_settings.json")
GH_BINS = ("/opt/homebrew/bin/gh", "gh")
CRON_NAMED_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
CRON_NAMED_DAYS = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}
CRON_MACROS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}


def _load_app_settings():
    if not os.path.exists(APP_SETTINGS):
        return {"version": 1}
    try:
        with open(APP_SETTINGS, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"version": 1}
    except Exception:
        return {"version": 1}


def _save_app_settings(data):
    os.makedirs(DATA, exist_ok=True)
    data["version"] = data.get("version", 1)
    fd, tmp = tempfile.mkstemp(dir=DATA, prefix=".app-settings.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, APP_SETTINGS)
    try:
        os.chmod(APP_SETTINGS, 0o600)
    except OSError:
        pass


def _pushover_public(settings=None):
    settings = settings or _load_app_settings()
    po = settings.get("pushover") or {}
    user_key = None
    app_token = None
    try:
        user_key = auth.decrypt_secret(po.get("user_key")) if po.get("user_key") else None
        app_token = auth.decrypt_secret(po.get("app_token")) if po.get("app_token") else None
    except Exception:
        pass
    return {
        "enabled": bool(po.get("enabled") and user_key and app_token),
        "has_user_key": bool(user_key),
        "has_app_token": bool(app_token),
        "user_key_last4": user_key[-4:] if user_key else None,
        "app_token_last4": app_token[-4:] if app_token else None,
    }


def _cron_field_values(field, min_v, max_v, names=None):
    names = names or {}
    raw = (field or "").lower()
    values = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            raise ValueError("empty cron field")
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError("cron step must be positive")
        else:
            base, step = part, 1
        if base == "*":
            start, end = min_v, max_v
        elif "-" in base:
            a, b = base.split("-", 1)
            start = names.get(a, None)
            end = names.get(b, None)
            start = int(a) if start is None else start
            end = int(b) if end is None else end
        else:
            start = names.get(base, None)
            start = int(base) if start is None else start
            end = start
        if start < min_v or end > max_v or start > end:
            raise ValueError("cron field out of range")
        values.update(range(start, end + 1, step))
    return values


def _cron_next_run(expr):
    fields = CRON_MACROS.get(expr.lower(), expr).split()
    if len(fields) != 5:
        return None
    try:
        minutes = _cron_field_values(fields[0], 0, 59)
        hours = _cron_field_values(fields[1], 0, 23)
        doms = _cron_field_values(fields[2], 1, 31)
        months = _cron_field_values(fields[3], 1, 12, CRON_NAMED_MONTHS)
        dows = _cron_field_values(fields[4], 0, 7, CRON_NAMED_DAYS)
    except Exception:
        return None
    if 7 in dows:
        dows.add(0)
        dows.discard(7)

    now = time.localtime()
    candidate = int(time.mktime((now.tm_year, now.tm_mon, now.tm_mday,
                                 now.tm_hour, now.tm_min, 0,
                                 now.tm_wday, now.tm_yday, now.tm_isdst))) + 60
    # Minute-by-minute is small, dependency-free, and handles daylight-saving
    # transitions through localtime/mktime without a cron-specific scheduler.
    limit = candidate + 366 * 24 * 60 * 60
    while candidate <= limit:
        t = time.localtime(candidate)
        cron_dow = (t.tm_wday + 1) % 7
        if (t.tm_min in minutes and t.tm_hour in hours and t.tm_mday in doms
                and t.tm_mon in months and cron_dow in dows):
            return time.strftime("%Y-%m-%d %H:%M", t)
        candidate += 60
    return None


def _cron_description(expr):
    e = expr.lower()
    labels = {
        "@reboot": "At system startup",
        "@yearly": "Yearly",
        "@annually": "Yearly",
        "@monthly": "Monthly",
        "@weekly": "Weekly",
        "@daily": "Daily",
        "@midnight": "Daily at midnight",
        "@hourly": "Hourly",
    }
    if e in labels:
        return labels[e]
    parts = expr.split()
    if len(parts) != 5:
        return expr
    minute, hour, dom, month, dow = parts
    if expr == "* * * * *":
        return "Every minute"
    if minute.startswith("*/") and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return "Every %s minutes" % minute[2:]
    if minute == "0" and hour.startswith("*/") and dom == "*" and month == "*" and dow == "*":
        return "Every %s hours" % hour[2:]
    if minute == "0" and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return "Hourly"
    if dom == "*" and month == "*" and dow == "*":
        return "Daily at %s:%s" % (hour.zfill(2), minute.zfill(2))
    return expr


def _parse_crontab_text(text, source, system_style=False):
    entries = []
    variables = {}
    for idx, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", line):
            key, val = line.split("=", 1)
            variables[key.strip()] = val.strip().strip('"')
            continue
        parts = line.split(None, 6 if system_style else 5)
        if parts[0].startswith("@"):
            if parts[0].lower() == "@reboot":
                user = parts[1] if system_style and len(parts) >= 3 else None
                command = parts[2] if system_style and len(parts) >= 3 else (parts[1] if len(parts) >= 2 else "")
                schedule = parts[0]
            else:
                if system_style:
                    if len(parts) < 3:
                        continue
                    schedule, user, command = parts[0], parts[1], parts[2]
                else:
                    if len(parts) < 2:
                        continue
                    schedule, user, command = parts[0], None, parts[1]
            next_run = None if schedule.lower() == "@reboot" else _cron_next_run(schedule)
        else:
            if system_style:
                if len(parts) < 7:
                    continue
                schedule = " ".join(parts[:5])
                user = parts[5]
                command = parts[6]
            else:
                if len(parts) < 6:
                    continue
                schedule = " ".join(parts[:5])
                user = None
                command = parts[5]
            next_run = _cron_next_run(schedule)
        entries.append({
            "source": source,
            "line": idx,
            "schedule": schedule,
            "description": _cron_description(schedule),
            "next_run": next_run,
            "user": user,
            "command": command,
        })
    return entries, variables


def list_cron_jobs():
    entries = []
    errors = []
    sources = []
    try:
        proc = subprocess.run(["crontab", "-l"], text=True, capture_output=True, timeout=10)
        if proc.returncode == 0:
            found, _vars = _parse_crontab_text(proc.stdout, "current user crontab")
            entries.extend(found)
            sources.append("current user crontab")
        else:
            msg = (proc.stderr or proc.stdout or "").strip()
            if msg and "no crontab" not in msg.lower():
                errors.append("current user crontab: " + msg[:300])
    except Exception as e:
        errors.append("current user crontab: " + str(e))

    system_files = ["/etc/crontab"]
    cron_d = "/etc/cron.d"
    try:
        if os.path.isdir(cron_d):
            for name in sorted(os.listdir(cron_d)):
                if name.startswith(".") or name.endswith(("~", ".bak", ".swp")):
                    continue
                system_files.append(os.path.join(cron_d, name))
    except OSError as e:
        errors.append("/etc/cron.d: " + str(e))

    for fp in system_files:
        if not os.path.exists(fp) or not os.path.isfile(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            found, _vars = _parse_crontab_text(text, fp, system_style=True)
            entries.extend(found)
            sources.append(fp)
        except OSError as e:
            errors.append(fp + ": " + str(e))

    entries.sort(key=lambda e: ((e.get("next_run") or "9999"), e.get("source") or "", e.get("line") or 0))
    return {"entries": entries, "errors": errors, "sources": sources}


def send_pushover(title, message, url=None):
    settings = _load_app_settings()
    po = settings.get("pushover") or {}
    if not po.get("enabled"):
        return False
    try:
        user_key = auth.decrypt_secret(po.get("user_key"))
        app_token = auth.decrypt_secret(po.get("app_token"))
    except Exception:
        return False
    if not user_key or not app_token:
        return False
    payload = {"token": app_token, "user": user_key, "title": title[:250], "message": message[:1024]}
    if url:
        payload["url"] = url
        payload["url_title"] = "Open LifeKanban"
    data = urlparse.urlencode(payload).encode("utf-8")
    req = urlrequest.Request("https://api.pushover.net/1/messages.json", data=data, method="POST",
                             headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlrequest.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def github_issue_url(card):
    issue_pattern = r"(?:https?://)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/\d+"
    issue_url = str(card.get("github_issue_url") or "").strip()
    match = re.search(issue_pattern, issue_url)
    if match:
        url = match.group(0)
        return url if url.startswith(("http://", "https://")) else "https://" + url

    desc = str(card.get("description") or "")
    match = re.search(issue_pattern, desc)
    if match:
        url = match.group(0)
        return url if url.startswith(("http://", "https://")) else "https://" + url

    repo = str(card.get("github_repo") or "").strip()
    number = str(card.get("github_issue_number") or "").strip()
    if re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repo) and number.isdigit():
        return "https://github.com/%s/issues/%s" % (repo, number)
    return None


def _one_line(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def completion_title(card):
    title = _one_line(card.get("title"))
    prefix = "LifeKanban done: " + str(card.get("id", "card"))
    if title:
        return (prefix + " - " + title)[:250]
    return prefix[:250]


def completion_message(card):
    lines = [
        "Card: " + str(card.get("id", "unknown")),
        "Title: " + (_one_line(card.get("title")) or "AI card completed"),
    ]
    project = _one_line(card.get("project"))
    if project:
        lines.append("Project: " + project)
    priority = _one_line(card.get("priority"))
    if priority:
        lines.append("Priority: " + priority)
    due = _one_line(card.get("due"))
    if due:
        lines.append("Due: " + due)
    description = str(card.get("description") or "").strip()
    if description:
        lines.extend(["", "Description:", description])
    result = _one_line(card.get("result"))
    if result:
        lines.extend(["", "Result: " + result])
    issue_url = github_issue_url(card)
    if issue_url:
        lines.extend(["", "GitHub issue: " + issue_url])
    return "\n".join(lines)[:1024]


def is_ai_assignee(v):
    return v in ("AI", "Claude")


def _ai_done_notifications(old_board, new_board):
    old_status = {}
    for c in (old_board or {}).get("cards", []):
        if isinstance(c, dict) and c.get("id"):
            old_status[c.get("id")] = c.get("status")
    out = []
    for c in (new_board or {}).get("cards", []):
        if not isinstance(c, dict) or not c.get("id"):
            continue
        cid = c.get("id")
        if cid not in old_status:
            continue
        if c.get("status") != "done" or old_status.get(cid) == "done":
            continue
        if is_ai_assignee(c.get("assignee")):
            out.append(c)
    return out


def notify_ai_done(cards):
    if not cards:
        return

    def run():
        for c in cards:
            title = completion_title(c)
            message = completion_message(c)
            send_pushover(title, message, "http://127.0.0.1:8787/")

    threading.Thread(target=run, daemon=True).start()


def github_issue_ref(card):
    issue_url = str(card.get("github_issue_url") or "")
    if not issue_url:
        issue_url = str(card.get("description") or "")
    match = re.search(r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/(\d+)",
                      issue_url)
    if match:
        return match.group(1), match.group(2)

    repo = str(card.get("github_repo") or "").strip()
    number = str(card.get("github_issue_number") or "").strip()
    if re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repo) and number.isdigit():
        return repo, number
    return None, None


def close_github_issue(card):
    repo, number = github_issue_ref(card)
    if not repo or not number or card.get("github_issue_closed_at"):
        return False

    message = "Completed by LifeKanban card %s." % card.get("id", "unknown")
    last_error = ""
    for gh in GH_BINS:
        cmd = [gh, "issue", "close", number, "--repo", repo,
               "--reason", "completed", "--comment", message]
        try:
            proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=30)
        except FileNotFoundError:
            continue
        except Exception as e:
            last_error = str(e)
            continue
        if proc.returncode == 0:
            card["github_issue_closed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            card.setdefault("log", []).append(
                "%s closed GitHub issue %s#%s" %
                (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), repo, number))
            return True
        last_error = (proc.stderr or proc.stdout or "gh issue close failed").strip()

    card.setdefault("log", []).append(
        "%s could not close GitHub issue %s#%s: %s" %
        (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         repo, number, last_error[:300] or "gh not available"))
    return False


def close_newly_done_github_issues(old_board, new_board):
    old_status = {}
    for c in (old_board or {}).get("cards", []):
        if isinstance(c, dict) and c.get("id"):
            old_status[c.get("id")] = c.get("status")
    for c in (new_board or {}).get("cards", []):
        if not isinstance(c, dict) or not c.get("id"):
            continue
        cid = c.get("id")
        if cid not in old_status:
            continue
        if c.get("status") == "done" and old_status.get(cid) != "done":
            close_github_issue(c)



def wake_ai_worker():
    """Ask the local AI worker to run now.

    Desktop installs use launchd, so prefer kicking that job. If the app is
    running without the LaunchAgent installed, start one detached worker pass.
    The worker script has its own lock, so repeated nudges are harmless.
    """
    def run():
        uid = str(os.getuid())
        job = "gui/%s/com.chatto.kanban.worker" % uid
        env = os.environ.copy()
        env["PATH"] = ("/Applications/Codex.app/Contents/Resources:"
                       "/Users/adrianchatto/.local/bin:/opt/homebrew/bin:"
                       "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin")
        try:
            subprocess.run(["/bin/launchctl", "kickstart", job],
                           cwd=ROOT, env=env, timeout=10,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,
                           check=True)
            return
        except Exception:
            pass

        worker = os.path.join(ROOT, "worker", "worker.sh")
        if not os.path.exists(worker):
            return
        try:
            log = open(os.path.join(ROOT, "worker", "worker.log"), "ab")
            subprocess.Popen(["/bin/bash", worker], cwd=ROOT, env=env,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass  # quiet

    # ----- auth helpers ----------------------------------------------------- #
    def _json(self, code, obj, cookie=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location):
        body = b""
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw or b"{}")
        except Exception:
            return None

    def _cookie_token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            jar = SimpleCookie()
            jar.load(raw)
            if COOKIE_NAME in jar:
                return jar[COOKIE_NAME].value
        except Exception:
            return None
        return None

    def _set_cookie(self, token, clear=False):
        parts = ["%s=%s" % (COOKIE_NAME, "" if clear else token),
                 "Path=/", "HttpOnly", "SameSite=Strict"]
        if SECURE_COOKIES:
            parts.append("Secure")
        if clear:
            parts.append("Max-Age=0")
        else:
            parts.append("Max-Age=%d" % auth.SESSION_TTL)
        return "; ".join(parts)

    def current_session(self):
        return auth.get_session(self._cookie_token())

    def _bearer_token(self):
        authz = self.headers.get("Authorization", "")
        if authz.startswith("Bearer "):
            return authz[7:].strip()
        return self.headers.get("X-Kanban-Token")

    def resolve_session(self):
        """Authenticate a request by API token first (programmatic clients like
        the AI worker), then by browser session cookie."""
        tok = self._bearer_token()
        if tok:
            u = auth.verify_token(tok)
            if u:
                return {"uid": u["id"], "username": u["username"],
                        "role": u.get("role", "user"), "csrf": None, "via": "token"}
        s = auth.get_session(self._cookie_token())
        if s:
            s = dict(s)
            s["via"] = "cookie"
        return s

    def require_user(self):
        """Return the session dict, or send 401 and return None."""
        s = self.resolve_session()
        if not s:
            self._json(401, {"error": "not authenticated"})
            return None
        return s

    def require_admin(self):
        s = self.require_user()
        if not s:
            return None
        if s.get("role") != "admin":
            self._json(403, {"error": "admin only"})
            return None
        return s

    def check_csrf(self, session):
        # API-token clients aren't subject to CSRF: the token isn't a cookie the
        # browser sends automatically, so cross-site forgery doesn't apply.
        if session and session.get("via") == "token":
            return True
        token = self.headers.get("X-Kanban-CSRF", "")
        import hmac as _hmac
        if not session or not _hmac.compare_digest(token, session.get("csrf", "")):
            self._json(403, {"error": "bad or missing CSRF token"})
            return False
        return True

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/" or path == "/index.html":
            if not self.current_session():
                self._redirect("/login.html")
                return
            try:
                with open(os.path.join(ROOT, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, "index.html missing", "text/plain")
            return
        if path == "/pomodoro.html":
            if not self.current_session():
                self._redirect("/login.html")
                return
            try:
                with open(os.path.join(ROOT, "pomodoro.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, "pomodoro.html missing", "text/plain")
            return
        # Public UI shells (login/settings/admin). Their data is gated by /api.
        page = path.lstrip("/")
        if page in PUBLIC_PAGES:
            fp = os.path.join(ROOT, page)
            if os.path.exists(fp):
                with open(fp, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            else:
                self._send(404, "not found", "text/plain")
            return
        # ----- auth / account / admin read endpoints ----- #
        if path == "/api/me":
            s = self.require_user()
            if not s:
                return
            u = auth.find_user(s["username"])
            if not u:
                self._json(401, {"error": "not authenticated"})
                return
            pub = auth._public(u)
            self._json(200, {"user": pub, "csrf": s["csrf"],
                             "ai_provider": u.get("ai_provider"),
                             "ai_model": u.get("ai_model"),
                             "guide_url": (guide_url() if s.get("role") == "admin" else None)})
            return
        if path == "/api/admin/users":
            s = self.require_admin()
            if not s:
                return
            self._json(200, {"users": auth.list_users()})
            return
        if path == "/api/admin/integrations":
            s = self.require_admin()
            if not s:
                return
            self._json(200, {"pushover": _pushover_public()})
            return
        if path == "/api/admin/cron":
            s = self.require_admin()
            if not s:
                return
            self._json(200, list_cron_jobs())
            return
        if path == "/api/board":
            s = self.require_user()
            if not s:
                return
            u = auth.find_user(s["username"])
            try:
                with open(auth.board_path(u), "rb") as f:
                    self._send(200, f.read(), "application/json")
            except FileNotFoundError:
                self._send(200, json.dumps(
                    {"version": 1, "projects": ["General"], "next_id": 1,
                     "cards": []}), "application/json")
            return
        if path.startswith("/results/"):
            s = self.require_user()
            if not s:
                return
            u = auth.find_user(s["username"])
            self._serve_result(path[len("/results/"):], auth.results_dir(u))
            return
        if path.startswith("/attachments/"):
            s = self.require_user()
            if not s:
                return
            u = auth.find_user(s["username"])
            self._serve_attachment(path[len("/attachments/"):], auth.attach_dir(u))
            return
        name = path.lstrip("/")
        if name in STATIC:
            fp = os.path.join(ROOT, name)
            if os.path.exists(fp):
                ext = os.path.splitext(fp)[1].lower()
                with open(fp, "rb") as f:
                    self._send(200, f.read(), MIME.get(ext, "application/octet-stream"))
                return
            self._send(404, "not found", "text/plain")
            return
        self._send(404, "not found", "text/plain")

    def _serve_result(self, name, base=None):
        base = base or RESULTS
        # prevent path traversal
        safe = os.path.normpath(name).lstrip("/")
        if safe.startswith("..") or "/" in safe and safe.split("/")[0] == "..":
            self._send(403, "forbidden", "text/plain")
            return
        fp = os.path.join(base, safe)
        if not os.path.abspath(fp).startswith(os.path.abspath(base)):
            self._send(403, "forbidden", "text/plain")
            return
        if not os.path.exists(fp):
            self._send(404, "result not found", "text/plain")
            return
        ext = os.path.splitext(fp)[1].lower()
        ctype = MIME.get(ext, "application/octet-stream")
        with open(fp, "rb") as f:
            data = f.read()
        if ext == ".md":
            # wrap markdown in a minimal HTML viewer
            html = MD_VIEWER.replace("__BODY__", json.dumps(data.decode("utf-8")))
            self._send(200, html, "text/html; charset=utf-8")
        else:
            self._send(200, data, ctype)

    def _serve_attachment(self, name, base=None):
        base = base or ATTACH
        # prevent path traversal — attachments are flat files, no subdirs
        safe = os.path.basename(os.path.normpath(name))
        if not safe or safe.startswith("."):
            self._send(403, "forbidden", "text/plain")
            return
        fp = os.path.join(base, safe)
        if not os.path.abspath(fp).startswith(os.path.abspath(base)):
            self._send(403, "forbidden", "text/plain")
            return
        if not os.path.exists(fp):
            self._send(404, "attachment not found", "text/plain")
            return
        ext = os.path.splitext(fp)[1].lower()
        ctype = MIME.get(ext, "application/octet-stream")
        with open(fp, "rb") as f:
            self._send(200, f.read(), ctype)

    def _safe_attach_name(self, raw_name, ctype):
        """Build a unique, sanitised on-disk filename for an upload."""
        base = os.path.basename((raw_name or "").strip()) or "attachment"
        base = base.replace("\x00", "")
        name, ext = os.path.splitext(base)
        name = re.sub(r"[^A-Za-z0-9._-]", "-", name).strip("-.") or "attachment"
        ext = re.sub(r"[^A-Za-z0-9.]", "", ext).lower()
        if not ext:
            # derive an extension from the content type when the name lacks one
            for e, m in MIME.items():
                if m.split(";")[0] == (ctype or "").split(";")[0]:
                    ext = e
                    break
        name = name[:60]
        stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + str(int(time.time() * 1000) % 1000)
        return "%s-%s%s" % (stamp, name, ext)

    def do_POST(self):
        path = self.path.split("?", 1)[0]

        # ----- login is the one mutating endpoint with no session/CSRF ----- #
        if path == "/api/login":
            body = self._read_json()
            if body is None:
                self._json(400, {"error": "bad JSON"})
                return
            user, err = auth.authenticate(body.get("username", ""),
                                          body.get("password", ""))
            if err:
                self._json(401, {"error": err})
                return
            token, csrf = auth.create_session(user)
            self._json(200, {"user": auth._public(user), "csrf": csrf},
                       cookie=self._set_cookie(token))
            return

        if path == "/api/signup":
            body = self._read_json()
            if body is None:
                self._json(400, {"error": "bad JSON"})
                return
            role = "admin" if auth.user_count() == 0 else "user"
            try:
                pub = auth.create_user(body.get("username", ""),
                                       body.get("password", ""),
                                       role=role,
                                       must_change=False)
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            user = auth.find_user(pub["username"])
            token, csrf = auth.create_session(user)
            self._json(200, {"user": pub, "csrf": csrf,
                             "first_admin": role == "admin"},
                       cookie=self._set_cookie(token))
            return

        if path == "/api/logout":
            s = self.current_session()
            if s and not self.check_csrf(s):
                return
            auth.destroy_session(self._cookie_token())
            self._json(200, {"ok": True}, cookie=self._set_cookie("", clear=True))
            return

        # Everything below requires a valid session + CSRF token.
        if path == "/api/board":
            s = self.require_user()
            if not s or not self.check_csrf(s):
                return
            body = self._read_json()
            if body is None or not isinstance(body.get("cards"), list):
                self._json(400, {"error": "board must have a cards list"})
                return
            u = auth.find_user(s["username"])
            bp = auth.board_path(u)
            old = {"cards": []}
            if os.path.exists(bp):
                try:
                    with open(bp, "r", encoding="utf-8") as f:
                        old = json.load(f)
                except Exception:
                    old = {"cards": []}
            done_cards = _ai_done_notifications(old, body)
            close_newly_done_github_issues(old, body)
            os.makedirs(os.path.dirname(bp), exist_ok=True)
            tmp = bp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(body, f, indent=2, ensure_ascii=False)
            os.replace(tmp, bp)
            notify_ai_done(done_cards)
            self._json(200, {"ok": True})
            return

        if path == "/api/upload":
            s = self.require_user()
            if not s or not self.check_csrf(s):
                return
            self._handle_upload(auth.attach_dir(auth.find_user(s["username"])))
            return

        if path == "/api/account/password":
            s = self.require_user()
            if not s or not self.check_csrf(s):
                return
            body = self._read_json() or {}
            u = auth.find_user(s["username"])
            if not auth.verify_password(body.get("old_password", ""), u["password"]):
                self._json(403, {"error": "current password is incorrect"})
                return
            try:
                auth.set_password(s["username"], body.get("new_password", ""))
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, {"ok": True})
            return

        if path == "/api/account/apikey":
            s = self.require_user()
            if not s or not self.check_csrf(s):
                return
            body = self._read_json() or {}
            try:
                pub = auth.set_api_key(s["username"], body.get("api_key") or None,
                                       provider=body.get("provider"),
                                       model=body.get("model"))
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, {"ok": True, "user": pub})
            return

        if path == "/api/account/preferences":
            s = self.require_user()
            if not s or not self.check_csrf(s):
                return
            body = self._read_json() or {}
            try:
                pub = auth.set_preferences(
                    s["username"],
                    topbar_color=body.get("topbar_color"),
                    pushover_enabled=body.get("pushover_enabled"),
                    pushover_user=body.get("pushover_user"),
                    pushover_token=body.get("pushover_token"),
                    clear_pushover=bool(body.get("clear_pushover")),
                )
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, {"ok": True, "user": pub})
            return

        if path == "/api/pushover":
            s = self.require_user()
            if not s or not self.check_csrf(s):
                return
            body = self._read_json() or {}
            title = (body.get("title") or "LifeKanban").strip()[:250]
            message = (body.get("message") or "").strip()[:1024]
            if not message:
                self._json(400, {"error": "message required"})
                return
            creds = auth.get_pushover(s["username"])
            if not creds:
                self._json(400, {"error": "Pushover is not configured"})
                return
            try:
                self._send_pushover(creds["token"], creds["user"], title, message)
            except Exception as e:
                self._json(502, {"error": "Pushover failed: " + str(e)})
                return
            self._json(200, {"ok": True})
            return

        if path == "/api/ai/parse":
            s = self.require_user()
            if not s or not self.check_csrf(s):
                return
            body = self._read_json() or {}
            text = (body.get("text") or "").strip()
            if not text:
                self._json(400, {"error": "text required"})
                return
            u = auth.find_user(s["username"])
            provider = (u.get("ai_provider") or "").strip().lower()
            model = (u.get("ai_model") or "").strip()
            api_key = auth.get_api_key(s["username"])
            if provider not in AI_PROVIDERS or not model or not api_key:
                self._json(400, {"error": "AI provider, model and API key are not configured"})
                return
            try:
                parsed = self._ai_parse(provider, model, api_key,
                                        text, body.get("projects") or [])
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            except Exception as e:
                self._json(502, {"error": "AI request failed: " + str(e)})
                return
            self._json(200, {"ok": True, "card": parsed})
            return

        if path in ("/api/ai/clean", "/api/ai/rewrite-message"):
            s = self.require_user()
            if not s or not self.check_csrf(s):
                return
            body = self._read_json() or {}
            text = (body.get("text") or "").strip()
            if not text:
                self._json(400, {"error": "text required"})
                return
            if len(text) > 12000:
                self._json(400, {"error": "text is too long"})
                return
            u = auth.find_user(s["username"])
            provider = (u.get("ai_provider") or "").strip().lower()
            model = (u.get("ai_model") or "").strip()
            api_key = auth.get_api_key(s["username"])
            if provider not in AI_PROVIDERS or not model or not api_key:
                self._json(400, {"error": "AI provider, model and API key are not configured"})
                return
            try:
                if path == "/api/ai/clean":
                    output = self._ai_clean(provider, model, api_key, text)
                else:
                    output = self._ai_rewrite_message(provider, model, api_key, text)
            except Exception as e:
                self._json(502, {"error": "AI request failed: " + str(e)})
                return
            self._json(200, {"ok": True, "text": output})
            return

        if path == "/api/worker/run":
            s = self.require_user()
            if not s or not self.check_csrf(s):
                return
            wake_ai_worker()
            self._json(202, {"ok": True, "queued": True})
            return

        if path == "/api/notify/done":
            s = self.require_user()
            if not s or not self.check_csrf(s):
                return
            body = self._read_json() or {}
            title = (body.get("title") or "LifeKanban done").strip()
            message = (body.get("message") or "A card was moved to Done.").strip()
            ok = send_pushover(title, message, body.get("url") or "http://127.0.0.1:8787/")
            self._json(200, {"ok": True, "sent": bool(ok)})
            return

        if path == "/api/github/issue":
            s = self.require_user()
            if not s or not self.check_csrf(s):
                return
            body = self._read_json() or {}
            repo = (body.get("repo") or "").strip()
            title = (body.get("title") or "").strip()
            issue_body = (body.get("body") or "").strip()
            if not repo or not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repo):
                self._json(400, {"error": "GitHub repo must look like owner/repo"})
                return
            if not title:
                self._json(400, {"error": "issue title required"})
                return
            try:
                proc = subprocess.run(["/opt/homebrew/bin/gh", "issue", "create",
                                       "--repo", repo, "--title", title,
                                       "--body", issue_body or title],
                                      cwd=ROOT, text=True, capture_output=True, timeout=30)
                if proc.returncode != 0:
                    proc = subprocess.run(["gh", "issue", "create",
                                           "--repo", repo, "--title", title,
                                           "--body", issue_body or title],
                                          cwd=ROOT, text=True, capture_output=True, timeout=30)
                if proc.returncode != 0:
                    self._json(502, {"error": (proc.stderr or proc.stdout or "gh issue create failed").strip()[:500]})
                    return
                url = (proc.stdout or "").strip().splitlines()[-1]
            except Exception as e:
                self._json(502, {"error": "could not create GitHub issue: " + str(e)})
                return
            self._json(200, {"ok": True, "url": url})
            return

        if path == "/api/admin/pushover":
            s = self.require_admin()
            if not s or not self.check_csrf(s):
                return
            body = self._read_json() or {}
            settings = _load_app_settings()
            if body.get("clear"):
                settings.pop("pushover", None)
            else:
                user_key = (body.get("user_key") or "").strip()
                app_token = (body.get("app_token") or "").strip()
                current = settings.get("pushover") or {}
                if not user_key and current.get("user_key"):
                    user_key = auth.decrypt_secret(current.get("user_key"))
                if not app_token and current.get("app_token"):
                    app_token = auth.decrypt_secret(current.get("app_token"))
                if not user_key or not app_token:
                    self._json(400, {"error": "Pushover user key and app token are required"})
                    return
                settings["pushover"] = {
                    "enabled": bool(body.get("enabled", True)),
                    "user_key": auth.encrypt_secret(user_key),
                    "app_token": auth.encrypt_secret(app_token),
                    "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            _save_app_settings(settings)
            if body.get("test") and not body.get("clear"):
                send_pushover("LifeKanban", "Pushover notifications are connected.", "http://127.0.0.1:8787/")
            self._json(200, {"ok": True, "pushover": _pushover_public(settings)})
            return

        # ----- admin: create user ----- #
        if path == "/api/admin/users":
            s = self.require_admin()
            if not s or not self.check_csrf(s):
                return
            body = self._read_json() or {}
            try:
                pub = auth.create_user(body.get("username", ""),
                                       body.get("password", ""),
                                       role=body.get("role", "user"),
                                       must_change=True)
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, {"ok": True, "user": pub})
            return

        # ----- admin: reset password / change role (POST sub-routes) ----- #
        m = re.match(r"^/api/admin/users/([^/]+)/(password|role)$", path)
        if m:
            s = self.require_admin()
            if not s or not self.check_csrf(s):
                return
            username, action = m.group(1), m.group(2)
            body = self._read_json() or {}
            try:
                if action == "password":
                    auth.set_password(username, body.get("new_password", ""),
                                      must_change=True)
                else:
                    auth.set_role(username, body.get("role", "user"))
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, {"ok": True})
            return

        self._json(404, {"error": "not found"})

    def do_DELETE(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/account/apikey":
            s = self.require_user()
            if not s or not self.check_csrf(s):
                return
            auth.set_api_key(s["username"], None)
            self._json(200, {"ok": True})
            return
        m = re.match(r"^/api/admin/users/([^/]+)$", path)
        if m:
            s = self.require_admin()
            if not s or not self.check_csrf(s):
                return
            username = m.group(1)
            if username == s["username"]:
                self._json(400, {"error": "you cannot delete your own account"})
                return
            target = auth.find_user(username)
            if target and target.get("role") == "admin":
                admins = [u for u in auth.list_users() if u["role"] == "admin"]
                if len(admins) <= 1:
                    self._json(400, {"error": "cannot delete the last admin"})
                    return
            try:
                auth.delete_user(username)
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            self._json(200, {"ok": True})
            return
        self._json(404, {"error": "not found"})

    def _ai_parse(self, provider, model, api_key, text, projects):
        projects = [p for p in projects if isinstance(p, str)][:80]
        today = time.strftime("%Y-%m-%d")
        sys_prompt = (
            "You convert a short task request into a JSON object for a kanban board. "
            "Today is %s. Reply with ONLY a JSON object, no prose. Keys: "
            "title (a SHORT headline of 3-8 words, no trailing full stop), "
            "description (string: the full request and any extra detail), "
            "project (if the user named a project, return it exactly; use one from %s "
            "if it matches; return null only if no project was named), "
            "assignee (\"AI\" if the user wants AI to do it, \"Ch@o\" if "
            "the user said it is for them, else null), "
            "priority (\"high\"|\"medium\"|\"low\", default \"medium\"), "
            "due (YYYY-MM-DD or null), "
            "recur (null, or {\"freq\":\"daily|weekdays|weekly|monthly\","
            "\"days\":[0-6],\"day\":1-31})."
        ) % (today, json.dumps(projects, ensure_ascii=False))
        if provider == "anthropic":
            payload = {"model": model, "max_tokens": 500, "system": sys_prompt,
                       "messages": [{"role": "user", "content": text}]}
            headers = {"Content-Type": "application/json", "x-api-key": api_key,
                       "anthropic-version": "2023-06-01"}
            raw = self._http_json("https://api.anthropic.com/v1/messages",
                                  headers, payload)
            content = raw.get("content") or []
            answer = content[0].get("text", "") if content else ""
        else:
            payload = {"model": model, "temperature": 0,
                       "response_format": {"type": "json_object"},
                       "messages": [{"role": "system", "content": sys_prompt},
                                    {"role": "user", "content": text}]}
            headers = {"Content-Type": "application/json",
                       "Authorization": "Bearer " + api_key}
            raw = self._http_json("https://api.openai.com/v1/chat/completions",
                                  headers, payload)
            choices = raw.get("choices") or []
            answer = choices[0].get("message", {}).get("content", "") if choices else ""
        try:
            parsed = json.loads(answer)
        except Exception:
            m = re.search(r"\{[\s\S]*\}", answer)
            if not m:
                raise ValueError("AI did not return JSON")
            parsed = json.loads(m.group(0))
        return self._normalise_ai_card(parsed)

    def _ai_clean(self, provider, model, api_key, text):
        sys_prompt = (
            "Clean up rough, dictated, pasted, or messy text. Preserve the user's "
            "meaning and important details. Fix spelling, punctuation, grammar, "
            "line breaks, and obvious wording issues. Return only the cleaned text, "
            "with no preamble, no notes, and no Markdown fence."
        )
        return self._ai_text_transform(provider, model, api_key, text, sys_prompt, 1200)

    def _ai_rewrite_message(self, provider, model, api_key, text):
        sys_prompt = (
            "Rewrite the user's text for spelling, grammar, clarity, and polish. "
            "Use the tone of a senior product manager with over 20 years of experience: "
            "clear, calm, direct, pragmatic, and professional without sounding stiff. "
            "Preserve the user's meaning, facts, names, dates, and intent. "
            "Do not add new commitments, claims, greetings, signatures, explanations, "
            "Markdown fences, or commentary. Return only the rewritten message."
        )
        return self._ai_text_transform(provider, model, api_key, text, sys_prompt, 2000)

    def _ai_text_transform(self, provider, model, api_key, text, sys_prompt, max_tokens):
        if provider == "anthropic":
            payload = {"model": model, "max_tokens": max_tokens, "temperature": 0.2,
                       "system": sys_prompt,
                       "messages": [{"role": "user", "content": text}]}
            headers = {"Content-Type": "application/json", "x-api-key": api_key,
                       "anthropic-version": "2023-06-01"}
            raw = self._http_json("https://api.anthropic.com/v1/messages",
                                  headers, payload)
            content = raw.get("content") or []
            answer = "\n".join(c.get("text", "") for c in content
                               if isinstance(c, dict) and (c.get("type") == "text" or c.get("text")))
        else:
            payload = {"model": model, "temperature": 0.2,
                       "messages": [{"role": "system", "content": sys_prompt},
                                    {"role": "user", "content": text}]}
            headers = {"Content-Type": "application/json",
                       "Authorization": "Bearer " + api_key}
            raw = self._http_json("https://api.openai.com/v1/chat/completions",
                                  headers, payload)
            choices = raw.get("choices") or []
            answer = choices[0].get("message", {}).get("content", "") if choices else ""
        answer = str(answer or "").strip()
        if not answer:
            raise ValueError("AI returned no text")
        return answer

    def _http_json(self, url, headers, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urlrequest.Request(url, data=data, method="POST", headers=headers)
        try:
            with urlrequest.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urlerror.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError("%s %s" % (e.code, detail))

    def _send_pushover(self, app_token, user_key, title, message):
        data = urlparse.urlencode({
            "token": app_token,
            "user": user_key,
            "title": title,
            "message": message,
        }).encode("utf-8")
        req = urlrequest.Request("https://api.pushover.net/1/messages.json",
                                 data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urlrequest.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except urlerror.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError("%s %s" % (e.code, detail))

    def _normalise_ai_card(self, p):
        if not isinstance(p, dict):
            raise ValueError("AI response was not an object")
        title = str(p.get("title") or "").strip()
        if not title:
            raise ValueError("AI response did not include a title")
        priority = p.get("priority") if p.get("priority") in ("high", "medium", "low") else "medium"
        assignee = p.get("assignee") if p.get("assignee") in ("AI", "Claude", "Ch@o") else None
        if assignee == "Claude":
            assignee = "AI"
        due = p.get("due") if isinstance(p.get("due"), str) and re.match(r"^\d{4}-\d{2}-\d{2}$", p.get("due")) else None
        recur = p.get("recur") if isinstance(p.get("recur"), dict) and p.get("recur", {}).get("freq") else None
        return {"title": title[:120],
                "description": str(p.get("description") or ""),
                "project": p.get("project") if isinstance(p.get("project"), str) and p.get("project").strip() else None,
                "assignee": assignee,
                "priority": priority,
                "due": due,
                "recur": recur}

    def _handle_upload(self, base):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            self._send(400, json.dumps({"error": "empty body"}))
            return
        if length > MAX_UPLOAD:
            self._send(413, json.dumps({"error": "file too large",
                                        "max": MAX_UPLOAD}))
            return
        ctype = self.headers.get("Content-Type", "application/octet-stream")
        raw_name = self.headers.get("X-Filename", "")
        data = self.rfile.read(length)
        os.makedirs(base, exist_ok=True)
        fname = self._safe_attach_name(raw_name, ctype)
        fp = os.path.join(base, fname)
        tmp = fp + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, fp)
        self._send(200, json.dumps({
            "ok": True,
            "name": fname,
            "orig": os.path.basename(raw_name) or fname,
            "url": "/attachments/" + fname,
            "type": ctype,
            "size": len(data),
        }))


MD_VIEWER = """<!doctype html><html><head><meta charset="utf-8">
<title>Result</title>
<style>
body{font:16px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
max-width:760px;margin:40px auto;padding:0 20px;color:#1d2330;background:#f7f8fb}
pre{white-space:pre-wrap;word-wrap:break-word}
a.back{display:inline-block;margin-bottom:20px;color:#3b6cf6;text-decoration:none}
.card{background:#fff;border:1px solid #e4e7ef;border-radius:12px;padding:28px 32px;
box-shadow:0 1px 3px rgba(20,30,60,.06)}
h1,h2,h3{line-height:1.3}
code{background:#eef1f7;padding:1px 5px;border-radius:4px}
</style></head><body>
<a class="back" href="/">&larr; Back to board</a>
<div class="card"><div id="c"></div></div>
<script>
var src = __BODY__;
// extremely small markdown -> html (headings, bold, italics, lists, code, links)
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function md(t){
  var lines = t.split(/\\n/), out=[], inList=false;
  for(var i=0;i<lines.length;i++){
    var l=lines[i];
    var h=l.match(/^(#{1,4})\\s+(.*)/);
    if(h){ if(inList){out.push('</ul>');inList=false;}
      out.push('<h'+h[1].length+'>'+inline(h[2])+'</h'+h[1].length+'>'); continue;}
    if(/^\\s*[-*]\\s+/.test(l)){ if(!inList){out.push('<ul>');inList=true;}
      out.push('<li>'+inline(l.replace(/^\\s*[-*]\\s+/,''))+'</li>'); continue;}
    if(inList){out.push('</ul>');inList=false;}
    if(l.trim()==='') out.push('<br>'); else out.push('<p>'+inline(l)+'</p>');
  }
  if(inList)out.push('</ul>');
  return out.join('\\n');
}
function inline(s){ s=esc(s);
  s=s.replace(/\\*\\*([^*]+)\\*\\*/g,'<strong>$1</strong>');
  s=s.replace(/\\*([^*]+)\\*/g,'<em>$1</em>');
  s=s.replace(/`([^`]+)`/g,'<code>$1</code>');
  s=s.replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g,'<a href="$2">$1</a>');
  return s;
}
document.getElementById('c').innerHTML = md(src);
</script></body></html>"""


def main():
    os.makedirs(RESULTS, exist_ok=True)
    os.makedirs(ATTACH, exist_ok=True)
    os.makedirs(auth.BOARDS_DIR, exist_ok=True)
    if auth.user_count() == 0:
        print("WARNING: no users exist yet. Create the first admin with:\n"
              "  python3 kanban.py user-add <name> --admin\n"
              "Until then, login will reject everyone.")
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    url = "http://%s:%d/" % (HOST, PORT)
    print("Kanban board running at " + url)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
