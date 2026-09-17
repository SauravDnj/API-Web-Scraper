"""Many Serper API keys per account.

Keys live in the user document as `api_keys` (each one encrypted). Every Serper call picks
the next enabled key in turn; when a key is rejected (no credits, revoked) it is marked and
the call is retried with the next key, so a job only stops when every key has failed.
Balances are checked with Serper's /account endpoint and shown per key in Settings."""
import hashlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from auth import now_iso
from serper_client import SerperClient, SerperError

MAX_KEYS = 100
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
CACHE_SECONDS = 20       # how long decrypted keys stay in memory per account
RECHECK_SECONDS = 60     # balances newer than this are not fetched again for the top bar
CHECK_WORKERS = 16


def fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()[:24]


def parse_keys(text: str) -> list[tuple[str, str]]:
    """'key' or 'Label | key', one per line (commas also separate keys) -> [(label, key)]."""
    out = []
    for line in re.split(r"[\r\n,]+", str(text or "")):
        line = line.strip()
        if not line:
            continue
        label, _, key = line.rpartition("|")
        out.append((label.strip()[:40], key.strip()))
    return out


def check_key(api_key: str) -> dict:
    """Ask Serper for the key's balance -> {balance, status, error}."""
    try:
        balance = SerperClient(api_key).account().get("balance")
    except SerperError as e:
        rejected = e.status in (400, 401, 403) or e.fatal
        return {"balance": None, "status": "invalid" if rejected else "error", "error": str(e)[:200]}
    try:
        balance = int(balance)
    except (TypeError, ValueError):
        balance = None
    return {"balance": balance, "status": "active" if balance is None or balance > 0 else "empty", "error": ""}


class ApiKeys:
    def __init__(self, users, keybox):
        self.users, self.keybox = users, keybox
        self._cache: dict[str, tuple[float, list]] = {}
        self._turn: dict[str, int] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ stored entries

    def entries(self, user: dict) -> list[dict]:
        """The user's key list. An old single `api_key` becomes the first entry."""
        keys = user.setdefault("api_keys", [])
        if user.get("api_key") and not keys:
            plain = self.keybox.decrypt(user["api_key"])
            if plain:
                keys.append(self._new_entry(plain, "Key 1"))
        return keys

    def _new_entry(self, plain: str, label: str) -> dict:
        return {"id": fingerprint(plain), "label": label, "key": self.keybox.encrypt(plain),
                "hint": f"{plain[:4]}…{plain[-4:]}", "enabled": True, "status": "active",
                "balance": None, "error": "", "checked_at": None, "added_at": now_iso()}

    def count_enabled(self, user: dict) -> int:
        return sum(1 for k in self.entries(user) if k.get("enabled", True))

    def save(self, user: dict):
        self.entries(user)
        for legacy in ("api_key", "api_key_last4", "api_key_updated_at"):
            user.pop(legacy, None)
        self.users.save(user)
        with self._lock:
            self._cache.pop(user["email"], None)

    def summary(self, user: dict) -> dict:
        keys = self.entries(user)
        public = [{k: v for k, v in e.items() if k != "key"} for e in keys]
        enabled = [e for e in keys if e.get("enabled", True)]
        return {
            "keys": public, "count": len(keys), "enabled": len(enabled),
            "active": sum(1 for e in enabled if e.get("status") == "active"),
            "total_balance": sum(e["balance"] for e in enabled if isinstance(e.get("balance"), int)),
            "all_balance": sum(e["balance"] for e in keys if isinstance(e.get("balance"), int)),
            "max_keys": MAX_KEYS,
        }

    # ------------------------------------------------------------ changes from Settings

    def add(self, user: dict, text: str) -> dict:
        existing = {e["id"] for e in self.entries(user)}
        wanted, errors, seen = [], [], set()
        for label, key in parse_keys(text):
            short = f"{key[:4]}…{key[-4:]}" if len(key) > 8 else key
            if not KEY_RE.match(key):
                errors.append(f"{short}: doesn't look like a Serper API key")
            elif fingerprint(key) in existing or fingerprint(key) in seen:
                errors.append(f"{short}: already added")
            else:
                seen.add(fingerprint(key))
                wanted.append((label, key))
        room = MAX_KEYS - len(existing)
        if len(wanted) > room:
            errors.append(f"{len(wanted) - room} key(s) skipped: an account can hold at most {MAX_KEYS} keys")
            wanted = wanted[:max(0, room)]

        with ThreadPoolExecutor(max_workers=CHECK_WORKERS) as pool:
            results = list(pool.map(lambda lk: check_key(lk[1]), wanted))
        added = 0
        for (label, key), res in zip(wanted, results):
            if res["status"] in ("invalid", "error"):
                errors.append(f"{key[:4]}…{key[-4:]}: Serper rejected it ({res['error']})")
                continue
            entry = self._new_entry(key, label or f"Key {len(self.entries(user)) + 1}")
            entry.update(balance=res["balance"], status=res["status"], checked_at=now_iso())
            self.entries(user).append(entry)
            added += 1
        if added:
            self.save(user)
        return {"added": added, "errors": errors}

    def refresh(self, user: dict, max_age: int = 0) -> dict:
        """Re-check balances (all keys, or only ones older than max_age seconds)."""
        keys = self.entries(user)
        now = time.time()

        def stale(e):
            try:
                return now - datetime.fromisoformat(e["checked_at"]).timestamp() > max_age
            except (TypeError, ValueError, KeyError):
                return True

        todo = [e for e in keys if not max_age or stale(e)]
        if todo:
            with ThreadPoolExecutor(max_workers=CHECK_WORKERS) as pool:
                results = list(pool.map(lambda e: check_key(self.keybox.decrypt(e["key"])), todo))
            for e, res in zip(todo, results):
                e.update(res, checked_at=now_iso())
            self.save(user)
        return self.summary(user)

    def update(self, user: dict, key_id: str, changes: dict) -> bool:
        entry = next((e for e in self.entries(user) if e["id"] == key_id), None)
        if not entry:
            return False
        if "enabled" in changes:
            entry["enabled"] = bool(changes["enabled"])
        if "label" in changes:
            entry["label"] = str(changes["label"] or "").strip()[:40] or entry["label"]
        self.save(user)
        return True

    def remove(self, user: dict, key_id: str) -> bool:
        keys = self.entries(user)
        kept = [e for e in keys if e["id"] != key_id]
        if len(kept) == len(keys):
            return False
        user["api_keys"] = kept
        self.save(user)
        return True

    # ------------------------------------------------------------ use while scraping

    def pool(self, email: str) -> list[tuple[str, str]]:
        """[(key id, plain key)] of enabled keys that still work, cached briefly in memory."""
        with self._lock:
            hit = self._cache.get(email)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
        user = self.users.get(email)
        keys = []
        for e in self.entries(user) if user else []:
            if e.get("enabled", True) and e.get("status") not in ("empty", "invalid"):
                plain = self.keybox.decrypt(e["key"])
                if plain:
                    keys.append((e["id"], plain))
        with self._lock:
            self._cache[email] = (time.time(), keys)
        return keys

    def next_start(self, email: str, n: int) -> int:
        with self._lock:
            i = self._turn.get(email, int(time.time() * 1000))
            self._turn[email] = i + 1
        return i % n

    def mark_failed(self, email: str, key_id: str, error: SerperError):
        msg = str(error).lower()
        status = "empty" if "credit" in msg or "balance" in msg else "invalid"
        with self._lock:
            hit = self._cache.get(email)
            if hit:
                self._cache[email] = (hit[0], [k for k in hit[1] if k[0] != key_id])
        user = self.users.get(email)
        entry = next((e for e in self.entries(user) if e["id"] == key_id), None) if user else None
        if entry and entry.get("status") != status:
            entry.update(status=status, error=str(error)[:200], checked_at=now_iso())
            if status == "empty":
                entry["balance"] = 0
            self.users.save(user)


class MultiKeyClient:
    """Same interface as SerperClient; spreads calls over the account's keys."""

    def __init__(self, manager: ApiKeys, email: str):
        self.manager, self.email = manager, email

    def _call(self, fn):
        keys = self.manager.pool(self.email)
        if not keys:
            raise SerperError("No API key with credits left. Add a key or click 'Refresh credits' in Settings.", 403)
        start = self.manager.next_start(self.email, len(keys))
        last = None
        for i in range(len(keys)):
            key_id, plain = keys[(start + i) % len(keys)]
            try:
                return fn(SerperClient(plain))
            except SerperError as e:
                if e.status in (401, 403) or e.fatal:
                    self.manager.mark_failed(self.email, key_id, e)
                elif e.status != 429:  # a bad request fails the same way on every key
                    raise
                last = e
        raise SerperError(f"All {len(keys)} API key(s) failed. Last error: {last}", 403)

    def post(self, endpoint: str, payload: dict) -> dict:
        return self._call(lambda c: c.post(endpoint, payload))

    def scrape(self, url: str, include_markdown: bool = True) -> dict:
        return self._call(lambda c: c.scrape(url, include_markdown))
