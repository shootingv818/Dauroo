"""
relay/probe.py — کلاینتِ کوچکِ SOCKS5 برای سنجشِ خودِ تونل.
==========================================================

چرا جدا از `health_probe`ِ تلگرام:

    `getMe` می‌گوید «تلگرام در دسترس است؟» — که همان health-checkِ واقعی است و
    باید بماند. ولی زمانش **تأخیرِ تونل نیست**: اولین `getMe` روی یک تونلِ تازه،
    دست‌دادنِ MTProto و انتخابِ دیتاسنتر را هم شامل می‌شود و چند ثانیه طول می‌کشد،
    در حالی که خودِ تونل ۳ میلی‌ثانیه است. اگر تأخیر را از `getMe` بگیریم،
    نگهبان یک تونلِ کاملاً سالم را «کند» می‌بیند و بی‌دلیل failover می‌کند —
    که روی سرورِ واقعی همین اتفاق افتاد (پنل: 🔴 خراب · 5466ms، ولی selfcheck: ۳ms).

پس تفکیک این است:

    سلامت  = getMe (تلگرام واقعاً جواب می‌دهد؟)
    تأخیر  = همین probe (یک CONNECT از داخلِ تونل، بدونِ هیچ هزینه‌ی MTProto)

فقط کتابخانه‌ی استاندارد. همان دست‌دادنی که Telethon با پروکسی می‌کند: greeting
بدونِ احراز، بعد CONNECT — پس موفقیتش یعنی کلِ زنجیره‌ی SOCKS→SSH→تلگرام کار
می‌کند.
"""
from __future__ import annotations

import asyncio
import time

#: مقصدِ پیش‌فرضِ سنجش: یکی از دیتاسنترهای تلگرام. IP است نه دامنه، تا زمانِ DNS
#: در عدد نیفتد و «تأخیرِ تونل» واقعاً تأخیرِ تونل باشد.
DEFAULT_TARGET = ("149.154.167.50", 443)


def _is_ip(s: str) -> bool:
    parts = s.split(".")
    return len(parts) == 4 and all(p.isdigit() for p in parts)


async def socks5_connect(local_port: int, dst_host: str, dst_port: int,
                         timeout: float = 12.0, local_host: str = "127.0.0.1"):
    """از طریقِ SOCKS5 محلی به مقصد CONNECT می‌زند. `(ok, پیام)` را می‌دهد."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(local_host, local_port), timeout)
    except Exception as exc:  # noqa: BLE001
        return False, f"به پورتِ محلیِ SOCKS وصل نشد: {exc!r}"
    try:
        writer.write(b"\x05\x01\x00")          # VER=5، ۱ روش، 0x00=بدونِ احراز
        await writer.drain()
        resp = await asyncio.wait_for(reader.readexactly(2), timeout)
        if resp != b"\x05\x00":
            return False, f"greetingِ SOCKS رد شد: {resp!r}"
        host = dst_host.encode() if _is_ip(dst_host) else dst_host.encode("idna")
        writer.write(b"\x05\x01\x00\x03" + bytes([len(host)]) + host
                     + int(dst_port).to_bytes(2, "big"))
        await writer.drain()
        rep = await asyncio.wait_for(reader.readexactly(4), timeout)
        if rep[1] != 0x00:
            return False, f"CONNECT رد شد (کد {rep[1]})"
        # آدرسِ bound را بخوان و دور بریز تا کانال تمیز بسته شود.
        atyp = rep[3]
        if atyp == 1:
            await asyncio.wait_for(reader.readexactly(4), timeout)
        elif atyp == 3:
            ln = await asyncio.wait_for(reader.readexactly(1), timeout)
            await asyncio.wait_for(reader.readexactly(ln[0]), timeout)
        elif atyp == 4:
            await asyncio.wait_for(reader.readexactly(16), timeout)
        await asyncio.wait_for(reader.readexactly(2), timeout)
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, f"{exc!r}"
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


async def tunnel_ping_ms(local_port: int, timeout: float = 12.0,
                         target=DEFAULT_TARGET):
    """تأخیرِ تونل به میلی‌ثانیه، یا None اگر نشد.

    این عدد است که نگهبان برای «کند شده؟» استفاده می‌کند.
    """
    t0 = time.monotonic()
    ok, _msg = await socks5_connect(local_port, target[0], target[1], timeout)
    if not ok:
        return None
    return int((time.monotonic() - t0) * 1000)
