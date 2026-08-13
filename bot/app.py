"""
bot/app.py — قطعات مشترک هر دو ربات.
====================================

این فایل عمداً کوچک است و بیشترش **عیناً از پروژه‌ی اصلی کپی شده**. هر دو ربات
(`owner_bot.py` و `customer_bot.py`) از اینجا استفاده می‌کنند:

* `LiveCard` — یک پیام تلگرام که در جا ویرایش می‌شود تا جاب تمام شود. بیت‌به‌بیت
  از پروژه‌ی اصلی کپی شده، چون تستِ خودش (`bot/tests/test_live_card.py`) این کلاس
  را با regex از همین فایل می‌کشد و اجرا می‌کند. دست‌نخورده ماند تا آن تست معتبر
  بماند.
* `account_name_for_phone` — نرمال‌سازی شماره‌ی ایرانی، عیناً کپی‌شده. توجه: اینجا
  این تابع **کلید اکانت نیست**، فقط شماره را یکدست می‌کند. کلید اکانت در این
  سرویس `accounts.id` است (docstring فایل `db.py`).
* `make_report` / `make_send_document` — کارخانه‌هایی که خروجی موتور را به یک چت
  مشخص می‌بندند. در پروژه‌ی اصلی همیشه به چتِ مالک می‌رفت؛ در سرویس چند-مشتری هر
  جاب باید به چتِ صاحبش برود، پس چت پارامتر شد.
* `delete_account_profile` — کپی‌شده از `delete_account_files` اصلی.
* `sweep_pending_profiles` — جاروی بوت برای پروفایل‌های نیمه‌ساخته‌ی لاگین.
"""
from __future__ import annotations

import asyncio
import re
import shutil
import socket
import time

from bot import blocked_store, contacts_store, progress_store
from config import config


# --------------------------------------------------------------------------- #
# کپی عینی از پروژه‌ی اصلی
# --------------------------------------------------------------------------- #
def account_name_for_phone(phone: str) -> str:
    """شماره‌ی نرمال‌شده: فقط رقم‌ها.

    شماره‌های ایرانی به `98XXXXXXXXXX` نرمال می‌شوند، پس `0930…`، `930…` و
    `+98930…` همه به یک شماره‌ی واحد نگاشت می‌شوند — که همان چیزی است که یکتاییِ
    `(customer_id, phone)` در دیتابیس رویش حساب می‌کند.
    """
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("0098"):
        digits = digits[4:]
    elif digits.startswith("98"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits.startswith("9"):
        return "98" + digits
    # موبایل ایرانی نیست: رقم‌ها را همان‌طور که آمده نگه دار، نه اینکه خرابش کنی.
    return re.sub(r"\D", "", phone or "")


def _ping_blocking() -> int | None:
    """زمان رفت‌وبرگشت (میلی‌ثانیه) برای باز کردن یک TCP به هاست ایتا."""
    try:
        t = time.monotonic()
        with socket.create_connection((config.PING_HOST, 443), timeout=4):
            pass
        return int((time.monotonic() - t) * 1000)
    except Exception:  # noqa: BLE001
        return None


async def server_ping_ms() -> int | None:
    try:
        return await asyncio.to_thread(_ping_blocking)
    except Exception:  # noqa: BLE001
        return None


def delete_account_profile(account: str) -> list:
    """پروفایل مرورگر و فایل‌های یک اکانت را پاک می‌کند.

    برچسب‌های آدمیزادِ آنچه واقعاً پاک شد را برمی‌گرداند، برای کارت لاگ.
    `account` اینجا کلید اکانت (شناسه‌ی ردیف) است، نه شماره.
    """
    removed = []
    profile = config.profile_dir(account)
    try:
        if profile.is_dir():
            shutil.rmtree(profile)
            removed.append("پروفایل مرورگر")
    except OSError as exc:
        print(f"[account] پروفایل {profile} پاک نشد: {exc}", flush=True)

    if contacts_store.forget(account):
        removed.append("مخاطبین ذخیره‌شده")
    if progress_store.clear(account):
        removed.append("لاگ ارسال")
    if blocked_store.forget(account):
        removed.append("فهرست ردشده‌ها")

    try:
        from contacts_boost import numbers as boost_numbers
        if boost_numbers.forget(account):
            removed.append("سابقه‌ی boost")
    except Exception:  # noqa: BLE001 - contacts_boost/ اختیاری است
        pass

    try:
        from direct import peers as peer_store
        if peer_store.forget(account):
            removed.append("peers ذخیره‌شده")
    except Exception:  # noqa: BLE001 - direct/ ممکن است عمداً حذف شده باشد
        pass

    sessions = config.ARTIFACTS_DIR / "sessions"
    if sessions.is_dir():
        patterns = (f"capall_{account}_*.json", f"worker_tx_{account}_*.json",
                    f"cookies_{account}.json", f"{account}_*.json")
        hit = False
        for pattern in patterns:
            for path in sessions.glob(pattern):
                try:
                    path.unlink()
                    hit = True
                except OSError:
                    pass
        if hit:
            removed.append("سشن کپچرشده")
    return removed


def sweep_pending_profiles() -> int:
    """پروفایل‌های `_pending_*` مانده از یک کرش را پاک می‌کند.

    لاگین با نام موقت شروع می‌شود و در صورت موفقیت rename می‌شود. اگر پروسه وسط
    کار کشته شود آن پوشه می‌ماند — و چون فضا می‌گیرد و به هیچ ردیفی در دیتابیس وصل
    نیست، هنگام بوت جارو می‌شود.
    """
    n = 0
    try:
        base = config.PROFILES_DIR
        if not base.is_dir():
            return 0
        for p in base.iterdir():
            if p.is_dir() and p.name.startswith("_pending_"):
                try:
                    shutil.rmtree(p)
                    n += 1
                except OSError:
                    pass
    except Exception as exc:  # noqa: BLE001
        print(f"[sweep] {exc}", flush=True)
    return n


# --------------------------------------------------------------------------- #
# کارخانه‌ها: در پروژه‌ی اصلی مقصد همیشه چتِ مالک بود؛ اینجا پارامتر است
# --------------------------------------------------------------------------- #
def make_report(client, chat_id):
    """یک `Report` می‌سازد که به `chat_id` می‌فرستد.

    موتور این را به‌عنوان `report` می‌گیرد و کارت‌های خودش را با آن پست می‌کند.
    هرگز raise نمی‌کند: یک خطای گزارش نباید جاب را بشکند.
    """
    async def _report(text: str) -> None:
        try:
            await client.send_message(int(chat_id), text)
        except Exception:  # noqa: BLE001
            pass
    return _report


def make_send_document(client, chat_id):
    """یک تابع تحویل فایل برای `run_photo_export`.

    خطا عمداً propagate می‌شود تا جاب خودش روی کارت خودش گزارشش کند، نه اینکه
    بی‌صدا شکست بخورد — همان رفتار پروژه‌ی اصلی.
    """
    async def _send(path: str, caption: str = "") -> object:
        return await client.send_file(int(chat_id), path, caption=caption,
                                      force_document=True)
    return _send


class LiveCard:
    """One Telegram message edited in place while a job runs.

    IMPORTANT: `set()` does NOT wait for Telegram. It used to, and that put a
    Telegram round trip (plus any edit rate limiting Telethon sleeps through)
    directly inside the send loop - so a job that should pace itself by Eitaa's
    speed was also paying for its own progress card. Now the newest text is
    stashed and a single background painter delivers it.

    Only the LATEST text matters, so intermediate updates are dropped instead of
    queued; a card that is one tick behind is fine, a send loop that stalls is
    not.
    """

    def __init__(self, chat_id, min_interval: float = 2.0) -> None:
        self.chat_id = chat_id
        self.min_interval = min_interval
        self._msg = None
        self._pending: str | None = None
        self._sent_text: str | None = None
        self._painter: asyncio.Task | None = None
        self._wake = asyncio.Event()
        # Serialises the painter and flush(): without it they raced and the card
        # could end up showing an OLD state ('Sending 50/100' after 'Done').
        self._paint_lock = asyncio.Lock()
        self._closed = False
        self.paints = 0
        self.dropped = 0

    async def set(self, text: str, force: bool = False) -> None:
        """Queue `text` for painting. Returns immediately (never blocks a job)."""
        if self._closed:
            return
        if self._pending is not None:
            self.dropped += 1
        self._pending = text
        self._wake.set()
        if self._painter is None or self._painter.done():
            self._painter = asyncio.create_task(self._paint_loop())
        if force:
            # Give the painter a chance to flush a final/important state without
            # actually waiting on the network.
            await asyncio.sleep(0)

    async def _paint_loop(self) -> None:
        try:
            while not self._closed:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=30)
                except asyncio.TimeoutError:
                    if self._pending is None:
                        return
                self._wake.clear()
                async with self._paint_lock:
                    text, self._pending = self._pending, None
                    if text is None or text == self._sent_text:
                        continue
                    try:
                        if self._msg is None:
                            self._msg = await bot.send_message(self.chat_id, text)
                        else:
                            await self._msg.edit(text)
                        self._sent_text = text
                        self.paints += 1
                    except MessageNotModifiedError:
                        self._sent_text = text
                    except Exception:  # noqa: BLE001 - never break a job
                        pass
                # Rate-limit ourselves so Telegram never has to.
                await asyncio.sleep(self.min_interval)
        except asyncio.CancelledError:
            return

    async def flush(self) -> None:
        """Paint the last queued text now (used when a job ends).

        Takes the same lock as the painter, so the final state can never be
        overwritten by an in-flight older edit.
        """
        async with self._paint_lock:
            text, self._pending = self._pending, None
            if text is None or text == self._sent_text:
                return
            try:
                if self._msg is None:
                    self._msg = await bot.send_message(self.chat_id, text)
                else:
                    await self._msg.edit(text)
                self._sent_text = text
                self.paints += 1
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        self._closed = True
        if self._painter is not None:
            self._painter.cancel()
            self._painter = None



# ---- keyboards ------------------------------------------------------------
# صفحه‌کلیدهای مشترک هر دو ربات. صفحه‌بندی از پروژه‌ی اصلی کپی شده.

#: چند مورد در هر صفحه‌ی فهرست.
PAGE_SIZE = 10


def page_slice(items: list, page: int, size: int = PAGE_SIZE) -> tuple:
    """(موارد این صفحه، شماره‌ی صفحه‌ی محدودشده، تعداد صفحه‌ها)."""
    pages = max(1, (len(items) + size - 1) // size)
    page = max(0, min(int(page or 0), pages - 1))
    start = page * size
    return items[start:start + size], page, pages


def pager_row(prefix: str, page: int, pages: int) -> list:
    """یک سطر ◀ / صفحه / ▶ — فقط وقتی بیش از یک صفحه باشد.

    عیناً منطق پروژه‌ی اصلی: صفحه‌ها حلقه‌ای‌اند، و شمارنده‌ی وسط یک دکمه‌ی `noop`
    است تا فشردنش کاری نکند.
    """
    from telethon import Button
    if pages <= 1:
        return []
    prev_page = (page - 1) % pages
    next_page = (page + 1) % pages
    return [
        Button.inline("◀", f"{prefix}{prev_page}".encode()),
        Button.inline(f"{page + 1}/{pages}", b"noop"),
        Button.inline("▶", f"{prefix}{next_page}".encode()),
    ]
