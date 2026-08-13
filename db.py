"""
db.py — the multi-customer store (data/dauroo.db).
==================================================

ONE SQLite file, WAL enabled, shared by the two bot processes (owner +
customer). WAL and a busy timeout are set from the first connection because two
OS processes write here.

The design rule that matters most: **every accessor takes customer_id and is
scoped by it.** There is deliberately no unscoped `get_account(id)` sitting next
to a scoped one, because one forgotten argument is a cross-customer data leak
and there is no test that would catch it.

Two things are worth reading before changing anything:

* `accounts.phone` is NOT unique. It is unique per (customer_id, phone). Two
  customers may legitimately add the same number, and the account KEY handed to
  the engine is the row id as a string -- never the phone. If the key were the
  phone, two customers' sessions, pool leases and locks would collide.

* `events` is ONE table with four jobs: the log feed, the rate-limit counter,
  the "add an account" quota, and the per-customer timeline. That is on purpose.
  If the list of things logged and the list of things counted toward the block
  ever diverge, a block becomes unexplainable -- you see someone blocked and
  cannot find the actions in the log. Keeping them the same rows makes that
  impossible.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from config import config

#: مسیر دیتابیس از `DATA_DIR` می‌آید، نه از محل این فایل.
#: اگر نسبت به `__file__` حساب می‌شد، تنظیم `DATA_DIR` روی دیتابیس بی‌اثر بود و
#: استقرارهای جدا (یا یک اجرای تست) بی‌خبر همان فایل را به اشتراک می‌گذاشتند.
DB_PATH = Path(config.DATA_DIR) / "dauroo.db"

#: Events older than this are pruned. The add-quota window only needs 24h; the
#: rest is kept so the owner's timeline view has history to show.
EVENT_RETENTION_DAYS = 30


#: True پس از اینکه schema یک بار در این پروسه تضمین شد.
_ready = False


def _raw_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # timeout + busy_timeout: پروسه‌ی مالک و مشتری یک فایل را به اشتراک
    # می‌گذارند، پس منتظر آزادشدن قفل نوشتن بمان نه اینکه فوراً
    # "database is locked" بدهی.
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _conn() -> sqlite3.Connection:
    """یک کانکشن، با تضمین اینکه schema وجود دارد.

    خودش `init()` را در اولین استفاده صدا می‌زند تا هیچ ماژولی به ترتیبِ
    راه‌اندازی وابسته نباشد: هر کسی که `db` را لمس کند جدول‌ها را آماده می‌بیند،
    حتی اگر پیش از `amain()` باشد. `init()` هم idempotent است.
    """
    global _ready
    if not _ready:
        _ready = True          # قبل از init ست می‌شود تا بازگشتی نشود
        try:
            init()
        except Exception as exc:  # noqa: BLE001
            print(f"[db init] {exc}", flush=True)
    return _raw_conn()


def init() -> None:
    """همه‌چیز را می‌سازد. Idempotent؛ هر دو ربات در startup صدایش می‌زنند."""
    global _ready
    _ready = True
    conn = _raw_conn()
    c = conn.cursor()

    # ---- customers (the tenant table; the tenant id IS the Telegram user id)
    c.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            telegram_id INTEGER PRIMARY KEY,
            name        TEXT DEFAULT '',
            username    TEXT DEFAULT '',
            created_at  REAL,
            blocked     INTEGER DEFAULT 0,
            blocked_at  REAL,
            block_reason TEXT DEFAULT '',
            note        TEXT DEFAULT '',
            last_seen   REAL
        )
    """)

    # ---- accounts
    # id is the surrogate key handed to the engine as a string. phone is NOT
    # globally unique -- see the module docstring.
    c.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id   INTEGER NOT NULL,
            phone         TEXT NOT NULL,
            status        TEXT DEFAULT 'active',
            contacts      INTEGER DEFAULT 0,
            pvs           INTEGER DEFAULT 0,
            with_hash     INTEGER DEFAULT 0,
            total_sent    INTEGER DEFAULT 0,
            seq           INTEGER DEFAULT 0,
            added_at      REAL,
            last_run_json TEXT DEFAULT '',
            UNIQUE (customer_id, phone)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_acc_cust ON accounts(customer_id, seq)")

    # ---- per-customer content + preferences
    c.execute("""
        CREATE TABLE IF NOT EXISTS customer_settings (
            customer_id     INTEGER PRIMARY KEY,
            content_kind    TEXT DEFAULT '',
            content_text    TEXT DEFAULT '',
            content_path    TEXT DEFAULT '',
            content_name    TEXT DEFAULT '',
            content_caption TEXT DEFAULT '',
            content_sha256  TEXT DEFAULT '',
            content_size    INTEGER DEFAULT 0,
            photo_direction TEXT DEFAULT 'both',
            selected_json   TEXT DEFAULT ''
        )
    """)

    # ---- owner-global settings (engine, delays, pool size, ...)
    # The engine reads these through bot/store.py, which is why the copied
    # runner/warmpath/boost code needed no changes at all.
    c.execute("""
        CREATE TABLE IF NOT EXISTS owner_settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # ---- jobs (durable, so a restart can report honestly)
    c.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id      TEXT PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            account_id  INTEGER,
            kind        TEXT,
            state       TEXT DEFAULT 'queued',
            engine      TEXT DEFAULT '',
            trace_id    TEXT DEFAULT '',
            cust_msg_id INTEGER DEFAULT 0,
            log_msg_id  INTEGER DEFAULT 0,
            sent        INTEGER DEFAULT 0,
            failed      INTEGER DEFAULT 0,
            skipped     INTEGER DEFAULT 0,
            total       INTEGER DEFAULT 0,
            last_error  TEXT DEFAULT '',
            started_at  REAL,
            finished_at REAL
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_jobs_cust ON jobs(customer_id, started_at)")

    # ---- events: log feed + rate counter + add quota + timeline (see docstring)
    c.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            kind        TEXT NOT NULL,
            label       TEXT DEFAULT '',
            summary     TEXT DEFAULT '',
            trace_id    TEXT DEFAULT '',
            counted     INTEGER DEFAULT 1,
            at          REAL NOT NULL
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_ev_rate ON events(customer_id, counted, at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ev_kind ON events(customer_id, kind, at)")

    # ---- owner -> customer outbox
    # The owner bot has a different token and cannot DM a customer who never
    # started it, so owner actions queue here and the customer bot delivers.
    c.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            text        TEXT NOT NULL,
            sent        INTEGER DEFAULT 0,
            created_at  REAL
        )
    """)

    # ---- relays (SSH → Telegram; owner-only, see relay/)
    # The password is stored ENCRYPTED in secret_enc (never plaintext -- see
    # relay/crypto.py). host_key holds the server key captured on first connect
    # (trust-on-first-use); a later mismatch refuses the connection. status is
    # telemetry the guardian loop maintains: idle | active | broken | disabled.
    c.execute("""
        CREATE TABLE IF NOT EXISTS relays (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            host         TEXT NOT NULL,
            ssh_port     INTEGER DEFAULT 22,
            username     TEXT DEFAULT 'root',
            auth_kind    TEXT DEFAULT 'password',
            secret_enc   TEXT DEFAULT '',
            host_key     TEXT DEFAULT '',
            status       TEXT DEFAULT 'idle',
            priority     INTEGER DEFAULT 100,
            fail_count   INTEGER DEFAULT 0,
            disconnects  INTEGER DEFAULT 0,
            last_check   REAL,
            last_ok      REAL,
            last_ping_ms INTEGER DEFAULT 0,
            last_error   TEXT DEFAULT '',
            source       TEXT DEFAULT 'panel',
            added_at     REAL,
            UNIQUE (host, ssh_port)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_relay_pri ON relays(priority, id)")

    conn.commit()
    conn.close()


# =========================================================================== #
# customers
# =========================================================================== #
def ensure_customer(telegram_id: int, name: str = "", username: str = "") -> dict:
    """Create the row on first gated action (idempotent), refresh name, return it."""
    now = time.time()
    conn = _conn()
    conn.execute(
        "INSERT OR IGNORE INTO customers (telegram_id, name, username, created_at, last_seen) "
        "VALUES (?, ?, ?, ?, ?)",
        (int(telegram_id), name or "", username or "", now, now))
    conn.execute("UPDATE customers SET name=?, username=?, last_seen=? WHERE telegram_id=?",
                 (name or "", username or "", now, int(telegram_id)))
    conn.commit()
    conn.close()
    return get_customer(telegram_id) or {}


def get_customer(telegram_id: int):
    conn = _conn()
    row = conn.execute("SELECT * FROM customers WHERE telegram_id=?",
                       (int(telegram_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def is_blocked(telegram_id: int) -> bool:
    conn = _conn()
    row = conn.execute("SELECT blocked FROM customers WHERE telegram_id=?",
                       (int(telegram_id),)).fetchone()
    conn.close()
    return bool(row and row["blocked"])


def all_blocked_ids() -> list:
    """Every blocked id, to warm the in-memory cache at boot.

    The cache is what makes a blocked user cost nothing: without it, every
    message from a spammer is a disk read.
    """
    conn = _conn()
    rows = conn.execute("SELECT telegram_id FROM customers WHERE blocked=1").fetchall()
    conn.close()
    return [int(r["telegram_id"]) for r in rows]


def set_blocked(telegram_id: int, blocked: bool, reason: str = "") -> None:
    conn = _conn()
    conn.execute(
        "UPDATE customers SET blocked=?, blocked_at=?, block_reason=? WHERE telegram_id=?",
        (1 if blocked else 0, time.time() if blocked else None,
         reason if blocked else "", int(telegram_id)))
    conn.commit()
    conn.close()


def set_note(telegram_id: int, note: str) -> None:
    conn = _conn()
    conn.execute("UPDATE customers SET note=? WHERE telegram_id=?",
                 (str(note)[:400], int(telegram_id)))
    conn.commit()
    conn.close()


#: Owner-panel filters, expressed in SQL so only one page is ever read.
_FILTERS = {
    "all": "",
    "active": "WHERE c.blocked = 0",
    "blocked": "WHERE c.blocked = 1",
    "withacc": "WHERE EXISTS (SELECT 1 FROM accounts a WHERE a.customer_id = c.telegram_id)",
    "noacc": "WHERE NOT EXISTS (SELECT 1 FROM accounts a WHERE a.customer_id = c.telegram_id)",
}


def count_customers(filter_key: str = "all") -> int:
    where = _FILTERS.get(filter_key, "")
    conn = _conn()
    n = conn.execute(f"SELECT COUNT(*) AS n FROM customers c {where}").fetchone()["n"]
    conn.close()
    return int(n)


def list_customers_page(offset: int, limit: int, filter_key: str = "all") -> list:
    where = _FILTERS.get(filter_key, "")
    conn = _conn()
    rows = conn.execute(
        f"SELECT * FROM customers c {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (int(limit), int(offset))).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_customers(term: str, limit: int = 30) -> list:
    """By id, name, username -- or by ANY of their accounts' phone numbers.

    The phone search is the one that matters in practice: a problem gets
    reported as a number, not as a Telegram id.
    """
    like = f"%{(term or '').strip()}%"
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM customers WHERE CAST(telegram_id AS TEXT) LIKE ? "
        "OR name LIKE ? OR username LIKE ? "
        "OR telegram_id IN (SELECT customer_id FROM accounts WHERE phone LIKE ?) "
        "ORDER BY created_at DESC LIMIT ?",
        (like, like, like, like, int(limit))).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def owner_totals() -> dict:
    """Fleet-wide numbers for the owner dashboard."""
    conn = _conn()
    q = conn.execute
    out = {
        "customers": q("SELECT COUNT(*) n FROM customers").fetchone()["n"],
        "blocked": q("SELECT COUNT(*) n FROM customers WHERE blocked=1").fetchone()["n"],
        "accounts": q("SELECT COUNT(*) n FROM accounts").fetchone()["n"],
        "ready": q("SELECT COUNT(*) n FROM accounts WHERE contacts>0").fetchone()["n"],
        "fast_ready": q("SELECT COUNT(*) n FROM accounts "
                        "WHERE contacts>0 AND with_hash>=contacts").fetchone()["n"],
        "contacts": q("SELECT COALESCE(SUM(contacts),0) n FROM accounts").fetchone()["n"],
        "sent": q("SELECT COALESCE(SUM(total_sent),0) n FROM accounts").fetchone()["n"],
        "logins_today": q("SELECT COUNT(*) n FROM events WHERE kind='account_added' "
                          "AND at > ?", (time.time() - 86400,)).fetchone()["n"],
    }
    conn.close()
    return {k: int(v) for k, v in out.items()}


# =========================================================================== #
# accounts  (every accessor is scoped by customer_id)
# =========================================================================== #
def add_account(customer_id: int, phone: str) -> int:
    """Insert and return the new account id (the engine's account KEY).

    Unique on (customer_id, phone): re-adding the same number for the same
    customer reuses the row instead of creating a duplicate.
    """
    conn = _conn()
    c = conn.cursor()
    seq = c.execute("SELECT COALESCE(MAX(seq),0)+1 AS s FROM accounts WHERE customer_id=?",
                    (int(customer_id),)).fetchone()["s"]
    c.execute(
        "INSERT INTO accounts (customer_id, phone, status, added_at, seq) "
        "VALUES (?, ?, 'active', ?, ?) "
        "ON CONFLICT(customer_id, phone) DO UPDATE SET status='active'",
        (int(customer_id), str(phone), time.time(), int(seq)))
    conn.commit()
    row = c.execute("SELECT id FROM accounts WHERE customer_id=? AND phone=?",
                    (int(customer_id), str(phone))).fetchone()
    conn.close()
    return int(row["id"])


def get_account(customer_id: int, account_id: int):
    """Fetch an account ONLY if it belongs to this customer."""
    conn = _conn()
    row = conn.execute("SELECT * FROM accounts WHERE id=? AND customer_id=?",
                       (int(account_id), int(customer_id))).fetchone()
    conn.close()
    return dict(row) if row else None


def get_account_any(account_id: int):
    """Owner-only: fetch without a customer scope (for the owner panel)."""
    conn = _conn()
    row = conn.execute("SELECT * FROM accounts WHERE id=?", (int(account_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_accounts(customer_id: int) -> list:
    conn = _conn()
    rows = conn.execute("SELECT * FROM accounts WHERE customer_id=? ORDER BY seq, id",
                        (int(customer_id),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_accounts(customer_id: int) -> int:
    conn = _conn()
    n = conn.execute("SELECT COUNT(*) AS n FROM accounts WHERE customer_id=?",
                     (int(customer_id),)).fetchone()["n"]
    conn.close()
    return int(n)


def phone_taken(customer_id: int, phone: str) -> bool:
    conn = _conn()
    row = conn.execute("SELECT 1 FROM accounts WHERE customer_id=? AND phone=?",
                       (int(customer_id), str(phone))).fetchone()
    conn.close()
    return bool(row)


def delete_account(customer_id: int, account_id: int) -> bool:
    conn = _conn()
    cur = conn.execute("DELETE FROM accounts WHERE id=? AND customer_id=?",
                       (int(account_id), int(customer_id)))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def set_account_meta(account_id: int, **fields) -> None:
    """Update contacts / pvs / with_hash / status / total_sent for one account.

    Not customer-scoped on purpose: this is called by the ENGINE, which already
    only ever holds keys it was given. Unknown fields are ignored so a future
    engine change cannot crash it.
    """
    allowed = {"phone", "contacts", "pvs", "with_hash", "status", "total_sent"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return
    vals.append(int(account_id))
    conn = _conn()
    conn.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()
    conn.close()


def set_account_seq(account_id: int, seq: int) -> None:
    """جایگاه اکانت در فهرست مشتری. اکانت تازه آخر می‌رود، نه اول."""
    conn = _conn()
    conn.execute("UPDATE accounts SET seq = ? WHERE id = ?",
                 (int(seq), int(account_id)))
    conn.commit()
    conn.close()


def bump_account_sent(account_id: int, n: int) -> None:
    if int(n) <= 0:
        return
    conn = _conn()
    conn.execute("UPDATE accounts SET total_sent = total_sent + ? WHERE id=?",
                 (int(n), int(account_id)))
    conn.commit()
    conn.close()


def set_account_last_run(account_id: int, payload: dict) -> None:
    conn = _conn()
    conn.execute("UPDATE accounts SET last_run_json=? WHERE id=?",
                 (json.dumps(payload, ensure_ascii=False), int(account_id)))
    conn.commit()
    conn.close()


def newest_last_run() -> dict:
    """The most recent run's payload, used only as an ETA hint.

    The engine asks for this without an account (it just wants a per-send
    duration to estimate with), so the newest one across the fleet is the right
    answer and matches the original single-owner behaviour.
    """
    conn = _conn()
    row = conn.execute("SELECT last_run_json FROM accounts WHERE last_run_json != '' "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    if not row:
        return {}
    try:
        return json.loads(row["last_run_json"]) or {}
    except (ValueError, TypeError):
        return {}


def customer_totals(customer_id: int) -> dict:
    """Per-customer numbers for their own panel. Scoped, always."""
    conn = _conn()
    row = conn.execute(
        "SELECT COUNT(*) AS accounts, "
        "  COALESCE(SUM(contacts),0) AS contacts, "
        "  COALESCE(SUM(total_sent),0) AS sent, "
        "  SUM(CASE WHEN contacts>0 AND with_hash>=contacts THEN 1 ELSE 0 END) AS fast, "
        "  SUM(CASE WHEN contacts>0 AND with_hash<contacts THEN 1 ELSE 0 END) AS browser, "
        "  SUM(CASE WHEN contacts=0 THEN 1 ELSE 0 END) AS nocontacts "
        "FROM accounts WHERE customer_id=?", (int(customer_id),)).fetchone()
    conn.close()
    return {k: int(row[k] or 0) for k in row.keys()}


# =========================================================================== #
# per-customer settings + content
# =========================================================================== #
def _ensure_settings(c, customer_id: int) -> None:
    c.execute("INSERT OR IGNORE INTO customer_settings (customer_id) VALUES (?)",
              (int(customer_id),))


def customer_settings(customer_id: int) -> dict:
    conn = _conn()
    c = conn.cursor()
    _ensure_settings(c, customer_id)
    conn.commit()
    row = c.execute("SELECT * FROM customer_settings WHERE customer_id=?",
                    (int(customer_id),)).fetchone()
    conn.close()
    return dict(row) if row else {}


def set_customer_setting(customer_id: int, **fields) -> None:
    allowed = {"content_kind", "content_text", "content_path", "content_name",
               "content_caption", "content_sha256", "content_size",
               "photo_direction", "selected_json"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return
    conn = _conn()
    c = conn.cursor()
    _ensure_settings(c, customer_id)
    vals.append(int(customer_id))
    c.execute(f"UPDATE customer_settings SET {', '.join(sets)} WHERE customer_id=?", vals)
    conn.commit()
    conn.close()


def content_of(customer_id: int) -> dict:
    """The customer's content in the shape the engine expects.

    The engine's contract is {"kind", "text", "file_path", "file_name",
    "caption"} -- exactly what the original single-owner store returned, so the
    copied send loop needed no change.
    """
    s = customer_settings(customer_id)
    kind = s.get("content_kind") or ""
    return {
        "kind": kind,
        "text": s.get("content_text") or "",
        "file_path": s.get("content_path") or "",
        "file_name": s.get("content_name") or "",
        "caption": s.get("content_caption") or "",
    }


# =========================================================================== #
# owner-global settings (what the engine reads through bot/store.py)
# =========================================================================== #
def owner_setting(key: str, default=None):
    conn = _conn()
    row = conn.execute("SELECT value FROM owner_settings WHERE key=?", (str(key),)).fetchone()
    conn.close()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (ValueError, TypeError):
        return default


def set_owner_setting(key: str, value) -> None:
    conn = _conn()
    conn.execute("INSERT INTO owner_settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (str(key), json.dumps(value, ensure_ascii=False)))
    conn.commit()
    conn.close()


def all_owner_settings() -> dict:
    conn = _conn()
    rows = conn.execute("SELECT key, value FROM owner_settings").fetchall()
    conn.close()
    out = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except (ValueError, TypeError):
            pass
    return out


# =========================================================================== #
# events: log feed + rate counter + add quota + timeline
# =========================================================================== #
def log_event(customer_id: int, kind: str, label: str = "", summary: str = "",
              trace_id: str = "", counted: bool = True) -> None:
    """Record one event.

    `counted=True` means it also counts toward the rate limit. Navigation passes
    counted=False so a customer browsing menus cannot block themselves.
    """
    conn = _conn()
    conn.execute(
        "INSERT INTO events (customer_id, kind, label, summary, trace_id, counted, at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (int(customer_id), str(kind), str(label)[:120], str(summary)[:400],
         str(trace_id), 1 if counted else 0, time.time()))
    conn.commit()
    conn.close()


def rate_count(customer_id: int, window: int) -> int:
    """Counted actions inside a SLIDING window.

    Sliding, not a fixed bucket: a fixed window lets a burst straddle the
    boundary and take twice the quota, which at 20/60 means 40 actions.
    """
    conn = _conn()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE customer_id=? AND counted=1 AND at > ?",
        (int(customer_id), time.time() - int(window))).fetchone()["n"]
    conn.close()
    return int(n)


def recent_actions(customer_id: int, limit: int, window: int | None = None) -> list:
    """The last counted actions, newest first -- what the block card lists."""
    conn = _conn()
    if window:
        rows = conn.execute(
            "SELECT label, kind, at FROM events WHERE customer_id=? AND counted=1 "
            "AND at > ? ORDER BY at DESC LIMIT ?",
            (int(customer_id), time.time() - int(window), int(limit))).fetchall()
    else:
        rows = conn.execute(
            "SELECT label, kind, at FROM events WHERE customer_id=? AND counted=1 "
            "ORDER BY at DESC LIMIT ?", (int(customer_id), int(limit))).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def timeline(customer_id: int, offset: int, limit: int) -> list:
    conn = _conn()
    rows = conn.execute(
        "SELECT kind, label, summary, trace_id, at FROM events WHERE customer_id=? "
        "ORDER BY at DESC LIMIT ? OFFSET ?",
        (int(customer_id), int(limit), int(offset))).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_timeline(customer_id: int) -> int:
    conn = _conn()
    n = conn.execute("SELECT COUNT(*) AS n FROM events WHERE customer_id=?",
                     (int(customer_id),)).fetchone()["n"]
    conn.close()
    return int(n)


# ---- the "add an account" quota, on a ROLLING window ---------------------- #
def adds_in_window(customer_id: int) -> int:
    conn = _conn()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE customer_id=? AND kind='account_added' "
        "AND at > ?",
        (int(customer_id), time.time() - config.ADD_QUOTA_WINDOW)).fetchone()["n"]
    conn.close()
    return int(n)


def next_add_slot(customer_id: int) -> float:
    """Seconds until the quota frees up, or 0 if it already has room.

    Rolling means "when does the OLDEST of the last N adds fall out of the
    window" -- which is also the number shown to the customer, so the limit
    message can say "6h 20m" instead of just refusing.
    """
    quota = max(1, int(config.DAILY_ADD_QUOTA))
    conn = _conn()
    rows = conn.execute(
        "SELECT at FROM events WHERE customer_id=? AND kind='account_added' AND at > ? "
        "ORDER BY at ASC",
        (int(customer_id), time.time() - config.ADD_QUOTA_WINDOW)).fetchall()
    conn.close()
    if len(rows) < quota:
        return 0.0
    oldest = float(rows[0]["at"])
    return max(0.0, (oldest + config.ADD_QUOTA_WINDOW) - time.time())


def blocked_attempt_counts() -> dict:
    """Placeholder for the daily blocked-attempt summary.

    Attempts by blocked users are counted IN MEMORY (see gate.py) and never
    written here, because writing one row per attempt is exactly the server load
    a block is supposed to remove.
    """
    return {}


def prune_events() -> int:
    conn = _conn()
    cur = conn.execute("DELETE FROM events WHERE at < ?",
                       (time.time() - EVENT_RETENTION_DAYS * 86400,))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return int(n or 0)


# =========================================================================== #
# jobs
# =========================================================================== #
def create_job(job_id: str, customer_id: int, account_id, kind: str,
               engine: str = "", trace_id: str = "", total: int = 0) -> None:
    conn = _conn()
    conn.execute(
        "INSERT OR REPLACE INTO jobs (job_id, customer_id, account_id, kind, state, "
        "engine, trace_id, total, started_at) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?)",
        (str(job_id), int(customer_id),
         int(account_id) if account_id is not None else None,
         str(kind), str(engine), str(trace_id), int(total), time.time()))
    conn.commit()
    conn.close()


def finish_job(job_id: str, state: str, sent: int = 0, failed: int = 0,
               skipped: int = 0, last_error: str = "") -> None:
    conn = _conn()
    conn.execute(
        "UPDATE jobs SET state=?, sent=?, failed=?, skipped=?, last_error=?, "
        "finished_at=? WHERE job_id=?",
        (str(state), int(sent), int(failed), int(skipped), str(last_error)[:400],
         time.time(), str(job_id)))
    conn.commit()
    conn.close()


def set_job_msg(job_id: str, cust_msg_id: int = 0, log_msg_id: int = 0) -> None:
    sets, vals = [], []
    if cust_msg_id:
        sets.append("cust_msg_id=?")
        vals.append(int(cust_msg_id))
    if log_msg_id:
        sets.append("log_msg_id=?")
        vals.append(int(log_msg_id))
    if not sets:
        return
    vals.append(str(job_id))
    conn = _conn()
    conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE job_id=?", vals)
    conn.commit()
    conn.close()


def running_jobs(customer_id: int | None = None) -> list:
    conn = _conn()
    if customer_id is None:
        rows = conn.execute("SELECT * FROM jobs WHERE state='running' ORDER BY started_at").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE state='running' AND customer_id=? ORDER BY started_at",
            (int(customer_id),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def last_jobs(customer_id: int, limit: int = 5) -> list:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE customer_id=? ORDER BY started_at DESC LIMIT ?",
        (int(customer_id), int(limit))).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_stale_jobs() -> int:
    """At boot, any job still 'running' is from a killed process.

    Fail them closed rather than leaving them to look alive forever -- the same
    reasoning the plan applies to un-attributable rows: do not guess.
    """
    conn = _conn()
    cur = conn.execute(
        "UPDATE jobs SET state='failed', last_error='ربات ری‌استارت شد', finished_at=? "
        "WHERE state='running'", (time.time(),))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return int(n or 0)


# =========================================================================== #
# owner -> customer outbox
# =========================================================================== #
def enqueue_notification(customer_id: int, text: str) -> None:
    conn = _conn()
    conn.execute("INSERT INTO notifications (customer_id, text, sent, created_at) "
                 "VALUES (?, ?, 0, ?)", (int(customer_id), str(text), time.time()))
    conn.commit()
    conn.close()


def broadcast(text: str, only_active: bool = True) -> int:
    """One row per customer. Delivery is the customer bot's problem, which is
    what makes this instant for the owner."""
    conn = _conn()
    where = "WHERE blocked=0" if only_active else ""
    rows = conn.execute(f"SELECT telegram_id FROM customers {where}").fetchall()
    now = time.time()
    conn.executemany(
        "INSERT INTO notifications (customer_id, text, sent, created_at) VALUES (?, ?, 0, ?)",
        [(int(r["telegram_id"]), str(text), now) for r in rows])
    conn.commit()
    conn.close()
    return len(rows)


def fetch_unsent_notifications(limit: int = 50) -> list:
    conn = _conn()
    rows = conn.execute(
        "SELECT id, customer_id, text FROM notifications WHERE sent=0 "
        "ORDER BY id ASC LIMIT ?", (int(limit),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_notification_sent(notif_id: int) -> None:
    conn = _conn()
    conn.execute("UPDATE notifications SET sent=1 WHERE id=?", (int(notif_id),))
    conn.commit()
    conn.close()


# =========================================================================== #
# maintenance (flag file, so the other process sees it without importing state)
# =========================================================================== #
def maintenance_on() -> bool:
    return config.maintenance_flag().exists()


def set_maintenance(on: bool) -> None:
    flag = config.maintenance_flag()
    flag.parent.mkdir(parents=True, exist_ok=True)
    if on:
        flag.write_text(str(time.time()), encoding="utf-8")
    elif flag.exists():
        flag.unlink()



# =========================================================================== #
# relays  (SSH → Telegram)  — owner-only; see relay/manager.py
# =========================================================================== #
def add_relay(host: str, ssh_port: int, username: str, secret_enc: str,
              priority: int = 100, source: str = "panel",
              auth_kind: str = "password") -> int:
    """Insert a relay (secret already ENCRYPTED) and return its id.

    Unique on (host, ssh_port): re-adding the same endpoint updates its
    credentials instead of duplicating it, which is also how the bootstrap
    relay from .env stays in sync when the file changes.
    """
    conn = _conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO relays (host, ssh_port, username, secret_enc, priority, "
        "source, auth_kind, status, added_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'idle', ?) "
        "ON CONFLICT(host, ssh_port) DO UPDATE SET "
        "  username=excluded.username, secret_enc=excluded.secret_enc, "
        "  auth_kind=excluded.auth_kind",
        (str(host), int(ssh_port), str(username), str(secret_enc),
         int(priority), str(source), str(auth_kind), time.time()))
    conn.commit()
    row = c.execute("SELECT id FROM relays WHERE host=? AND ssh_port=?",
                    (str(host), int(ssh_port))).fetchone()
    conn.close()
    return int(row["id"])


def get_relay(relay_id: int):
    conn = _conn()
    row = conn.execute("SELECT * FROM relays WHERE id=?", (int(relay_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def find_relay(host: str, ssh_port: int):
    conn = _conn()
    row = conn.execute("SELECT * FROM relays WHERE host=? AND ssh_port=?",
                       (str(host), int(ssh_port))).fetchone()
    conn.close()
    return dict(row) if row else None


def list_relays(include_disabled: bool = True) -> list:
    """Ordered the way the guardian tries them: priority asc, then id."""
    conn = _conn()
    where = "" if include_disabled else "WHERE status != 'disabled'"
    rows = conn.execute(
        f"SELECT * FROM relays {where} ORDER BY priority ASC, id ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_relay(relay_id: int) -> bool:
    conn = _conn()
    cur = conn.execute("DELETE FROM relays WHERE id=?", (int(relay_id),))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def set_relay_fields(relay_id: int, **fields) -> None:
    """Update guardian telemetry / status. Unknown fields ignored on purpose."""
    allowed = {"status", "priority", "fail_count", "disconnects", "last_check",
               "last_ok", "last_ping_ms", "last_error", "host_key",
               "secret_enc", "username", "auth_kind"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return
    vals.append(int(relay_id))
    conn = _conn()
    conn.execute(f"UPDATE relays SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()
    conn.close()


def count_relays() -> int:
    conn = _conn()
    n = conn.execute("SELECT COUNT(*) AS n FROM relays").fetchone()["n"]
    conn.close()
    return int(n)
