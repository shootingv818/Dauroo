"""
relay/selfcheck.py — تستِ دستیِ relay، برای اجرا روی سرورِ ایران.
=================================================================

این اسکریپت **مستقل** است: بدون ربات، بدون تلگرام‌توکن، فقط تونل را می‌سنجد. با
SSH به relay وصل می‌شود، یک SOCKS5 باز می‌کند و از داخلِ همان مسیری که Telethon
استفاده می‌کند (SOCKS → SSH → بیرون) به چند مقصدِ تلگرام CONNECT می‌زند و زمانش
را می‌سنجد. کلاینتِ SOCKS با کتابخانه‌ی استاندارد نوشته شده، پس جز `asyncssh`
چیزی لازم ندارد.

اجرا (روی سرور، داخلِ پوشه‌ی پروژه و venv):

    # از .env می‌خواند (RELAY_HOST/RELAY_SSH_PORT/RELAY_USER/RELAY_PASSWORD)
    python -m relay.selfcheck

    # یا صریح؛ رمز را امن می‌پرسد (در history نمی‌ماند)
    python -m relay.selfcheck 1.2.3.4 22 root

    # اگر relay را از پنلِ مالک اضافه کرده‌ای و در دیتابیس است:
    python -m relay.selfcheck --from-db

خروجی، هر گام را با ✅/❌ و پینگ نشان می‌دهد. اگر همه سبز شد، ربات هم از همین
تونل به تلگرام می‌رسد.
"""
from __future__ import annotations

import asyncio
import getpass
import sys
import time

from config import config

#: مقصدهایی که تلگرام واقعاً به آن‌ها وصل می‌شود. Telethon با MTProto به IPِ
#: دیتاسنترها می‌رود؛ پس هم دامنه‌ی Bot API و هم چند IPِ دیتاسنتر را می‌سنجیم.
TELEGRAM_TARGETS = [
    ("api.telegram.org", 443, "Bot API"),
    ("149.154.167.50", 443, "DC2 (اصلی)"),
    ("149.154.175.50", 443, "DC4"),
]

#: پورتِ محلیِ تست — عمداً با پورتِ ربات (۱۰۸۰/۱۰۸۱) فرق دارد تا اگر ربات در حال
#: اجراست، تداخل نکند.
TEST_LOCAL_PORT = 11080


def _p(msg: str = "") -> None:
    print(msg, flush=True)


#: کلاینتِ SOCKS از `relay/probe.py` می‌آید — همان کدی که نگهبانِ ربات برای
#: سنجشِ تأخیر استفاده می‌کند. یکی بودنشان مهم است: اگر این اسکریپت مسیرِ
#: دیگری را می‌سنجید، سبزشدنش دربارهٔ ربات چیزی ثابت نمی‌کرد.
from relay.probe import socks5_connect as _socks5_connect  # noqa: E402


def _resolve_relay(argv) -> tuple:
    """(host, port, user, password) را از argv / env / db درمی‌آورد."""
    if "--from-db" in argv:
        import db
        from relay import crypto
        relays = db.list_relays()
        if not relays:
            _p("❌ در دیتابیس هیچ relayی نیست. اول یکی اضافه کن یا آرگومان بده.")
            raise SystemExit(2)
        r = relays[0]
        pw = ""
        if r.get("secret_enc"):
            pw = crypto.decrypt(r["secret_enc"], config.relay_secret_key())
        _p(f"• از دیتابیس: {r['host']}:{r['ssh_port']} (#{r['id']})")
        return r["host"], int(r["ssh_port"]), r.get("username") or "root", pw

    pos = [a for a in argv if not a.startswith("-")]
    if len(pos) >= 1:
        host = pos[0]
        port = int(pos[1]) if len(pos) >= 2 else 22
        user = pos[2] if len(pos) >= 3 else "root"
        pw = config.RELAY_PASSWORD or getpass.getpass("رمز عبور SSH: ")
        return host, port, user, pw

    # از .env
    if not config.RELAY_HOST:
        _p("❌ RELAY_HOST در .env نیست و آرگومانی هم ندادی.")
        _p("   استفاده: python -m relay.selfcheck <host> [port] [user]")
        raise SystemExit(2)
    pw = config.RELAY_PASSWORD or getpass.getpass("رمز عبور SSH: ")
    return (config.RELAY_HOST, int(config.RELAY_SSH_PORT),
            config.RELAY_USER or "root", pw)


async def _run(argv) -> int:
    _p("تستِ relay (SSH → تلگرام)")
    _p("=" * 48)

    # ۱) asyncssh نصب است؟
    try:
        import asyncssh
    except Exception:  # noqa: BLE001
        _p("❌ asyncssh نصب نیست.  رفع:  pip install asyncssh")
        return 1
    _p(f"✅ asyncssh نصب است (نسخه {getattr(asyncssh, '__version__', '?')})")

    # ۱b) کلاینتِ SOCKS که خودِ Telethon لازم دارد.
    # چرا اینجا و چرا مهم: بدونِ آن همه‌ی گام‌های زیر سبز می‌شوند و تونل واقعاً کار
    # می‌کند، ولی **ربات** با «No module named 'socks'» می‌میرد. یک بار همین
    # اتفاق افتاد و چون این اسکریپت سبز بود، اشتباهاً به‌نظر رسید مشکل جای دیگری
    # است. پس این شکاف را همین‌جا می‌بندیم.
    socks_ok = False
    try:
        import python_socks  # noqa: F401
        socks_ok = True
        _p(f"✅ python-socks نصب است (نسخه "
           f"{getattr(python_socks, '__version__', '?')})")
    except Exception:  # noqa: BLE001
        try:
            import socks  # noqa: F401
            socks_ok = True
            _p("✅ PySocks نصب است (مسیرِ قدیمی، کار می‌کند)")
        except Exception:  # noqa: BLE001
            _p("❌ python-socks نصب نیست — تونل کار می‌کند ولی **ربات** "
               "نمی‌تواند از آن استفاده کند.")
            _p("   رفع:  pip install 'python-socks[asyncio]'")

    host, port, user, password = _resolve_relay(argv)
    _p(f"• هدف: {user}@{host}:{port}")
    _p("")

    # ۲) اتصالِ SSH
    _p("① اتصالِ SSH …")
    t0 = time.monotonic()
    try:
        conn = await asyncio.wait_for(
            asyncssh.connect(host, port=port, username=user, password=password,
                             known_hosts=None, keepalive_interval=15),
            timeout=20)
    except Exception as exc:  # noqa: BLE001
        _p(f"   ❌ اتصال شکست خورد: {exc!r}")
        _p("   بررسی کن: IP/پورت درست است؟ رمز درست است؟ فایروالِ سرور SSH را باز گذاشته؟")
        return 1
    ssh_ms = int((time.monotonic() - t0) * 1000)
    _p(f"   ✅ وصل شد ({ssh_ms}ms)")

    # اثرِانگشتِ کلیدِ میزبان (همان که TOFU ذخیره می‌کند)
    try:
        import base64
        import hashlib
        key = conn.get_server_host_key()
        if key is not None:
            fp = base64.b64encode(
                hashlib.sha256(key.public_data).digest()).decode()[:24]
            _p(f"   🔑 کلیدِ میزبان: SHA256:{fp}")
    except Exception:  # noqa: BLE001
        pass

    rc = 0
    try:
        # ۳) بازکردنِ SOCKS5
        _p("")
        _p(f"② بازکردنِ SOCKS5 روی 127.0.0.1:{TEST_LOCAL_PORT} …")
        try:
            listener = await conn.forward_socks("127.0.0.1", TEST_LOCAL_PORT)
        except Exception as exc:  # noqa: BLE001
            _p(f"   ❌ نشد: {exc!r}")
            return 1
        _p("   ✅ باز شد")

        # ۴) رسیدن به تلگرام از داخلِ تونل
        _p("")
        _p("③ رسیدن به تلگرام از داخلِ تونل …")
        any_ok = False
        for dst_host, dst_port, label in TELEGRAM_TARGETS:
            t = time.monotonic()
            ok, msg = await _socks5_connect(TEST_LOCAL_PORT, dst_host, dst_port)
            ms = int((time.monotonic() - t) * 1000)
            if ok:
                any_ok = True
                _p(f"   ✅ {label} ({dst_host}:{dst_port}) — {ms}ms")
            else:
                _p(f"   ❌ {label} ({dst_host}:{dst_port}) — {msg}")
        rc = 0 if any_ok else 1

        try:
            listener.close()
        except Exception:  # noqa: BLE001
            pass
    finally:
        conn.close()
        try:
            await conn.wait_closed()
        except Exception:  # noqa: BLE001
            pass

    _p("")
    _p("=" * 48)
    if rc == 0 and socks_ok:
        _p("🟢 نتیجه: تونل کار می‌کند — ربات هم از همین مسیر به تلگرام می‌رسد.")
    elif rc == 0 and not socks_ok:
        # سبزِ گمراه‌کننده را صریح رد می‌کنیم.
        _p("🟡 نتیجه: تونل کار می‌کند، ولی ربات نمی‌تواند از آن استفاده کند.")
        _p("   python-socks نصب نیست:  pip install 'python-socks[asyncio]'")
        rc = 1
    else:
        _p("🔴 نتیجه: SSH وصل شد ولی به تلگرام نرسید.")
        _p("   یعنی خودِ سرورِ relay به تلگرام دسترسی ندارد (نه سرورِ ایران).")
    return rc


def main() -> int:
    try:
        return asyncio.run(_run(sys.argv[1:]))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
