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
import sys
import threading
import time
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlrequest
from urllib import error as urlerror

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
PUBLIC_PAGES = ("login.html", "settings.html", "admin.html")
AI_PROVIDERS = ("anthropic", "openai")

MIME = {".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8",
        ".html": "text/html; charset=utf-8", ".json": "application/json",
        ".pdf": "application/pdf", ".png": "image/png", ".csv": "text/csv",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
        ".webp": "image/webp", ".svg": "image/svg+xml", ".heic": "image/heic",
        ".webmanifest": "application/manifest+json", ".ico": "image/x-icon"}

# Static files served from the project root (for PWA install)
STATIC = ("manifest.webmanifest", "icon-192.png", "icon-512.png",
          "icon-maskable-512.png", "favicon.ico")


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
            os.makedirs(os.path.dirname(bp), exist_ok=True)
            tmp = bp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(body, f, indent=2, ensure_ascii=False)
            os.replace(tmp, bp)
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

    def _http_json(self, url, headers, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urlrequest.Request(url, data=data, method="POST", headers=headers)
        try:
            with urlrequest.urlopen(req, timeout=30) as r:
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
