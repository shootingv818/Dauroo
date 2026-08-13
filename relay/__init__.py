"""
relay/ — تونلِ SSH به تلگرام برای هاستِ ایران، با failover هوشمند.
==================================================================

طرحِ کلی (چرا و چگونه):

* هاست در ایران است؛ تلگرام فیلتر است ولی خروجیِ عمومیِ سرور باز است. یک یا چند
  VPSِ خارجی نقشِ relay دارند. ربات با SSH به relay وصل می‌شود و روی
  `127.0.0.1` یک شنونده‌ی SOCKS5 باز می‌کند که از داخلِ همان SSH به بیرون تونل
  می‌زند. **فقط** کلاینتِ Telethon به این پورت اشاره می‌کند → تنها تلگرام تونل
  می‌شود.

* **مسئله‌ی bootstrap:** تا وقتی relayی بالا نیست، ربات به تلگرام وصل نمی‌شود و
  نمی‌تواند دستورِ «relay اضافه کن» را از تلگرام بگیرد. پس **اولین** relay از
  `.env` خوانده می‌شود (مستقل از تلگرام)، و relayهای بعدی از پنلِ مالک اضافه
  می‌شوند. `ensure_bootstrap()` همین کار را در startup می‌کند.

* هر پروسه‌ی ربات (مالک و مشتری) تونلِ خودش را دارد، روی پورت‌های محلیِ متفاوت
  (`relay_local_port`)، تا هیچ‌کدام برای خروجی به دیگری وابسته نباشد.

اجزا:
    crypto.py   — رمزنگاریِ رمز عبور در حالت سکون (بدون وابستگی).
    manager.py  — RelayManager: اتصالِ asyncssh، SOCKS5، TOFU، نگهبان، failover.
    db (جدولِ relays) — مدلِ داده و CRUD.
    owner_bot   — پنلِ مدیریت (افزودن/حذف/سویچ/وضعیت) — فقط مالک.
"""
from __future__ import annotations

import db
from config import config
from relay import crypto
from relay.manager import RelayManager, asyncssh_available, manager

__all__ = ["manager", "RelayManager", "telethon_proxy", "ensure_bootstrap",
           "make_event_sink", "asyncssh_available", "crypto"]


def telethon_proxy():
    """پروکسیِ Telethon برای این پروسه، یا None اگر relay خاموش است.

    قالبِ `('socks5', host, port)` را Telethon (از طریقِ python-socks) می‌فهمد.
    شنونده روی `127.0.0.1` است، پس فقط همین پروسه از آن استفاده می‌کند — نه یک
    پروکسیِ سیستمی. اگر relay خاموش باشد None برمی‌گردد و ربات مستقیم وصل می‌شود
    (همان رفتارِ قبلی، برای محیطِ توسعه یا سروری که فیلتر نیست).
    """
    if not config.RELAY_ENABLED:
        return None
    return ("socks5", "127.0.0.1", config.relay_local_port())


def ensure_bootstrap() -> int | None:
    """relayِ اولیه را از `.env` وارد می‌کند (اگر لازم باشد). id را برمی‌گرداند.

    * اگر relay خاموش است یا `RELAY_HOST` ست نشده → کاری نمی‌کند.
    * رمز عبور **رمزنگاری‌شده** ذخیره می‌شود؛ اگر رمز داده شده ولی کلیدِ
      رمزنگاری (`RELAY_SECRET_KEY`/`RAW_ENCRYPTION_KEY`) نیست، relay را **بدون**
      رمز ذخیره نمی‌کند و None برمی‌گرداند (هرگز plaintext).
    * upsert روی (host, port): ویرایشِ `.env` رمز/کاربر را همگام می‌کند.
    """
    if not config.RELAY_ENABLED or not config.RELAY_HOST:
        return None
    secret_enc = ""
    if config.RELAY_PASSWORD:
        key = config.relay_secret_key()
        if not crypto.is_key_set(key):
            print("[relay] RELAY_PASSWORD داده شده ولی کلیدِ رمزنگاری ست نیست — "
                  "relay ذخیره نشد.", flush=True)
            return None
        secret_enc = crypto.encrypt(config.RELAY_PASSWORD, key)
    return db.add_relay(
        host=config.RELAY_HOST, ssh_port=config.RELAY_SSH_PORT,
        username=config.RELAY_USER, secret_enc=secret_enc,
        priority=0, source="bootstrap")


def make_event_sink(logbus):
    """یک sink می‌سازد که رویدادهای مدیر را به گروهِ لاگ می‌فرستد.

    مدیر به logbus وابسته نیست (تا تست‌پذیر بماند)؛ این تابع پل است. کارت‌های
    relay رویدادِ سیستمیِ مالک‌اند: `customer_id=None`، شمرده‌نشده، فقط گروهِ لاگ.
    شماره یا رمزی اینجا نیست، پس چیزی برای مخفی‌کردن به پیوی مشتری نمی‌رود.
    """
    _TITLES = {
        "relay_up": "🟢 تونل relay برقرار شد",
        "relay_down": "🔴 relay از کار افتاد",
        "relay_error": "⚠️ خطای relay",
        "relay_slow": "🐢 relay کند شد",
        "relay_none": "⛔ هیچ relayی در دسترس نیست",
        "relay_tofu": "🔑 کلیدِ میزبانِ relay ذخیره شد (TOFU)",
        "relay_hostkey": "⛔ کلیدِ میزبانِ relay عوض شد",
        "relay_retry": "🔄 تلاشِ مجددِ relay",
    }

    async def sink(ev: dict) -> None:
        kind = ev.get("kind", "relay")
        relay = ev.get("relay") or {}
        rows = []
        if relay.get("host"):
            rows.append(f"• relay: {relay.get('host')}:{relay.get('ssh_port', 22)}"
                        f" (#{relay.get('id')})")
        if ev.get("reason"):
            rows.append(f"• {ev['reason']}")
        if ev.get("detail"):
            rows.append(f"• جزئیات: {ev['detail']}")
        try:
            await logbus.emit(
                kind=kind, title=_TITLES.get(kind, "🛰 relay"),
                rows=rows or ["—"], customer_id=None,
                log_label="relay", counted=False)
        except Exception:  # noqa: BLE001
            pass

    return sink
