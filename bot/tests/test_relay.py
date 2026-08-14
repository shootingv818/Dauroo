"""
تست ماژول relay (تونل SSH → تلگرام) — آفلاین، بدون شبکه و بدون asyncssh.
=======================================================================

اجرا:  python -m bot.tests.test_relay

asyncssh نصب نیست و لازم هم نیست: مدیر کانکتورش تزریق‌پذیر است، پس تست یک
کانکتورِ جعلی می‌دهد که اتصال موفق/شکست/قطعی/تغییرِ کلیدِ میزبان را شبیه‌سازی
می‌کند. هیچ‌چیز اینجا به SSH، تلگرام یا ایتا وصل نمی‌شود.

هر تست یک قاعده‌ی مشخص از طرح را می‌بندد؛ دلیلش در کامنت هست.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# ---- محیط ایزوله: قبل از import گرفتن config ------------------------------- #
_TMP = tempfile.mkdtemp(prefix="dauroo_relay_")
os.environ["DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["PROFILES_DIR"] = os.path.join(_TMP, "profiles")
os.environ["ARTIFACTS_DIR"] = os.path.join(_TMP, "artifacts")
os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "x")
os.environ.setdefault("OWNER_BOT_TOKEN", "owner-token")
os.environ.setdefault("CUSTOMER_BOT_TOKEN", "customer-token")
os.environ.setdefault("OWNER_ID", "1000")
# relay را روشن کن و کلیدِ رمزنگاری بده تا رمزها ذخیره شوند.
os.environ["RELAY_ENABLED"] = "1"
os.environ["RELAY_SECRET_KEY"] = "unit-test-secret-key-please-ignore"
os.environ["RELAY_LOCAL_PORT"] = "1080"
os.environ["RELAY_HEALTH_INTERVAL"] = "5"
os.environ["RELAY_PROBE_TIMEOUT"] = "3"
os.environ["RELAY_FAIL_THRESHOLD"] = "3"
os.environ["RELAY_MAX_PING_MS"] = "4000"
os.environ["RELAY_PING_HIGH_STREAK"] = "3"

def _stub_playwright() -> None:
    try:
        import playwright.async_api  # noqa: F401
        return
    except Exception:  # noqa: BLE001
        pass
    pkg = types.ModuleType("playwright")
    api = types.ModuleType("playwright.async_api")
    for name in ("Browser", "BrowserContext", "CDPSession", "Page", "Locator", "Error"):
        setattr(api, name, type(name, (object,), {}))
    api.TimeoutError = type("TimeoutError", (Exception,), {})
    api.async_playwright = lambda: None
    pkg.async_api = api
    sys.modules.setdefault("playwright", pkg)
    sys.modules.setdefault("playwright.async_api", api)


def _stub_telethon() -> None:
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

        async def get_me(self):
            return types.SimpleNamespace(id=1, username="bot")

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
    sys.modules["telethon.tl"] = types.ModuleType("telethon.tl")
    sys.modules["telethon.tl.types"] = types.ModuleType("telethon.tl.types")


_stub_playwright()
_stub_telethon()

import db                       # noqa: E402
import relay                    # noqa: E402
from config import config       # noqa: E402
from relay import crypto        # noqa: E402
from relay.manager import RelayManager  # noqa: E402

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


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# =========================================================================== #
# کانکتورِ جعلی (به‌جای asyncssh)
# =========================================================================== #
class FakeTunnel:
    def __init__(self, host_key_b64: str = "KEY_A"):
        self.host_key_b64 = host_key_b64
        self._closed = False
        self.forwarded = None

    async def forward_socks(self, host, port):
        self.forwarded = (host, port)

    def is_closed(self) -> bool:
        return self._closed

    async def wait_closed(self):
        return None

    async def close(self):
        self._closed = True


def make_connector(*, host_key="KEY_A", fail_hosts=None, fail_all=False):
    """یک کانکتورِ جعلی می‌سازد و آخرین آرگومان‌ها را ثبت می‌کند."""
    calls = []

    async def connector(*, host, port, username, password, keepalive):
        calls.append({"host": host, "port": port, "username": username,
                      "password": password, "keepalive": keepalive})
        if fail_all or (fail_hosts and host in fail_hosts):
            raise ConnectionError(f"refused: {host}")
        hk = host_key(host) if callable(host_key) else host_key
        return FakeTunnel(host_key_b64=hk)

    connector.calls = calls
    return connector



# =========================================================================== #
def test_crypto() -> None:
    section("رمزنگاری: رفت‌وبرگشت، اصالت، کلیدِ اشتباه، نبودِ کلید")
    key = "some-key-123"
    tok = crypto.encrypt("hunter2", key)
    check("رمزگشایی همان متن را می‌دهد", crypto.decrypt(tok, key) == "hunter2")
    check("توکن، متنِ ساده نیست", "hunter2" not in tok, tok)
    check("توکن پیشوندِ نسخه دارد", tok.startswith("v1:"), tok)

    # هر رمزنگاری nonce تازه دارد، پس دو خروجی فرق می‌کنند (ولی هر دو باز می‌شوند).
    tok2 = crypto.encrypt("hunter2", key)
    check("دو رمزنگاریِ یک متن، دو توکنِ متفاوت", tok != tok2)
    check("توکنِ دوم هم درست باز می‌شود", crypto.decrypt(tok2, key) == "hunter2")

    # کلیدِ اشتباه نباید باز کند.
    try:
        crypto.decrypt(tok, "wrong-key")
        check("کلیدِ اشتباه رد می‌شود", False)
    except crypto.DecryptError:
        check("کلیدِ اشتباه رد می‌شود", True)

    # دستکاریِ توکن باید با برچسبِ اصالت گیر بیفتد.
    bad = tok[:-2] + ("aa" if not tok.endswith("aa") else "bb")
    try:
        crypto.decrypt(bad, key)
        check("توکنِ دستکاری‌شده رد می‌شود", False)
    except crypto.DecryptError:
        check("توکنِ دستکاری‌شده رد می‌شود", True)

    # بدونِ کلید، رمزنگاری باید صریح شکست بخورد (هرگز plaintext).
    try:
        crypto.encrypt("x", "")
        check("رمزنگاری بدونِ کلید ممنوع است", False)
    except ValueError:
        check("رمزنگاری بدونِ کلید ممنوع است", True)
    check("is_key_set خالی را False می‌دهد", crypto.is_key_set("") is False)
    check("is_key_set مقدار را True می‌دهد", crypto.is_key_set("k") is True)
    check("mask رمز را لو نمی‌دهد", "hunter2" not in crypto.mask("hunter2"))
    # یونیکد هم باید سالم رد و برگردد.
    utok = crypto.encrypt("رمزِ فارسی ۱۲۳", key)
    check("یونیکد سالم رمزگشایی می‌شود", crypto.decrypt(utok, key) == "رمزِ فارسی ۱۲۳")


def test_db_model() -> None:
    section("مدلِ داده relay: افزودن، upsert، ترتیب، حذف")
    key = config.relay_secret_key()
    enc = crypto.encrypt("pw-A", key)
    rid = db.add_relay("1.1.1.1", 22, "root", enc, priority=10, source="panel")
    check("افزودن id می‌دهد", isinstance(rid, int) and rid > 0)

    r = db.get_relay(rid)
    check("رمز به‌صورت رمزنگاری‌شده ذخیره شده", r["secret_enc"] == enc)
    check("متنِ سادهٔ رمز در دیتابیس نیست", "pw-A" not in (r["secret_enc"] or ""))
    check("رمزِ ذخیره‌شده به همان متن باز می‌شود",
          crypto.decrypt(r["secret_enc"], key) == "pw-A")

    # upsert روی (host, port): افزودن دوباره باید ردیف جدید نسازد.
    rid2 = db.add_relay("1.1.1.1", 22, "admin", crypto.encrypt("pw-B", key),
                        priority=10, source="panel")
    check("افزودنِ دوباره‌ی همان endpoint، id تازه نمی‌سازد", rid2 == rid)
    r = db.get_relay(rid)
    check("upsert نام کاربری را به‌روز می‌کند", r["username"] == "admin")
    check("upsert رمز را به‌روز می‌کند",
          crypto.decrypt(r["secret_enc"], key) == "pw-B")

    # پورتِ متفاوت = endpointِ متفاوت = ردیفِ جدید.
    rid3 = db.add_relay("1.1.1.1", 2222, "root", enc, priority=5, source="panel")
    check("پورتِ متفاوت ردیفِ جدا می‌سازد", rid3 != rid)

    # ترتیبِ list_relays باید priority صعودی باشد (نگهبان به همین ترتیب امتحان می‌کند).
    lst = db.list_relays()
    pris = [x["priority"] for x in lst]
    check("list_relays بر اساسِ priority مرتب است", pris == sorted(pris), str(pris))

    check("find_relay endpoint را پیدا می‌کند",
          db.find_relay("1.1.1.1", 2222)["id"] == rid3)
    check("set_relay_fields وضعیت را عوض می‌کند",
          (db.set_relay_fields(rid, status="broken"), db.get_relay(rid)["status"])[1]
          == "broken")
    check("delete_relay حذف می‌کند", db.delete_relay(rid3) is True)
    check("relayِ حذف‌شده دیگر نیست", db.get_relay(rid3) is None)


def test_bootstrap_and_proxy() -> None:
    section("bootstrap از .env و پروکسیِ Telethon")
    # telethon_proxy وقتی روشن است باید تاپلِ SOCKS بدهد، و پورتِ لوکال درست باشد.
    px = relay.telethon_proxy()
    check("پروکسی SOCKS5 است", px is not None and px[0] == "socks5", str(px))
    check("پروکسی روی 127.0.0.1 است", px[1] == "127.0.0.1", str(px))
    check("پورتِ مالک = RELAY_LOCAL_PORT", px[2] == config.RELAY_LOCAL_PORT)

    # bootstrap با host + password + کلید → ردیفِ رمزنگاری‌شده می‌سازد.
    config.RELAY_HOST = "boot.example.com"
    config.RELAY_SSH_PORT = 22
    config.RELAY_USER = "root"
    config.RELAY_PASSWORD = "bootstrap-pass"
    bid = relay.ensure_bootstrap()
    check("bootstrap ردیف ساخت", bid is not None)
    r = db.get_relay(bid)
    check("bootstrap منبعش bootstrap است", r["source"] == "bootstrap")
    check("رمزِ bootstrap متنِ ساده نیست", "bootstrap-pass" not in (r["secret_enc"] or ""))
    check("رمزِ bootstrap درست باز می‌شود",
          crypto.decrypt(r["secret_enc"], config.relay_secret_key()) == "bootstrap-pass")

    # بدونِ کلیدِ رمزنگاری، هرگز نباید رمز ذخیره شود.
    saved_key = config.RELAY_SECRET_KEY
    saved_raw = config.RAW_ENCRYPTION_KEY
    config.RELAY_SECRET_KEY = ""
    config.RAW_ENCRYPTION_KEY = ""
    config.RELAY_HOST = "nokey.example.com"
    check("بدونِ کلید، bootstrap رد می‌شود", relay.ensure_bootstrap() is None)
    check("relayِ بدونِ کلید ذخیره نشده", db.find_relay("nokey.example.com", 22) is None)
    config.RELAY_SECRET_KEY = saved_key
    config.RAW_ENCRYPTION_KEY = saved_raw
    config.RELAY_HOST = ""
    config.RELAY_PASSWORD = ""



# =========================================================================== #
def _fresh_relays(key, specs):
    """جدولِ relay را پاک و از نو با specs=[(host,port,priority,pw)] پر می‌کند."""
    for r in db.list_relays():
        db.delete_relay(r["id"])
    ids = {}
    for host, port, pri, pw in specs:
        ids[host] = db.add_relay(host, port, "root", crypto.encrypt(pw, key),
                                 priority=pri, source="panel")
    return ids


def test_connect_and_tofu() -> None:
    section("اتصال + TOFU: کلیدِ میزبان اولین‌بار ذخیره، تغییرش رد می‌شود")
    key = config.relay_secret_key()
    ids = _fresh_relays(key, [("a.host", 22, 10, "secretA")])
    rid = ids["a.host"]

    conn = make_connector(host_key="KEY_A")
    mgr = RelayManager(local_port=1080, connector=conn)
    ok = run(mgr._connect(db.get_relay(rid)))
    check("اتصالِ موفق", ok is True)
    check("رمز درست رمزگشایی و پاس داده شد", conn.calls[-1]["password"] == "secretA")
    check("SOCKS روی پورتِ درست باز شد", mgr._tunnel.forwarded == ("127.0.0.1", 1080))
    check("current_id ست شد", mgr.current_id == rid)
    check("وضعیت در دیتابیس active شد", db.get_relay(rid)["status"] == "active")
    check("کلیدِ میزبان (TOFU) ذخیره شد", db.get_relay(rid)["host_key"] == "KEY_A")

    # اتصالِ دوباره با همان کلید → مشکلی نیست.
    run(mgr._teardown_tunnel())
    ok = run(mgr._connect(db.get_relay(rid)))
    check("اتصالِ مجدد با همان کلیدِ میزبان قبول است", ok is True)

    # حالا سرور کلیدِ میزبانِ متفاوت نشان می‌دهد → باید رد شود (احتمال MITM).
    run(mgr._teardown_tunnel())
    mgr.configure(connector=make_connector(host_key="KEY_B"))
    ok = run(mgr._connect(db.get_relay(rid)))
    check("کلیدِ میزبانِ عوض‌شده رد می‌شود", ok is False)
    check("relay با کلیدِ عوض‌شده broken می‌شود", db.get_relay(rid)["status"] == "broken")
    check("تونل پذیرفته نشد", mgr.current_id is None)


def test_connect_failure_and_selection() -> None:
    section("انتخاب و failover: relayِ خراب رد، بعدی انتخاب می‌شود")
    key = config.relay_secret_key()
    ids = _fresh_relays(key, [("primary", 22, 0, "pw1"),
                              ("backup", 22, 10, "pw2")])

    # کانکتوری که فقط primary را رد می‌کند.
    mgr = RelayManager(local_port=1080,
                       connector=make_connector(fail_hosts={"primary"}))
    ok = run(mgr._select_and_connect())
    check("وقتی اولی وصل نشد، دومی انتخاب می‌شود", ok is True)
    check("relayِ فعال، backup است", db.get_relay(mgr.current_id)["host"] == "backup")
    check("primary خراب علامت خورد", db.get_relay(ids["primary"])["status"] == "broken")

    # candidateها: سالم اول، خراب آخر، ترجیحی جلوتر.
    cands = mgr._candidates()
    hosts = [c["host"] for c in cands]
    check("candidate سالم قبل از خراب می‌آید",
          hosts.index("backup") < hosts.index("primary"), str(hosts))

    # ترجیحی (preferred) باید جلوی صف بیاید حتی اگر خراب باشد.
    db.set_owner_setting("relay_preferred", ids["primary"])
    cands = mgr._candidates()
    check("relayِ ترجیحی جلوی صف می‌آید", cands[0]["host"] == "primary",
          str([c["host"] for c in cands]))
    db.set_owner_setting("relay_preferred", None)


def test_health_and_failover() -> None:
    section("health-check: شکستِ پیاپی و پینگِ بالا → failover")
    key = config.relay_secret_key()
    ids = _fresh_relays(key, [("h1", 22, 0, "pw1"), ("h2", 22, 10, "pw2")])

    # ---- شکستِ سلامت تا آستانه → failover ----
    probe_state = {"healthy": True, "ping": 100}

    async def probe():
        return probe_state["healthy"]

    mgr = RelayManager(local_port=1080,
                       connector=make_connector(fail_hosts=set()),
                       health_probe=probe)
    run(mgr._select_and_connect())
    first = mgr.current_id
    check("اول به h1 وصل شد", db.get_relay(first)["host"] == "h1")

    # probe سالم → هیچ اتفاقی نمی‌افتد.
    check("probeِ سالم اقدامش ok است", run(mgr._health_cycle()) == "ok")

    # probe خراب: تا آستانه (۳) نباید سویچ کند، در سومی باید.
    probe_state["healthy"] = False
    check("شکستِ اول فقط fail است", run(mgr._health_cycle()) == "fail")
    check("شکستِ دوم فقط fail است", run(mgr._health_cycle()) == "fail")
    action = run(mgr._health_cycle())
    check("در آستانه، failover رخ می‌دهد", action == "failover", action)
    check("بعد از failover به h2 رفت", db.get_relay(mgr.current_id)["host"] == "h2")
    check("h1 خراب علامت خورد", db.get_relay(first)["status"] == "broken")
    check("شمارنده‌ی قطعیِ h1 بالا رفت", db.get_relay(first)["disconnects"] >= 1)

    # ---- نگهبانِ تأخیر: پینگِ بالا برای چند دور → failover ----
    ids = _fresh_relays(key, [("p1", 22, 0, "pw1"), ("p2", 22, 10, "pw2")])
    probe_state = {"healthy": True, "ping": 9999}

    async def slow_probe():
        # سالم است ولی کند؛ مدیر خودش زمان را می‌سنجد، پس اینجا فقط sleep می‌کنیم.
        await asyncio.sleep(0)
        return True

    # برای کنترلِ دقیقِ پینگ، probe را طوری می‌گذاریم که مدیر پینگ را از زمانِ
    # واقعی می‌سنجد؛ به‌جایش max_ping را صفر-تحمل می‌کنیم تا هر پینگی «بالا» شود.
    saved_max = config.RELAY_MAX_PING_MS
    config.RELAY_MAX_PING_MS = 1  # هر round-trip از این بیشتر است

    async def ok_but_slow():
        await asyncio.sleep(0.005)  # ~5ms > 1ms
        return True

    mgr2 = RelayManager(local_port=1081,
                        connector=make_connector(fail_hosts=set()),
                        health_probe=ok_but_slow)
    run(mgr2._select_and_connect())
    p1 = mgr2.current_id
    check("پینگِ بالا دورِ اول فقط slow است", run(mgr2._health_cycle()) == "slow")
    check("پینگِ بالا دورِ دوم فقط slow است", run(mgr2._health_cycle()) == "slow")
    check("پینگِ بالا در آستانه، failover می‌کند",
          run(mgr2._health_cycle()) == "slow_failover")
    check("بعد از پینگِ بالا به p2 رفت", db.get_relay(mgr2.current_id)["host"] == "p2")
    config.RELAY_MAX_PING_MS = saved_max



# =========================================================================== #
def test_disconnect_reconnect() -> None:
    section("قطعیِ ناگهانی: تونلِ مُرده → failover در دورِ نگهبان")
    key = config.relay_secret_key()
    ids = _fresh_relays(key, [("d1", 22, 0, "pw1"), ("d2", 22, 10, "pw2")])
    mgr = RelayManager(local_port=1080, connector=make_connector(fail_hosts=set()))
    run(mgr._select_and_connect())
    first = mgr.current_id
    check("اول وصل شد", first is not None)

    # تونل ناگهان می‌میرد.
    mgr._tunnel._closed = True
    check("تونل مُرده تشخیص داده می‌شود", mgr._tunnel.is_closed() is True)
    run(mgr._reconnect_cycle())   # همان کاری که نگهبان می‌کند
    check("بعد از قطعی، به relayِ دیگری وصل شد", mgr.current_id is not None)
    check("relayِ جدید با اولی فرق دارد", mgr.current_id != first)
    check("relayِ قطع‌شده disconnect خورد", db.get_relay(first)["disconnects"] >= 1)


def test_switch_and_delete() -> None:
    section("سویچِ دستی و حذفِ relayِ فعال")
    key = config.relay_secret_key()
    ids = _fresh_relays(key, [("s1", 22, 0, "pw1"), ("s2", 22, 10, "pw2")])
    mgr = RelayManager(local_port=1080, connector=make_connector(fail_hosts=set()))
    run(mgr._select_and_connect())
    check("اول به s1 وصل شد (priority کمتر)",
          db.get_relay(mgr.current_id)["host"] == "s1")

    ok = run(mgr.switch(ids["s2"]))
    check("سویچ به s2 موفق بود", ok is True)
    check("حالا فعال s2 است", db.get_relay(mgr.current_id)["host"] == "s2")
    check("s2 به‌عنوان ترجیحی ذخیره شد", db.owner_setting("relay_preferred") == ids["s2"])

    # سویچ به یک relayِ خراب باید status را ریست کند و شانسِ تازه بدهد.
    db.set_relay_fields(ids["s1"], status="broken")
    ok = run(mgr.switch(ids["s1"]))
    check("سویچ به relayِ خراب دوباره امتحانش می‌کند", ok is True)
    check("سویچ به s1 فعالش کرد", db.get_relay(mgr.current_id)["host"] == "s1")
    db.set_owner_setting("relay_preferred", None)


def test_no_candidates_and_backoff() -> None:
    section("نبودِ relay، شکستِ همه، backoff و تایم‌اوتِ probe")
    key = config.relay_secret_key()

    # هیچ relayی نیست → start باید False بدهد و کرش نکند.
    for r in db.list_relays():
        db.delete_relay(r["id"])
    mgr = RelayManager(local_port=1080, connector=make_connector())
    check("بدونِ relay، select_and_connect ناموفق است",
          run(mgr._select_and_connect()) is False)

    # همه‌ی relayها وصل نمی‌شوند → همچنان بدونِ کرش، ناموفق.
    _fresh_relays(key, [("x1", 22, 0, "pw"), ("x2", 22, 10, "pw")])
    mgr = RelayManager(local_port=1080, connector=make_connector(fail_all=True))
    check("وقتی همه شکست می‌خورند، ناموفق ولی بدونِ کرش",
          run(mgr._select_and_connect()) is False)

    # backoff: صعودی، ولی هرگز از cap بیشتر و هرگز صفر (jitter).
    ds = [mgr._backoff(i) for i in range(1, 8)]
    check("backoff هیچ‌وقت صفر نیست", all(d > 0 for d in ds), str(ds))
    check("backoff از cap بیشتر نمی‌شود",
          all(d <= config.RELAY_BACKOFF_CAP for d in ds), str(ds))
    check("backoff تمایل به رشد دارد", ds[-1] >= ds[0], str(ds))

    # probeِ کند از تایم‌اوت رد نمی‌شود → ناسالم شمرده می‌شود، نه اینکه قفل کند.
    async def hang():
        await asyncio.sleep(10)
        return True

    saved = config.RELAY_PROBE_TIMEOUT
    os.environ["RELAY_PROBE_TIMEOUT"] = "1"
    config.RELAY_PROBE_TIMEOUT = 1
    mgr = RelayManager(local_port=1080, connector=make_connector(), health_probe=hang)
    healthy, ping = run(mgr._run_probe())
    check("probeِ معلق با تایم‌اوت ناسالم می‌شود، نه قفل", healthy is False)
    config.RELAY_PROBE_TIMEOUT = saved


def test_start_stop_status() -> None:
    section("start/stop و کارتِ وضعیت")
    key = config.relay_secret_key()
    _fresh_relays(key, [("live", 22, 0, "pw")])

    async def scenario():
        # start + status + stop در یک event loop، تا تسکِ نگهبان سرگردان نماند.
        mgr = RelayManager(local_port=1080, connector=make_connector(fail_hosts=set()))
        ok = await mgr.start()
        st = mgr.status()
        await mgr.stop()
        return ok, st, mgr

    ok, st, mgr = run(scenario())
    check("start وصل شد و True داد", ok is True)
    check("status می‌گوید فعال است", st["enabled"] is True)
    check("status تونلِ وصل را نشان می‌دهد", st["connected"] is True)
    check("status پورتِ لوکال را دارد", st["local_port"] == 1080)
    check("status لیستِ relayها را می‌دهد", len(st["relays"]) >= 1)
    check("status می‌گوید asyncssh نصب نیست (این سندباکس)",
          st["have_asyncssh"] is False)
    check("بعد از stop، تونل بسته است", mgr._tunnel is None)


def test_no_tunnel_leak_under_concurrency() -> None:
    section("گذارهای هم‌زمان تونلِ رهاشده به‌جا نمی‌گذارند")
    key = config.relay_secret_key()
    _fresh_relays(key, [("cA", 22, 0, "pw"), ("cB", 22, 10, "pw")])

    created = []

    class SlowTunnel(FakeTunnel):
        def __init__(self, host):
            super().__init__(host_key_b64="K")
            self.host = host
            created.append(self)

        async def forward_socks(self, h, p):
            await asyncio.sleep(0.01)   # پنجره‌ی رقابت
            self.forwarded = (h, p)

    async def slow_conn(*, host, port, username, password, keepalive):
        await asyncio.sleep(0.02)       # اتصال زمان می‌برد
        return SlowTunnel(host)

    async def scenario():
        mgr = RelayManager(local_port=1080, connector=slow_conn)
        await mgr.reconnect()
        # نگهبان و مالک هم‌زمان گذار می‌زنند — همان چیزی که تونل را لو می‌داد.
        await asyncio.gather(mgr.reconnect(), mgr.switch(None))
        leaked = [t for t in created if not t.is_closed() and t is not mgr._tunnel]
        active = mgr._tunnel
        await mgr.stop()
        return leaked, active, created

    leaked, active, all_t = run(scenario())
    check("تونلی ساخته شد", len(all_t) >= 2, str(len(all_t)))
    check("هیچ تونلِ رهاشده‌ای نماند", leaked == [],
          f"{len(leaked)} رهاشده: {[t.host for t in leaked]}")
    check("یک تونلِ فعال هست", active is not None)
    check("بعد از stop همه بسته‌اند", all(t.is_closed() for t in all_t))

    # ترجیح نباید برای relayِ ناموجود ذخیره شود، وگرنه یک آیدیِ مرده برای همیشه
    # در تنظیمات می‌ماند.
    db.set_owner_setting("relay_preferred", None)

    async def bad_switch():
        mgr = RelayManager(local_port=1080, connector=make_connector(fail_hosts=set()))
        ok = await mgr.switch(999999)
        await mgr.stop()
        return ok

    ok = run(bad_switch())
    check("سویچ به relayِ ناموجود کرش نمی‌کند", isinstance(ok, bool))
    check("ترجیحِ مرده ذخیره نمی‌شود",
          db.owner_setting("relay_preferred") is None,
          str(db.owner_setting("relay_preferred")))


def test_owner_panel_wired() -> None:
    section("پنلِ مالک: هندلرهای relay ثبت و کارت رندر می‌شود")
    import owner_bot
    handlers = owner_bot.bot.list_event_handlers()
    check("owner_bot هندلر دارد", len(handlers) > 10, str(len(handlers)))
    # کلاینتِ ربات مالک باید با پروکسیِ relay ساخته شده باشد (چون RELAY_ENABLED=1).
    # استابِ تلگرام kwargها را می‌پذیرد؛ فقط مطمئن می‌شویم import و ساخت نشکست.
    check("owner_bot با موفقیت import شد", owner_bot is not None)
    txt = run(owner_bot.home_text())
    check("خانه‌ی مالک رندر می‌شود", "پنل مالک" in txt, txt[:40])
    kb = owner_bot.home_kb()
    labels = [b.text for row in kb for b in row]
    check("دکمه‌ی relayها در خانه هست", any("relay" in x for x in labels), str(labels))


def main_() -> int:
    print("تست ماژول relay (SSH → تلگرام)")
    print("=" * 52)
    config.ensure_dirs()
    db.init()

    test_crypto()
    test_db_model()
    test_bootstrap_and_proxy()
    test_connect_and_tofu()
    test_connect_failure_and_selection()
    test_health_and_failover()
    test_disconnect_reconnect()
    test_switch_and_delete()
    test_no_candidates_and_backoff()
    test_start_stop_status()
    test_no_tunnel_leak_under_concurrency()
    test_owner_panel_wired()

    print("\n" + "=" * 52)
    print(f"{PASS} پاس، {FAIL} ناموفق")
    shutil.rmtree(_TMP, ignore_errors=True)
    if FAIL:
        print("تست‌های relay شکست خوردند")
        return 1
    print("همه‌ی تست‌های relay پاس شدند")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
