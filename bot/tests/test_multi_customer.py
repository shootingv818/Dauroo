"""
تست لایه‌ی چند-مشتری — آفلاین، بدون شبکه و بدون مرورگر.

اجرا:  python -m bot.tests.test_multi_customer

`playwright` و `telethon` هنگام import استاب می‌شوند، همان الگویی که بقیه‌ی
تست‌های این پروژه دارند. هیچ‌چیز اینجا به تلگرام یا ایتا وصل نمی‌شود.

هر تست یک قاعده‌ی مشخص از طرح را می‌بندد، و هر کدام دلیلی دارد که در کامنتش نوشته
شده — چون مواردی مثل «آیا فهرست ردشده‌ها دست‌نخورده ماند» یا «آیا مشتری دیگری را
می‌بیند» چیزهایی‌اند که فقط با تست معلوم می‌مانند.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# ---- محیط ایزوله: قبل از import گرفتن config ------------------------------- #
_TMP = tempfile.mkdtemp(prefix="dauroo_test_")
os.environ["DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["PROFILES_DIR"] = os.path.join(_TMP, "profiles")
os.environ["ARTIFACTS_DIR"] = os.path.join(_TMP, "artifacts")
os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "x")
os.environ.setdefault("OWNER_BOT_TOKEN", "owner-token")
os.environ.setdefault("CUSTOMER_BOT_TOKEN", "customer-token")
os.environ.setdefault("OWNER_ID", "1000")
os.environ["RATE_LIMIT_MAX"] = "20"
os.environ["RATE_LIMIT_WINDOW"] = "60"
os.environ["ACCOUNT_CAP"] = "40"
os.environ["DAILY_ADD_QUOTA"] = "10"


def _stub_playwright() -> None:
    """همان استابی که بقیه‌ی تست‌های این پروژه دارند (test_engines.py)."""
    try:
        import playwright.async_api  # noqa: F401
        return
    except Exception:  # noqa: BLE001
        pass
    pkg = types.ModuleType("playwright")
    api = types.ModuleType("playwright.async_api")
    for name in ("Browser", "BrowserContext", "CDPSession", "Page", "Locator",
                 "Error"):
        setattr(api, name, type(name, (object,), {}))
    api.TimeoutError = type("TimeoutError", (Exception,), {})
    api.async_playwright = lambda: None
    pkg.async_api = api
    sys.modules.setdefault("playwright", pkg)
    sys.modules.setdefault("playwright.async_api", api)


def _stub_telethon() -> None:
    """کوچک‌ترین سطحی که دو ربات هنگام import لمس می‌کنند."""
    try:
        import telethon  # noqa: F401
        return
    except Exception:  # noqa: BLE001
        pass

    class _Btn:
        def __init__(self, text, data=None, url=None):
            self.text, self.data, self.url = text, data, url

        @staticmethod
        def inline(text, data=None):
            return _Btn(text, data=data)

        @staticmethod
        def url(text, url):
            return _Btn(text, url=url)

    class _Client:
        def __init__(self, *a, **k):
            self.sent = []
            self._handlers = []

        def on(self, _evt):
            def deco(fn):
                self._handlers.append(fn)
                return fn
            return deco

        def add_event_handler(self, fn, evt=None):
            self._handlers.append(fn)

        def list_event_handlers(self):
            return list(self._handlers)

        async def start(self, *a, **k):
            return self

        async def send_message(self, chat, text, buttons=None):
            self.sent.append((chat, text))
            return types.SimpleNamespace(id=len(self.sent))

        async def send_file(self, chat, file, caption="", **k):
            self.sent.append((chat, f"<file:{file}>"))
            return types.SimpleNamespace(id=len(self.sent))

        async def edit_message(self, *a, **k):
            return None

        async def get_entity(self, uid):
            return types.SimpleNamespace(first_name="تستی", username="tester")

        async def run_until_disconnected(self):
            return None

    class _Ev:
        class NewMessage:
            def __init__(self, *a, **k):
                pass

        class CallbackQuery:
            def __init__(self, *a, **k):
                pass

    tl = types.ModuleType("telethon")
    tl.TelegramClient = _Client
    tl.Button = _Btn
    tl.events = _Ev
    tl.utils = types.ModuleType("telethon.utils")
    sys.modules["telethon"] = tl
    sys.modules["telethon.events"] = _Ev
    sys.modules["telethon.utils"] = tl.utils
    errs = types.ModuleType("telethon.errors")
    errs.FloodWaitError = type("FloodWaitError", (Exception,), {})
    sys.modules["telethon.errors"] = errs
    tt = types.ModuleType("telethon.tl")
    sys.modules["telethon.tl"] = tt
    ttypes = types.ModuleType("telethon.tl.types")
    sys.modules["telethon.tl.types"] = ttypes


_stub_playwright()
_stub_telethon()

import db                     # noqa: E402
import gate                   # noqa: E402
import ratelimit              # noqa: E402
from bot import cards, logbus  # noqa: E402
from bot.store import store   # noqa: E402
from config import config     # noqa: E402

PASS = FAIL = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"  → {extra}" if extra else ""))


def section(title: str) -> None:
    print(f"\n{title}")


# =========================================================================== #
def test_scoping() -> None:
    section("جداسازی مستأجرها: هیچ مشتری داده‌ی دیگری را نمی‌بیند")
    a, b = 111, 222
    db.ensure_customer(a, "علی", "ali")
    db.ensure_customer(b, "رضا", "reza")

    # همان شماره برای دو مشتری — باید مجاز باشد و دو ردیف جدا بسازد.
    # اگر phone یکتاییِ سراسری داشت، مالکیت بی‌صدا منتقل می‌شد و A اکانتش را از
    # دست می‌داد. این همان باگی است که در پروژه‌ی مرجع وجود داشت.
    id_a = db.add_account(a, "989120000001")
    id_b = db.add_account(b, "989120000001")
    check("یک شماره برای دو مشتری، دو اکانت جدا", id_a != id_b, f"{id_a} vs {id_b}")

    check("اکانت A از دید B قابل خواندن نیست", db.get_account(b, id_a) is None)
    check("اکانت A از دید خودش خوانده می‌شود", db.get_account(a, id_a) is not None)
    check("حذف اکانت A توسط B ناممکن است", db.delete_account(b, id_a) is False)
    check("اکانت A بعد از تلاش B سر جایش است", db.get_account(a, id_a) is not None)

    # افزودن دوباره‌ی همان شماره برای همان مشتری باید همان ردیف را بدهد.
    check("افزودن تکراری همان شماره، ردیف جدید نمی‌سازد",
          db.add_account(a, "989120000001") == id_a)

    db.set_account_meta(id_a, contacts=100, with_hash=100)
    db.set_account_meta(id_b, contacts=50, with_hash=10)
    ta, tb = db.customer_totals(a), db.customer_totals(b)
    check("آمار هر مشتری فقط مال خودش است",
          ta["contacts"] == 100 and tb["contacts"] == 50, f"{ta} {tb}")
    check("اکانتِ ⚡ آماده درست شمرده می‌شود", ta["fast"] == 1 and tb["fast"] == 0)
    check("اکانتِ 🌐 فقط-مرورگر درست شمرده می‌شود",
          tb["browser"] == 1, f"browser={tb['browser']}")


def test_rate_limit() -> None:
    section("ریت‌لیمیت: پنجره‌ی لغزان، مسدودی دائمی، کش حافظه")
    uid = 333
    db.ensure_customer(uid, "اسپمر", "spam")
    ratelimit.load()

    for _ in range(config.RATE_LIMIT_MAX):
        db.log_event(uid, "action", label="افزودن اکانت", counted=True)
    v = ratelimit.check(uid)
    check(f"دقیقاً {config.RATE_LIMIT_MAX} اکشن مسدود نمی‌کند", v["ok"] is True,
          str(v))
    check("در آستانه‌ی هشدار، هشدار داده می‌شود", v["warn"] is True, str(v))

    db.log_event(uid, "action", label="افزودن اکانت", counted=True)
    v = ratelimit.check(uid)
    check(f"{config.RATE_LIMIT_MAX + 1} اکشن مسدود می‌کند", v["ok"] is False, str(v))
    check("کش حافظه فوراً مسدود می‌داند", ratelimit.is_blocked(uid) is True)
    check("دیتابیس هم مسدود ثبت کرده", db.is_blocked(uid) is True)

    # ناوبری نباید شمرده شود، وگرنه گشتن در منو خودش را مسدود می‌کند.
    uid2 = 444
    db.ensure_customer(uid2, "کاوشگر", "browse")
    for _ in range(config.RATE_LIMIT_MAX * 3):
        db.log_event(uid2, "nav", label="باز کردن منو", counted=False)
    check("ناوبری در شمارنده حساب نمی‌شود", ratelimit.count(uid2) == 0,
          str(ratelimit.count(uid2)))
    check("گشتنِ زیاد در منو مسدود نمی‌کند", ratelimit.check(uid2)["ok"] is True)

    # کارت مسدودی باید بتواند اکشن‌های مقصر را فهرست کند — چون مسدودی دائمی است و
    # این فهرست تعیین می‌کند مشتری واقعی از دست می‌رود یا نه.
    acts = ratelimit.offending_actions(uid)
    check("کارت مسدودی اکشن‌های مقصر را دارد", len(acts) > 0, str(len(acts)))
    check("اکشن‌ها برچسب آدمیزاد دارند",
          all(a.get("label") for a in acts), str(acts[:1]))

    ratelimit.unblock(uid)
    check("آزادسازی کش را هم پاک می‌کند", ratelimit.is_blocked(uid) is False)

    # تلاش کاربر مسدود فقط در حافظه شمرده می‌شود، نه در دیتابیس — همان چیزی که
    # «صفر بار روی سرور» را حفظ می‌کند.
    before = db.count_timeline(999)
    for _ in range(5):
        ratelimit.note_blocked_attempt(999)
    snap = ratelimit.blocked_attempts_snapshot(reset=True)
    check("تلاش مسدودها در حافظه شمرده شد", snap and snap[0][1] == 5, str(snap))
    check("تلاش مسدودها در دیتابیس نوشته نشد", db.count_timeline(999) == before)


def test_quota() -> None:
    section("سهمیه‌ی افزودن اکانت: پنجره‌ی غلتان ۲۴ ساعته")
    uid = 555
    db.ensure_customer(uid, "پرکار", "busy")
    q = gate.add_quota(uid)
    check("مشتری تازه سهمیه‌ی کامل دارد",
          q["used"] == 0 and q["left"] == config.DAILY_ADD_QUOTA, str(q))

    for _ in range(config.DAILY_ADD_QUOTA):
        db.log_event(uid, "account_added", label="اکانت اضافه شد")
    q = gate.add_quota(uid)
    check(f"بعد از {config.DAILY_ADD_QUOTA} افزودن، سهمیه پر است", q["left"] == 0,
          str(q))
    check("زمان آزادسازی بعدی مثبت است", q["next_in"] > 0, str(q["next_in"]))
    check("زمان آزادسازی حداکثر ۲۴ ساعت است",
          q["next_in"] <= config.ADD_QUOTA_WINDOW + 1, str(q["next_in"]))
    check("متن انسانیِ انتظار ساخته می‌شود",
          "ساعت" in gate.human_wait(q["next_in"]) or
          "دقیقه" in gate.human_wait(q["next_in"]),
          gate.human_wait(q["next_in"]))

    # ارسال سهمیه ندارد: فقط افزودن اکانت شمرده می‌شود.
    for _ in range(50):
        db.log_event(uid, "send_started", label="شروع ارسال")
    check("ارسال روی سهمیه‌ی افزودن اکانت اثر ندارد",
          gate.add_quota(uid)["used"] == config.DAILY_ADD_QUOTA)


def test_account_cap() -> None:
    section("سقف اکانت")
    uid = 666
    db.ensure_customer(uid, "جمع‌کن", "hoard")
    check("مشتری تازه به سقف نخورده", gate.account_cap_reached(uid) is False)
    for i in range(config.ACCOUNT_CAP):
        db.add_account(uid, f"9891999{i:05d}")
    check(f"در {config.ACCOUNT_CAP} اکانت، سقف اعمال می‌شود",
          gate.account_cap_reached(uid) is True,
          str(db.count_accounts(uid)))


def test_engine_switch() -> None:
    section("کلید موتور: گلوبال، مالک-only، تنزل امن")
    store.set_setting("engine", "hybrid")
    store.reload()
    check("موتور روی سریع ست شد", store.engine == "hybrid", store.engine)

    # همان تنظیم برای هر مشتری‌ای که پرسیده شود یکی است — چون گلوبال است.
    check("settings برای همه یکی است",
          store.settings.get("engine") == "hybrid")

    store.set_setting("engine", "bridge")
    store.reload()
    check("سویچ به مرورگر کار می‌کند", store.engine == "bridge", store.engine)

    # مقدار نامعتبر باید امن تنزل کند، نه اینکه کار را جای عجیبی بفرستد.
    from bot.runner import effective_engine
    check("مقدار نامعتبر به موتور مرورگر تنزل می‌کند",
          effective_engine({"engine": "چیز-بی‌ربط"}) == "bridge")
    check("موتور سریع بدون ENABLE_DIRECT هم مجاز است (پشتیبان دارد)",
          effective_engine({"engine": "hybrid"}) == "hybrid")


def test_limit_scope() -> None:
    section("دامنه‌ی محدودیت: «یک مخاطب» با «کل اکانت» یکی نیست")
    per = cards.limit_kind("PEER_FLOOD")
    allp = cards.limit_kind("ALL_PEER_FLOOD")
    fw = cards.limit_kind("FLOOD_WAIT_3600")

    check("PEER_FLOOD دامنه‌اش یک مخاطب است", per["scope"] == "همین مخاطب",
          per["scope"])
    check("ALL_PEER_FLOOD دامنه‌اش کل اکانت است", allp["scope"] == "کل اکانت",
          allp["scope"])
    check("FLOOD_WAIT انتظار زمان‌دار است و ثانیه‌اش را می‌داند",
          fw["wait"] == 3600, str(fw["wait"]))
    check("این سه با هم قاطی نمی‌شوند",
          len({per["key"], allp["key"], fw["key"]}) == 3)

    # کارت باید بگوید فهرست ردشده‌ها چه شد: روی انتظار زمان‌دار نباید کسی ثبت شود.
    c = cards.restriction_card("1", "FLOOD_WAIT_3600", 10)
    check("کارت انتظار زمان‌دار می‌گوید مخاطب ثبت نشد", "ثبت نشد" in c)
    c2 = cards.restriction_card("1", "ALL_PEER_FLOOD", 10)
    check("کارت کل-اکانت می‌گوید علت اکانت است نه مخاطب", "علت، اکانت است" in c2)


def test_error_card_split() -> None:
    section("کارت خطا: مشتری جزئیات فنی نمی‌بیند")
    full = cards.error_card(
        "send", account="7", code="PEER_FLOOD", detail="raw eitaa body here",
        trace_id="A3F9C1", engine="hybrid", phase="targets",
        customer=123456789, scope="همین مخاطب")
    check("کارت کامل کد پیگیری دارد", "A3F9C1" in full)
    check("کارت کامل آیدی مشتری دارد", "123456789" in full)
    check("کارت کامل دامنه را نشان می‌دهد", "همین مخاطب" in full)
    check("کارت کامل جزئیات خام دارد", "raw eitaa body" in full)
    check("کارت کامل سطر debug کپی‌پیست‌شدنی دارد", "🔍" in full and "trace=" in full)

    safe = logbus.safe_reason(Exception("PEER_FLOOD"), "limit")
    check("جمله‌ی امن، متن خام سرور را لو نمی‌دهد", "PEER_FLOOD" not in safe, safe)
    check("جمله‌ی امن فارسی و قابل‌عمل است", "محدودیت" in safe, safe)

    # روی FLOOD حتماً باید عدد واقعی گفته شود، وگرنه مشتری پشت‌سرهم تلاش می‌کند و
    # آخرش ریت‌لیمیت خودمان مسدودش می‌کند در حالی که تقصیر او نبوده.
    mins = logbus.flood_wait_minutes("FLOOD_WAIT_3600")
    check("دقیقه‌ی انتظار FLOOD از پیام سرور درمی‌آید", mins == 60, str(mins))
    check("پیام بی‌FLOOD عددی نمی‌دهد",
          logbus.flood_wait_minutes("something else") is None)


def test_phone_masking() -> None:
    section("ماسک شماره")
    m = logbus.mask_phone("989168226736")
    check("شماره ماسک می‌شود", m == "98916***736", m)
    check("شماره‌ی کامل در ماسک نیست", "989168226736" not in m)
    check("شماره‌ی کوتاه کاملاً پنهان می‌شود", logbus.mask_phone("123") == "***")
    check("ورودی خالی نمی‌شکند", logbus.mask_phone(None) == "***")


def test_cards_contract() -> None:
    section("قرارداد کارت‌ها با موتور کپی‌شده")
    need = ["card", "error_card", "eta", "fmt_duration", "now_hms", "sanitize",
            "bar", "panel_home", "account_panel", "account_added",
            "contacts_saved", "send_started", "send_progress", "send_finished",
            "contacts_started", "contacts_progress", "contacts_finished",
            "preflight_card", "dry_run_card", "pool_card", "timing_card",
            "restriction_card", "paused_card", "contacts_probe", "peers_saved",
            "account_deleted", "multi_send_finished", "live_send", "live_contacts",
            "live_stages", "live_send_multi", "multi_ready", "multi_account_done",
            "limit_kind", "code_hint", "coverage", "pace", "debug_line"]
    missing = [n for n in need if not hasattr(cards, n)]
    check("همه‌ی توابعی که موتور صدا می‌زند وجود دارند", not missing, str(missing))

    check("خط جداکننده همان سبک خط تیره است",
          cards.DIVIDER == "-------------------------------", cards.DIVIDER)
    rendered = cards.card("🤖 تست", [("کلید", "مقدار")], footer="▪ پانویس")
    check("سطرها با • شروع می‌شوند", "• کلید: مقدار" in rendered, rendered)
    check("کارت دو خط جداکننده دارد", rendered.count(cards.DIVIDER) == 2)
    check("None در سطرها حذف می‌شود",
          "خالی" not in cards.card("t", [("خالی", None)]))


def test_store_adapter() -> None:
    section("آداپتور store: موتور کپی‌شده بی‌تغییر کار می‌کند")
    for attr in ("settings", "engine", "warmpath", "boost", "boost_prefix",
                 "boost_probe", "last_run", "pool_max_open"):
        check(f"store.{attr} وجود دارد", hasattr(store, attr))
    for meth in ("set_last_run", "set_account_meta", "set_setting", "reload"):
        check(f"store.{meth}() وجود دارد", callable(getattr(store, meth, None)))

    # set_account_meta با کلید اکانت (شناسه‌ی ردیف) صدا زده می‌شود، همان‌طور که
    # session_check و runner صدا می‌زنند.
    uid = 777
    db.ensure_customer(uid, "آداپتور", "adapt")
    aid = db.add_account(uid, "989127777777")
    store.set_account_meta(str(aid), contacts=42)
    check("set_account_meta روی دیتابیس اثر می‌گذارد",
          (db.get_account(uid, aid) or {}).get("contacts") == 42)

    # last_run فقط یک راهنمای تخمین است؛ نباید بشکند.
    store.set_last_run(account=str(aid), sent=10, timing={"per_send": 1.5})
    lr = store.last_run
    check("last_run بعد از ثبت خوانده می‌شود", isinstance(lr, dict), str(type(lr)))
    check("ارسال‌ها به شمارنده‌ی اکانت اضافه شد",
          (db.get_account(uid, aid) or {}).get("total_sent") == 10)

    # کلید نامعتبر نباید استثنا بدهد — موتور همه‌جا این تماس‌ها را try/except دارد
    # ولی خودِ آداپتور هم باید بی‌خطر باشد.
    store.set_account_meta("not-an-int", contacts=1)
    store.set_last_run(account=None)
    check("کلید نامعتبر استثنا نمی‌دهد", True)


def test_jobs_and_events() -> None:
    section("جاب‌ها و جدول events")
    uid = 888
    db.ensure_customer(uid, "جابی", "jobby")
    aid = db.add_account(uid, "989128888888")
    db.create_job("job-1", uid, aid, "send", "hybrid", "TRACE1", total=100)
    check("جاب در حال اجرا دیده می‌شود", len(db.running_jobs(uid)) == 1)
    check("جاب مشتری دیگر دیده نمی‌شود", len(db.running_jobs(111)) == 0)
    db.finish_job("job-1", "done", sent=90, failed=10)
    check("جاب تمام‌شده دیگر در حال اجرا نیست", len(db.running_jobs(uid)) == 0)
    check("نتیجه‌ی جاب ثبت شد",
          (db.last_jobs(uid, 1)[0] or {}).get("sent") == 90)

    # جاب‌های مانده از یک پروسه‌ی کشته‌شده باید fail بخورند نه اینکه ابدی زنده بمانند.
    db.create_job("job-2", uid, aid, "send", "hybrid", "TRACE2")
    n = db.mark_stale_jobs()
    check("جاب مانده از ری‌استارت fail می‌شود", n >= 1 and not db.running_jobs(uid),
          str(n))

    # تاریخچه فقط رویدادهای همان مشتری را می‌دهد.
    db.log_event(uid, "test", label="یک کار", trace_id="TRACE3")
    tl = db.timeline(uid, 0, 10)
    check("تاریخچه رویدادهای مشتری را دارد", any(e["label"] == "یک کار" for e in tl))
    check("تاریخچه‌ی مشتری دیگر خالی از این رویداد است",
          all(e["label"] != "یک کار" for e in db.timeline(111, 0, 50)))


def test_outbox() -> None:
    section("صف پیام مالک → مشتری")
    a, b = 111, 222
    db.enqueue_notification(a, "سلام A")
    n = db.broadcast("اطلاعیه", only_active=True)
    check("پیام همگانی برای مشتری‌های فعال صف شد", n >= 2, str(n))
    unsent = db.fetch_unsent_notifications(100)
    check("پیام‌های صف‌شده خوانده می‌شوند", len(unsent) >= 3, str(len(unsent)))
    db.mark_notification_sent(unsent[0]["id"])
    check("پیام تحویل‌شده دوباره برنمی‌گردد",
          len(db.fetch_unsent_notifications(100)) == len(unsent) - 1)

    # مسدودها پیام همگانی نمی‌گیرند.
    ratelimit.block(b, "تست")
    before = len(db.fetch_unsent_notifications(1000))
    db.broadcast("دومی", only_active=True)
    after = db.fetch_unsent_notifications(1000)
    got_b = [x for x in after if int(x["customer_id"]) == b and x["text"] == "دومی"]
    check("مشتری مسدود پیام همگانی نمی‌گیرد", not got_b, str(len(got_b)))
    ratelimit.unblock(b)
    check("شمارش پیام‌ها بعد از همگانی رشد کرد",
          len(after) > before, f"{before} → {len(after)}")


def test_maintenance() -> None:
    section("حالت تعمیر (فایل flag، بین دو پروسه)")
    db.set_maintenance(False)
    check("پیش‌فرض خاموش است", db.maintenance_on() is False)
    db.set_maintenance(True)
    check("روشن‌شدن از فایل flag دیده می‌شود", db.maintenance_on() is True)
    check("فایل flag واقعاً ساخته شد", config.maintenance_flag().exists())
    db.set_maintenance(False)
    check("خاموش‌شدن فایل را پاک می‌کند", not config.maintenance_flag().exists())


def test_bots_import() -> None:
    section("هر دو ربات import می‌شوند و پنل رندر می‌شود")
    import customer_bot
    import owner_bot
    import main  # noqa: F401
    check("customer_bot import شد", customer_bot is not None)
    check("owner_bot import شد", owner_bot is not None)
    check("ربات مشتری هندلر ثبت کرده",
          len(customer_bot.bot.list_event_handlers()) > 10,
          str(len(customer_bot.bot.list_event_handlers())))
    check("ربات مالک هندلر ثبت کرده",
          len(owner_bot.bot.list_event_handlers()) > 10,
          str(len(owner_bot.bot.list_event_handlers())))

    uid = 111
    txt = customer_bot.home_text(uid)
    check("پنل مشتری رندر می‌شود", "پنل من" in txt, txt[:60])
    check("پنل مشتری خط تیره دارد", cards.DIVIDER in txt)
    check("پنل مشتری سهمیه را نشان می‌دهد", "سهمیه" in txt)
    # مشتری نباید ping، نام موتور، یا آمار ناوگان ببیند.
    check("پنل مشتری ping نشان نمی‌دهد", "ms" not in txt, txt)
    check("پنل مشتری نام موتور نشان نمی‌دهد",
          "hybrid" not in txt and "bridge" not in txt, txt)
    check("پنل مشتری شماره‌ی کامل نشان نمی‌دهد", "989120000001" not in txt)


def main_() -> int:
    print("تست لایه‌ی چند-مشتری Dauroo")
    print("=" * 52)
    config.ensure_dirs()
    db.init()
    ratelimit.load()

    test_scoping()
    test_rate_limit()
    test_quota()
    test_account_cap()
    test_engine_switch()
    test_limit_scope()
    test_error_card_split()
    test_phone_masking()
    test_cards_contract()
    test_store_adapter()
    test_jobs_and_events()
    test_outbox()
    test_maintenance()
    test_bots_import()

    print("\n" + "=" * 52)
    print(f"{PASS} پاس، {FAIL} ناموفق")
    shutil.rmtree(_TMP, ignore_errors=True)
    if FAIL:
        print("تست‌های چند-مشتری شکست خوردند")
        return 1
    print("همه‌ی تست‌های چند-مشتری پاس شدند")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
