"""Accounts: email + password users stored as JSON documents, each with their own
Serper API keys (encrypted at rest, see apikeys.py). Sessions are signed cookies that expire 24 hours
after login, so logging in costs no storage writes."""
import base64
import hashlib
import hmac
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from functools import wraps

from cryptography.fernet import Fernet, InvalidToken
from flask import jsonify, redirect, request, session, url_for

SESSION_HOURS = 24
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PBKDF2_ITERATIONS = 240_000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def user_key(email: str) -> str:
    return f"users/{hashlib.sha256(normalize_email(email).encode()).hexdigest()[:40]}.json"


# ---------------------------------------------------------------- passwords + encryption

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_b64, hash_b64 = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(iters))
        return hmac.compare_digest(digest, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


class KeyBox:
    """Encrypts API keys with a key derived from SECRET_KEY."""

    def __init__(self, secret: str):
        self.fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(("apikey:" + secret).encode()).digest()))

    def encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self.fernet.decrypt(token.encode()).decode()
        except (InvalidToken, AttributeError, ValueError):
            return ""


# ---------------------------------------------------------------- user repository

class Users:
    def __init__(self, store, keybox: KeyBox):
        self.store, self.keybox = store, keybox

    def get(self, email: str) -> dict | None:
        return self.store.get_json(user_key(email))

    def create(self, email: str, password: str, name: str = "") -> dict:
        email = normalize_email(email)
        user = {"id": uuid.uuid4().hex, "email": email, "name": name.strip()[:80], "password": hash_password(password),
                "api_keys": [],
                "created_at": now_iso(), "updated_at": now_iso()}
        self.store.put_json(user_key(email), user)
        return user

    def save(self, user: dict):
        user["updated_at"] = now_iso()
        self.store.put_json(user_key(user["email"]), user)


def validate_signup(email: str, password: str) -> str:
    if not EMAIL_RE.match(normalize_email(email)):
        return "Enter a valid email address."
    if len(password or "") < 8:
        return "Password must be at least 8 characters."
    return ""


# ---------------------------------------------------------------- sessions

def start_session(user: dict, key_count: int):
    session.clear()
    session.permanent = True
    session["uid"] = user["id"]
    session["email"] = user["email"]
    session["name"] = user.get("name", "")
    session["iat"] = int(time.time())
    session["nk"] = key_count  # enabled API keys; the keys themselves stay in storage (50 keys won't fit a cookie)


def session_valid() -> bool:
    iat = session.get("iat")
    return bool(session.get("uid")) and isinstance(iat, int) and time.time() - iat < SESSION_HOURS * 3600


def login_required(api: bool = False, need_key: bool = True):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not session_valid():
                session.clear()
                if api:
                    return jsonify({"error": "Your session expired. Please log in again.", "auth": True}), 401
                return redirect(url_for("login_page", next=request.path))
            if need_key and not (session.get("nk") or session.get("k")):  # "k": sessions from before multi-key
                if api:
                    return jsonify({"error": "Add your Serper API key first.", "need_key": True}), 403
                return redirect(url_for("api_key_page"))
            return fn(*args, **kwargs)
        return wrapper
    return deco


def load_secret_key(data_dir) -> str:
    secret = os.environ.get("SECRET_KEY", "").strip()
    if secret:
        return secret
    if os.environ.get("VERCEL"):
        raise RuntimeError("SECRET_KEY environment variable is required on Vercel")
    path = data_dir / "secret_key.txt"  # local: generate once and keep it
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()
