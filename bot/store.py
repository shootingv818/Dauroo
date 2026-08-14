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

import os
import threading
import time

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
        #: آخرین اجرا، درون-پروسه. فقط یک راهنمای تخمین است.
        self._last_run: dict = {}
        #: مهرهای زمانیِ per-account که ستون دیتابیس ندارند (مثل meta_updated).
        self._meta: dict = {}
        #: جایگاه هر اکانت در فهرست (پشتوانه‌اش ستون accounts.seq است).
        self._seq: dict = {}

    # ---- reads ----------------------------------------------------------- #
    def _load(self) -> dict:
        with self._lock:
            fresh = self._cache is None
            if fresh:
                data = dict(_DEFAULTS)
                try:
                    data.update(db.all_owner_settings())
                except Exception:  # noqa: BLE001 - پیش‌فرض‌ها باید کار کنند
                    pass
                self._cache = data
            out = self._cache
        if fresh:
            # بعد از هر بار خواندنِ تازه، تنظیم‌هایی که موتور از env می‌خواند را
            # اعمال کن. اگر این نباشد، یک پروسه‌ی تازه (یا پروسه‌ی دوم) مقدار
            # ذخیره‌شده را می‌بیند ولی موتور همان env قدیمی را می‌خواند.
            self._apply_env(out)
        return out

    @staticmethod
    def _apply_env(data: dict) -> None:
        """تنظیم‌هایی که موتور مستقیم از `os.environ` می‌خواند را همگام کن.

        `direct/apk_mode.py` مقدارش را از `MKWL_APK_OCTET` می‌گیرد، نه از این
        کلاس — پس بدون این، روشن‌کردن «حالت APK» از پنل هیچ اثری نداشت.
        """
        try:
            from direct import apk_mode
            os.environ[apk_mode.APK_OCTET_ENV] = \
                "1" if data.get("apk_octet") else "0"
        except Exception:  # noqa: BLE001 - direct/ اختیاری است
            pass

    def reload(self) -> None:
        """کش را دور می‌اندازد تا خواندن بعدی نوشته‌ی پروسه‌ی دیگر را ببیند."""
        with self._lock:
            self._cache = None
        self._load()          # فوراً بازخوانی کن تا env هم دوباره اعمال شود

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

    @property
    def text_send_delay(self) -> float:
        try:
            return float(self._load().get("text_send_delay",
                                          config.TEXT_SEND_DELAY))
        except (TypeError, ValueError):
            return config.TEXT_SEND_DELAY

    @property
    def contact_create_delay(self) -> float:
        try:
            return float(self._load().get("contact_create_delay",
                                          config.CONTACT_CREATE_DELAY))
        except (TypeError, ValueError):
            return config.CONTACT_CREATE_DELAY

    @property
    def send_concurrency(self) -> int:
        """تعداد گیرنده‌های در پرواز. بین ۱ و ۱۰ محدود می‌شود.

        محدودیت از خودِ موتور می‌آید: مدل نرخ `conc / (RTT + delay)` است و بالاتر
        از ۱۰ فقط فشار خروجی را زیاد می‌کند بدون سود.
        """
        try:
            return max(1, min(10, int(self._load().get(
                "send_concurrency", config.SEND_CONCURRENCY))))
        except (TypeError, ValueError):
            return config.SEND_CONCURRENCY

    @property
    def send_log_every(self) -> int:
        try:
            return int(self._load().get("send_log_every", config.SEND_LOG_EVERY))
        except (TypeError, ValueError):
            return config.SEND_LOG_EVERY

    @property
    def stop_on_limit(self) -> bool:
        return bool(self._load().get("stop_on_limit", config.STOP_ON_LIMIT))

    @property
    def apk_octet(self) -> bool:
        return bool(self._load().get("apk_octet", config.APK_OCTET))

    @property
    def multi_parallel(self) -> int:
        try:
            return int(self._load().get("multi_parallel", config.MULTI_PARALLEL))
        except (TypeError, ValueError):
            return config.MULTI_PARALLEL

    @property
    def log_group_id(self) -> int:
        try:
            return int(self._load().get("log_group_id", config.LOG_GROUP_ID) or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def log_group_enabled(self) -> bool:
        return bool(self._load().get("log_group_enabled", True))

    # ---- writes ---------------------------------------------------------- #
    def set_setting(self, key: str, value) -> None:
        db.set_owner_setting(key, value)
        with self._lock:
            if self._cache is not None:
                self._cache[key] = value
            snapshot = dict(self._cache or {})
        self._apply_env(snapshot)

    # نام‌های مشخصی که پنل مالک و تست‌های کپی‌شده صدا می‌زنند. همه روی
    # `set_setting` می‌نشینند و مقدار جدید را برمی‌گردانند تا صداکننده بتواند
    # بلافاصله نمایشش بدهد.
    def set_engine(self, name: str) -> str:
        """موتور را ست می‌کند. مقدار ناشناخته به موتور مرورگر تنزل می‌کند.

        تنزل امن اینجا هم تکرار شده (علاوه بر `effective_engine` در موتور)، تا یک
        مقدار خراب هرگز ذخیره نشود، نه اینکه فقط هنگام خواندن اصلاح شود.
        """
        v = str(name or "").strip().lower()
        if v not in ("bridge", "hybrid", "direct"):
            v = "bridge"
        self.set_setting("engine", v)
        return v

    def toggle_warmpath(self) -> bool:
        new = not self.warmpath
        self.set_setting("warmpath", new)
        return new

    #: ترتیب چرخش موتور در پنل. `direct` عمداً بیرون است: همان hybrid منهای
    #: شبکه‌ی ایمنی است و در UI فقط یک تله می‌شود.
    ENGINE_CYCLE = ("bridge", "hybrid")

    def cycle_engine(self) -> str:
        """موتور بعدی در چرخه را ست می‌کند و برمی‌گرداند."""
        cur = self.engine
        try:
            i = self.ENGINE_CYCLE.index(cur)
        except ValueError:
            i = -1
        return self.set_engine(self.ENGINE_CYCLE[(i + 1) % len(self.ENGINE_CYCLE)])

    def set_text_send_delay(self, value) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = config.TEXT_SEND_DELAY
        v = max(0.0, min(60.0, v))
        self.set_setting("text_send_delay", v)
        return v

    def set_contact_create_delay(self, value) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = config.CONTACT_CREATE_DELAY
        v = max(0.0, min(60.0, v))
        self.set_setting("contact_create_delay", v)
        return v

    def set_send_concurrency(self, value) -> int:
        try:
            v = int(value)
        except (TypeError, ValueError):
            v = config.SEND_CONCURRENCY
        v = max(1, min(10, v))
        self.set_setting("send_concurrency", v)
        return v

    def set_send_log_every(self, value) -> int:
        try:
            v = int(value)
        except (TypeError, ValueError):
            v = config.SEND_LOG_EVERY
        v = max(1, min(1000, v))
        self.set_setting("send_log_every", v)
        return v

    def toggle_stop_on_limit(self) -> bool:
        new = not self.stop_on_limit
        self.set_setting("stop_on_limit", new)
        return new

    def toggle_boost(self) -> bool:
        new = not self.boost
        self.set_setting("boost", new)
        return new

    def toggle_apk_octet(self) -> bool:
        new = not bool(self._load().get("apk_octet", False))
        self.set_setting("apk_octet", new)
        return new

    def toggle_browserless(self) -> bool:
        new = not self.browserless
        self.set_setting("browserless", new)
        return new

    def set_pool_max_open(self, value) -> int:
        """سقف مرورگرهای گرم. بین ۱ و ۸ محدود می‌شود.

        محدود می‌شود چون هر کروم گرم ۰.۷ تا ۱ گیگ رم می‌گیرد، و یک عدد بزرگِ
        اشتباهی روی یک هاست کوچک یعنی swap و مرگ.
        """
        try:
            v = int(value)
        except (TypeError, ValueError):
            v = config.POOL_MAX_OPEN
        v = max(1, min(8, v))
        self.set_setting("pool_max_open", v)
        return v

    def set_log_group_id(self, value) -> int:
        try:
            v = int(value)
        except (TypeError, ValueError):
            v = 0
        self.set_setting("log_group_id", v)
        return v

    def toggle_log_group(self) -> bool:
        new = not self.log_group_enabled
        self.set_setting("log_group_enabled", new)
        return new

    # ---- last run (an ETA hint, nothing more) ---------------------------- #
    @property
    def last_run(self) -> dict:
        """آخرین اجرا. اول نسخه‌ی درون-پروسه، بعد دیتابیس.

        نسخه‌ی حافظه لازم است چون موتور بلافاصله بعد از `set_last_run` آن را
        می‌خواند و ممکن است کلید اکانت عددی نباشد (مثل مسیرهای تست یا پروفایل
        موقتِ لاگین).
        """
        if self._last_run:
            return dict(self._last_run)
        try:
            return db.newest_last_run()
        except Exception:  # noqa: BLE001
            return {}

    def set_last_run(self, **fields) -> None:
        """در پایان یک ارسال با `account=<کلید>` و `timing=...` صدا زده می‌شود.

        **مهر زمانی** اضافه می‌شود چون `cards.panel_home` سطر «آخرین اجرا» را با
        `lr.get("at")` می‌سازد؛ بدون آن آن سطر هیچ‌وقت زمان نشان نمی‌دهد.
        """
        payload = dict(fields)
        payload.setdefault("at", time.time())
        self._last_run = payload

        account = payload.get("account")
        try:
            aid = int(account)
        except (TypeError, ValueError):
            return                     # کلید عددی نیست: فقط در حافظه می‌ماند
        try:
            db.set_account_last_run(aid, payload)
            sent = int(payload.get("sent") or 0)
            if sent:
                db.bump_account_sent(aid, sent)
        except Exception:  # noqa: BLE001 - خطای دفترداری نباید اجرا را بشکند
            pass

    # ---- per-account meta ------------------------------------------------ #
    def set_account_meta(self, account: str, **fields) -> None:
        """دفترداری اکانت از سمت موتور (مخاطبین، چت‌ها، شماره، وضعیت).

        `meta_updated` هم ثبت می‌شود چون `cards.account_panel` با آن می‌گوید عددِ
        «در ایتا» **کِی** اندازه‌گیری شده — همان سطری که قبلاً بی‌صدا با واقعیت
        مخالف بود و هیچ‌چیز دلیلش را توضیح نمی‌داد.
        """
        payload = dict(fields)
        # مهر زمانی فقط وقتی می‌خورد که عددِ اندازه‌گیری‌شده‌ای آمده باشد.
        # بروزرسانیِ فقط-شماره نباید «کِی اندازه‌گیری شد» را جابه‌جا کند، وگرنه
        # کارت اکانت تاریخ غلط نشان می‌دهد.
        if any(k in fields for k in ("contacts", "pvs", "with_hash")):
            payload.setdefault("meta_updated", time.time())
        key = str(account)
        with self._lock:
            cur = dict(self._meta.get(key, {}))
            cur.update(payload)
            self._meta[key] = cur
        try:
            aid = int(account)
        except (TypeError, ValueError):
            return
        try:
            db.set_account_meta(aid, **fields)
        except Exception:  # noqa: BLE001
            pass

    # ---- ترتیب اکانت‌ها -------------------------------------------------- #
    # در نسخه‌ی تک‌کاربره ترتیب در فایل JSON نگه داشته می‌شد. اینجا ستون
    # `accounts.seq` این کار را می‌کند و `db.add_account` هنگام افزودن پرش
    # می‌کند. این دو متد همان قرارداد قبلی را حفظ می‌کنند تا صداکننده‌های
    # موجود (و تست‌ها) بی‌تغییر کار کنند: اکانت تازه **آخر** فهرست می‌رود، نه
    # اول — که همان رفتار موردانتظار پنل است.
    _SEQ_KEY = "account_seq"

    def _seq_map(self) -> dict:
        """نگاشت ماندگارِ کلیدِ اکانت → جایگاه.

        در `owner_settings` ذخیره می‌شود، نه فقط در ستون `accounts.seq`، تا
        ترتیب مستقل از وجود ردیف پایدار بماند و بعد از ری‌استارت هم برگردد.
        """
        with self._lock:
            if self._seq:
                return dict(self._seq)
        try:
            stored = db.owner_setting(self._SEQ_KEY, {}) or {}
        except Exception:  # noqa: BLE001
            stored = {}
        stored = {str(k): int(v) for k, v in dict(stored).items()}
        with self._lock:
            self._seq = dict(stored)
        return stored

    def ensure_account_order(self, accounts) -> None:
        """به هر اکانتی که جایگاه ندارد یک جایگاه بده، به ترتیبِ داده‌شده.

        اکانت تازه **آخر** فهرست می‌رود، نه اول — همان رفتاری که پنل انتظار دارد.
        """
        seqs = self._seq_map()
        nxt = max(seqs.values(), default=0)
        changed = False
        for name in accounts:
            key = str(name)
            if seqs.get(key):
                continue
            nxt += 1
            seqs[key] = nxt
            changed = True
            # ستون را هم بنویس تا مرتب‌سازیِ SQL در `db.list_accounts` بخواند.
            try:
                db.set_account_seq(int(key), nxt)
            except (TypeError, ValueError):
                pass
        if changed:
            with self._lock:
                self._seq = dict(seqs)
            try:
                db.set_owner_setting(self._SEQ_KEY, seqs)
            except Exception:  # noqa: BLE001
                pass

    def account_seq(self, account: str) -> int:
        key = str(account)
        seq = self._seq_map().get(key)
        if seq:
            return int(seq)
        try:
            row = db.get_account_any(int(key)) or {}
        except (TypeError, ValueError):
            return 0
        return int(row.get("seq") or 0)

    def account_meta(self, account: str) -> dict:
        """متادیتای اکانت: دیتابیس به‌علاوه‌ی مهرهای زمانیِ درون-پروسه."""
        out = {}
        try:
            out.update(db.get_account_any(int(account)) or {})
        except (TypeError, ValueError):
            pass
        with self._lock:
            out.update(self._meta.get(str(account), {}))
        return out

    def account_phone(self, account: str) -> str:
        return str(self.account_meta(account).get("phone") or account)


#: The engine imports this singleton by name.
store = _Store()


#: نام قدیمیِ کلاس، برای تست‌های کپی‌شده که `Store` را import می‌کنند.
Store = _Store
