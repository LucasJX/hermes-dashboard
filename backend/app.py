#!/usr/bin/env python3
"""
Hermes Dashboard Backend — Flask API (port 3801)
"""

import os
import sys
import json
import sqlite3
import subprocess
import time
import calendar

# Force Beijing timezone from the start — eliminates any TZ ambiguity
os.environ['TZ'] = 'Asia/Shanghai'
try:
    time.tzset()
except AttributeError:
    pass  # Not available on Windows

from datetime import datetime, timedelta, timezone
from pathlib import Path
from functools import wraps
from flask import Flask, jsonify, request, send_from_directory, session
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import uuid

# Reusable +08:00 timezone
UTC8 = timezone(timedelta(hours=8))

# ─── Config ───────────────────────────────────────────────────────────────────

HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
DB_PATH = os.path.join(HERMES_HOME, "state.db")
LOGS_DIR = os.path.join(HERMES_HOME, "logs")
SKILLS_DIR = os.path.join(HERMES_HOME, "skills")
GATEWAY_PID_FILE = os.path.join(HERMES_HOME, "gateway.pid")

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("DASHBOARD_SECRET", os.urandom(32).hex())
CORS(app, supports_credentials=True)

# ─── Auth ────────────────────────────────────────────────────────────────────
ACCOUNTS_DB_PATH = os.path.join(HERMES_HOME, "dashboard_auth.db")

# In-memory brute-force protection: {ip: [(ts, ok), ...]}
BRUTE_FORCE = {}   # noqa: F811
MAX_ATTEMPTS = 5
LOCKOUT_SECS = 300

def init_auth_db():
    """Create accounts table if not exists."""
    with sqlite3.connect(ACCOUNTS_DB_PATH) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                username  TEXT    UNIQUE NOT NULL,
                password  TEXT    NOT NULL,
                is_admin  INTEGER NOT NULL DEFAULT 0,
                created_at TEXT   NOT NULL DEFAULT (datetime('now','localtime'))
            )
        """)
        db.commit()
    # Auto-create default admin if no accounts exist
    with sqlite3.connect(ACCOUNTS_DB_PATH) as db:
        row = db.execute("SELECT COUNT(*) FROM accounts").fetchone()
        if row[0] == 0:
            pw_hash = generate_password_hash("admin")
            db.execute("INSERT INTO accounts (username, password, is_admin) VALUES (?, ?, 1)",
                       ("admin", pw_hash))
            db.commit()

def check_brute(ip):
    """Return True if IP is locked out."""
    now = time.time()
    window = BRUTE_FORCE.get(ip, [])
    # Keep only attempts in last LOCKOUT_SECS
    window = [(ts, ok) for ts, ok in window if now - ts < LOCKOUT_SECS]
    BRUTE_FORCE[ip] = window
    failures = sum(1 for ts, ok in window if not ok)
    return failures >= MAX_ATTEMPTS

def record_attempt(ip, ok):
    """Log a login attempt."""
    BRUTE_FORCE.setdefault(ip, []).append((time.time(), ok))

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# Initialize auth DB on module load (works for both `python app.py` and WSGI servers)
init_auth_db()

# ─── Global auth guard ────────────────────────────────────────────────────────
# ─── Token-based auth (bypasses cookie issues with reverse proxies) ─────────
TOKEN_DB_PATH = os.path.join(os.path.dirname(__file__), ".auth_tokens.db")

def init_token_db():
    with sqlite3.connect(TOKEN_DB_PATH) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS auth_tokens (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_token_user ON auth_tokens(user_id)")

init_token_db()

def create_token(user_id, username, is_admin, days=7):
    """Create a short-lived bearer token stored server-side."""
    token = uuid.uuid4().hex + uuid.uuid4().hex
    now = int(time.time())
    with sqlite3.connect(TOKEN_DB_PATH) as db:
        db.execute("DELETE FROM auth_tokens WHERE expires_at < ?", (now,))
        db.execute(
            "INSERT INTO auth_tokens (token, user_id, username, is_admin, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (token, user_id, username, is_admin, now, now + days * 86400)
        )
    return token

def validate_token(token):
    """Return (user_id, username, is_admin) if valid, else None."""
    if not token:
        return None
    with sqlite3.connect(TOKEN_DB_PATH) as db:
        row = db.execute(
            "SELECT user_id, username, is_admin FROM auth_tokens WHERE token = ? AND expires_at > ?",
            (token, int(time.time()))
        ).fetchone()
    return row if row else None

# ─── Bearer-token auth guard (replaces cookie session) ────────────────────────
PUBLIC_PATHS = {"/api/auth/login", "/api/auth/logout", "/api/auth/session",
                "/health", "/api/stats", "/api/quota"}

@app.before_request
def require_auth():
    if request.path in PUBLIC_PATHS:
        return None
    # Try cookie session first, fall back to Bearer token
    uid = session.get("user_id")
    if not uid:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
            row = validate_token(token)
            if row:
                session["user_id"] = row[0]
                session["username"] = row[1]
                session["is_admin"] = bool(row[2])
                session.permanent = True
                uid = row[0]
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    return None

# ─── Model Pricing (USD per 1M tokens) ───────────────────────────────────────
# Input / Output
MODEL_PRICING = {
    "MiniMax-M2.7":  (0.30, 1.20),
    "mimo-v2.5":     (0.40, 2.00),
    "MiniMax-M2.0":  (0.30, 1.20),
}
USD_TO_CNY = 7.25

# ─── Helpers ─────────────────────────────────────────────────────────────────

def get_db():
    return sqlite3.connect(DB_PATH)

def ts_to_iso(ts):
    """Convert Unix timestamp (float seconds) to Beijing-time ISO string."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=UTC8).isoformat()

def calc_cost(model, input_tokens, output_tokens):
    """Calculate cost from token counts + pricing table."""
    key = None
    for k in MODEL_PRICING:
        if k.lower() in model.lower():
            key = k
            break
    if key is None:
        key = "MiniMax-M2.7"  # default
    inp, out = MODEL_PRICING[key]
    usd = (input_tokens / 1_000_000) * inp + (output_tokens / 1_000_000) * out
    return round(usd * USD_TO_CNY, 4)

def get_uptime():
    """Return Hermes uptime by checking gateway.pid (JSON) or gateway.log mtime."""
    import json as _json
    pid = None
    try:
        with open(GATEWAY_PID_FILE) as f:
            raw = f.read().strip()
        try:
            pid_data = _json.loads(raw)
            pid = pid_data.get("pid")
        except Exception:
            pid = int(raw)
    except Exception:
        pass

    if pid:
        try:
            proc = psutil.Process(pid)
            started = datetime.fromtimestamp(proc.create_time())
            delta = datetime.now() - started
            return str(delta).split('.')[0], started.isoformat()
        except Exception:
            pass

    log_path = os.path.join(LOGS_DIR, "gateway.log")
    if os.path.exists(log_path):
        mtime = os.path.getmtime(log_path)
        started = datetime.fromtimestamp(mtime)
        delta = datetime.now() - started
        return str(delta).split('.')[0], started.isoformat()

    return "unknown", None

def check_channel(name):
    """Check real channel status by parsing gateway.log tail and config."""
    import re, json as _json
    config_path = os.path.join(HERMES_HOME, "config.yaml")
    gateway_log = os.path.join(LOGS_DIR, "gateway.log")
    pid_file = GATEWAY_PID_FILE

    cfg_value = None
    try:
        import yaml
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
        cfg_value = cfg.get(name) or cfg.get(name.title()) or cfg.get(name.lower())
    except Exception:
        pass

    has_real_config = cfg_value and isinstance(cfg_value, dict) and len(cfg_value) > 0

    gateway_alive = False
    try:
        if os.path.exists(pid_file):
            raw = open(pid_file).read().strip()
            try:
                pid_data = _json.loads(raw)
                pid = pid_data.get("pid")
            except Exception:
                pid = int(raw)
            if pid:
                psutil.Process(pid)
                gateway_alive = True
    except Exception:
        pass

    tag_map = {
        "Telegram":   (r"\[Telegram\]", "platform=telegram"),
        "Weixin":     (r"\[Weixin\]|\[WeChat\]", "platform=weixin"),
        "Discord":    (r"\[Discord\]", "platform=discord"),
        "Slack":      (r"\[Slack\]", "platform=slack"),
        "WhatsApp":  (r"\[WhatsApp\]", "platform=whatsapp"),
        "Mattermost": (r"\[Mattermost\]", "platform=mattermost"),
    }
    tag_pattern, platform_pattern = tag_map.get(name, (rf"\[{re.escape(name)}\]", rf"platform={name.lower()}"))

    status = "unknown"
    last_active = None

    try:
        fsize = os.path.getsize(gateway_log)
        read_size = min(fsize, 100 * 1024)

        with open(gateway_log, 'rb') as f:
            f.seek(fsize - read_size)
            buf = f.read()
        lines = buf.decode('utf-8', errors='replace').splitlines()

        channel_lines = []
        for line in lines:
            if re.search(tag_pattern, line) or re.search(platform_pattern, line, re.IGNORECASE):
                channel_lines.append(line.rstrip())

        if not channel_lines:
            if has_real_config:
                status = "unused"
            else:
                status = "unconfigured"
            return {"name": name, "status": status, "latency_ms": None, "last_active": None}

        last_line = channel_lines[-1]
        m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", last_line)
        if m:
            last_active = m.group(1)

        lower_line = last_line.lower()
        # "error" is too broad — only match explicit failure patterns
        if "send failed" in lower_line or "rate limited" in lower_line:
            status = "error"
        elif "reconnect" in lower_line or ("network error" in lower_line and "resumed" not in lower_line):
            status = "reconnecting"
        elif "polling resumed" in lower_line or "connected" in lower_line or "started" in lower_line:
            status = "online"
        elif "disconnected" in lower_line:
            status = "disconnected"
        else:
            status = "online"

    except Exception:
        status = "unknown"

    if not gateway_alive:
        status = "offline"

    return {"name": name, "status": status, "latency_ms": None, "last_active": last_active}

def list_skills():
    """List installed skills from SKILLS_DIR, recursively scanning subdirectories."""
    skills = []
    if not os.path.isdir(SKILLS_DIR):
        return skills

    def scan_dir(base_dir):
        """Recursively scan a directory for SKILL.md files."""
        for name in sorted(os.listdir(base_dir)):
            item_path = os.path.join(base_dir, name)
            md_path = os.path.join(item_path, "SKILL.md")

            if os.path.isfile(item_path):
                # Top-level skill file (e.g. a bare SKILL.md at root) — skip for now
                continue

            if os.path.isdir(item_path):
                if os.path.exists(md_path):
                    # This directory IS a skill (has its own SKILL.md)
                    desc = ""
                    category = ""
                    try:
                        with open(md_path) as f:
                            lines = f.readlines()
                        in_frontmatter = False
                        for line in lines:
                            if line.strip() == "---":
                                in_frontmatter = not in_frontmatter
                                continue
                            if in_frontmatter:
                                if line.startswith("description:"):
                                    desc = line.split(":", 1)[1].strip().strip('"')
                                elif line.startswith("category:"):
                                    category = line.split(":", 1)[1].strip().strip('"')
                        if not desc:
                            for line in lines:
                                if not line.startswith("---") and line.startswith("# "):
                                    desc = line[2:].strip()
                                    break
                    except Exception:
                        pass
                    # Fallback: if frontmatter has no category, use the top-level
                    # category (first component of the path relative to SKILLS_DIR,
                    # or the skill's own name for top-level skills)
                    if not category:
                        rel = os.path.relpath(base_dir, SKILLS_DIR)
                        parts = rel.split(os.sep)
                        category = parts[0] if parts[0] != '.' else name
                    skills.append({"name": name, "description": desc, "category": category, "path": str(item_path)})
                else:
                    # No SKILL.md here — it's a category folder, recurse into it
                    scan_dir(item_path)

    scan_dir(SKILLS_DIR)
    return skills

def list_logs():
    """List available log files."""
    logs = []
    if not os.path.isdir(LOGS_DIR):
        return logs
    for fname in sorted(os.listdir(LOGS_DIR)):
        fpath = os.path.join(LOGS_DIR, fname)
        if os.path.isfile(fpath):
            logs.append({"name": fname, "size": os.path.getsize(fpath)})
    return logs

def read_log_file(fname, keyword="", level="", limit=200):
    """Read last N lines from log file efficiently (tail-like)."""
    fpath = os.path.join(LOGS_DIR, fname)
    if not os.path.isfile(fpath):
        return []
    try:
        fsize = os.path.getsize(fpath)
        kw_lower = keyword.lower() if keyword else None
        lvl_upper = level.upper() if level else None

        if not kw_lower and not lvl_upper:
            if fsize == 0:
                return []
            # Tail the last ~2MB to cover several hours of dense log files
            read_size = min(fsize, 2 * 1024 * 1024)
            with open(fpath, 'rb') as f:
                f.seek(fsize - read_size)
                buf = f.read()
            text = buf.decode('utf-8', errors='replace')
            lines = text.splitlines()
            # Drop first line if we skipped content (it's a partial/tail line)
            start = 1 if fsize > read_size else 0
            # Always return the LAST `limit` lines
            return lines[start:][-limit:]

        from collections import deque
        filtered = deque(maxlen=limit)
        with open(fpath, errors="replace") as f:
            for line in f:
                if kw_lower and kw_lower not in line.lower():
                    continue
                if lvl_upper and lvl_upper not in line.upper():
                    continue
                filtered.append(line.rstrip())
        return list(filtered)
    except Exception:
        return []

def get_cron_jobs():
    """Get cron jobs from ~/.hermes/cron/jobs.json."""
    cron_path = os.path.join(HERMES_HOME, "cron", "jobs.json")
    try:
        with open(cron_path) as f:
            data = json.loads(f.read())
        return data.get("jobs", [])
    except Exception:
        return []

def _fmt_remains(seconds):
    """Format remaining seconds as 'Xd Xh XM' or 'Xh XM'."""
    if not seconds:
        return '—'
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = []
    if d > 0: parts.append(f'{d}天')
    if h > 0: parts.append(f'{h}小时')
    if m > 0: parts.append(f'{m}分')
    if not parts: parts.append(f'{s}秒')
    return ''.join(parts)

def _per_round_countdown(end_time_ms):
    """Seconds until next daily 20:00 reset (Beijing time)."""
    if not end_time_ms:
        return None
    import datetime
    reset_utc = datetime.datetime.utcfromtimestamp(end_time_ms / 1000)
    now_utc = datetime.datetime.utcnow()
    delta = reset_utc - now_utc
    total_secs = int(delta.total_seconds())
    return max(0, total_secs)

def get_quota():
    """Get MiniMax quota via mmx CLI — enriched with reset timestamps."""
    try:
        result = subprocess.run(
            ["mmx", "quota", "show"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            try:
                data = json.loads(output)
                models = data.get("model_remains", [])
                quota = {
                    "raw": output,
                    "models": [],
                    "daily_reset_ts": None, "weekly_reset_ts": None,
                    "per_round_limit": None, "per_round_used": None,
                    "weekly_limit": None, "weekly_used": None,
                    "weekly_total": None,
                    "per_round_reset_str": None,
                    "per_round_countdown": None,
                    "weekly_reset_str": None,
                    "monthly_reset_str": None,
                }
                for m in models:
                    name = m.get("model_name", "")
                    weekly_total = m.get("current_weekly_total_count", 0) or 0
                    weekly_used  = m.get("current_weekly_usage_count", 0) or 0
                    interval_total = m.get("current_interval_total_count", 0) or 0
                    interval_used  = m.get("current_interval_usage_count", 0) or 0
                    weekly_end_ts  = m.get("weekly_end_time")
                    interval_end_ts = m.get("end_time")

                    quota["models"].append({
                        "name": name,
                        "weekly_total": weekly_total,
                        "weekly_used": weekly_used,
                        "weekly_remains": max(0, weekly_total - weekly_used),
                        "interval_total": interval_total,
                        "interval_used": interval_used,
                        "interval_remains": max(0, interval_total - interval_used),
                        "per_round_reset_at": interval_end_ts,
                        "monthly_total": weekly_total * 4 if weekly_total > 0 else 0,
                        "monthly_used": weekly_used * 4 if weekly_total > 0 else 0,
                        "monthly_remains": max(0, weekly_total * 4 - weekly_used * 4) if weekly_total > 0 else 0,
                    })

                    # Anchor on MiniMax-M* (or first model with weekly quota)
                    if ("MiniMax-M" in name or quota["weekly_limit"] is None) and weekly_total > 0:
                        quota["weekly_limit"] = weekly_total
                        quota["weekly_used"]  = weekly_used
                        quota["weekly_total"] = weekly_total
                        quota["per_round_limit"] = interval_total
                        quota["per_round_used"]  = interval_used
                        quota["daily_reset_ts"]  = interval_end_ts   # per-round reset (ms)
                        quota["weekly_reset_ts"] = weekly_end_ts     # weekly reset (ms)
                        # Per-round countdown to next 20:00 daily reset
                        countdown = _per_round_countdown(interval_end_ts)
                        quota["per_round_countdown"] = countdown
                        quota["per_round_reset_str"] = _fmt_remains(countdown) if countdown is not None else None

                # Human-readable weekly reset: "05-11 00:00"
                if quota["weekly_reset_ts"]:
                    dt = datetime.datetime.fromtimestamp(quota["weekly_reset_ts"] / 1000)
                    quota["weekly_reset_str"] = dt.strftime("%m-%d %H:%M")

                # Monthly reset: last day of current month 23:59
                if quota["weekly_reset_ts"]:
                    dt = datetime.datetime.fromtimestamp(quota["weekly_reset_ts"] / 1000)
                    last_day = calendar.monthrange(dt.year, dt.month)[1]
                    quota["monthly_reset_str"] = dt.replace(day=last_day, hour=23, minute=59).strftime("%m-%d %H:%M")

                return quota
            except json.JSONDecodeError:
                pass
            return {"raw": output, "models": [], "weekly_reset_str": None,
                    "per_round_reset_str": None, "per_round_countdown": None, "monthly_reset_str": None}
    except Exception:
        pass
    return {"raw": "", "models": [], "daily_reset_ts": None, "weekly_reset_ts": None,
            "per_round_limit": None, "per_round_used": None,
            "weekly_limit": None, "weekly_used": None, "weekly_total": None,
            "per_round_reset_str": None, "per_round_countdown": None,
            "weekly_reset_str": None, "monthly_reset_str": None}

def get_github_releases():
    """Fetch Hermes Agent GitHub releases."""
    try:
        import urllib.request
        url = "https://api.github.com/repos/nousresearch/hermes-agent/releases"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            releases = []
            for r in data[:10]:
                releases.append({
                    "tag": r.get("tag_name", ""),
                    "name": r.get("name", ""),
                    "body": r.get("body", ""),
                    "published_at": r.get("published_at", ""),
                    "html_url": r.get("html_url", ""),
                })
            return releases
    except Exception:
        return []

def get_providers():
    """Discover providers from auth.json + config.yaml."""
    try:
        auth = json.load(open(os.path.join(HERMES_HOME, "auth.json")))
    except Exception:
        auth = {}

    cfg = {}
    try:
        import yaml
        cfg = yaml.safe_load(open(os.path.join(HERMES_HOME, "config.yaml"))) or {}
    except Exception:
        pass

    pool = auth.get("credential_pool", {}) or {}
    active = auth.get("active_provider")
    result = []

    for prov_name, creds in pool.items():
        if not isinstance(creds, list) or not creds:
            continue
        c = creds[0]
        result.append({
            "id": prov_name,
            "name": prov_name,
            "base_url": c.get("base_url", ""),
            "auth_type": c.get("auth_type", "api_key"),
            "status": "active" if c.get("last_status") == "ok" else "error",
            "is_default": prov_name == active,
            "priority": c.get("priority", 0),
        })

    # Also include active model config
    model_cfg = cfg.get("model", {})
    if model_cfg.get("provider") and not any(p["id"] == model_cfg["provider"] for p in result):
        result.insert(0, {
            "id": model_cfg["provider"],
            "name": model_cfg["provider"],
            "base_url": model_cfg.get("base_url", ""),
            "auth_type": "configured",
            "status": "active",
            "is_default": True,
            "priority": -1,
        })

    return result

# ─── Model cache (avoid re-querying all providers every request) ────────────
_MODEL_CACHE = {"data": None, "ts": 0}
_MODEL_CACHE_TTL = 120  # seconds

def get_models():
    """Discover models from /v1/models of each provider — parallel requests, cached."""
    import time as _time
    now = _time.time()
    if _MODEL_CACHE["data"] is not None and now - _MODEL_CACHE["ts"] < _MODEL_CACHE_TTL:
        return _MODEL_CACHE["data"]

    from concurrent.futures import ThreadPoolExecutor, as_completed
    import urllib.request

    providers = get_providers()

    # Fallback model lists for providers whose /v1/models returns 404
    FALLBACK_MODELS = {
        "xiaomi": ["mimo-v2.5", "mimo-v2.5-pro", "mimo-v2-pro", "mimo-v2-flash", "mimo-v2-omni"],
        "xiaomi-token-plan-sgp": ["mimo-v2.5", "mimo-v2.5-pro", "mimo-v2-pro", "mimo-v2-flash", "mimo-v2-omni"],
        "xiaomi-token-plan-cn": ["mimo-v2.5", "mimo-v2.5-pro", "mimo-v2-pro", "mimo-v2-flash", "mimo-v2-omni"],
        "xiaomi-token-plan-ams": ["mimo-v2.5", "mimo-v2.5-pro", "mimo-v2-pro", "mimo-v2-flash", "mimo-v2-omni"],
    }

    # Also load known models from models_dev_cache.json
    _dev_cache_path = os.path.join(HERMES_HOME, "models_dev_cache.json")
    try:
        dev_cache = json.load(open(_dev_cache_path))
        for prov_id, prov_info in dev_cache.items():
            models_dict = prov_info.get("models", {})
            if models_dict and prov_id not in FALLBACK_MODELS:
                FALLBACK_MODELS[prov_id] = list(models_dict.keys())
    except Exception:
        pass

    def fetch_models_for_provider(prov):
        base_url = prov.get("base_url", "")
        api_key = None
        try:
            auth = json.load(open(os.path.join(HERMES_HOME, "auth.json")))
            creds = auth.get("credential_pool", {}).get(prov["id"], [])
            if creds and isinstance(creds, list):
                api_key = creds[0].get("access_token")
        except Exception:
            pass

        if not api_key or not base_url:
            # Try fallback models even without API key
            fb = FALLBACK_MODELS.get(prov["id"], [])
            return [(prov["id"], m, "", None) for m in fb]

        try:
            req = urllib.request.Request(
                base_url.rstrip('/') + "/v1/models",
                headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
                models = [(prov["id"], m.get("id", ""), m.get("owned_by", ""), m.get("created"))
                        for m in data.get("data", []) if m.get("id")]
                if models:
                    return models
        except Exception:
            pass

        # Fallback to known model lists when API fails
        fb = FALLBACK_MODELS.get(prov["id"], [])
        return [(prov["id"], m, "", None) for m in fb]

    all_models = []
    seen = set()

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch_models_for_provider, p): p for p in providers}
        for future in as_completed(futures):
            for prov_id, mid, owned_by, created in future.result():
                if mid and mid not in seen:
                    seen.add(mid)
                    all_models.append({
                        "id": mid,
                        "provider": prov_id,
                        "owned_by": owned_by,
                        "created": created,
                    })

    _MODEL_CACHE["data"] = all_models
    _MODEL_CACHE["ts"] = now
    return all_models

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/api/stats", methods=["GET"])
def api_stats():
    """System overview stats."""
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens) FROM sessions")
    row = cur.fetchone()
    total_sessions = row[0] or 0
    total_input_tokens = row[1] or 0
    total_output_tokens = row[2] or 0
    total_cost = calc_cost("MiniMax-M2.7", total_input_tokens, total_output_tokens)

    cur.execute("SELECT COUNT(*) FROM messages")
    total_messages = cur.fetchone()[0] or 0

    cur.execute("""
        SELECT id, source, model, message_count, input_tokens, output_tokens,
               started_at, ended_at
        FROM sessions
        ORDER BY started_at DESC
        LIMIT 5
    """)
    recent = []
    for r in cur.fetchall():
        recent.append({
            "id": r[0], "source": r[1], "model": r[2],
            "message_count": r[3] or 0,
            "input_tokens": r[4] or 0, "output_tokens": r[5] or 0,
            "cost": calc_cost(r[2] or "MiniMax-M2.7", r[4] or 0, r[5] or 0),
            "started_at": ts_to_iso(r[6]), "ended_at": ts_to_iso(r[7]),
        })

    conn.close()

    uptime, started_iso = get_uptime()

    channels = [
        check_channel("Telegram"),
        check_channel("Weixin"),
        check_channel("Discord"),
        check_channel("Slack"),
        check_channel("WhatsApp"),
        check_channel("Mattermost"),
    ]

    import platform, psutil
    python_v = f"Python {platform.python_version().strip()}"

    try:
        import pathlib
        os_release = {}
        for line in pathlib.Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                os_release[k] = v.strip('"')
        os_name = os_release.get("PRETTY_NAME", os.uname().release)
    except Exception:
        os_name = os.uname().release

    return jsonify({
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_cost_cny": total_cost,
        "recent_sessions": recent,
        "channels": channels,
        "uptime": uptime,
        "system_started": started_iso,
        "python_version": python_v,
        "os_name": os_name,
        "hermes_home": HERMES_HOME,
    })


@app.route("/api/quota_debug2", methods=["GET"])
def api_quota_debug2():
    import subprocess as s, os, json, traceback
    try:
        result = s.run(["mmx", "quota", "show"], capture_output=True, text=True, timeout=15)
        output = result.stdout.strip()
        data = json.loads(output)
        models = data.get("model_remains", [])
        return jsonify({"rc": result.returncode, "models_count": len(models), "output_len": len(output)})
    except Exception as e:
        return jsonify({"error": str(e), "tb": traceback.format_exc()})

@app.route("/api/quota", methods=["GET"])
def api_quota():
    """Token quota — provider-aware. MiniMax uses mmx CLI, others calculate from sessions DB."""
    config_path = os.path.join(HERMES_HOME, "config.yaml")
    current_provider = ""
    current_model = ""
    try:
        import yaml
        cfg = yaml.safe_load(open(config_path)) or {}
        model_cfg = cfg.get("model", {})
        current_provider = model_cfg.get("provider", "")
        current_model = model_cfg.get("default", "")
    except Exception:
        pass

    is_minimax = "minimax" in current_provider.lower()
    if is_minimax:
        # ... existing MiniMax mmx quota logic ...
        try:
            import datetime as dt
            import calendar

            result = subprocess.run(
                ["mmx", "quota", "show"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                return jsonify({"error": "mmx failed", "stderr": result.stderr}), 500

            data = json.loads(result.stdout.strip())
            models = data.get("model_remains", [])
            if not models:
                return jsonify({"error": "no model_remains", "raw": result.stdout[:200]}), 200

            def fmt_remains(seconds):
                if not seconds: return "—"
                s = int(seconds)
                d, s = divmod(s, 86400)
                h, s = divmod(s, 3600)
                m, s = divmod(s, 60)
                parts = []
                if d > 0: parts.append(f"{d}天")
                if h > 0: parts.append(f"{h}小时")
                if m > 0: parts.append(f"{m}分")
                if not parts: parts.append(f"{s}秒")
                return "".join(parts)

            m0 = models[0]
            weekly_total   = m0.get("current_weekly_total_count", 0) or 0
            weekly_used    = m0.get("current_weekly_usage_count", 0) or 0
            interval_total = m0.get("current_interval_total_count", 0) or 0
            interval_used  = m0.get("current_interval_usage_count", 0) or 0
            weekly_end_ms  = m0.get("weekly_end_time")
            interval_end_ms = m0.get("end_time")

            if interval_end_ms:
                reset_utc = dt.datetime.utcfromtimestamp(interval_end_ms / 1000)
                now_utc  = dt.datetime.utcnow()
                countdown = max(0, int((reset_utc - now_utc).total_seconds()))
            else:
                countdown = None

            if weekly_end_ms:
                weekly_reset_str = dt.datetime.fromtimestamp(weekly_end_ms / 1000).strftime("%m-%d %H:%M")
            else:
                weekly_reset_str = None

            monthly_reset_str = None
            if weekly_end_ms:
                wdt = dt.datetime.fromtimestamp(weekly_end_ms / 1000)
                last_day = calendar.monthrange(wdt.year, wdt.month)[1]
                monthly_reset_str = wdt.replace(day=last_day, hour=23, minute=59).strftime("%m-%d %H:%M")

            return jsonify({
                "provider": current_provider,
                "model": current_model,
                "quota_available": True,
                "weekly_limit":   weekly_total,
                "weekly_used":    weekly_used,
                "weekly_total":   weekly_total,
                "per_round_limit": interval_total,
                "per_round_used":  interval_used,
                "per_round_countdown": countdown,
                "per_round_reset_str": fmt_remains(countdown) if countdown is not None else None,
                "weekly_reset_str":   weekly_reset_str,
                "monthly_reset_str":  monthly_reset_str,
                "daily_reset_ts":     interval_end_ms,
                "weekly_reset_ts":    weekly_end_ms,
                "models": [
                    {
                        "name":              mm.get("model_name", ""),
                        "weekly_total":      mm.get("current_weekly_total_count", 0) or 0,
                        "weekly_used":       mm.get("current_weekly_usage_count", 0) or 0,
                        "weekly_remains":    max(0, (mm.get("current_weekly_total_count", 0) or 0) - (mm.get("current_weekly_usage_count", 0) or 0)),
                        "interval_total":    mm.get("current_interval_total_count", 0) or 0,
                        "interval_used":     mm.get("current_interval_usage_count", 0) or 0,
                        "interval_remains":  max(0, (mm.get("current_interval_total_count", 0) or 0) - (mm.get("current_interval_usage_count", 0) or 0)),
                        "monthly_total":     ((mm.get("current_weekly_total_count", 0) or 0) * 4) if (mm.get("current_weekly_total_count", 0) or 0) > 0 else 0,
                        "monthly_used":      ((mm.get("current_weekly_usage_count", 0) or 0) * 4) if (mm.get("current_weekly_total_count", 0) or 0) > 0 else 0,
                        "monthly_remains":   max(0, ((mm.get("current_weekly_total_count", 0) or 0) * 4) - ((mm.get("current_weekly_usage_count", 0) or 0) * 4)) if (mm.get("current_weekly_total_count", 0) or 0) > 0 else 0,
                    }
                    for mm in models
                ],
            })
        except Exception as e:
            import traceback
            return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

    # ── Non-MiniMax provider: calculate usage from sessions DB ──────
    try:
        import datetime as dt
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT model, input_tokens, output_tokens, 
                   COALESCE(estimated_cost_usd, 0) * ?, started_at
            FROM sessions ORDER BY started_at DESC LIMIT 2000
        """, (USD_TO_CNY,))
        rows = cur.fetchall()
        conn.close()

        now = dt.datetime.utcnow()
        today_str = now.strftime("%Y-%m-%d")
        week_start = (now - dt.timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        month_start = now.strftime("%Y-%m-01")

        total_in = total_out = total_cost = 0
        today_in = today_out = today_cost = 0
        week_in = week_out = week_cost = 0
        month_in = month_out = month_cost = 0
        model_stats = {}

        for model, inp, out, cost, started in rows:
            inp = inp or 0; out = out or 0; cost = cost or 0
            total_in += inp; total_out += out; total_cost += cost
            if started:
                day_str = dt.datetime.utcfromtimestamp(started).strftime("%Y-%m-%d")
            else:
                day_str = ""

            if day_str == today_str:
                today_in += inp; today_out += out; today_cost += cost
            if day_str >= week_start:
                week_in += inp; week_out += out; week_cost += cost
            if day_str >= month_start:
                month_in += inp; month_out += out; month_cost += cost

            m = model or "unknown"
            if m not in model_stats:
                model_stats[m] = {"in": 0, "out": 0, "cost": 0, "count": 0}
            model_stats[m]["in"] += inp
            model_stats[m]["out"] += out
            model_stats[m]["cost"] += cost
            model_stats[m]["count"] += 1

        return jsonify({
            "provider": current_provider,
            "model": current_model,
            "quota_available": False,
            "usage_stats": True,
            "totals": {"in": total_in, "out": total_out, "cost": total_cost},
            "today":  {"in": today_in, "out": today_out, "cost": today_cost},
            "week":   {"in": week_in, "out": week_out, "cost": week_cost},
            "month":  {"in": month_in, "out": month_out, "cost": month_cost},
            "models": [{"name": m, **v} for m, v in sorted(model_stats.items(), key=lambda x: x[1]["in"]+x[1]["out"], reverse=True)],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/channels", methods=["GET"])
def api_channels():
    """Channel statuses."""
    return jsonify({
        "channels": [
            check_channel("Telegram"),
            check_channel("Weixin"),
            check_channel("Discord"),
            check_channel("Slack"),
            check_channel("WhatsApp"),
            check_channel("Mattermost"),
        ]
    })


@app.route("/api/sessions", methods=["GET"])
def api_sessions():
    """List sessions with optional ?source= filter."""
    source = request.args.get("source", "")
    limit = int(request.args.get("limit", 200))

    conn = get_db()
    cur = conn.cursor()
    if source:
        cur.execute("""
            SELECT id, source, user_id, model, message_count,
                   input_tokens, output_tokens, started_at, ended_at, end_reason
            FROM sessions
            WHERE source = ?
            ORDER BY started_at DESC
            LIMIT ?
        """, (source, limit))
    else:
        cur.execute("""
            SELECT id, source, user_id, model, message_count,
                   input_tokens, output_tokens, started_at, ended_at, end_reason
            FROM sessions
            ORDER BY started_at DESC
            LIMIT ?
        """, (limit,))
    sessions = []
    for r in cur.fetchall():
        sessions.append({
            "id": r[0], "source": r[1], "user_id": r[2], "model": r[3],
            "message_count": r[4] or 0,
            "input_tokens": r[5] or 0, "output_tokens": r[6] or 0,
            "cost": calc_cost(r[3] or "MiniMax-M2.7", r[5] or 0, r[6] or 0),
            "started_at": ts_to_iso(r[7]), "ended_at": ts_to_iso(r[8]), "end_reason": r[9],
        })
    conn.close()
    return jsonify({"sessions": sessions})


@app.route("/api/messages", methods=["GET"])
def api_messages():
    """Get messages for a session. ?session_id=...&limit=50"""
    session_id = request.args.get("session_id")
    limit = int(request.args.get("limit", 50))

    if not session_id:
        return jsonify({"error": "session_id required"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, session_id, role, content, token_count,
               timestamp, tool_calls, tool_call_id, finish_reason
        FROM messages
        WHERE session_id = ?
        ORDER BY timestamp ASC
        LIMIT ?
    """, (session_id, limit))
    msgs = []
    for r in cur.fetchall():
        tool_calls = []
        try:
            if r[6]:
                tool_calls = json.loads(r[6])
        except Exception:
            pass
        msgs.append({
            "id": r[0], "session_id": r[1], "role": r[2],
            "content": r[3], "token_count": r[4] or 0,
            "timestamp": r[5], "tool_calls": tool_calls,
            "tool_call_id": r[7], "finish_reason": r[8],
        })
    conn.close()
    return jsonify({"messages": msgs})


@app.route("/api/sessions/<session_id>", methods=["GET"])
def api_session_detail(session_id):
    """Session detail + messages."""
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, source, user_id, model, message_count,
               input_tokens, output_tokens, started_at, ended_at,
               end_reason, title, api_call_count
        FROM sessions WHERE id = ?
    """, (session_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Session not found"}), 404

    session = {
        "id": row[0], "source": row[1], "user_id": row[2], "model": row[3],
        "message_count": row[4] or 0,
        "input_tokens": row[5] or 0, "output_tokens": row[6] or 0,
        "cost": calc_cost(row[3] or "MiniMax-M2.7", row[5] or 0, row[6] or 0),
        "started_at": row[7], "ended_at": row[8], "end_reason": row[9],
        "title": row[10], "api_call_count": row[11] or 0,
    }

    cur.execute("""
        SELECT id, role, content, token_count, timestamp
        FROM messages
        WHERE session_id = ?
        ORDER BY timestamp ASC
    """, (session_id,))
    messages = []
    for r in cur.fetchall():
        messages.append({
            "id": r[0], "role": r[1], "content": (r[2] or "")[:500],
            "token_count": r[3] or 0, "created_at": r[4],
        })
    conn.close()
    return jsonify({**session, "messages": messages})


@app.route("/api/skills", methods=["GET"])
def api_skills():
    """List installed skills."""
    skills = list_skills()
    return jsonify({"skills": skills})


@app.route("/api/providers", methods=["GET"])
def api_providers():
    """List configured providers."""
    return jsonify({"providers": get_providers()})


@app.route("/api/models", methods=["GET"])
def api_models():
    """List available models from all providers."""
    return jsonify({"models": get_models()})


@app.route("/api/config/model", methods=["GET"])
def api_config_model_get():
    """Return current model config from config.yaml.
    If provider matches a custom_provider, return that provider's base_url."""
    config_path = os.path.join(HERMES_HOME, "config.yaml")
    try:
        import yaml
        cfg = yaml.safe_load(open(config_path)) or {}
    except Exception:
        cfg = {}
    model_cfg = cfg.get("model", {})
    provider = model_cfg.get("provider", "")
    base_url = model_cfg.get("base_url", "")

    # Check custom_providers for a matching provider name
    for cp in cfg.get("custom_providers", []):
        if cp.get("name") == provider:
            base_url = cp.get("base_url", base_url)
            break

    return jsonify({
        "provider": provider,
        "base_url": base_url,
        "api_key": model_cfg.get("api_key", ""),
        "model": model_cfg.get("default", ""),
    })


@app.route("/api/config/model", methods=["POST"])
def api_config_model():
    """Update model config in config.yaml. Body: {provider, base_url, api_key, model}. Returns updated config."""
    body = request.get_json() or {}
    provider = (body.get("provider") or "").strip()
    base_url = (body.get("base_url") or "").strip()
    api_key  = (body.get("api_key")  or "").strip()
    model    = (body.get("model")    or "").strip()

    if not provider:
        return jsonify({"error": "provider is required"}), 400

    config_path = os.path.join(HERMES_HOME, "config.yaml")
    cfg = {}
    try:
        import yaml
        cfg = yaml.safe_load(open(config_path)) or {}
    except Exception as e:
        return jsonify({"error": f"Failed to read config: {e}"}), 500

    if "model" not in cfg:
        cfg["model"] = {}
    cfg["model"]["provider"] = provider
    if base_url:
        cfg["model"]["base_url"] = base_url
    if api_key:
        cfg["model"]["api_key"] = api_key
    if model:
        cfg["model"]["default"] = model

    try:
        import yaml
        with open(config_path, "w") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        return jsonify({"error": f"Failed to write config: {e}"}), 500

    # Notify hermes gateway to reload (SIGUSR1)
    try:
        pid_file = os.path.join(HERMES_HOME, "gateway.pid")
        with open(pid_file) as pf:
            raw = pf.read().strip()
        try:
            pid = int(json.loads(raw).get("pid"))
        except Exception:
            pid = int(raw)
        os.kill(pid, 10)  # SIGUSR1
    except Exception:
        pass

    # Invalidate model cache so next request fetches fresh
    _MODEL_CACHE["data"] = None
    _MODEL_CACHE["ts"] = 0

    return jsonify({"ok": True, "config": cfg["model"]})


@app.route("/api/cron", methods=["GET"])
def api_cron():
    """List cron jobs."""
    jobs = get_cron_jobs()
    return jsonify({"jobs": jobs})


@app.route("/api/cron", methods=["POST"])
def api_cron_create():
    """Create a cron job. Body: {schedule, prompt, name?, deliver?, repeat?, skills?, script?, workdir?}"""
    body = request.get_json() or {}
    schedule = body.get("schedule", "")
    prompt = body.get("prompt", "")
    name = body.get("name", "")
    deliver = body.get("deliver", "")
    repeat = body.get("repeat")

    if not schedule:
        return jsonify({"error": "schedule required"}), 400

    cmd = ["hermes", "cron", "create"]
    if name:
        cmd += ["--name", name]
    if deliver:
        cmd += ["--deliver", deliver]
    if repeat:
        cmd += ["--repeat", str(repeat)]
    for skill in (body.get("skills") or []):
        cmd += ["--skill", skill]
    if body.get("script"):
        cmd += ["--script", body["script"]]
    if body.get("workdir"):
        cmd += ["--workdir", body["workdir"]]
    cmd.append(schedule)
    if prompt:
        cmd.append(prompt)

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=HERMES_HOME)
        if r.returncode == 0:
            return jsonify({"ok": True, "output": r.stdout})
        else:
            return jsonify({"ok": False, "error": r.stderr}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cron/<job_id>", methods=["PUT"])
def api_cron_edit(job_id):
    """Edit a cron job. Body: {schedule?, prompt?, name?, deliver?, repeat?}"""
    body = request.get_json() or {}
    cmd = ["hermes", "cron", "edit", job_id]
    if body.get("schedule"):
        cmd += ["--schedule", body["schedule"]]
    if body.get("prompt"):
        cmd += ["--prompt", body["prompt"]]
    if body.get("name"):
        cmd += ["--name", body["name"]]
    if body.get("deliver"):
        cmd += ["--deliver", body["deliver"]]
    if body.get("repeat"):
        cmd += ["--repeat", str(body["repeat"])]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=HERMES_HOME)
        if r.returncode == 0:
            return jsonify({"ok": True, "output": r.stdout})
        else:
            return jsonify({"ok": False, "error": r.stderr}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cron/<job_id>", methods=["DELETE"])
def api_cron_delete(job_id):
    """Delete a cron job."""
    try:
        r = subprocess.run(["hermes", "cron", "remove", job_id],
                          capture_output=True, text=True, timeout=10, cwd=HERMES_HOME)
        if r.returncode == 0:
            return jsonify({"ok": True})
        else:
            return jsonify({"ok": False, "error": r.stderr}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cron/<job_id>/pause", methods=["POST"])
def api_cron_pause(job_id):
    try:
        r = subprocess.run(["hermes", "cron", "pause", job_id],
                          capture_output=True, text=True, timeout=10, cwd=HERMES_HOME)
        return jsonify({"ok": r.returncode == 0, "output": r.stdout, "error": r.stderr})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/cron/<job_id>/resume", methods=["POST"])
def api_cron_resume(job_id):
    try:
        r = subprocess.run(["hermes", "cron", "resume", job_id],
                          capture_output=True, text=True, timeout=10, cwd=HERMES_HOME)
        return jsonify({"ok": r.returncode == 0, "output": r.stdout, "error": r.stderr})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/cron/<job_id>/run", methods=["POST"])
def api_cron_run(job_id):
    try:
        r = subprocess.run(["hermes", "cron", "run", job_id],
                          capture_output=True, text=True, timeout=30, cwd=HERMES_HOME)
        return jsonify({"ok": r.returncode == 0, "output": r.stdout, "error": r.stderr})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/logs", methods=["GET"])
def api_logs():
    """List log files or read content."""
    action = request.args.get("action", "list")
    if action == "list":
        return jsonify({"logs": list_logs()})

    fname = request.args.get("file", "agent.log")
    keyword = request.args.get("keyword", "")
    level = request.args.get("level", "")
    limit = int(request.args.get("limit", 200))
    lines = read_log_file(fname, keyword, level, limit)
    return jsonify({"file": fname, "lines": lines, "count": len(lines)})


@app.route("/api/terminal/exec", methods=["POST"])
def api_terminal_exec():
    """Execute a shell command. Body: {cmd: string}"""
    body = request.get_json() or {}
    cmd = body.get("cmd", "")
    if not cmd:
        return jsonify({"error": "No command provided"}), 400

    # Security: restrict to safe read-only commands for web-facing use
    allowed = ["ls", "cat", "pwd", "whoami", "uptime", "date", "df", "free", "top",
               "ps", "hermes", "mmx", "python3", "python", "node", "npm", "git"]
    base = cmd.strip().split()[0] if cmd.strip() else ""
    if base not in allowed:
        return jsonify({"error": f"Command '{base}' not allowed"}), 403

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30,
            cwd=HERMES_HOME
        )
        return jsonify({
            "stdout": result.stdout, "stderr": result.stderr,
            "returncode": result.returncode
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/releases", methods=["GET"])
def api_releases():
    """Fetch GitHub releases."""
    return jsonify({"releases": get_github_releases()})


@app.route("/api/system", methods=["GET"])
def api_system():
    """System info: Hermes version, model, provider, Python version, uptime."""
    import platform, psutil

    hermes_version = "unknown"
    try:
        ver = subprocess.run(
            ["hermes", "--version"],
            capture_output=True, text=True, timeout=5
        )
        if ver.returncode == 0:
            first_line = ver.stdout.strip().split("\n")[0]
            if "v" in first_line:
                hermes_version = first_line.split("v")[1].split(" ")[0]
    except Exception:
        pass

    model = provider = base_url = "unknown"
    cfg_path = os.path.join(HERMES_HOME, "config.yaml")
    try:
        import yaml
        cfg = yaml.safe_load(open(cfg_path))
        model = cfg.get("model", {}).get("default", "unknown")
        provider = cfg.get("model", {}).get("provider", "unknown")
        base_url = cfg.get("model", {}).get("base_url", "unknown")
    except Exception:
        pass

    try:
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
    except Exception:
        cpu = mem = disk = None

    try:
        import pathlib
        os_release = {}
        for line in pathlib.Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                os_release[k] = v.strip('"')
        os_name = os_release.get("PRETTY_NAME", platform.platform())
    except Exception:
        os_name = platform.platform()

    uptime = None
    system_started = None
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT MIN(started_at) FROM sessions"
        ).fetchone()
        conn.close()
        if row and row[0]:
            start_ts = float(row[0])
            system_started = ts_to_iso(start_ts)
            uptime_sec = time.time() - start_ts
            h = int(uptime_sec // 3600)
            m = int((uptime_sec % 3600) // 60)
            s = int(uptime_sec % 60)
            uptime = f"{h:02d}:{m:02d}:{s:02d}"
    except Exception:
        pass

    return jsonify({
        "hermes_version": hermes_version,
        "model": model,
        "provider": provider,
        "base_url": base_url,
        "python_version": platform.python_version(),
        "uptime": uptime,
        "system_started": system_started,
        "platform": os_name,
        "cpu_percent": cpu,
        "memory_percent": mem.percent if mem else None,
        "memory_used_gb": round(mem.used / (1024**3), 1) if mem else None,
        "disk_percent": disk.percent if disk else None,
        "disk_used_gb": round(disk.used / (1024**3), 1) if disk else None,
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "ts": datetime.now().isoformat()})


# ─── Auth API ─────────────────────────────────────────────────────────────────

@app.route("/api/auth/login", methods=["POST"])
def api_login():
    ip = request.remote_addr or "127.0.0.1"
    if check_brute(ip):
        return jsonify({"error": "次数过多，请5分钟后再试"}), 429

    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        record_attempt(ip, False)
        return jsonify({"error": "请输入用户名和密码"}), 400

    with sqlite3.connect(ACCOUNTS_DB_PATH) as db:
        row = db.execute(
            "SELECT id, username, password, is_admin FROM accounts WHERE username = ?",
            (username,)
        ).fetchone()

    if not row or not check_password_hash(row[2], password):
        record_attempt(ip, False)
        return jsonify({"error": "用户名或密码错误"}), 401

    record_attempt(ip, True)
    session["user_id"] = row[0]
    session["username"] = row[1]
    session["is_admin"] = bool(row[3])
    session.permanent = True
    app.permanent_session_lifetime = timedelta(days=7)
    token = create_token(row[0], row[1], row[3])
    return jsonify({
        "ok": True,
        "user_id": row[0],
        "username": row[1],
        "is_admin": bool(row[3]),
        "token": token
    })


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/auth/session", methods=["GET"])
def api_session():
    uid = session.get("user_id")
    # Also check Bearer token (needed when cookie is lost to reverse proxy)
    if not uid:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            row = validate_token(auth[7:])
            if row:
                session["user_id"] = row[0]
                session["username"] = row[1]
                session["is_admin"] = bool(row[2])
                session.permanent = True
                uid = row[0]
    if not uid:
        return jsonify({"logged_in": False})
    return jsonify({
        "logged_in": True,
        "username": session.get("username"),
        "is_admin": session.get("is_admin")
    })


@app.route("/api/auth/accounts", methods=["GET"])
@login_required
def api_accounts_list():
    if not session.get("is_admin"):
        return jsonify({"error": "需要管理员权限"}), 403
    with sqlite3.connect(ACCOUNTS_DB_PATH) as db:
        rows = db.execute(
            "SELECT id, username, is_admin, created_at FROM accounts ORDER BY id"
        ).fetchall()
    return jsonify([{
        "id": r[0], "username": r[1],
        "is_admin": bool(r[2]), "created_at": r[3]
    } for r in rows])


@app.route("/api/auth/accounts", methods=["POST"])
@login_required
def api_accounts_create():
    if not session.get("is_admin"):
        return jsonify({"error": "需要管理员权限"}), 403
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    is_admin = bool(data.get("is_admin"))

    if not username or len(username) < 2:
        return jsonify({"error": "用户名至少2个字符"}), 400
    if not password or len(password) < 6:
        return jsonify({"error": "密码至少6个字符"}), 400

    pw_hash = generate_password_hash(password)
    try:
        with sqlite3.connect(ACCOUNTS_DB_PATH) as db:
            db.execute(
                "INSERT INTO accounts (username, password, is_admin) VALUES (?, ?, ?)",
                (username, pw_hash, is_admin)
            )
            db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "用户名已存在"}), 409

    return jsonify({"ok": True, "username": username})


@app.route("/api/auth/accounts/<int:account_id>", methods=["DELETE"])
@login_required
def api_accounts_delete(account_id):
    if not session.get("is_admin"):
        return jsonify({"error": "需要管理员权限"}), 403
    if account_id == session.get("user_id"):
        return jsonify({"error": "不能删除当前登录账号"}), 400
    with sqlite3.connect(ACCOUNTS_DB_PATH) as db:
        db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        db.commit()
    return jsonify({"ok": True})


@app.route("/api/auth/accounts/password", methods=["PUT"])
@login_required
def api_accounts_password():
    data = request.get_json() or {}
    account_id = data.get("id")
    new_password = data.get("password") or ""

    if not session.get("is_admin") and session.get("user_id") != account_id:
        return jsonify({"error": "权限不足"}), 403

    if not new_password or len(new_password) < 6:
        return jsonify({"error": "密码至少6个字符"}), 400

    pw_hash = generate_password_hash(new_password)
    with sqlite3.connect(ACCOUNTS_DB_PATH) as db:
        db.execute("UPDATE accounts SET password = ? WHERE id = ?", (pw_hash, account_id))
        db.commit()
    return jsonify({"ok": True})


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import psutil
    init_auth_db()
    print(f"[Hermes Dashboard Backend] Starting on port 3801")
    print(f"  HERMES_HOME: {HERMES_HOME}")
    print(f"  DB: {DB_PATH}")
    app.run(host="0.0.0.0", port=3801, debug=False)
