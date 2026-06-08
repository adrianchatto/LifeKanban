#!/usr/bin/env python3
"""
auth.py - accounts, sessions and secret storage for LifeKanban.

Pure standard library (no third-party deps), to match the rest of the app.

What lives here:
  * users.json          - the user records (hashed passwords, encrypted API keys)
  * .secret.key         - a 32-byte master secret used to encrypt API keys at
                          rest. Generated once, chmod 600, gitignored.
  * in-memory sessions  - session tokens are never written to disk (a restart
                          logs everyone out, which is the safe default).

Security choices:
  * Passwords: PBKDF2-HMAC-SHA256, 200k iterations, per-user random salt,
    constant-time comparison.
  * API keys at rest: authenticated stream cipher (SHA-256 keystream,
    encrypt-then-MAC with HMAC-SHA256) keyed by .secret.key. This is stdlib-only
    (no AES available without a dependency) but is a real, authenticated cipher
    and is far better than storing keys in clear text. If you later add the
    `cryptography` package, swap _encrypt/_decrypt for AES-GCM.
  * Sessions: 256-bit random tokens, per-session CSRF token, sliding expiry.
  * Login throttling: per-username failure counter with a lockout window.

Both the web server and the kanban.py CLI import this module so all writes go
through one path.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("KANBAN_DATA", ROOT)
USERS = os.path.join(DATA, "users.json")
SECRET_FILE = os.path.join(DATA, ".secret.key")
LOCK = os.path.join(DATA, ".users.lock")
BOARDS_DIR = os.path.join(DATA, "boards")

PBKDF2_ITERS = 200_000
SESSION_TTL = int(os.environ.get("KANBAN_SESSION_TTL", str(12 * 3600)))  # seconds
# Login throttling
MAX_FAILS = int(os.environ.get("KANBAN_MAX_LOGIN_FAILS", "5"))
LOCKOUT_SECS = int(os.environ.get("KANBAN_LOCKOUT_SECS", "300"))

VALID_ROLES = ("admin", "user")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# Tiny cross-process lock (same pattern kanban.py uses).
# --------------------------------------------------------------------------- #
class _Lock:
    def __enter__(self):
        for _ in range(100):
            try:
                os.mkdir(LOCK)
                return self
            except FileExistsError:
                time.sleep(0.05)
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


# --------------------------------------------------------------------------- #
# Master secret (for API-key encryption).
# --------------------------------------------------------------------------- #
def _secret():
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "rb") as f:
            raw = f.read().strip()
        try:
            return base64.urlsafe_b64decode(raw)
        except Exception:
            pass
    key = secrets.token_bytes(32)
    fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(base64.urlsafe_b64encode(key))
    try:
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass
    return key


def _keystream(key, nonce, n):
    out = bytearray()
    ctr = 0
    while len(out) < n:
        out += hashlib.sha256(key + nonce + ctr.to_bytes(8, "big")).digest()
        ctr += 1
    return bytes(out[:n])


def encrypt_secret(plaintext):
    """Encrypt a short secret (API key). Returns a urlsafe-base64 token."""
    if plaintext is None:
        return None
    key = _secret()
    nonce = secrets.token_bytes(16)
    pt = plaintext.encode("utf-8")
    ks = _keystream(key, nonce, len(pt))
    ct = bytes(a ^ b for a, b in zip(pt, ks))
    tag = hmac.new(key, nonce + ct, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + ct + tag).decode("ascii")


def decrypt_secret(token):
    if not token:
        return None
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    nonce, ct, tag = raw[:16], raw[16:-32], raw[-32:]
    key = _secret()
    expect = hmac.new(key, nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(expect, tag):
        raise ValueError("secret failed integrity check")
    ks = _keystream(key, nonce, len(ct))
    return bytes(a ^ b for a, b in zip(ct, ks)).decode("utf-8")


# --------------------------------------------------------------------------- #
# Password hashing.
# --------------------------------------------------------------------------- #
def hash_password(pw):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, PBKDF2_ITERS)
    return "pbkdf2_sha256$%d$%s$%s" % (
        PBKDF2_ITERS, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(pw, stored):
    try:
        algo, iters, salt_b, hash_b = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b)
        expected = base64.b64decode(hash_b)
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# User store.
# --------------------------------------------------------------------------- #
def _load_raw():
    if not os.path.exists(USERS):
        return {"version": 1, "next_id": 1, "users": []}
    with open(USERS, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_raw(data):
    fd, tmp = tempfile.mkstemp(dir=DATA, prefix=".users.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, USERS)
    try:
        os.chmod(USERS, 0o600)
    except OSError:
        pass


def _public(u):
    """A user record safe to send to the browser — never the password hash or
    the encrypted key. API key is reported only as set/unset + last 4 chars."""
    last4 = None
    has_key = bool(u.get("api_key"))
    if has_key:
        try:
            last4 = decrypt_secret(u["api_key"])[-4:]
        except Exception:
            last4 = None
    return {
        "id": u["id"],
        "username": u["username"],
        "role": u.get("role", "user"),
        "created": u.get("created"),
        "ai_provider": u.get("ai_provider"),
        "ai_model": u.get("ai_model"),
        "has_api_key": has_key,
        "api_key_last4": last4,
        "must_change": bool(u.get("must_change")),
    }


def find_user(username):
    for u in _load_raw().get("users", []):
        if u["username"] == username:
            return u
    return None


def find_user_by_id(uid):
    for u in _load_raw().get("users", []):
        if u["id"] == uid:
            return u
    return None


def list_users():
    return [_public(u) for u in _load_raw().get("users", [])]


def user_count():
    return len(_load_raw().get("users", []))


def create_user(username, password, role="user", must_change=False):
    username = (username or "").strip()
    if not username:
        raise ValueError("username required")
    if role not in VALID_ROLES:
        raise ValueError("role must be one of %s" % (VALID_ROLES,))
    if not password or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    with _Lock():
        data = _load_raw()
        if any(u["username"].lower() == username.lower() for u in data["users"]):
            raise ValueError("username already exists: " + username)
        n = data.get("next_id", 1)
        data["next_id"] = n + 1
        uid = "u-%03d" % n
        # First user ever, or any admin, keeps the legacy single-board layout so
        # the existing board.json + worker keep working untouched. Additional
        # users get their own isolated board under boards/.
        legacy = not data["users"]  # very first account adopts the existing board
        rec = {
            "id": uid,
            "username": username,
            "role": role,
            "password": hash_password(password),
            "api_key": None,
            "ai_provider": None,
            "ai_model": None,
            "board": "board.json" if legacy else os.path.join("boards", uid + ".json"),
            "results_dir": "results" if legacy else os.path.join("results", uid),
            "attach_dir": "attachments" if legacy else os.path.join("attachments", uid),
            "created": now_iso(),
            "must_change": bool(must_change),
        }
        data["users"].append(rec)
        _save_raw(data)
    return _public(rec)


def delete_user(username):
    with _Lock():
        data = _load_raw()
        before = len(data["users"])
        data["users"] = [u for u in data["users"] if u["username"] != username]
        if len(data["users"]) == before:
            raise ValueError("no such user: " + username)
        _save_raw(data)
    return True


def set_password(username, password, must_change=False):
    if not password or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    with _Lock():
        data = _load_raw()
        u = next((x for x in data["users"] if x["username"] == username), None)
        if not u:
            raise ValueError("no such user: " + username)
        u["password"] = hash_password(password)
        u["must_change"] = bool(must_change)
        _save_raw(data)
    return True


def set_role(username, role):
    if role not in VALID_ROLES:
        raise ValueError("role must be one of %s" % (VALID_ROLES,))
    with _Lock():
        data = _load_raw()
        u = next((x for x in data["users"] if x["username"] == username), None)
        if not u:
            raise ValueError("no such user: " + username)
        # Don't allow demoting the last admin.
        if u.get("role") == "admin" and role != "admin":
            admins = [x for x in data["users"] if x.get("role") == "admin"]
            if len(admins) <= 1:
                raise ValueError("cannot demote the last admin")
        u["role"] = role
        _save_raw(data)
    return True


def set_api_key(username, api_key, provider=None, model=None):
    """Store (and encrypt) a user's API key. Pass api_key=None to clear it."""
    with _Lock():
        data = _load_raw()
        u = next((x for x in data["users"] if x["username"] == username), None)
        if not u:
            raise ValueError("no such user: " + username)
        u["api_key"] = encrypt_secret(api_key) if api_key else None
        if provider is not None:
            u["ai_provider"] = provider or None
        if model is not None:
            u["ai_model"] = model or None
        _save_raw(data)
    return _public(u)


def get_api_key(username):
    """Return the *decrypted* API key for a user (or None). Callers must already
    be authenticated as that user."""
    u = find_user(username)
    if not u or not u.get("api_key"):
        return None
    try:
        return decrypt_secret(u["api_key"])
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Per-user data paths.
# --------------------------------------------------------------------------- #
def board_path(user):
    return os.path.join(DATA, user.get("board", "board.json"))


def results_dir(user):
    d = os.path.join(DATA, user.get("results_dir", "results"))
    os.makedirs(d, exist_ok=True)
    return d


def attach_dir(user):
    d = os.path.join(DATA, user.get("attach_dir", "attachments"))
    os.makedirs(d, exist_ok=True)
    return d


# --------------------------------------------------------------------------- #
# Login throttling (in memory).
# --------------------------------------------------------------------------- #
_FAILS = {}  # username -> {"count": int, "until": epoch}


def _locked_out(username):
    rec = _FAILS.get(username)
    if not rec:
        return 0
    if rec["count"] >= MAX_FAILS and time.time() < rec["until"]:
        return int(rec["until"] - time.time())
    return 0


def _record_fail(username):
    rec = _FAILS.setdefault(username, {"count": 0, "until": 0})
    rec["count"] += 1
    if rec["count"] >= MAX_FAILS:
        rec["until"] = time.time() + LOCKOUT_SECS


def _clear_fails(username):
    _FAILS.pop(username, None)


def authenticate(username, password):
    """Return (user_record, error). On success error is None; on failure
    user_record is None and error is a short string."""
    wait = _locked_out(username)
    if wait:
        return None, "too many attempts — try again in %ds" % wait
    u = find_user(username)
    # Always run a hash comparison to avoid leaking whether the user exists.
    stored = u["password"] if u else "pbkdf2_sha256$1$AAAA$AAAA"
    ok = verify_password(password, stored)
    if not u or not ok:
        _record_fail(username)
        return None, "invalid username or password"
    _clear_fails(username)
    return u, None


# --------------------------------------------------------------------------- #
# Sessions (in memory; CSRF per session).
# --------------------------------------------------------------------------- #
_SESSIONS = {}  # token -> {"uid","username","role","csrf","expires"}


def _sweep():
    nowt = time.time()
    for tok in [t for t, s in _SESSIONS.items() if s["expires"] < nowt]:
        _SESSIONS.pop(tok, None)


def create_session(user):
    _sweep()
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    _SESSIONS[token] = {
        "uid": user["id"],
        "username": user["username"],
        "role": user.get("role", "user"),
        "csrf": csrf,
        "expires": time.time() + SESSION_TTL,
    }
    return token, csrf


def get_session(token):
    if not token:
        return None
    s = _SESSIONS.get(token)
    if not s:
        return None
    if s["expires"] < time.time():
        _SESSIONS.pop(token, None)
        return None
    # sliding expiry
    s["expires"] = time.time() + SESSION_TTL
    return s


def destroy_session(token):
    _SESSIONS.pop(token, None)
