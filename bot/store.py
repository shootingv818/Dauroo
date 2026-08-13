"""
bot/store.py — the adapter that lets the copied engine stay unchanged.
=====================================================================

The engine code in `bot/runner.py`, `eitaa/warmpath.py`, `contacts_boost/` and
`session_check/` was copied VERBATIM from the single-owner project. It reaches
for panel state like this:

    from bot.store import store as _store
    return dict(_store.settings)

Rather than editing those files (and re-testing an engine that already works),
this module keeps that exact contract and re-points it at the multi-customer
database. Every attribute below exists because a specific copied call site asks
for it:

  .settings          bot/runner.py  settings_provider()
  .engine            bot/runner.py, eitaa/warmpath.py
  .warmpath          eitaa/warmpath.py
  .boost             contacts_boost/engine.py  enabled()
  .boost_prefix      contacts_boost/engine.py  settings()
  .boost_probe       contacts_boost/engine.py  settings()
  .last_run          bot/runner.py  (ETA hint only)
  .set_last_run()    bot/runner.py  (end of a send)
  .set_account_meta()bot/runner.py, session_check/checker.py

Why every setting here is OWNER-GLOBAL: in this service the owner holds all the
knobs -- engine, delays, concurrency, pool size -- and customers hold only their
own content. So a single global settings dict is not a shortcut, it is the
design. Per-customer state (content, photo direction, selection) lives in
`db.customer_settings` and never comes through here.

Nothing in here raises: every copied call site wraps its lookup in try/except
and falls back to the env default, so a database hiccup must degrade to "the old
behaviour" rather than break a job.
"""
from __future__ import annotations

import threading

import db
from config import config

#: Owner-tunable settings, with the env value as the default. These keys are the
#: same names the original panel used, because the engine reads them by name.
_DEFAULTS: dict = {
    "engine": config.ENGINE,
    "browserless": False,
    "text_send_delay": config.TEXT_SEND_DELAY,
    "contact_create_delay": config.CONTACT_CREATE_DELAY,
    "send_log_every": config.SEND_LOG_EVERY,
    "send_concurrency": config.SEND_CONCURRENCY,
    "stop_on_limit": config.STOP_ON_LIMIT,
    "apk_octet": config.APK_OCTET,
    "warmpath": config.WARMPATH,
    "boost": getattr(config, "BOOST", False),
    "boost_prefix": getattr(config, "BOOST_PREFIX", ""),
    "boost_probe": getattr(config, "BOOST_PROBE", 400),
    "pool_max_open": config.POOL_MAX_OPEN,
    "multi_parallel": config.MULTI_PARALLEL,
}


class _Store:
    """Owner-global settings, cached in memory and written through to SQLite.

    The cache exists because `settings` is read on every job and the engine also
    reads `.engine` inside loops; a query per read would be wasteful. Writes go
    to the database first, so the other bot process sees them.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict | None = None

    # ---- reads ----------------------------------------------------------- #
    def _load(self) -> dict:
        with self._lock:
            if self._cache is None:
                data = dict(_DEFAULTS)
                try:
                    data.update(db.all_owner_settings())
                except Exception:  # noqa: BLE001 - defaults must still work
                    pass
                self._cache = data
            return self._cache

    def reload(self) -> None:
        """Drop the cache so the next read sees what the other process wrote."""
        with self._lock:
            self._cache = None

    @property
    def settings(self) -> dict:
        return dict(self._load())

    def get(self, key: str, default=None):
        return self._load().get(key, default)

    # Named properties for the exact attributes the copied engine touches.
    @property
    def engine(self) -> str:
        return str(self._load().get("engine", config.ENGINE))

    @property
    def browserless(self) -> bool:
        return bool(self._load().get("browserless", False))

    @property
    def warmpath(self) -> bool:
        return bool(self._load().get("warmpath", config.WARMPATH))

    @property
    def boost(self) -> bool:
        return bool(self._load().get("boost", False))

    @property
    def boost_prefix(self) -> str:
        return str(self._load().get("boost_prefix", "") or "")

    @property
    def boost_probe(self) -> int:
        try:
            return int(self._load().get("boost_probe", 400) or 400)
        except (TypeError, ValueError):
            return 400

    @property
    def pool_max_open(self) -> int:
        try:
            return int(self._load().get("pool_max_open", config.POOL_MAX_OPEN))
        except (TypeError, ValueError):
            return config.POOL_MAX_OPEN

    # ---- writes ---------------------------------------------------------- #
    def set_setting(self, key: str, value) -> None:
        db.set_owner_setting(key, value)
        with self._lock:
            if self._cache is not None:
                self._cache[key] = value

    # ---- last run (an ETA hint, nothing more) ---------------------------- #
    @property
    def last_run(self) -> dict:
        try:
            return db.newest_last_run()
        except Exception:  # noqa: BLE001
            return {}

    def set_last_run(self, **fields) -> None:
        """Called at the end of a send with account=<key>, timing=..., etc.

        `account` is the engine's account key, which here is the accounts row id.
        The payload is stored on that row so the owner panel can show a real
        "last run" line per account, and `newest_last_run()` serves the engine's
        fleet-wide ETA hint.
        """
        account = fields.get("account")
        try:
            aid = int(account)
        except (TypeError, ValueError):
            return
        try:
            db.set_account_last_run(aid, dict(fields))
            sent = int(fields.get("sent") or 0)
            if sent:
                db.bump_account_sent(aid, sent)
        except Exception:  # noqa: BLE001 - a bookkeeping failure must not fail a run
            pass

    # ---- per-account meta ------------------------------------------------ #
    def set_account_meta(self, account: str, **fields) -> None:
        """Engine-facing account bookkeeping (contacts, pvs, phone, status)."""
        try:
            aid = int(account)
        except (TypeError, ValueError):
            return
        try:
            db.set_account_meta(aid, **fields)
        except Exception:  # noqa: BLE001
            pass

    def account_meta(self, account: str) -> dict:
        try:
            return db.get_account_any(int(account)) or {}
        except (TypeError, ValueError):
            return {}

    def account_phone(self, account: str) -> str:
        return str(self.account_meta(account).get("phone") or account)


#: The engine imports this singleton by name.
store = _Store()
