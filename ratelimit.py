"""
ratelimit.py — ضد-اسپم، با مسدودی خودکار.
=========================================

بیش از `RATE_LIMIT_MAX` اکشن **شمرده‌شده** در `RATE_LIMIT_WINDOW` ثانیه ⇒ مسدودیِ
**دائمی** تا وقتی مالک دستی آزاد کند.

سه تصمیم طراحی که ارزش خواندن دارند:

۱. **کش مسدودها در حافظه.** اولین چک هر پیام «مسدود است؟» است. اگر آن یک کوئری
   SQLite باشد، زیر سیلِ واقعی هر پیامِ اسپمر یک خواندن دیسک است — یعنی دقیقاً
   همان باری که مسدودی باید حذفش کند. کش هنگام بوت پر می‌شود و روی هر
   بلاک/آنبلاک آپدیت می‌شود، پس هزینه‌ی یک کاربر مسدود **صفر** است.

۲. **پنجره‌ی لغزان، نه ثابت.** پنجره‌ی ثابت اجازه می‌دهد یک انفجار روی مرزِ پنجره
   دو برابر سهمیه بگیرد — با ۲۰/۶۰ یعنی عملاً ۴۰ اکشن. شمارش از جدول `events`
   می‌آید که زمان دقیق هر اکشن را دارد، پس پنجره واقعاً لغزان است.

۳. **شمارنده و لاگ یک فهرست‌اند.** همان ردیف‌های `events` که در گروه لاگ دیده
   می‌شوند، شمارنده را هم می‌سازند. پس وقتی کسی مسدود می‌شود، آن ۲۰ اکشن **در
   لاگ پیدا می‌شوند** — و کارت مسدودی می‌تواند فهرستشان کند. اگر این دو فهرست جدا
   بودند، مسدودی قابل توضیح نبود.

تلاش‌های کاربران مسدود هم **در حافظه** شمرده می‌شوند و یک بار در روز خلاصه
می‌شوند، نه یک پیام به‌ازای هر تلاش.
"""
from __future__ import annotations

import time

import db
from config import config

#: آیدی‌های مسدود، در حافظه. هنگام بوت پر می‌شود.
_blocked: set[int] = set()
_loaded = False

#: تلاش کاربران مسدود: uid -> [تعداد، آخرین زمان]. هرگز در دیتابیس نوشته نمی‌شود.
_attempts: dict[int, list] = {}

#: چه کسانی هشدار ۷۵٪ گرفته‌اند، تا در یک پنجره دوباره هشدار نرود.
_warned: dict[int, float] = {}


def load() -> None:
    """کش را از دیتابیس پر می‌کند. هر دو ربات این را در startup صدا می‌زنند."""
    global _loaded
    try:
        _blocked.clear()
        _blocked.update(db.all_blocked_ids())
        _loaded = True
    except Exception as exc:  # noqa: BLE001
        print(f"[ratelimit load] {exc}", flush=True)


def is_blocked(uid: int) -> bool:
    """چک بی‌هزینه. اگر کش پر نشده باشد، یک بار fallback به دیتابیس می‌زند."""
    uid = int(uid)
    if uid in _blocked:
        return True
    if not _loaded:
        try:
            if db.is_blocked(uid):
                _blocked.add(uid)
                return True
        except Exception:  # noqa: BLE001
            return False
    return False


def note_blocked_attempt(uid: int) -> None:
    """یک تلاش از کاربر مسدود را **فقط در حافظه** بشمار.

    نه کوئری، نه پیام. خلاصه‌ی روزانه از همین دیکشنری ساخته می‌شود.
    """
    rec = _attempts.setdefault(int(uid), [0, 0.0])
    rec[0] += 1
    rec[1] = time.time()


def blocked_attempts_snapshot(reset: bool = True) -> list:
    """[(uid, تعداد، آخرین زمان)] برای کارت خلاصه‌ی روزانه."""
    out = [(uid, rec[0], rec[1]) for uid, rec in _attempts.items() if rec[0]]
    out.sort(key=lambda t: t[1], reverse=True)
    if reset:
        _attempts.clear()
    return out


def block(uid: int, reason: str = "") -> None:
    uid = int(uid)
    _blocked.add(uid)
    try:
        db.set_blocked(uid, True, reason)
    except Exception as exc:  # noqa: BLE001
        print(f"[ratelimit block] {exc}", flush=True)


def unblock(uid: int) -> None:
    uid = int(uid)
    _blocked.discard(uid)
    _attempts.pop(uid, None)
    _warned.pop(uid, None)
    try:
        db.set_blocked(uid, False)
    except Exception as exc:  # noqa: BLE001
        print(f"[ratelimit unblock] {exc}", flush=True)


def count(uid: int) -> int:
    """اکشن‌های شمرده‌شده در پنجره‌ی لغزان."""
    try:
        return db.rate_count(int(uid), config.RATE_LIMIT_WINDOW)
    except Exception:  # noqa: BLE001
        return 0


def check(uid: int) -> dict:
    """بعد از ثبت یک اکشن صدا زده می‌شود.

    برمی‌گرداند: {"ok": bool, "count": int, "warn": bool}
      ok=False  ⇒ از سقف گذشت و همین‌جا مسدود شد.
      warn=True ⇒ به آستانه‌ی هشدار رسید (پیش‌فرض ۷۵٪) و قبلاً هشدار نگرفته.
    """
    n = count(uid)
    limit = max(1, int(config.RATE_LIMIT_MAX))
    if n > limit:
        block(uid, f"{n} اکشن در {config.RATE_LIMIT_WINDOW} ثانیه")
        return {"ok": False, "count": n, "warn": False}

    warn = False
    threshold = limit * float(config.RATE_LIMIT_WARN_AT or 0.75)
    if n >= threshold:
        last = _warned.get(int(uid), 0.0)
        if time.time() - last > config.RATE_LIMIT_WINDOW:
            _warned[int(uid)] = time.time()
            warn = True
    return {"ok": True, "count": n, "warn": warn}


def offending_actions(uid: int) -> list:
    """اکشن‌هایی که مسدودی را ساختند، برای فهرست‌کردن در کارت مسدودی.

    این فهرست است که تصمیم را می‌سازد: اگر همه یک نوع باشند یعنی کسی روی یک دکمه
    می‌کوبد؛ اگر متنوع باشند ممکن است باگ UI باشد. و چون مسدودی دائمی است، این
    قضاوت مهم است — یک مشتری واقعی با ۲۴ اکانت نباید به‌خاطر دو تپ اشتباهی از دست
    برود.
    """
    try:
        return db.recent_actions(int(uid), config.BLOCK_CARD_ACTIONS,
                                 config.RATE_LIMIT_WINDOW)
    except Exception:  # noqa: BLE001
        return []
