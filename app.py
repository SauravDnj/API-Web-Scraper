"""Waloop Data Scraper - multi-user web app for the Serper.dev APIs.

* Accounts (email + password) and each user's own Serper API keys are stored as JSON documents.
* Scrape calls rotate over the user's enabled keys and skip keys that run out of credits.
* Sessions last 24 hours from login.
* Scrape jobs are driven by the browser one API call at a time (works on Vercel's
  short-lived functions) and saved to storage as compressed JSON checkpoints.

Run locally:  .venv\\Scripts\\python app.py   ->  http://127.0.0.1:5000
Deploy:       Vercel (see README.md)
"""
import gzip
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for

BASE = Path(__file__).parent


def load_env(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(BASE / ".env")

from apikeys import RECHECK_SECONDS, ApiKeys, MultiKeyClient  # noqa: E402
from auth import (SESSION_HOURS, KeyBox, Users, load_secret_key, login_required,  # noqa: E402
                  normalize_email, now_iso, start_session, user_key, validate_signup, verify_password)
from serper_client import SerperError  # noqa: E402
from storage import StorageError, get_store, local_data_dir  # noqa: E402
from tools import TOOL_LIST, TOOLS, to_int  # noqa: E402

ON_VERCEL = bool(os.environ.get("VERCEL"))
MAX_TASKS = 2000
JOB_ID_RE = re.compile(r"^[A-Za-z0-9-]{6,64}$")

app = Flask(__name__, static_folder="public/static", static_url_path="/static")
app.config.update(
    SECRET_KEY=load_secret_key(local_data_dir()),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_HOURS),
    SESSION_REFRESH_EACH_REQUEST=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=ON_VERCEL,
    SESSION_COOKIE_NAME="sds_session",
    MAX_CONTENT_LENGTH=(4_400_000 if ON_VERCEL else 60_000_000),  # Vercel request bodies max out at 4.5 MB
)
try:
    store, STORE_ERROR = get_store(), ""
except StorageError as e:  # keep the app up so it can explain what is missing
    store, STORE_ERROR = None, str(e)
keybox = KeyBox(app.config["SECRET_KEY"])
users = Users(store, keybox)
apikeys = ApiKeys(users, keybox)
ASSET_VERSION = (os.environ.get("VERCEL_GIT_COMMIT_SHA") or str(int(time.time())))[:10]
# Blob writes are limited on the free plan, so save running jobs less often there
CHECKPOINT_SECONDS = 180 if getattr(store, "kind", "") == "blob" else 30 if getattr(store, "kind", "") == "redis" else 15


@app.context_processor
def inject_globals():
    return {"asset_v": ASSET_VERSION, "user_email": session.get("email", ""), "user_name": session.get("name", "")}


@app.before_request
def storage_check():
    if store is None and not request.path.startswith("/static"):
        msg = f"Storage is not configured. {STORE_ERROR}"
        if request.path.startswith("/api/"):
            return jsonify({"error": msg}), 503
        return f"<h1>Setup needed</h1><p>{msg}</p>", 503


@app.before_request
def csrf_guard():
    # all state-changing API calls come from our own fetch() with this header (blocks cross-site form posts)
    if request.path.startswith("/api/") and request.method in ("POST", "PUT", "DELETE"):
        if request.headers.get("X-Requested-With") != "fetch":
            return jsonify({"error": "bad request"}), 400


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if request.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.errorhandler(StorageError)
def storage_error(e):
    return jsonify({"error": f"Storage error: {e}"}), 500


def body() -> dict:
    return request.get_json(silent=True) or {}


def jobs_index_key(uid: str) -> str:
    return f"accounts/{uid}/jobs.json"


def job_key(uid: str, job_id: str) -> str:
    return f"accounts/{uid}/jobs/{job_id}.json"


def load_index(uid: str) -> dict:
    return store.get_json(jobs_index_key(uid)) or {"jobs": []}


# ================================================================ pages

@app.get("/")
@login_required()
def index():
    return render_template("index.html")


@app.get("/login")
def login_page():
    return render_template("auth.html", mode="login")


@app.get("/signup")
def signup_page():
    return render_template("auth.html", mode="signup")


@app.get("/api-key")
@login_required(need_key=False)
def api_key_page():
    return render_template("settings.html", first_time=not (session.get("nk") or session.get("k")))


@app.get("/settings")
@login_required(need_key=False)
def settings_page():
    return render_template("settings.html", first_time=False)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ================================================================ auth API

@app.post("/api/auth/signup")
def api_signup():
    data = body()
    email, password = normalize_email(data.get("email")), data.get("password") or ""
    err = validate_signup(email, password)
    if err:
        return jsonify({"error": err}), 400
    if users.get(email):
        return jsonify({"error": "An account with this email already exists. Please log in."}), 409
    is_first_local_user = getattr(store, "kind", "") == "local" and not any((local_data_dir() / "users").glob("*.json"))
    user = users.create(email, password, data.get("name") or "")
    if is_first_local_user:
        migrate_legacy_jobs(user["id"])
    start_session(user, 0)
    return jsonify({"ok": True, "next": url_for("api_key_page")})


@app.post("/api/auth/login")
def api_login():
    data = body()
    user = users.get(data.get("email") or "")
    ok = verify_password(data.get("password") or "", user["password"] if user else
                         "pbkdf2_sha256$240000$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    if not user or not ok:
        time.sleep(0.6)
        return jsonify({"error": "Wrong email or password."}), 401
    key_count = apikeys.count_enabled(user)
    start_session(user, key_count)
    nxt = data.get("next") if str(data.get("next") or "").startswith("/") and not str(data.get("next")).startswith("//") else "/"
    return jsonify({"ok": True, "next": nxt if key_count else url_for("api_key_page")})


@app.post("/api/auth/logout")
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
@login_required(api=True, need_key=False)
def api_me():
    return jsonify({
        "email": session["email"], "name": session.get("name", ""),
        "has_key": bool(session.get("nk") or session.get("k")), "session_expires_at": session["iat"] + SESSION_HOURS * 3600,
        "storage": store.kind, "checkpoint_seconds": CHECKPOINT_SECONDS,
    })


def current_user():
    user = users.get(session["email"])
    if not user:
        session.clear()
    return user


def keys_response(user, summary=None, **extra):
    """Key list for Settings; also keeps the session's key count in step."""
    summary = summary or apikeys.summary(user)
    session["nk"] = summary["enabled"]
    session.pop("k", None)
    session.pop("k4", None)
    return jsonify({**summary, **extra})


NO_USER = ({"error": "Account not found.", "auth": True}, 401)


@app.get("/api/me/keys")
@login_required(api=True, need_key=False)
def api_list_keys():
    user = current_user()
    return keys_response(user) if user else NO_USER


@app.post("/api/me/keys")
@login_required(api=True, need_key=False)
def api_add_keys():
    """Add one or many keys (one per line, optional 'Label | key'). Each is checked with Serper first."""
    user = current_user()
    if not user:
        return NO_USER
    text = str(body().get("keys") or "")
    if not text.strip():
        return jsonify({"error": "Paste at least one API key."}), 400
    result = apikeys.add(user, text)
    if not result["added"]:
        return jsonify({"error": "No keys were added.", "errors": result["errors"]}), 400
    return keys_response(user, **result)


@app.post("/api/me/keys/refresh")
@login_required(api=True, need_key=False)
def api_refresh_keys():
    user = current_user()
    return keys_response(user, apikeys.refresh(user)) if user else NO_USER


@app.post("/api/me/keys/<key_id>")
@login_required(api=True, need_key=False)
def api_update_key(key_id):
    user = current_user()
    if not user:
        return NO_USER
    data = body()
    if not apikeys.update(user, key_id, {k: data[k] for k in ("enabled", "label") if k in data}):
        return jsonify({"error": "Key not found."}), 404
    return keys_response(user)


@app.delete("/api/me/keys/<key_id>")
@login_required(api=True, need_key=False)
def api_remove_key(key_id):
    user = current_user()
    if not user:
        return NO_USER
    if not apikeys.remove(user, key_id):
        return jsonify({"error": "Key not found."}), 404
    return keys_response(user)


@app.post("/api/me/password")
@login_required(api=True, need_key=False)
def api_change_password():
    data = body()
    user = users.get(session["email"])
    if not user or not verify_password(data.get("current") or "", user["password"]):
        return jsonify({"error": "Current password is wrong."}), 400
    if len(data.get("new") or "") < 8:
        return jsonify({"error": "New password must be at least 8 characters."}), 400
    from auth import hash_password
    user["password"] = hash_password(data["new"])
    users.save(user)
    return jsonify({"ok": True})


# ================================================================ tools API

@app.post("/api/me/delete")
@login_required(api=True, need_key=False)
def api_delete_account():
    """Delete the account and everything stored for it."""
    user = users.get(session["email"])
    if not user or not verify_password(body().get("password") or "", user["password"]):
        return jsonify({"error": "Password is wrong."}), 400
    uid = session["uid"]
    for job in load_index(uid)["jobs"]:
        store.delete(job_key(uid, job["id"]))
    store.delete(jobs_index_key(uid))
    store.delete(user_key(user["email"]))
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/tools")
@login_required(api=True)
def list_tools():
    return jsonify([t.meta() for t in TOOL_LIST])


@app.get("/api/account")
@login_required(api=True)
def account():
    """Total credits over the enabled keys (balances older than a minute are re-checked)."""
    user = current_user()
    if not user:
        return NO_USER
    s = apikeys.refresh(user, max_age=RECHECK_SECONDS)
    return jsonify({"balance": s["total_balance"], "keys": s["enabled"], "active": s["active"], "count": s["count"]})


def _tool(tool_id):
    return TOOLS.get(tool_id)


@app.post("/api/tools/<tool_id>/preview")
@login_required(api=True)
def preview(tool_id):
    tool = _tool(tool_id)
    if not tool:
        return jsonify({"error": "unknown tool"}), 404
    spec = body()
    est = tool.estimate(spec)
    limit, budget = to_int(spec.get("limit"), 0, 0, 1_000_000), to_int(spec.get("budget"), 0, 0, 10_000_000)
    capped = dict(est)
    if limit:
        capped["max_results"] = min(est["max_results"], limit)
    if budget:
        capped["max_credits"] = min(est["max_credits"], budget)
    return jsonify({**capped, "uncapped_credits": est["max_credits"], "uncapped_results": est["max_results"],
                    "limit": limit, "budget": budget, "tasks": est["tasks"][:300]})


@app.post("/api/tools/<tool_id>/jobs")
@login_required(api=True)
def create_job(tool_id):
    tool = _tool(tool_id)
    if not tool:
        return jsonify({"error": "unknown tool"}), 404
    spec = body()
    tasks = tool.tasks(spec)
    if not tasks:
        return jsonify({"error": "Nothing to run - fill in the inputs for this tab first."}), 400
    if len(tasks) > MAX_TASKS:
        return jsonify({"error": f"{len(tasks)} tasks is too many for one job (max {MAX_TASKS})."}), 400
    uid = session["uid"]
    job_id = time.strftime("%Y%m%d-%H%M%S-") + os.urandom(3).hex()
    summary = {"id": job_id, "tool": tool_id, "name": (str(spec.get("name") or "").strip() or f"{tool.label} job")[:80],
               "status": "running", "created_at": now_iso(), "updated_at": now_iso(), "count": 0, "credits_used": 0,
               "total": len(tasks), "done": 0, "limit": to_int(spec.get("limit"), 0, 0, 1_000_000),
               "budget": to_int(spec.get("budget"), 0, 0, 10_000_000), "stop_reason": ""}
    idx = load_index(uid)
    idx["jobs"].insert(0, summary)
    store.put_json(jobs_index_key(uid), idx)
    return jsonify({"job": summary, "tasks": tasks})


@app.post("/api/tools/<tool_id>/step")
@login_required(api=True)
def run_step(tool_id):
    """One API call for one task. The browser loops over tasks and cursors."""
    tool = _tool(tool_id)
    if not tool:
        return jsonify({"error": "unknown tool"}), 404
    data = body()
    task = data.get("task")
    if not isinstance(task, dict):
        return jsonify({"error": "missing task"}), 400
    claimed = data.get("claimed") if isinstance(data.get("claimed"), list) else []
    try:
        return jsonify(tool.step(MultiKeyClient(apikeys, session["email"]), data.get("spec") or {}, task,
                                 data.get("cursor"), claimed))
    except SerperError as e:
        return jsonify({"error": str(e), "fatal": e.fatal, "rows": [], "credits": 0, "next": None})
    except (KeyError, TypeError, ValueError) as e:
        return jsonify({"error": f"Bad task data: {e}", "fatal": False, "rows": [], "credits": 0, "next": None})


# ================================================================ jobs API

@app.get("/api/jobs")
@login_required(api=True)
def list_jobs():
    jobs = load_index(session["uid"])["jobs"]
    tool = request.args.get("tool")
    return jsonify([j for j in jobs if not tool or j["tool"] == tool])


@app.get("/api/jobs/<job_id>")
@login_required(api=True)
def get_job(job_id):
    if not JOB_ID_RE.match(job_id):
        return jsonify({"error": "bad id"}), 400
    data = store.get_gz(job_key(session["uid"], job_id))
    if data is None:
        return jsonify({"error": "No saved data for this job yet."}), 404
    return Response(data, mimetype="application/octet-stream", headers={"Cache-Control": "no-store"})


@app.put("/api/jobs/<job_id>")
@login_required(api=True)
def save_job(job_id):
    """Checkpoint from the browser: gzip-compressed job JSON."""
    if not JOB_ID_RE.match(job_id):
        return jsonify({"error": "bad id"}), 400
    uid = session["uid"]
    raw = request.get_data(cache=False)
    try:
        doc = json.loads(gzip.decompress(raw))
    except (OSError, ValueError):
        return jsonify({"error": "Checkpoint must be gzip-compressed JSON."}), 400
    idx = load_index(uid)
    entry = next((j for j in idx["jobs"] if j["id"] == job_id), None)
    if not entry:
        return jsonify({"error": "Job not found (it may have been deleted)."}), 404
    store.put_gz(job_key(uid, job_id), raw)

    fields = ("status", "count", "credits_used", "total", "done", "stop_reason", "name", "limit", "budget")
    changed = entry.get("status") != doc.get("status") or entry.get("name") != doc.get("name")
    try:
        stale = time.time() - datetime.fromisoformat(entry["updated_at"]).timestamp() > 900
    except (KeyError, ValueError):
        stale = True
    if changed or stale or doc.get("status") != "running":
        for f in fields:
            if f in doc:
                entry[f] = doc[f]
        entry["updated_at"] = now_iso()
        store.put_json(jobs_index_key(uid), idx)
    return jsonify({"ok": True, "saved_bytes": len(raw)})


@app.delete("/api/jobs/<job_id>")
@login_required(api=True)
def delete_job(job_id):
    if not JOB_ID_RE.match(job_id):
        return jsonify({"error": "bad id"}), 400
    uid = session["uid"]
    idx = load_index(uid)
    before = len(idx["jobs"])
    idx["jobs"] = [j for j in idx["jobs"] if j["id"] != job_id]
    store.delete(job_key(uid, job_id))
    if len(idx["jobs"]) != before:
        store.put_json(jobs_index_key(uid), idx)
    return jsonify({"deleted": len(idx["jobs"]) != before})


# ================================================================ local migration

def migrate_legacy_jobs(uid: str):
    """Jobs made by the earlier single-user version (data/jobs/*.json) go to the first local account."""
    legacy_dir = local_data_dir() / "jobs"
    files = sorted(legacy_dir.glob("*.json")) if legacy_dir.exists() else []
    if not files:
        return
    idx = load_index(uid)
    for path in files:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        tool_id = d.get("tool") or "maps"
        tool = TOOLS.get(tool_id)
        if not tool:
            continue
        rows = d.get("rows") if "rows" in d else d.get("maps", [])
        for r in rows:
            if "search_location" in r:
                r.setdefault("location", r.pop("search_location"))
            r["_key"] = tool.key(r)
        status = d.get("status") if d.get("status") in ("done", "stopped", "error") else "stopped"
        doc = {"id": d["id"], "tool": tool_id, "name": d.get("name") or f"{tool.label} job", "spec": d.get("spec", {}),
               "status": status, "created_at": d.get("created_at"), "updated_at": now_iso(),
               "finished_at": d.get("finished_at"), "total": d.get("total", 0), "done": d.get("done", 0),
               "credits_used": d.get("credits_used", 0), "errors": d.get("errors", 0),
               "counters": d.get("counters", {}), "stop_reason": d.get("stop_reason", ""),
               "limit": 0, "budget": 0, "log": d.get("log", [])[-300:], "rows": rows, "pending": [], "claimed": []}
        store.put_gz(job_key(uid, doc["id"]), gzip.compress(json.dumps(doc, ensure_ascii=False).encode()))
        idx["jobs"].append({k: doc[k] for k in ("id", "tool", "name", "status", "created_at", "updated_at", "total",
                                                 "done", "credits_used", "limit", "budget", "stop_reason")}
                           | {"count": len(rows)})
    idx["jobs"].sort(key=lambda j: j.get("created_at") or "", reverse=True)
    store.put_json(jobs_index_key(uid), idx)
    legacy_dir.rename(legacy_dir.with_name("jobs_imported"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  Waloop Data Scraper running at http://127.0.0.1:{port}  (storage: {store.kind})\n")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
