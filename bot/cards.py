"""کارت‌های فارسی پنل و گروه لاگ.

این فایل ترجمه‌ی فارسیِ کارت‌های پروژه‌ی اصلی است. **امضای هیچ تابعی عوض نشده** و
منطق محاسباتی همان است، چون موتور (`bot/runner.py`، `photo_export/`،
`contacts_boost/`، `session_check/`) عیناً کپی شده و این توابع را با همین نام‌ها و
پارامترها صدا می‌زند. تنها چیزی که تغییر کرده، متنِ دیده‌شدنی است.

قالب همه‌ی کارت‌ها یکی است:

    <اموجی> <عنوان>
    -------------------------------
    • کلید: مقدار
    • کلید: مقدار
    -------------------------------
    [پانویس]

هر مقداری که از ایتا می‌آید (پیام خطا، نام مخاطب) باید از `sanitize()` بگذرد تا نه
چیزی لو برود و نه قالب بشکند.
"""

from __future__ import annotations

import re
import time
from typing import Iterable

DIVIDER = "-------------------------------"

# اگر رمز یا توکنی اتفاقی داخل متن خطا آمد، پنهانش کن.
_SECRET_RE = re.compile(
    r"(?i)(token|api[_-]?hash|api[_-]?id|password|passwd|secret|authorization|bearer|session)"
    r"\s*[:=]\s*\S+"
)


def sanitize(text: str, limit: int = 300) -> str:
    """یک رشته‌ی دلخواه را برای نشستن در یک سطر کارت بی‌خطر می‌کند."""
    if text is None:
        return ""
    s = str(text)
    s = _SECRET_RE.sub(r"\1: <حذف‌شده>", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return s


def _rows(pairs: Iterable[tuple[str, object]]) -> list[str]:
    """جفت‌های کلید/مقدار را به شکل `• کلید: مقدار` درمی‌آورد.

    کلیدها با فاصله تراز **نمی‌شوند**: فونت تلگرام monospace نیست، پس فاصله‌های
    اضافی هیچ‌چیز را تراز نمی‌کنند و روی موبایل فقط سطرهای ناهموار با شکاف‌های
    تصادفی می‌سازند. صداکننده‌ها همچنان می‌توانند برچسبِ فاصله‌دار بدهند (خیلی‌هایشان
    می‌دهند)؛ فاصله‌ها همین‌جا حذف می‌شوند تا همه‌ی کارت‌ها یکدست دربیایند بدون
    اینکه لازم باشد هر نقطه‌ی صدازدن عوض شود.
    """
    out = []
    for k, v in pairs:
        if v is None:
            continue
        out.append(f"• {str(k).strip()}: {v}")
    return out


def card(title: str, pairs: Iterable[tuple[str, object]] | None = None,
         footer: str | None = None, body: str | None = None) -> str:
    """یک کارت می‌سازد. `pairs` سطرهای کلید/مقدار است؛ `body`/`footer` متن آزاد."""
    lines = [title, DIVIDER]
    if body:
        # سطر‌به‌سطر پاک‌سازی می‌شود: `sanitize()` همه‌ی فاصله‌ها را جمع می‌کند و
        # اگر یک‌جا اعمال شود، یک بدنه‌ی چندخطی (مثل صف شماره‌دار) را به یک سطر
        # می‌چسباند.
        lines.extend(sanitize(ln, 300) for ln in str(body).splitlines())
    rows = _rows(pairs or [])
    if rows:
        lines.extend(rows)
    lines.append(DIVIDER)
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def now_hms() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _pct(done: int, total: int) -> str:
    if not total:
        return "۰٪"
    return f"{int(done * 100 / total)}٪"


def bar(done: int, total: int, width: int = 12) -> str:
    """نوار پیشرفت متنی: `▰▰▰▰▱▱▱▱▱▱▱▱ 33%`.

    به کارت‌های زنده چیزی می‌دهد که با هر ویرایش واقعاً حرکت کند — که کل هدفِ
    کارتی است که در جا ویرایش می‌شود.
    """
    if total <= 0:
        return "▱" * width + "  ۰٪"
    ratio = min(1.0, max(0.0, done / total))
    filled = int(round(ratio * width))
    # تا واقعاً تمام نشده، نوار پر نشان داده نمی‌شود.
    if filled == width and done < total:
        filled = width - 1
    return "▰" * filled + "▱" * (width - filled) + f"  {int(ratio * 100)}٪"


def eta(done: int, total: int, elapsed: float) -> str:
    """تخمین زمان باقی‌مانده از میانگین سرعتِ تا اینجا."""
    if done <= 0 or total <= done or elapsed <= 0:
        return "—"
    return fmt_duration((elapsed / done) * (total - done))


# ---- تشخیص‌های مشترک کارت‌هایی که یک اجرا را می‌بندند یا قطع می‌کنند ----
#
# این‌ها هستند چون کارت‌ها قبلاً جواب «چند تا رفت» را می‌دادند ولی جواب سؤالی که
# واقعاً وقتی اجرا غیرمنتظره تمام می‌شود می‌پرسی را نه: آیا حلقه به **آخر فهرست
# رسید**، یا وسط راه تسلیم شد؟ همه‌ی محاسبات پایین از عددهایی است که کارت از قبل
# می‌گیرد، پس هیچ صداکننده‌ای لازم نیست عوض شود.

def _n(v: object) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def coverage(sent: object, failed: object, total: object) -> tuple[int, int]:
    """(تلاش‌شده، هرگز امتحان‌نشده) برای یک اجرا. `skipped` عمداً بیرون است:
    ردشده‌ها **قبل** از حلقه فیلتر شده‌اند، پس جزو چیزی که حلقه باید می‌پیمود
    نیستند."""
    attempted = _n(sent) + _n(failed)
    return attempted, max(0, _n(total) - attempted)


def pace(attempted: object, elapsed: object) -> str | None:
    """`۱.۹ ثانیه هر کدام · ۳۲ در دقیقه` — عددی که برای قضاوت «کند بود یا فقط
    طولانی» لازم است."""
    a, el = _n(attempted), float(elapsed or 0)
    if a <= 0 or el <= 0:
        return None
    return f"{el / a:.1f} ثانیه هر کدام · {a / el * 60:.0f} در دقیقه"


def debug_line(**kv: object) -> str:
    """یک خط فشرده‌ی کپی‌پیست‌شدنی.

    کلیدها عمداً انگلیسی می‌مانند: این سطر برای نقل‌کردن در گزارش خطاست، نه برای
    خواندن مشتری. وقتی چیزی خراب می‌شود، کل وضعیت اجرا در یک خط نقل می‌شود بدون
    اسکرین‌شات گرفتن از پنج کارت.
    """
    parts = [f"{k}={v}" for k, v in kv.items() if v is not None and v != ""]
    return "🔍 " + " ".join(parts)


# رشته‌های محدودیت سرور، طبقه‌بندی‌شده. **دامنه** آن چیزی است که اهمیت دارد:
# امتناعِ per-recipient یعنی «این نفر را رد کن»، و امتناعِ کل-اکانت یعنی «این
# اکانت فعلاً تمام است» — و این دو نباید یکسان رفتار شوند.
def limit_kind(reason: object) -> dict:
    up = str(reason or "").upper()
    m = re.search(r"FLOOD_WAIT_(\d+)", up)
    if m:
        secs = int(m.group(1))
        return {"key": "flood_wait", "scope": "انتظار زمان‌دار", "wait": secs,
                "label": f"انتظار زمان‌دار، {secs} ثانیه",
                "note": (f"یک انتظار زمان‌دار: سرور {secs} ثانیه خواسته و خودش رفع "
                         f"می‌شود. این امتناع دائمی **نیست** و مخاطب به فهرست "
                         f"ردشده‌ها اضافه نمی‌شود.")}
    if "ALL_PEER_FLOOD" in up:
        return {"key": "all_peer_flood", "scope": "کل اکانت", "wait": None,
                "label": "کل-اکانت",
                "note": ("ALL_PEER_FLOOD مربوط به یک مخاطب نیست، مربوط به **کل "
                         "اکانت** است: ایتا این اکانت را از پیام‌دادن به کسانی که "
                         "با آن‌ها ارتباط دوطرفه ندارد منع کرده. هر مخاطبی که از "
                         "این به بعد امتحان شود به همین دلیل رد می‌شود، پس فهرست "
                         "ردشده‌ها با آدم‌هایی پر می‌شود که هیچ تقصیری نداشتند. با "
                         "چند روز آرامش سبک می‌شود و سریع‌تر از همه به اکانت‌های "
                         "تازه می‌خورد.")}
    if "PEER_FLOOD" in up:
        return {"key": "peer_flood", "scope": "همین مخاطب", "wait": None,
                "label": "per-recipient",
                "note": ("PEER_FLOOD انتظار زمان‌دار نیست: ایتا پیام‌های این اکانت "
                         "به کسانی که با آن‌ها ارتباط دوطرفه ندارد را رد می‌کند. "
                         "صبر کردن رفعش نمی‌کند — معمولاً چند روز آرامش لازم است، و "
                         "سریع‌تر از همه به اکانت‌های تازه می‌خورد.")}
    if "SLOWMODE" in up:
        return {"key": "slowmode", "scope": "همان چت", "wait": None,
                "label": "حالت آرام",
                "note": ("روی آن چت حالت آرام تنظیم شده: سقف می‌گذارد که هر کسی هر "
                         "چند وقت بتواند پیام بدهد. محدودیت اکانت نیست.")}
    if "SPAM" in up:
        return {"key": "spam", "scope": "کل اکانت", "wait": None,
                "label": "هشدار اسپم",
                "note": ("هشدار اسپم به **اکانت** زده شده. ادامه دادنِ ارسال در "
                         "حالی که این هشدار برجاست، همان چیزی است که تبدیلش می‌کند "
                         "به بلاک طولانی‌تر.")}
    if "TOO_MANY" in up:
        return {"key": "too_many", "scope": "کل اکانت", "wait": None,
                "label": "درخواست بیش از حد",
                "note": "درخواست بیش از حد از این اکانت. سرعت اجرا را کم کن."}
    if "FLOOD" in up:
        return {"key": "flood", "scope": "کل اکانت", "wait": None,
                "label": "flood",
                "note": "محدودیت flood بدون زمان مشخص؛ مثل کل-اکانت رفتار کن."}
    return {"key": "unknown", "scope": "نامعلوم", "wait": None,
            "label": "طبقه‌بندی‌نشده",
            "note": ("این کد را پنل نمی‌شناسد. چون متنش با الگوی محدودیت خواند، "
                     "به‌عنوان محدودیت رفتار شد — اگر به نظرت اشتباه است این کارت "
                     "را نقل کن.")}


# کد خطا -> اولین کاری که ارزش انجام دادن دارد
_CODE_HINTS = {
    "not_logged_in": "🔎 چک سشن را بزن، بعد از ➕ افزودن اکانت دوباره لاگین کن.",
    "no_recipients": "هدفی نماند: «ردشده» و «امتناع‌شده» بالا را ببین، بعد "
                     "🧲 ساخت مخاطب یا 🧯 ریست فهرست امتناع‌شده‌ها.",
    "peer_id_invalid": "peer ذخیره‌شده کهنه است. 🔄 بروزرسانی مخاطبین را بزن تا "
                       "دوباره حل شود.",
    "ui_timeout": "صفحه به‌موقع جواب نداد — تقریباً همیشه **سرور** است "
                  "(CPU steal / swap)، نه ایتا.",
    "locate_failed": "آپلود دوباره در صفحه پیدا نشد؛ وضعیت فایل بازسازی شد و از "
                     "دست رفت.",
    "timeouterror": "چیزی از بودجه‌ی زمانی‌اش گذشت. با کارت ⏱ زمان‌بندی اجرا "
                    "مقایسه کن.",
    "modulenotfounderror": "کد روی این سرور نیست — استقرار ناقص است یا برنچ اشتباه.",
}


def code_hint(code: object) -> str | None:
    key = str(code or "").strip().lower()
    if not key:
        return None
    if key in _CODE_HINTS:
        return _CODE_HINTS[key]
    for k, v in _CODE_HINTS.items():
        if k in key:
            return v
    return None


def _live(title: str, phone: str, pairs: Iterable[tuple[str, object]],
          ts: str | None = None) -> str:
    """پوسته‌ی کارت زنده: عنوان، خط، 📱 شماره، سطرها، 🕒 زمان.
    این همان کارتی است که **در جا ویرایش می‌شود** تا جاب تمام شود."""
    lines = [title, DIVIDER, f"📱 {phone}", *_rows(pairs), f"🕒 {ts or now_hms()}"]
    return "\n".join(lines)


# ---- کارت‌های زنده (در جا ویرایش می‌شوند) ---------------------------------

def live_contacts(phone: str, prefix: str, found: int, probed: int, total: int,
                  status: str = "🟢 در حال جست‌وجو", engine: str | None = None,
                  not_on: int | None = None, failed: int | None = None) -> str:
    lines = [
        "🧲 ساخت مخاطب — زنده",
        DIVIDER,
        f"📱 {phone}",
        bar(probed, total),
        *_rows([
            ("وضعیت", status),
            ("پیش‌شماره", prefix),
            ("ساخته شد", f"✅ {found}"),
            ("بررسی شده", f"{probed} از {total}"),
            ("در ایتا نیست", not_on or None),
            ("ناموفق", failed or None),
        ]),
        f"🕒 {now_hms()}",
    ]
    return "\n".join(lines)


def live_stages(phone: str, stages: list[tuple[str, str, float | None]],
                elapsed: float, note: str | None = None) -> str:
    """کارت چک‌لیست، از لحظه‌ای که دکمه زده می‌شود.

    قبل از این، پنل در چند دقیقه‌ی اول یک جاب هیچ چیزی نشان نمی‌داد — و روی این
    هاست فقط باز کردن کروم ۱۵۰ تا ۲۰۰ ثانیه می‌برد، پس هیچ راهی نبود که رباتِ
    سالم را از رباتِ گیرکرده تشخیص بدهی.

    stages: [(برچسب، وضعیت، ثانیه)] که وضعیت یکی از done | active | pending |
    failed است. `ثانیه` مدتی است که یک مرحله‌ی تمام‌شده طول کشیده.
    """
    mark = {"done": "✅", "active": "⏳", "pending": "◻️", "failed": "⚠️"}
    rows = []
    for label, state, secs in stages:
        line = f"{mark.get(state, '◻️')} {label}"
        if state == "done" and secs:
            line += f"  ({secs:.0f} ثانیه)"
        elif state == "active":
            line += "  …"
        rows.append(line)
    lines = ["⚙️ در حال کار — زنده", DIVIDER, f"📱 {phone}", *rows,
             f"⏱ مجموع {fmt_duration(elapsed)}"]
    if note:
        lines.append(note)
    lines.append(f"🕒 {now_hms()}")
    return "\n".join(lines)


def live_send(phone: str, sent: int, failed: int, total: int, elapsed: float,
              status: str = "🟢 در حال ارسال", engine: str | None = None,
              kind: str | None = None) -> str:
    lines = [
        "🚀 در حال ارسال — زنده",
        DIVIDER,
        f"📱 {phone}",
        bar(sent + failed, total),
        *_rows([
            ("وضعیت", status),
            ("نوع", kind),
            ("موفق", f"{sent} از {total}"),
            ("ناموفق", failed or None),
            ("گذشته", fmt_duration(elapsed)),
            ("باقی‌مانده", eta(sent + failed, total, elapsed)),
        ]),
        f"🕒 {now_hms()}",
    ]
    return "\n".join(lines)


# علامت وضعیت هر اکانت در تفکیک چند-اکانتی.
_STATE_MARK = {
    "pending": "⏳",
    "running": "🟢",
    "done": "✅",
    "stopped": "🛑",
    "failed": "⚠️",
    "limited": "🚫",
    "no_targets": "🚧",
    "preparing": "📥",
}

_STATE_WORD = {
    "done": "تمام شد",
    "stopped": "متوقف شد",
    "failed": "خطا خورد",
    "limited": "به محدودیت خورد",
    "no_targets": "هدفی نداشت",
    "preparing": "در حال خواندن مخاطبین",
    "running": "در حال ارسال",
    "pending": "در نوبت",
}


def _account_tally(accounts: list[dict]) -> str:
    """`۸ · ✅۱ 🚧۷` — تا اکانت‌هایی که نتوانستند بفرستند هرگز پشت یک درصدِ ترکیبی
    پنهان نشوند."""
    states = [str(a.get("state", "pending")) for a in accounts]
    ok = sum(1 for s in states if s in ("done", "running", "stopped"))
    blocked = states.count("no_targets")
    failed = states.count("failed") + states.count("limited")
    out = str(len(accounts))
    if ok:
        out += f" · ✅{ok}"
    if blocked:
        out += f" · 🚧{blocked} بی‌مخاطب"
    if failed:
        out += f" · ⚠️{failed}"
    return out


#: چند اکانتِ تمام‌شده بلوک دو-سطریِ کامل روی کارت زنده نگه می‌دارند.
#: تلگرام پیام را حدود ۴۰۹۶ کاراکتر می‌بندد و یک اجرای ۵۰ اکانتی از آن رد می‌شود؛
#: بقیه در یک سطر خلاصه می‌شوند.
_MULTI_DETAIL_ROWS = 6


def live_send_multi(accounts: list[dict], current: str | None, sent: int,
                    failed: int, total: int, elapsed: float,
                    status: str = "🟢 در حال ارسال", engine: str | None = None,
                    kind: str | None = None, parallel: int = 1) -> str:
    """**یک** کارت زنده برای ارسال چند-اکانتی.

    `sent`/`failed`/`total` عددهای **ترکیبی** همه‌ی اکانت‌های انتخاب‌شده است
    (فهرست مخاطبینشان با هم جمع شده)، پس بلوک بالا جواب «چقدر از کل کار انجام
    شده» را می‌دهد. `current` اکانتی است که تازه‌ترین ارسال را داشته، و بلوک
    تفکیک، پیشرفت خودِ هر اکانت را نشان می‌دهد.

    accounts: [{"phone": str, "sent": int, "failed": int, "total": int,
                "state": "pending|running|done|stopped|failed|limited"}]
    """
    running = [a for a in accounts
               if str(a.get("state")) in ("running", "preparing")]
    now_label = "، ".join(str(a.get("phone")) for a in running) or (current or "—")
    lines = [
        "📨 ارسال چند-اکانتی — زنده",
        DIVIDER,
        bar(sent + failed, total),
        *_rows([
            ("وضعیت", status),
            ("نوع", kind),
            ("حالت", (f"موازی · {parallel} تا هم‌زمان" if parallel > 1
                      else "هر بار یک اکانت")),
            ("اکانت‌ها", _account_tally(accounts)),
            # با بیش از یک اکانت در جریان، این سطر باید همه را نام ببرد؛ یک فیلد
            # «الان» تنها برای اجرای ترتیبی درست بود.
            ("الان", now_label),
            ("موفق", f"{sent} از {total}"),
            ("ناموفق", failed or None),
            ("گذشته", fmt_duration(elapsed)),
            ("باقی‌مانده", eta(sent + failed, total, elapsed)),
        ]),
    ]
    if accounts:
        lines.append(DIVIDER)
        # یک نوار برای هر اکانت: چشم نوار را خیلی سریع‌تر از «۱۲۰/۵۰۰» می‌خواند، و
        # با چند اکانتِ هم‌زمان، عددها تنها خوانا نبودند. شماره‌ی ترتیب چاپ
        # **نمی‌شود** — در اجرای موازی ترتیبی را القا می‌کند که وجود ندارد.
        active = [a for a in accounts
                  if str(a.get("state")) in ("running", "preparing")]
        finished = [a for a in accounts
                    if str(a.get("state")) in ("done", "failed", "stopped",
                                               "limited", "no_targets")]
        waiting = [a for a in accounts if a not in active and a not in finished]
        # تلگرام پیام را حدود ۴۰۹۶ کاراکتر می‌بندد، پس فقط یک پنجره تفکیک می‌شود:
        # هرچه در جریان است، بعد تازه‌ترین تمام‌شده‌ها، بعد یک جمع.
        shown = active + finished[-_MULTI_DETAIL_ROWS:]
        for a in shown:
            state = str(a.get("state", "pending"))
            mark = _STATE_MARK.get(state, "•")
            done = int(a.get("sent", 0) or 0)
            tot = int(a.get("total", 0) or 0)
            head = f"{mark} {a.get('phone', '')}"
            if a.get("failed"):
                head += f" · ✗{a['failed']}"
            if state == "limited":
                head += " · محدود شد"
            lines.append(head)
            lines.append(f"   {bar(done, tot, width=10)}  {done:,}/{tot:,}")
        hidden = len(finished) - len(finished[-_MULTI_DETAIL_ROWS:])
        tail = []
        if hidden > 0:
            tail.append(f"{hidden} تمام‌شده‌ی دیگر")
        if waiting:
            tail.append(f"{len(waiting)} در نوبت")
        if tail:
            lines.append("• " + " · ".join(tail))
    lines.append(f"🕒 {now_hms()}")
    return "\n".join(lines)


def multi_ready(accounts: list[dict], total: int, kind: str | None = None) -> str:
    """وقتی دامنه‌ی هر اکانتِ تیک‌خورده مشخص شد، قبل از ارسال یک بار پست می‌شود.

    این همان کارت «اول جمعش کن» است: تعداد مخاطب هر اکانت و جمع کل، به همان
    ترتیبی که استفاده می‌شوند.
    """
    lines = ["🧾 ارسال چند-اکانتی — صف آماده", DIVIDER]
    width = max((len(str(a.get("phone", ""))) for a in accounts), default=0)
    for i, a in enumerate(accounts, start=1):
        phone = str(a.get("phone", "")).ljust(width)
        n = int(a.get("total", 0) or 0)
        lines.append(f"{i}. {phone} · {n:,} مخاطب" + ("" if n else "  ⚠️ هیچ"))
    lines += [
        DIVIDER,
        *_rows([
            ("اکانت‌ها", len(accounts)),
            ("نوع", kind),
            ("جمع کل", f"{total:,} پیام برای ارسال"),
        ]),
        DIVIDER,
        "با شماره ۱ شروع می‌شود. هر اکانت تا آخر می‌رود (یا تا وقتی متوقف شود یا "
        "خطا بخورد)، بعد نوبت بعدی شروع می‌شود.",
        f"🕒 {now_hms()}",
    ]
    return "\n".join(lines)


def multi_account_done(phone: str, order: int, of: int, state: str, sent: int,
                       failed: int, total: int,
                       next_phone: str | None = None) -> str:
    """نتیجه‌ی هر اکانت داخل یک اجرای ترتیبی، به‌علاوه اینکه بعدی چیست."""
    mark = _STATE_MARK.get(state, "•")
    word = _STATE_WORD.get(state, state)
    lines = [
        f"{mark} اکانت {order}/{of} {word}",
        DIVIDER,
        f"📱 {phone}",
        bar(sent + failed, total),
        *_rows([
            ("موفق", f"✅ {sent} از {total}"),
            ("ناموفق", f"✗ {failed}" if failed else None),
        ]),
        DIVIDER,
        (f"➡️ رفتن به اکانت {order + 1}/{of}: {next_phone}"
         if next_phone else "اکانتی در صف نماند."),
        f"🕒 {now_hms()}",
    ]
    return "\n".join(lines)


# ---- کارت‌های آماده ------------------------------------------------------

def _ping_mark(ping_ms: int | None) -> str:
    if ping_ms is None:
        return "🔴 بی‌پاسخ"
    if ping_ms < 300:
        return f"🟢 {ping_ms}ms"
    if ping_ms < 1000:
        return f"🟡 {ping_ms}ms"
    return f"🔴 {ping_ms}ms"


def _ago(ts: float | None) -> str:
    """چند وقت پیش، به زبان آدمیزاد."""
    if not ts:
        return "هرگز"
    d = max(0, time.time() - float(ts))
    if d < 90:
        return "همین الان"
    if d < 3600:
        return f"{int(d / 60)} دقیقه پیش"
    if d < 172800:
        return f"{int(d / 3600)} ساعت پیش"
    return f"{int(d / 86400)} روز پیش"


def panel_home(accounts: int, ready: int, active: str | None,
               engine: str | None = None, ping_ms: int | None = None,
               contacts: int | None = None, running: int = 0,
               content: str | None = None, last_run: dict | None = None) -> str:
    """صفحه‌ی خانه: چه چیزی می‌تواند بفرستد، چه چیزی بار شده، آخرین اتفاق چه بود.

    عمداً «ربات: آنلاین» نشان نمی‌دهد (اگر آفلاین بود که کارتی نمی‌رسید)، نه شماره‌ی
    نسخه، نه نوار پیشرفت. هر سطر اینجا چیزی است که می‌توان درباره‌اش کاری کرد.
    """
    acc_txt = None
    if accounts:
        missing = max(0, accounts - max(0, ready))
        acc_txt = f"{accounts}"
        if ready:
            acc_txt += f" ({ready} آماده"
            acc_txt += f" · {missing} بی‌مخاطب)" if missing else ")"
        elif missing:
            acc_txt += f" ({missing} بی‌مخاطب)"
    lr = last_run or {}
    lr_txt = None
    if lr:
        bits = [f"{int(lr.get('sent', 0)):,} موفق"]
        if lr.get("failed"):
            bits.append(f"{int(lr['failed']):,} ناموفق")
        if lr.get("skipped"):
            bits.append(f"{int(lr['skipped']):,} رد")
        if lr.get("elapsed"):
            bits.append(fmt_duration(float(lr["elapsed"])))
        lr_txt = " · ".join(bits) + f" ({_ago(lr.get('at'))})"

    lines = [
        "🤖 پنل مدیریت ایتا",
        DIVIDER,
        *_rows([
            ("ایتا", _ping_mark(ping_ms)),
            ("اکانت‌ها", acc_txt or "هنوز هیچ"),
            ("مخاطبین", f"{contacts:,} قابل ارسال" if contacts else "هنوز ذخیره نشده"),
            ("فعال", active or "—"),
            ("جاب", f"⏳ {running} در حال اجرا" if running else "بی‌کار"),
            ("محتوا", content or "تنظیم نشده"),
            ("آخرین اجرا", lr_txt),
            ("موتور", engine),
        ]),
        DIVIDER,
        "یکی از بخش‌های پایین را انتخاب کن.",
    ]
    return "\n".join(lines)


def account_added(account: str, phone: str, contacts: int | None, pvs: int | None,
                  engine: str | None = None, saved: int | None = None) -> str:
    return card(
        "✅ اکانت اضافه شد",
        [
            ("شماره", phone),
            ("مخاطبین", f"{contacts:,}"
                        if isinstance(contacts, int) and contacts >= 0 else "—"),
            ("چت‌ها", pvs if isinstance(pvs, int) and pvs >= 0 else "—"),
            ("ذخیره‌شده", f"{saved:,}" if saved else None),
            ("زمان", now_hms()),
        ],
        footer=("مخاطبین ذخیره شدند — این اکانت آماده‌ی ارسال است." if saved else
                "لاگین شد، ولی مخاطبی برنگشت. در ایتا مخاطب اضافه کن، بعد روی همین "
                "اکانت 🔄 بروزرسانی مخاطبین را بزن."),
    )


def account_panel(account: str, phone: str, contacts: int | None, pvs: int | None,
                  engine: str | None, busy: bool, peers: int | None = None,
                  saved: int | None = None, saved_age: float | None = None,
                  meta_age: float | None = None, pending: int | None = None,
                  refused: int | None = None,
                  engine_ready: bool | None = None) -> str:
    """پنل یک اکانت.

    `saved` تعداد مخاطبینِ کش لوکال است — همان چیزی که ارسال واقعاً رویش می‌چرخد،
    پس مهم‌ترین عدد اینجاست. `contacts` چیزی است که ایتا در آخرین بروزرسانی گفت.
    """
    age = None
    if saved:
        if saved_age is None:
            age = "همین الان"
        elif saved_age < 1:
            age = "کمتر از یک ساعت پیش"
        elif saved_age < 48:
            age = f"{int(saved_age)} ساعت پیش"
        else:
            age = f"{int(saved_age / 24)} روز پیش"

    if busy:
        footer = "یک جاب روی این اکانت در حال اجراست. با «توقف» زودتر تمامش کن."
    elif not saved:
        footer = ("برای این اکانت مخاطبی ذخیره نشده. 🔄 بروزرسانی مخاطبین را بزن تا "
                  "از ایتا خوانده شوند (چند ثانیه است).")
    else:
        footer = f"آماده‌ی ارسال به {saved:,} مخاطب ذخیره‌شده."

    # «در ایتا» یک عکس لحظه‌ای از آخرین اندازه‌گیری است، پس می‌گوید **کِی** گرفته
    # شده. بدون آن، این سطر بی‌صدا با واقعیت مخالف بود (برای اکانتی که ۱٬۰۹۴
    # مخاطب داشت عدد ۱٬۴۱۴ نشان می‌داد) و هیچ‌چیز دلیلش را توضیح نمی‌داد.
    on_eitaa = None
    if isinstance(contacts, int) and contacts >= 0:
        on_eitaa = f"{contacts:,}"
        if meta_age:
            on_eitaa += f" (اندازه‌گیری {_ago(time.time() - meta_age * 3600)})"

    return card(
        "👤 اکانت",
        [
            ("شماره", phone),
            ("وضعیت", "⏳ مشغول" if busy else "🟢 بی‌کار"),
            ("ذخیره‌شده", f"{saved:,} مخاطب ({age})" if saved else "هیچ"),
            # آنچه یک ارسال **واقعاً** به آن می‌رسد: ذخیره‌شده منهای کسانی که ایتا
            # از این اکانت مدام ردشان می‌کند.
            ("قابل دسترس", f"{max(0, (saved or 0) - refused):,} از {saved:,}"
                           if (saved and refused) else None),
            ("امتناع‌شده", f"{refused:,} (ایتا به آن‌ها تحویل نمی‌دهد)"
                          if refused else None),
            ("در ایتا", on_eitaa or "—"),
            ("چت‌ها", pvs if isinstance(pvs, int) and pvs >= 0 else None),
            ("قبلاً گرفته‌اند", f"{pending:,} محتوای فعلی را گرفته‌اند"
                              if pending else None),
            ("موتور", ("آماده‌ی بدون-مرورگر" if engine_ready
                       else "بدون-مرورگر هنوز کپچر نشده")
                      if engine_ready is not None else None),
            # فقط وقتی موتور بدون-مرورگر فعال است معنا دارد.
            ("peers", peers if (peers and engine == "direct") else None),
        ],
        footer=footer,
    )


def contacts_saved(phone: str, count: int, with_peer: int, elapsed: float,
                   replaced: int | None = None, partial: bool = False) -> str:
    """نتیجه‌ی کش‌کردن فهرست مخاطبین یک اکانت."""
    pairs = [
        ("شماره", phone),
        ("ذخیره شد", f"{count:,} مخاطب"),
        ("با شناسه", f"{with_peer:,}"),
        ("زمان", fmt_duration(elapsed)),
    ]
    if replaced is not None and replaced != count:
        pairs.insert(2, ("قبلاً", f"{replaced:,}"))
    if partial:
        title = "🛑 مخاطبین ذخیره شد (زودتر متوقف شد)"
        footer = ("قبل از تمام‌شدن فهرست متوقف شد، پس این فقط بخشی از آن است. "
                  "دوباره 🔄 بروزرسانی مخاطبین را بزن تا کامل شود.")
    elif count:
        title = "📥 مخاطبین ذخیره شد"
        footer = "ارسال از این اکانت از این به بعد فوراً شروع می‌شود."
    else:
        title = "📥 مخاطبی پیدا نشد"
        footer = ("فهرست مخاطبین خالی برگشت. یک بار بخش مخاطبین ایتا را باز کن، "
                  "بعد دوباره امتحان کن.")
    return card(title, pairs, footer=footer)


def send_started(account: str, kind: str, targets: int, delay: float) -> str:
    return card(
        "🚀 ارسال شروع شد",
        [
            ("شماره", account),
            ("نوع", kind),
            ("هدف‌ها", f"{targets:,}"),
            ("تأخیر", f"{delay:g} ثانیه"),
            ("زمان", now_hms()),
        ],
    )


def send_progress(sent: int, failed: int, skipped: int, left: int,
                  elapsed: float) -> str:
    """پیشرفت میان‌اجرا. نوار، سرعت و تخمین دارد تا اجرایی که فقط **کند** است از
    اجرایی که **گیر کرده** جدا شود — کارت قبلی چهار شمارنده نشان می‌داد بدون هیچ
    حسی از حرکت."""
    sent, failed, left = _n(sent), _n(failed), _n(left)
    attempted = sent + failed
    total = attempted + left
    return card(
        "📈 پیشرفت",
        [
            ("پیشرفت", bar(attempted, total) if total else None),
            ("موفق", f"{sent:,}"),
            ("ناموفق", f"{failed:,}  ({_pct(failed, attempted)} از تلاش‌شده‌ها)"
                       if failed else "۰"),
            ("رد شده", f"{_n(skipped):,}" if skipped else None),
            ("باقی", f"{left:,}"),
            ("سرعت", pace(attempted, elapsed)),
            ("تخمین باقی", eta(attempted, total, elapsed) if left else None),
            ("گذشته", fmt_duration(elapsed)),
        ],
    )


def send_finished(account: str, kind: str, sent: int, failed: int, skipped: int,
                  total: int, elapsed: float, stopped: bool = False,
                  reason: str | None = None, engine: str | None = None,
                  limits: int | None = None, via_bridge: int | None = None,
                  via_fallback: int | None = None,
                  job_id: str | None = None) -> str:
    """کارتی که یک اجرا را می‌بندد.

    کارت قبلی می‌گفت «۱۹۹ از ۱۲۳۲ فرستاده شد» و تنها سؤالی که اهمیت دارد را بی‌جواب
    می‌گذاشت: آن ۱۰۳۳ نفر **امتحان و رد شدند**، یا **هرگز نوبتشان نشد** چون حلقه
    تسلیم شد؟ این‌ها دو باگ کاملاً متفاوت‌اند و یکسان دیده می‌شدند. «تلاش‌شده» /
    «هرگز امتحان نشد» / «حکم» جدایشان می‌کند، و سطر debug پایین کل وضعیت اجرا را
    برای گزارش خطا حمل می‌کند.

    همه‌ی پارامترهای اضافه اختیاری‌اند، پس این تابع برای صداکننده‌های قبلی
    drop-in می‌ماند.
    """
    sent, failed, skipped = _n(sent), _n(failed), _n(skipped)
    total, elapsed = _n(total), float(elapsed or 0)
    attempted, untouched = coverage(sent, failed, total)

    if stopped:
        title = "🛑 ارسال متوقف شد"
    elif failed and not sent:
        title = "⚠️ ارسال ناموفق"
    elif failed:
        title = "⚠️ ارسال تمام شد (با خطا)"
    else:
        title = "✅ ارسال تمام شد"

    # حکم، اولین سطری است که باید خواند: می‌گوید فهرست پیموده شد یا نه.
    if untouched and stopped:
        verdict = f"🛑 زودتر تمام شد — {untouched:,} از {total:,} هرگز امتحان نشدند"
    elif untouched:
        verdict = (f"⚠️ حلقه تمام شد ولی {untouched:,} هرگز امتحان نشدند "
                   f"(متوقف نشده بود، پس این غیرمنتظره است)")
    elif total:
        verdict = f"✅ کل فهرست پیموده شد — همه‌ی {total:,} امتحان شدند"
    else:
        verdict = "◻️ چیزی برای پیمودن نبود"

    lines = [
        title,
        DIVIDER,
        f"📱 {account}",
        bar(attempted, total),
        verdict,
        *_rows([
            ("نوع", kind),
            ("موتور", engine),
            ("تحویل شد", f"✅ {sent:,} از {total:,}  ({_pct(sent, total)})"),
            ("ناموفق", (f"✗ {failed:,}  ({_pct(failed, attempted)} از تلاش‌شده‌ها)")
                       if failed else None),
            ("تلاش‌شده", f"{attempted:,} از {total:,}"),
            ("هرگز امتحان نشد", f"{untouched:,}  ← هنوز منتظرند" if untouched else None),
            ("رد شده", (f"{skipped:,} (در اجرای قبلی تحویل شده بودند)")
                       if skipped else None),
            ("امتناع‌شده", f"{_n(limits):,} به محدودیت سرور خوردند" if limits else None),
            ("مسیر", (f"api {_n(via_bridge):,} · مرورگر {_n(via_fallback):,}")
                     if (via_bridge is not None or via_fallback is not None)
                     else None),
            ("متوقف‌کننده", sanitize(reason, 160) if reason else None),
            ("مدت", fmt_duration(elapsed)),
            ("سرعت", pace(attempted, elapsed)),
            ("تخمین باقی", (eta(attempted, total, elapsed)) if untouched else None),
        ]),
        debug_line(job=job_id, sent=sent, failed=failed, skip=skipped, total=total,
                   tried=attempted, untried=untouched, limits=limits,
                   el=f"{elapsed:.0f}s", eng=engine, kind=kind,
                   stop=1 if stopped else 0),
        f"🕒 {now_hms()}",
    ]
    if untouched:
        lines.append("دوباره ارسال را بزن تا ادامه بدهد: تحویل‌شده‌ها رد می‌شوند، "
                     "پس هیچ‌کس دو بار نمی‌گیرد.")
    return "\n".join(lines)


def contacts_started(account: str, prefix: str, count: int, delay: float) -> str:
    return card(
        "🧲 ساخت مخاطب شروع شد",
        [
            ("اکانت", account),
            ("پیش‌شماره", prefix),
            ("برنامه", count),
            ("تأخیر", f"{delay:g} ثانیه"),
            ("زمان", now_hms()),
        ],
    )


def contacts_progress(added: int, not_on: int, invalid: int, error: int,
                      left: int) -> str:
    return card(
        "📈 پیشرفت ساخت مخاطب",
        [
            ("اضافه شد", added),
            ("در ایتا نیست", not_on),
            ("نامعتبر", invalid),
            ("خطا", error),
            ("باقی", left),
        ],
    )


def contacts_finished(account: str, added: int, not_on: int, invalid: int,
                      error: int, total: int, elapsed: float,
                      stopped: bool = False) -> str:
    if stopped:
        title = "🛑 ساخت مخاطب متوقف شد"
    elif added:
        title = "✅ ساخت مخاطب تمام شد"
    else:
        title = "⚠️ ساخت مخاطب تمام شد (چیزی اضافه نشد)"
    lines = [
        title,
        DIVIDER,
        f"📱 {account}",
        bar(added + not_on + invalid + error, total),
        *_rows([
            ("اضافه شد", f"✅ {added}"),
            ("در ایتا نیست", not_on or None),
            ("نامعتبر", invalid or None),
            ("خطا", error or None),
            ("بررسی شد", total),
            ("زمان", fmt_duration(elapsed)),
        ]),
        f"🕒 {now_hms()}",
    ]
    if not added:
        lines.append("هیچ‌کدام از این شماره‌ها در ایتا ثبت نیستند. "
                     "پیش‌شماره‌ی دیگری امتحان کن.")
    return "\n".join(lines)


def error_card(where: str, account: str | None = None, target: str | None = None,
               code: str | None = None, detail: str | None = None,
               trace_id: str | None = None, engine: str | None = None,
               phase: str | None = None, sent: int | None = None,
               total: int | None = None, attempt: str | None = None,
               customer: object | None = None, scope: str | None = None) -> str:
    """یک خطا، به‌علاوه اولین کاری که ارزش انجام دادن دارد.

    «اقدام» از خودِ کد مشتق می‌شود تا کارت به‌تنهایی قابل عمل باشد، نه یک کد که
    بعد باید بیایی بپرسی چه بوده. «کد پیگیری» / «کجا» / «مرحله» با هم دقیقاً
    می‌گویند کدام بخشِ کدام جاب این را ساخته.

    `customer` و `scope` در نسخه‌ی چند-مشتری اضافه شده‌اند: بدون آیدی مشتری، کارت
    خطا در سرویسی با چند مستأجر بی‌فایده است، و `scope` می‌گوید محدودیت مربوط به
    یک مخاطب است یا کل اکانت — همان تفکیکی که تصمیم را می‌سازد.
    """
    return card(
        "⚠️ خطا",
        [
            ("کد پیگیری", trace_id),
            ("مشتری", customer),
            ("کجا", where),
            ("مرحله", phase),
            ("موتور", engine),
            ("اکانت", account),
            ("مخاطب", sanitize(target, 60) if target else None),
            ("تلاش", attempt),
            ("دامنه", scope),
            ("پیشرفت", (f"{_n(sent):,} از {_n(total):,} در آن لحظه")
                       if total is not None else None),
            ("کد", code),
            ("جزئیات", sanitize(detail, 400) if detail else None),
            ("اقدام", code_hint(code)),
            ("زمان", now_hms()),
        ],
        footer=debug_line(trace=trace_id, cust=customer, where=where, phase=phase,
                          code=code, eng=engine, acc=account),
    )


def preflight_card(phone: str, engine: str, kind: str, total: int, skipped: int,
                   refused: int, concurrency: int, delay: float,
                   per_send: float | None, file_mb: float | None = None) -> str:
    """این اجرا می‌خواهد چه کند، و چقدر باید طول بکشد.

    قبل از اولین پیام پست می‌شود. بدون آن، اجرایی که ساعت‌ها طول می‌کشید عیناً مثل
    اجرایی که سه دقیقه بود دیده می‌شد، و لحظه‌ای نمی‌ماند که «توقف» بزنی و یک
    تنظیم را عوض کنی.

    تخمین از زمانِ **اندازه‌گیری‌شده‌ی** هر پیام در اجرای قبلی استفاده می‌کند (اگر
    وجود داشته باشد)، چون یک عددِ ثابتِ حدسی روی این هاست همیشه غلط بود:

        ثانیه = کل / (هم‌زمانی / (هر_ارسال + تأخیر))
    """
    per = per_send if (per_send and per_send > 0) else 2.0
    rate = max(0.01, concurrency / (per + max(0.0, delay)))
    eta_s = total / rate
    return card(
        "🚦 آماده‌ی ارسال",
        [
            ("شماره", phone),
            ("موتور", engine),
            ("نوع", kind + (f" ({file_mb:.1f} مگابایت)" if file_mb else "")),
            ("گیرندگان", f"{total:,}"),
            ("رد می‌شوند", (f"{skipped:,} قبلاً تحویل شده‌اند" if skipped else None)),
            ("امتناع‌شده", (f"{refused:,} ایتا قبول نمی‌کند" if refused else None)),
            ("سرعت", f"{concurrency} تا هم‌زمان، {delay:g} ثانیه بین دسته‌ها"),
            ("تخمین", f"~{fmt_duration(eta_s)} با {rate:.2f} پیام بر ثانیه"),
        ],
        footer=("تخمین بر پایه‌ی سرعت اندازه‌گیری‌شده‌ی اجرای قبلی."
                if per_send else
                "اولین اجرای این اکانت است، پس تخمین ۲ ثانیه برای هر پیام فرض کرده."),
    )


def dry_run_card(phone: str, engine: str, kind: str, ok: bool, detail: str,
                 send_seconds: float, total_seconds: float) -> str:
    """نتیجه‌ی ارسال تستِ یک پیام به «پیام‌های ذخیره‌شده»ی خودِ اکانت."""
    return card(
        "🧪 تست ارسال — موفق" if ok else "🧪 تست ارسال — ناموفق",
        [
            ("شماره", phone),
            ("موتور", engine),
            ("نوع", kind),
            ("نتیجه", "به «پیام‌های ذخیره‌شده»ی خودت تحویل شد" if ok
                      else sanitize(detail, 160)),
            ("ارسال طول کشید", f"{send_seconds:.1f} ثانیه"),
            ("کل تست", fmt_duration(total_seconds)),
        ],
        footer=("در «پیام‌های ذخیره‌شده»ی خودت ببینش: مخاطبینت **عیناً** همین را "
                "می‌گیرند. «ارسال طول کشید» اندازه‌گیری واقعیِ یک پیام با موتور "
                "فعلی است."
                if ok else
                "هیچ‌چیز به هیچ مخاطبی فرستاده نشد. قبل از شروع کمپین این را درست "
                "کن — برای همه به همین شکل خطا می‌خورد."),
    )


def pool_card(status: dict) -> str:
    """سشن‌های آماده‌ی مرورگر: چه چیزی گرم است و چه چیزی صرفه‌جویی شده."""
    accounts = status.get("accounts") or {}
    lines = []
    for acc, info in accounts.items():
        state = ("در حال استفاده" if info.get("leased")
                 else f"آماده، {info.get('idle')} ثانیه بی‌کار")
        lines.append(f"• {acc}: {state} · {info.get('uses')} بار استفاده")
    saved = int(status.get("saved_launches") or 0)
    return card(
        "🖥 مرورگرهای آماده",
        [
            ("فعال", "بله" if status.get("enabled") else "نه (هر جاب یک مرورگر)"),
            ("گرم الان", f"{status.get('warm', 0)} از {status.get('max_open')} مجاز"),
            ("بستن بی‌کار", f"بعد از {int(status.get('idle_ttl') or 0)} ثانیه"),
            ("لانچ صرفه‌جویی‌شده", saved or None),
            ("باز شده", status.get("created") or None),
            ("بازیافت‌شده", status.get("recycled") or None),
            ("بیرون‌انداخته", status.get("evicted") or None),
            ("دورانداخته", status.get("discarded") or None),
        ],
        body="\n".join(lines) if lines else None,
        footer=(f"هر لانچِ صرفه‌جویی‌شده حدود ۲ تا ۳ دقیقه است که برای بالا آوردن "
                f"کروم روی این سرور صرف نشده ({saved} تا تا الان). سشن‌ها فقط وقتی "
                f"جابی بخواهد باز می‌شوند، وقتی بی‌کار شوند بسته می‌شوند، و بازیافت "
                f"می‌شوند تا مرورگرِ عمر-طولانی بی‌مهار رشد نکند."),
    )


def timing_card(phone: str, engine: str, timing: dict, concurrency: int,
                limits: int = 0, fallbacks: int = 0) -> str:
    """وقتِ یک اجرا واقعاً کجا رفت.

    اضافه شد چون یک اجرا ۶.۲ ثانیه برای هر پیام اندازه‌گیری شد در حالی که خودِ
    transport در ۱ تا ۲ ثانیه جواب می‌داد، و هیچ‌چیز در پنل نمی‌توانست بگوید آن
    ۴ ثانیه‌ی دیگر چه بود.
    """
    total = float(timing.get("total") or 0) or 1.0

    def share(key: str) -> str | None:
        v = float(timing.get(key) or 0)
        if v <= 0:
            return None
        return f"{v:.0f} ثانیه ({v / total * 100:.0f}٪)"

    # بزرگ‌ترین هزینه را صریح نام ببر. چهار درصد هنوز آدم لازم داشت تا مقایسه‌شان
    # کند؛ «حکم» می‌گوید کدام پیچ ارزش دست‌زدن دارد.
    buckets = {k: float(timing.get(k) or 0)
               for k in ("transport", "fallback", "pacing", "other")}
    top = max(buckets, key=lambda k: buckets[k]) if any(buckets.values()) else None
    verdicts = {
        "transport": "خودِ ایتا کندترین بخش بود — گلوگاه، تنظیمات نیست.",
        "fallback": "مسیر کندِ مرورگر غالب بود: API داخل صفحه بیشتر ارسال‌ها را از "
                    "دست می‌داد. قبل از بالا بردن سرعت، ارزش بررسی دارد.",
        "pacing": "بیشترِ اجرا، تأخیرِ خودت بود. کم کردنش سریع‌ترین برد اینجاست.",
        "other": "بیشترِ وقت نه ایتا بود نه تنظیمات — این به **خودِ سرور** اشاره "
                 "می‌کند (CPU steal، swap، دیسک).",
    }
    verdict = None
    if top and buckets[top] / total >= 0.4:
        verdict = f"➡️ {verdicts[top]}"

    return card(
        "⏱ زمان‌بندی اجرا",
        [
            ("شماره", phone),
            ("موتور", f"{engine} · {concurrency} تا هم‌زمان"),
            ("مجموع", fmt_duration(total)),
            ("ارسال", share("transport")),
            ("مسیر کند", share("fallback")),
            ("انتظار تأخیر", share("pacing")),
            ("بقیه", share("other")),
            ("بزرگ‌ترین هزینه", top),
            ("هر پیام", f"{timing.get('per_send')} ثانیه"
                        if timing.get("per_send") else None),
            ("نرخ", f"{timing.get('msg_per_s')} پیام بر ثانیه"),
            ("امتناع‌شده", limits or None),
            ("برگشت به مرورگر", fallbacks or None),
        ],
        body=verdict,
        footer="«ارسال» وقتی است که خودِ ایتا گرفته. «انتظار تأخیر» تنظیم تأخیر "
               "توست. «بقیه»ی بزرگ یعنی سرور (CPU steal، swap)، نه ایتا و نه "
               "تنظیمات.",
    )


def restriction_card(account: str, reason: str, sent_before: int,
                     paused: bool = True) -> str:
    """سرور یک مخاطب را رد کرد (PEER_FLOOD، هشدار اسپم، ...).

    `paused=False` وقتی است که مالک «توقف روی محدودیت» را خاموش کرده: کارت باز هم
    پست می‌شود تا محدودیت هرگز پنهان نماند، ولی اجرا ادامه می‌دهد و فقط «توقف»
    تمامش می‌کند.
    """
    kind = limit_kind(reason)
    footer = kind["note"]
    if not paused:
        footer = ((footer + " ") if footer else "") + \
                 "«توقف روی محدودیت» خاموش است، پس اجرا ادامه می‌دهد. با «توقف» " \
                 "تمامش کن."

    # اینکه این محدودیت با فهرست ردشده‌ها چه می‌کند، همان بخشی است که بی‌صدا مخاطب
    # می‌سوزاند: کدِ کل-اکانت به‌ازای هر مخاطب ثبت می‌شود، پس رشد فهرست ردشده‌ها
    # طبیعی است و **دلیلی بر این نیست** که آن آدم‌ها چیزی را رد کرده‌اند.
    if kind["key"] == "flood_wait":
        effect = "ثبت نشد (انتظارهای زمان‌دار دوباره تلاش می‌شوند)"
    elif kind["scope"] == "کل اکانت":
        effect = ("مخاطب به فهرست ردشده‌ها اضافه شد — ولی علت، اکانت است نه او")
    elif kind["key"] in ("peer_flood",):
        effect = "مخاطب برای همیشه به فهرست ردشده‌ها اضافه شد"
    else:
        effect = None

    if paused:
        action = "جاب خودکار متوقف شد"
    else:
        action = "ادامه می‌دهد (توقف روی محدودیت خاموش است)"

    return card(
        "🚫 محدودیت تشخیص داده شد",
        [
            ("اکانت", account),
            ("دلیل", sanitize(reason, 200)),
            ("نوع", kind["label"]),
            ("مربوط به", kind["scope"]),
            ("انتظار سرور", f"{kind['wait']} ثانیه" if kind.get("wait") else None),
            ("قبلش فرستاده", f"{_n(sent_before):,} در **این** اجرا "
                            f"(اجراهای قبلی اینجا حساب نمی‌شوند)"),
            ("فهرست ردشده‌ها", effect),
            ("اقدام", action),
            ("بعدی", ("فقط «توقف» این اجرا را تمام می‌کند" if not paused
                      else "وقتی سبک شد، دوباره «ارسال» را بزن تا ادامه بدهد")),
            ("زمان", now_hms()),
        ],
        footer=footer,
    )


def paused_card(account: str, reason: str, sent_before: int,
                total: int | None = None, brake: str | None = None) -> str:
    """جاب توسط ترمز ایمنیِ **خودمان** متوقف شد (نه لزوماً یک محدودیت واقعی).

    صریح نوشته شده چون این کارت و «🚫 محدودیت تشخیص داده شد» شبیه هم‌اند ولی معنای
    مخالف دارند: این یکی **ترمز خودِ ما** است که تسلیم شده، نه ایتا که رد کرده.
    اشتباه گرفتنشان تو را می‌فرستد دنبال محدودیتی که هرگز اتفاق نیفتاده.
    """
    left = None
    if total is not None:
        left = max(0, _n(total) - _n(sent_before))
    return card(
        "⏸ ارسال متوقف شد",
        [
            ("اکانت", account),
            ("دلیل", sanitize(reason, 200)),
            ("توسط", brake or "یک ترمز ایمنیِ لوکال، نه سرور ایتا"),
            ("قبلش فرستاده", f"{_n(sent_before):,} در **این** اجرا"),
            ("امتحان نشده", f"{left:,}" if left else None),
            ("اقدام", "خودکار متوقف شد؛ کارت‌های خطای بالا می‌گویند چه چیزی مدام "
                      "خطا می‌داد"),
            ("بعدی", "علت را درست کن، بعد دوباره «ارسال» را بزن تا ادامه بدهد"),
            ("زمان", now_hms()),
        ],
        footer="این ترمزِ خودِ پنل است: خطای پیاپیِ زیاد، پس ایستاد تا به یک سشنِ "
               "خراب نکوبد. ایتا لزوماً چیزی را محدود نکرده — برای کدِ واقعی، "
               "کارت‌های خطا را ببین.",
    )


def contacts_probe(account: str, tried: list[dict], chosen: str | None,
                   fallback: bool = False, note: str | None = None) -> str:
    """**دقیقاً** بگو سرور برای اولین دسته‌ی ایمپورت چه جواب داد.

    ساخت مخاطب قبلاً وقتی سرور با هیچ‌کس مطابقت پیدا نمی‌کرد «۰ پیدا شد» گزارش
    می‌داد بدون هیچ توضیحی، که شبیه این بود که جاب اصلاً کاری نکرده. هر سطر پروب
    یک فرمت شماره است که امتحان کردیم، با عددهای خام.

    tried: [{"format": "98"|"+98", "imported": int, "users": int,
             "retry": int, "batch": int, "code": str|None}]
    """
    rows = []
    for t in tried:
        label = f"فرمت {t.get('format', '?')}"
        if t.get("code"):
            rows.append((label, f"خطا: {sanitize(t['code'], 90)}"))
            continue
        detail = (f"imported={t.get('imported', 0)} users={t.get('users', 0)} "
                  f"retry={t.get('retry', 0)} از {t.get('batch', 0)}")
        # پاسخ با سازنده‌ی غیرمنتظره هم شبیه «۰ imported» دیده می‌شود، پس نامش را
        # صریح بگو.
        if t.get("parse_ok") is False:
            cid = t.get("cid")
            detail += f" | پاسخ غیرمنتظره cid={('0x%08x' % cid) if cid else '?'}"
            if t.get("head"):
                detail += f" head={t['head'][:32]}"
        rows.append((label, detail))
    footer = note
    if note:
        pass
    elif chosen:
        footer = f"برای بقیه‌ی این جاب از فرمت شماره‌ی {chosen} استفاده می‌شود."
    elif fallback:
        footer = ("هیچ‌کدام از دو فرمت شماره با کسی مطابقت نکرد، پس جاب به مسیرِ "
                  "اثبات‌شده‌ی افزودن یکی‌یکی سوییچ کرد. اگر آن هم کسی را پیدا نکرد، "
                  "این شماره‌ها صرفاً در ایتا ثبت نیستند.")
    return card(
        "🔬 پروب ایمپورت",
        [("اکانت", account), *rows, ("زمان", now_hms())],
        footer=footer,
    )


def peers_saved(account: str, new_peers: int, total_peers: int,
                source: str = "import") -> str:
    """peers همان چیزی است که فرستنده‌ی بدون-مرورگر (سریع) برای رسیدن به یک مخاطب
    لازم دارد."""
    return card(
        "🔑 peers ذخیره شد",
        [
            ("اکانت", account),
            ("منبع", source),
            ("جدید", new_peers),
            ("مجموع", total_peers),
            ("زمان", now_hms()),
        ],
        footer="این مخاطبین از این به بعد با فرستنده‌ی سریع (بدون مرورگر) قابل "
               "دسترس‌اند.",
    )


def account_deleted(phone: str, removed: list[str]) -> str:
    return card(
        "🗑 اکانت حذف شد",
        [
            ("شماره", phone),
            ("حذف‌شده", "، ".join(removed) if removed else "چیزی پیدا نشد"),
            ("زمان", now_hms()),
        ],
        footer="پروفایل مرورگر، peers ذخیره‌شده و سشن کپچرشده‌اش رفتند.",
    )


def multi_send_finished(accounts: list[dict], sent: int, failed: int, total: int,
                        elapsed: float, kind: str | None = None,
                        engine: str | None = None, stopped: bool = False) -> str:
    """خلاصه‌ی نهاییِ یک ارسال چند-اکانتی (ترکیبی + به‌ازای هر اکانت)."""
    blocked = [a for a in accounts if str(a.get("state")) == "no_targets"]
    bad = [a for a in accounts
           if str(a.get("state")) in ("no_targets", "failed", "limited")]
    if stopped:
        title = "🛑 ارسال چند-اکانتی متوقف شد"
    elif bad:
        # عنوان صادقانه: بعضی اکانت‌ها همه‌چیز را تحویل ندادند.
        title = (f"⚠️ ارسال چند-اکانتی تمام شد — {len(bad)} از {len(accounts)} "
                 f"مشکل داشتند")
    else:
        title = "✅ ارسال چند-اکانتی تمام شد"
    attempted, untouched = coverage(sent, failed, total)
    if untouched and stopped:
        verdict = f"🛑 زودتر تمام شد — {untouched:,} از {total:,} هرگز امتحان نشدند"
    elif untouched:
        verdict = f"⚠️ {untouched:,} از {total:,} هرگز امتحان نشدند"
    elif total:
        verdict = f"✅ همه‌ی {total:,} امتحان شدند"
    else:
        verdict = None

    lines = [
        title,
        DIVIDER,
        bar(attempted, total),
    ]
    if verdict:
        lines.append(verdict)
    lines.extend(_rows([
        ("اکانت‌ها", _account_tally(accounts)),
        ("نوع", kind),
        ("موتور", engine),
        ("تحویل شد", f"✅ {sent:,} از {total:,}  ({_pct(sent, total)})"),
        ("ناموفق", f"✗ {failed:,}  ({_pct(failed, attempted)} از تلاش‌شده‌ها)"
                   if failed else None),
        ("تلاش‌شده", f"{attempted:,} از {total:,}"),
        ("هرگز امتحان نشد", f"{untouched:,}" if untouched else None),
        ("زمان", fmt_duration(elapsed)),
        ("سرعت", pace(attempted, elapsed)),
    ]))
    if accounts:
        lines.append(DIVIDER)
        for i, a in enumerate(accounts, start=1):
            state = str(a.get("state", "done"))
            mark = _STATE_MARK.get(state, "•")
            a_sent, a_total = _n(a.get("sent")), _n(a.get("total"))
            a_failed = _n(a.get("failed"))
            row = f"{i}. {mark} {a.get('phone', '')} · {a_sent}/{a_total}"
            if a_failed:
                row += f" · ✗{a_failed}"
            # برای هر اکانت هم: «امتحان‌شده ولی رد» با «هرگز نرسید» فرق دارد.
            a_left = max(0, a_total - a_sent - a_failed)
            if a_left:
                row += f" · {a_left} امتحان نشده"
            if state in ("no_targets", "failed", "limited", "stopped"):
                row += f" · {_STATE_WORD.get(state, state)}"
            lines.append(row)
    lines.append(DIVIDER)
    if blocked:
        lines.append(f"🚧 {len(blocked)} اکانت **هیچ‌چیز** نفرستادند چون مخاطبی "
                     "ذخیره ندارند. هر کدام را باز کن و «🔄 بروزرسانی مخاطبین» را "
                     "بزن، بعد دوباره بفرست.")
    lines.append(f"🕒 {now_hms()}")
    return "\n".join(lines)
