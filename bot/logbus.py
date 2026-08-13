"""
bot/logbus.py — یک نقطه‌ی خروجی برای همه‌ی لاگ‌ها.
================================================

قاعده‌ی اصلی: **یک رویداد، دو خروجی.**

    emit(...)
       ├─ گروه لاگ  : کارت کامل (با جزئیات فنی)
       └─ پیوی مشتری: نسخه‌ی امن (اگر داشته باشد)

کد پیگیری **یک بار** ساخته می‌شود و در هر دو نسخه یکی است، پس مشتری می‌گوید
«A3F9C1 خوردم» و مالک همان را در گروه سرچ می‌کند و کل داستان را دارد.

چرا همه‌چیز از یک تابع رد می‌شود: اگر این منطق پخش باشد، یک روز یک کارت کامل
اشتباهاً به مشتری می‌رود. اگر یک‌جا باشد، ممکن نیست.

هیچ‌چیز اینجا raise نمی‌کند. یک خطای لاگ هرگز نباید یک جاب را بشکند — به همین دلیل
`bot/runner.py` که عیناً کپی شده هم `configured()` و `event()` را همان‌طور که بود
صدا می‌زند و کار می‌کند.
"""
from __future__ import annotations

import uuid

import db
from bot import cards
from config import config

LINE = cards.DIVIDER

#: کلاینت Telethon هر پروسه‌ای که bind کرده.
_client = None
#: آیدی مالک، برای اینکه کارت‌های سیستمی به پیوی خودش هم برود.
_owner_id: int = 0


def bind(client, owner_id: int = 0) -> None:
    """هر دو ربات این را یک بار در startup صدا می‌زنند."""
    global _client, _owner_id
    _client = client
    _owner_id = int(owner_id or config.OWNER_ID or 0)


def configured() -> bool:
    """گروه لاگ آماده است؟ (`bot/runner.py` کپی‌شده این را صدا می‌زند.)"""
    return bool(_client is not None and config.LOG_GROUP_ID)


def new_trace() -> str:
    """کد پیگیری: ۶ کاراکتر hex، مثل `A3F9C1`."""
    return uuid.uuid4().hex[:6].upper()


def card(title: str, rows: list) -> str:
    """کارت ساده‌ی متنی — همان قالبی که `cards.card` می‌سازد."""
    rows = [r for r in rows if r is not None]
    return f"{title}\n{LINE}\n" + "\n".join(str(r) for r in rows)


def mask_phone(phone) -> str:
    """`98916***736`.

    همه‌جا اعمال می‌شود — پیوی مشتری **و** گروه لاگ. اگر روزی کسی به گروه لاگ
    دسترسی پیدا کند، فهرست کامل شماره‌های ناوگان لو نرود. شماره‌ی کامل فقط در پنل
    مالک دیده می‌شود.
    """
    p = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(p) <= 7:
        return "***"
    return f"{p[:5]}***{p[-3:]}"


# --------------------------------------------------------------------------- #
# ارسال‌های پایه (هیچ‌کدام raise نمی‌کنند)
# --------------------------------------------------------------------------- #
async def to_group(text: str) -> bool:
    if _client is None or not config.LOG_GROUP_ID:
        return False
    try:
        await _client.send_message(config.LOG_GROUP_ID, text)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[logbus group] {exc}", flush=True)
        return False


async def to_group_file(file, caption: str = "") -> bool:
    """خودِ فایل را به گروه لاگ می‌فرستد (برای «فایل عوض شد»)."""
    if _client is None or not config.LOG_GROUP_ID:
        return False
    try:
        await _client.send_file(config.LOG_GROUP_ID, file, caption=caption,
                                force_document=True)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[logbus group file] {exc}", flush=True)
        return False


async def to_pv(user_id: int, text: str, buttons=None) -> bool:
    if _client is None or not user_id:
        return False
    try:
        await _client.send_message(int(user_id), text, buttons=buttons)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[logbus pv] {exc}", flush=True)
        return False


async def event(title: str, rows: list, pv_user: int = None) -> bool:
    """سازگاری با کد کپی‌شده: `bot/runner.py` این را با همین امضا صدا می‌زند."""
    text = card(title, rows)
    ok = await to_group(text)
    if pv_user:
        await to_pv(pv_user, text)
    return ok


# --------------------------------------------------------------------------- #
# خطای امن برای مشتری
# --------------------------------------------------------------------------- #
#: کد داخلی → یک جمله‌ی کوتاه و قابل‌عمل فارسی. مشتری هرگز `repr(exc)` یا نام متد
#: یا پاسخ خام سرور نمی‌بیند.
_SAFE = {
    "flood": "ایتا موقتاً محدودیت گذاشته. کمی بعد امتحان کن.",
    "code": "کد درست نبود یا منقضی شد. دوباره شروع کن و کد تازه را سریع بفرست.",
    "timeout": "کد را به‌موقع نفرستادی، لغو شد. دوباره بزن.",
    "password": "رمز دومرحله‌ای درست نبود.",
    "no_code": "ایتا الان کد نداد. کمی بعد امتحان کن.",
    "not_registered": "این شماره روی ایتا ثبت نیست.",
    "not_logged_in": "این اکانت از ایتا خارج شده. حذفش کن و دوباره اضافه‌ش کن.",
    "no_contacts": "این اکانت مخاطبی ندارد. «بروزرسانی مخاطبین» را بزن.",
    "no_content": "اول از «✍️ محتوا» یک متن یا فایل تنظیم کن.",
    "busy": "یک کار دیگر از تو در جریان است. اول آن تمام یا متوقف شود.",
    "limit": "ایتا برای این اکانت محدودیت گذاشته. چند روز آرامش لازم دارد.",
    "generic": "مشکلی پیش آمد. چند لحظه بعد دوباره امتحان کن.",
}


def safe_reason(err: object, kind: str = "generic") -> str:
    """یک استثنا یا کد را به جمله‌ی امن فارسی نگاشت می‌کند.

    اول متن خطا را برای الگوهای شناخته‌شده می‌خواند، وگرنه به جمله‌ی `kind`
    برمی‌گردد. هرگز خودِ متن خطا را برنمی‌گرداند.
    """
    try:
        s = repr(err).lower()
    except Exception:  # noqa: BLE001
        s = ""
    if any(k in s for k in ("flood", "too_many", "slowmode", "spam")):
        return _SAFE["flood"]
    if any(k in s for k in ("code_invalid", "codeisinvalid", "phone_code",
                            "wrong_code", "invalid_code", "expired")):
        return _SAFE["code"]
    if any(k in s for k in ("password", "2fa")):
        return _SAFE["password"]
    if any(k in s for k in ("not_registered", "phone_number_invalid",
                            "phone_invalid")):
        return _SAFE["not_registered"]
    if any(k in s for k in ("peer_flood", "peer_id_invalid")):
        return _SAFE["limit"]
    if "timeout" in s:
        return _SAFE["timeout"]
    return _SAFE.get(kind, _SAFE["generic"])


def flood_wait_minutes(err: object) -> int | None:
    """اگر خطا FLOOD_WAIT_n بود، n را به دقیقه برمی‌گرداند.

    این عدد **باید** به مشتری گفته شود: اگر نگوییم، پشت‌سرهم تلاش می‌کند،
    محدودیت ایتا را بدتر می‌کند، و آخرش ریت‌لیمیت خودمان مسدودش می‌کند در حالی که
    تقصیر او نبوده.
    """
    import re
    m = re.search(r"FLOOD_WAIT_(\d+)", str(err or "").upper())
    if not m:
        return None
    return max(1, round(int(m.group(1)) / 60))


# --------------------------------------------------------------------------- #
# emit — تنها نقطه‌ای که هر دو نسخه از آن بیرون می‌آید
# --------------------------------------------------------------------------- #
async def emit(*, kind: str, title: str, rows: list,
               customer_id: int | None = None,
               customer_label: str = "",
               phone: object = None,
               trace: str = "",
               safe_title: str = "",
               safe_rows: list | None = None,
               safe_footer: str = "",
               buttons=None,
               counted: bool = True,
               log_label: str = "",
               summary: str = "") -> str:
    """یک رویداد را ثبت و پخش می‌کند. کد پیگیری را برمی‌گرداند.

    * `title` / `rows`  → کارت کامل، **فقط** گروه لاگ.
    * `safe_title` / `safe_rows` → نسخه‌ی مشتری. اگر ندهی، مشتری چیزی نمی‌گیرد
      (مثل کارت مسدودی یا کارت‌های داخلی).
    * `counted` → آیا در شمارنده‌ی ریت‌لیمیت حساب شود. ناوبری `False` می‌دهد.

    ردیف رویداد **همیشه** در دیتابیس نوشته می‌شود، حتی اگر گروه لاگ تنظیم نشده
    باشد، چون همان ردیف است که شمارنده‌ی مسدودی و تاریخچه‌ی مشتری را می‌سازد.
    """
    trace = trace or new_trace()

    # ۱) ردیف رویداد — منبع حقیقتِ شمارنده، سهمیه و تاریخچه.
    if customer_id:
        try:
            db.log_event(int(customer_id), kind,
                         label=log_label or title, summary=summary,
                         trace_id=trace, counted=counted)
        except Exception as exc:  # noqa: BLE001
            print(f"[logbus event] {exc}", flush=True)

    # ۲) کارت کامل به گروه لاگ.
    head = []
    if customer_id:
        who = f"{customer_label} · {customer_id}" if customer_label else str(customer_id)
        head.append(f"• مشتری: {who}")
    if phone is not None:
        head.append(f"• شماره: {mask_phone(phone)}")
    body = head + [r for r in (rows or []) if r is not None]
    body.append(LINE)
    body.append(f"▪ 🔖 {trace} · 🕒 {cards.now_hms()[11:]}")
    await to_group(f"{title}\n{LINE}\n" + "\n".join(str(r) for r in body))

    # ۳) نسخه‌ی امن به پیوی مشتری — فقط اگر تعریف شده باشد.
    if customer_id and safe_title:
        safe = [r for r in (safe_rows or []) if r is not None]
        safe.append(LINE)
        safe.append(safe_footer or f"▪ 🔖 {trace}")
        await to_pv(int(customer_id),
                    f"{safe_title}\n{LINE}\n" + "\n".join(str(r) for r in safe),
                    buttons=buttons)

    return trace


async def emit_error(*, where: str, customer_id: int | None = None,
                     customer_label: str = "", phone: object = None,
                     err: object = None, code: str = "", detail: str = "",
                     phase: str = "", engine: str = "", scope: str = "",
                     safe_kind: str = "generic", trace: str = "",
                     extra_rows: list | None = None,
                     tell_customer: bool = True) -> str:
    """یک خطا: کارت کامل به گروه، یک جمله‌ی امن به مشتری.

    این همان تفکیکی است که «کارت خطا به مشتری نمی‌رود، ولی خبرِ خطا می‌رود».
    """
    trace = trace or new_trace()
    reason = safe_reason(err if err is not None else (code or detail), safe_kind)
    mins = flood_wait_minutes(err if err is not None else detail)
    if mins:
        reason = f"ایتا موقتاً محدودیت گذاشته — {mins} دقیقه دیگر امتحان کن."

    rows = [
        f"• کجا: {where}" + (f" · مرحله: {phase}" if phase else ""),
        f"• موتور: {engine}" if engine else None,
        f"• دامنه: {scope}" if scope else None,
        f"• کد: {code}" if code else None,
        f"• جزئیات: {cards.sanitize(detail or repr(err), 400)}"
        if (detail or err is not None) else None,
    ]
    if extra_rows:
        rows.extend(extra_rows)
    hint = cards.code_hint(code)
    if hint:
        rows.append(f"• اقدام: {hint}")

    return await emit(
        kind="error", title="⚠️ خطا", rows=rows,
        customer_id=customer_id, customer_label=customer_label, phone=phone,
        trace=trace, log_label=f"خطا · {where}", summary=str(code or "")[:200],
        counted=False,
        safe_title="⚠️ مشکلی پیش آمد" if tell_customer else "",
        safe_rows=[f"• {reason}"] if tell_customer else None,
        safe_footer=f"▪ 🔖 {trace}",
    )
