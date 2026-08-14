"""
gate.py — دروازه‌ی ورودِ مشترک هر اکشن مشتری.
=============================================

**یک** پیاده‌سازی، نه چند کپی. در پروژه‌ی مرجع سه نسخه‌ی واگرای این دروازه وجود
داشت و قواعد بین بخش‌ها بی‌صدا فرق می‌کرد؛ اینجا هر هندلر همین یک تابع را صدا
می‌زند.

ترتیب چک‌ها عمدی است و کل ماجرای «بار روی سرور» همین ترتیب است:

    ۱. مسدود است؟      → return فوری، بی‌صدا. از کش حافظه، نه دیتابیس.
    ۲. حالت تعمیر؟
    ۳. ensure_customer
    ۴. ثبت اکشن + ریت‌لیمیت (لغزان)
    ۵. سقف‌ها (در خودِ هندلر، چون هر کدام پیام خودش را دارد)

مرحله‌ی ۱ **قبل از هر `await` و هر کوئری** است. یک کاربر مسدود نه جواب می‌گیرد، نه
لاگ تولید می‌کند، نه ردیفی می‌نویسد — فقط یک افزایش شمارنده در حافظه.

الگوی استفاده در هر هندلر:

    if not await gate(event):
        return

خودِ `gate` پیام لازم را فرستاده، پس هندلر مسیر خطا ندارد. fail-closed است: هر
استثنای غیرمنتظره‌ای هم `False` برمی‌گرداند.
"""
from __future__ import annotations

import db
import ratelimit
from bot import logbus
from config import config


async def _respond(event, text: str, buttons=None) -> None:
    """جواب می‌دهد، فرق نمی‌کند پیام باشد یا کالبک."""
    try:
        if hasattr(event, "edit") and getattr(event, "data", None) is not None:
            await event.edit(text, buttons=buttons)
        else:
            await event.respond(text, buttons=buttons)
    except Exception:  # noqa: BLE001
        try:
            await event.client.send_message(event.sender_id, text, buttons=buttons)
        except Exception:  # noqa: BLE001
            pass


def label_of(user) -> str:
    """`علی · @ali_x` برای کارت‌های لاگ."""
    name = (getattr(user, "first_name", "") or "").strip()
    uname = (getattr(user, "username", "") or "").strip()
    if name and uname:
        return f"{name} · @{uname}"
    return name or (f"@{uname}" if uname else "بی‌نام")


async def gate(event, *, action: str = "", counted: bool = True) -> bool:
    """اجازه‌ی اجرای یک اکشن. `False` یعنی هندلر باید فوراً return کند.

    `action` برچسبی است که در گروه لاگ و در کارت مسدودی دیده می‌شود، پس فارسیِ
    کوتاه باشد: «افزودن اکانت»، «شروع ارسال»، …

    `counted=False` برای ناوبری خالص است (باز کردن منو، صفحه‌بندی). ناوبری نباید
    شمرده شود، وگرنه مشتری‌ای که فقط پنل را می‌گردد **خودش را مسدود می‌کند**.
    """
    uid = int(getattr(event, "sender_id", 0) or 0)
    if not uid:
        return False

    # ۰) مالک هرگز مسدود یا ریت‌لیمیت نمی‌شود.
    #
    # چرا این استثنا لازم است: مالک ربات مشتری را **تست می‌کند** و طبیعتاً تند
    # دکمه می‌زند. بدونِ این استثنا از سقفِ ۲۰ اکشن در ۶۰ ثانیه می‌گذرد،
    # مسدودی **دائمی** می‌خورد، و از آن لحظه هر اکشن در ربات مشتری **بی‌صدا**
    # رد می‌شود (مسیرِ زیر پیامی نمی‌دهد، چون یک اسپمر نباید هزینه‌ای بسازد).
    # نتیجه‌اش «دکمه را می‌زنم و هیچ اتفاقی نمی‌افتد» بود، بدونِ هیچ سرنخی.
    # مالک تهدیدِ اسپم نیست و ابزارِ رفعِ مسدودی هم دستِ خودش است، پس این چک
    # برایش فقط یک تله است.
    if config.OWNER_ID and uid == int(config.OWNER_ID):
        try:
            user = await event.get_sender()
            db.ensure_customer(uid, (getattr(user, "first_name", "") or ""),
                               (getattr(user, "username", "") or ""))
            if action:
                db.log_event(uid, "action", label=action, counted=False)
        except Exception as exc:  # noqa: BLE001
            print(f"[gate owner] {exc}", flush=True)
        return True

    # ۱) مسدود: از کش حافظه، قبل از هر کوئری یا await. صفر هزینه.
    if ratelimit.is_blocked(uid):
        ratelimit.note_blocked_attempt(uid)
        return False

    try:
        # ۲) حالت تعمیر (فایل flag، پس پروسه‌ی دیگر هم می‌بیندش).
        if db.maintenance_on():
            await _respond(event, logbus.card("🛠 در حال تعمیر", [
                "• ربات موقتاً در حال تعمیر است.",
                "• کمی بعد دوباره امتحان کن.",
            ]))
            return False

        # ۳) مشتری را در اولین اکشنِ دروازه‌دار بساز.
        user = await event.get_sender()
        db.ensure_customer(uid, (getattr(user, "first_name", "") or ""),
                           (getattr(user, "username", "") or ""))

        # ۴) ثبت اکشن، بعد ریت‌لیمیت. ترتیب مهم است: خودِ همین ردیف است که شمرده
        #    می‌شود، پس آنچه در لاگ می‌بینی همان چیزی است که در شمارنده رفته.
        if action:
            db.log_event(uid, "action", label=action, counted=counted)
        if not counted:
            return True

        verdict = ratelimit.check(uid)
        if not verdict["ok"]:
            await _on_auto_block(event, uid, user, verdict["count"])
            return False
        if verdict["warn"]:
            await _warn_owner(uid, user, verdict["count"])
        return True

    except Exception as exc:  # noqa: BLE001 - fail closed
        print(f"[gate] {exc}", flush=True)
        return False


async def _on_auto_block(event, uid: int, user, n: int) -> None:
    """مسدودی خودکار: به مشتری خبر بده، و کارتی به گروه که **کارش را نشان بدهد**."""
    await _respond(event, logbus.card("⛔ حساب تو مسدود شد", [
        "• به‌خاطر فعالیت بیش از حد، دسترسی‌ات بسته شد.",
        "• برای رفع مسدودی با پشتیبانی تماس بگیر.",
    ]))

    cust = db.get_customer(uid) or {}
    acc_n = db.count_accounts(uid)
    stopped = stop_customer_jobs(uid)

    rows = [
        f"• اکشن‌ها: {n} در {config.RATE_LIMIT_WINDOW} ثانیه · سقف {config.RATE_LIMIT_MAX}",
        f"• عضویت: {_age_words(cust.get('created_at'))}",
        f"• جاب‌ها: {stopped} متوقف شد" if stopped else "• جاب‌ها: هیچ در حال اجرا نبود",
        f"• اکانت‌ها: {acc_n} · نگه داشته شد",
    ]
    acts = ratelimit.offending_actions(uid)
    if acts:
        rows.append("• آخرین اکشن‌ها:")
        for a in acts:
            rows.append(f"•   {_hhmmss(a.get('at'))} {a.get('label') or a.get('kind')}")

    await logbus.emit(
        kind="auto_block", title="🚫 مسدودی خودکار · اسپم", rows=rows,
        customer_id=uid, customer_label=label_of(user),
        log_label="مسدودی خودکار", counted=False,
        safe_title="",  # مشتری نسخه‌ی کارت را نمی‌گیرد؛ پیام ساده‌اش را بالا گرفت
        summary=f"{n} اکشن · {config.RATE_LIMIT_WINDOW}s",
    )


async def _warn_owner(uid: int, user, n: int) -> None:
    """هشدار ۷۵٪ — تا مشتری خوبی که به سقف نزدیک می‌شود **قبل** از مسدودی دیده شود."""
    cust = db.get_customer(uid) or {}
    await logbus.emit(
        kind="rate_warn", title="⚠️ نزدیک سقف اکشن",
        rows=[
            f"• اکشن‌ها: {n} در {config.RATE_LIMIT_WINDOW} ثانیه · سقف {config.RATE_LIMIT_MAX}",
            f"• عضویت: {_age_words(cust.get('created_at'))}",
            f"• اکانت‌ها: {db.count_accounts(uid)}",
            "• اگر ادامه بدهد مسدود می‌شود",
        ],
        customer_id=uid, customer_label=label_of(user),
        log_label="هشدار نزدیک سقف", counted=False,
    )


def stop_customer_jobs(uid: int, force: bool = True) -> int:
    """همه‌ی جاب‌های در حال اجرای یک مشتری را متوقف می‌کند. تعداد را برمی‌گرداند.

    مسدودی باید جاب در جریان را هم بخواباند، وگرنه مسدودکردن کسی که وسط یک ارسال
    دو ساعته است عملاً کاری نمی‌کند.
    """
    stopped = 0
    try:
        from bot.runner import manager
        for job in db.running_jobs(uid):
            jid = str(job.get("job_id") or "")
            if not jid:
                continue
            try:
                if manager.stop(jid, force=force):
                    stopped += 1
            except Exception:  # noqa: BLE001
                pass
            db.finish_job(jid, "stopped", last_error="مسدود شد")
    except Exception as exc:  # noqa: BLE001
        print(f"[gate stop_jobs] {exc}", flush=True)
    return stopped


# --------------------------------------------------------------------------- #
# سقف‌ها — هر کدام پیام خودش را دارد، پس هندلر صدایشان می‌زند نه دروازه
# --------------------------------------------------------------------------- #
def account_cap_reached(uid: int) -> bool:
    return db.count_accounts(uid) >= config.ACCOUNT_CAP


def add_quota(uid: int) -> dict:
    """{"used", "left", "next_in"} برای سهمیه‌ی افزودن اکانت (پنجره‌ی غلتان)."""
    used = db.adds_in_window(uid)
    return {
        "used": used,
        "left": max(0, config.DAILY_ADD_QUOTA - used),
        "next_in": db.next_add_slot(uid),
    }


def human_wait(seconds: float) -> str:
    """`۶ ساعت و ۲۰ دقیقه` — عددی که در پیام سهمیه نشان داده می‌شود."""
    s = int(max(0, seconds))
    if s < 60:
        return "کمتر از یک دقیقه"
    h, m = divmod(s // 60, 60)
    if h and m:
        return f"{h} ساعت و {m} دقیقه"
    if h:
        return f"{h} ساعت"
    return f"{m} دقیقه"


def _age_words(ts) -> str:
    if not ts:
        return "نامعلوم"
    import time as _t
    d = max(0, _t.time() - float(ts))
    if d < 3600:
        return f"{int(d / 60)} دقیقه پیش ⚠️"
    if d < 86400:
        return f"{int(d / 3600)} ساعت پیش"
    return f"{int(d / 86400)} روز پیش"


def _hhmmss(ts) -> str:
    if not ts:
        return "--:--:--"
    import time as _t
    return _t.strftime("%H:%M:%S", _t.localtime(float(ts)))
