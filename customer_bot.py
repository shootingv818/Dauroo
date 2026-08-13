"""
customer_bot.py — رباتی که مشتری‌ها استارت می‌زنند.
=================================================

مشتری فقط این کارها را دارد:

    افزودن اکانت · ارسال · ساخت مخاطب · بروزرسانی مخاطبین ·
    ایمپورت تصاویر → PDF · تست ارسال · تنظیم محتوای خودش

**هیچ تنظیمی اینجا نیست.** موتور، تأخیر، هم‌زمانی، سقف مرورگر و همه‌ی پیچ‌های
دیگر دست مالک است (`owner_bot.py`)، پس مشتری نه می‌بیندشان نه می‌تواند عوضشان کند.

سه چیزی که در هر هندلر رعایت می‌شود:

* **دروازه اول.** `if not await gate(event, action=...): return` — و `gate` خودش
  پیام لازم را فرستاده، پس مسیر خطا اینجا نیست.
* **همه‌چیز scope‌شده.** هیچ‌جا `account_id` بدون `customer_id` خوانده نمی‌شود؛
  `db.get_account(uid, aid)` اگر اکانت مال او نباشد `None` می‌دهد.
* **مشتری جزئیات فنی نمی‌بیند.** خطاها از `logbus.emit_error` می‌روند: کارت کامل
  به گروه لاگ، یک جمله‌ی امن + کد پیگیری به مشتری.

جریان لاگین با **پروفایل موقت** کار می‌کند: کروم با نام `_pending_<...>` باز
می‌شود و فقط در صورت موفقیت به کلید واقعی rename می‌شود. لاگین داخل مرورگر انجام
می‌شود، پس نمی‌توان «اول لاگین، بعد پروفایل» کرد — ولی با rename اتمی، هیچ‌وقت یک
پروفایل نیمه‌ساخته با نام واقعی دیده نمی‌شود، و در شکست/تایم‌اوت کامل پاک می‌شود.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import time
import uuid

from telethon import Button, TelegramClient, events

import db
import gate
import ratelimit
from bot import app as shared
from bot import cards, contacts_store, logbus
from bot.runner import manager
from bot.store import store
from config import config

LINE = cards.DIVIDER

config.DATA_DIR.mkdir(parents=True, exist_ok=True)
bot = TelegramClient(str(config.DATA_DIR / "customer_bot"),
                     config.API_ID, config.API_HASH)

#: وضعیت گفتگو: uid -> {"step": ..., ...}
state: dict = {}

#: انتخاب اکانت برای ارسال: uid -> [account_id, ...]
picked: dict = {}

#: دروازه‌ی پذیرش مرورگر. `capture/pool.py` سقف `max_open` دارد ولی آن فقط
#: سشن‌های **گرمِ آماده** را می‌بندد و جلوی یک لانچ جدید را نمی‌گیرد، پس بدون این
#: سمافور N مشتری یعنی N کروم و مرگ هاست.
_slots = asyncio.Semaphore(max(1, config.BROWSER_SLOTS))
_waiting = 0


# =========================================================================== #
# کمکی‌ها
# =========================================================================== #
def _mask(phone) -> str:
    return logbus.mask_phone(phone)


def _fa(n) -> str:
    """عدد با جداکننده‌ی هزارگان."""
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


async def _label(uid: int) -> str:
    try:
        return gate.label_of(await bot.get_entity(uid))
    except Exception:  # noqa: BLE001
        cust = db.get_customer(uid) or {}
        return cust.get("name") or str(uid)


async def _respond(event, text, buttons=None):
    try:
        if getattr(event, "data", None) is not None:
            await event.edit(text, buttons=buttons)
        else:
            await event.respond(text, buttons=buttons)
    except Exception:  # noqa: BLE001
        try:
            await bot.send_message(event.sender_id, text, buttons=buttons)
        except Exception:  # noqa: BLE001
            pass


def _acc_mark(a: dict) -> str:
    """⚡ آماده‌ی سریع · 🌐 فقط مرورگر · ⚠️ بی‌مخاطب.

    تفکیک سه‌حالته عمدی است: `_can_run_browserless` در موتور می‌خواهد **همه‌ی**
    مخاطبین `access_hash` داشته باشند، پس یک مخاطبِ اسکرول‌شده کل مسیر سریع آن
    اکانت را می‌بندد. این علامت تنها جایی است که مشتری زودتر از ارسال می‌فهمد.
    """
    c = int(a.get("contacts") or 0)
    h = int(a.get("with_hash") or 0)
    if (a.get("status") or "active") != "active":
        return "💀"
    if not c:
        return "⚠️"
    return "⚡" if h >= c else "🌐"


# =========================================================================== #
# پنل خانه
# =========================================================================== #
def home_text(uid: int) -> str:
    t = db.customer_totals(uid)
    s = db.customer_settings(uid)
    q = gate.add_quota(uid)

    content = "تنظیم نشده"
    if s.get("content_kind") == "text" and s.get("content_text"):
        content = f"📝 متن · {len(s['content_text'])} کاراکتر"
    elif s.get("content_kind") == "file" and s.get("content_name"):
        content = f"📎 {s['content_name']}"
        if s.get("content_caption"):
            content += " + کپشن"

    breakdown = []
    if t["fast"]:
        breakdown.append(f"⚡ {t['fast']} آماده")
    if t["browser"]:
        breakdown.append(f"🌐 {t['browser']}")
    if t["nocontacts"]:
        breakdown.append(f"⚠️ {t['nocontacts']}")

    rows = [
        f"• اکانت‌ها: {t['accounts']} از {config.ACCOUNT_CAP}",
    ]
    if breakdown:
        rows.append("   " + " · ".join(breakdown))
    rows += [
        f"• مخاطبین: {_fa(t['contacts'])}",
        f"• محتوا: {content}",
        f"• سهمیه امروز: {q['used']} از {config.DAILY_ADD_QUOTA}",
    ]
    return ("🤖 پنل من\n" + LINE + "\n\n" + "\n".join(rows) +
            "\n\n" + LINE + f"\n▪ کل ارسال: {_fa(t['sent'])}")


def home_kb() -> list:
    return [
        [Button.inline("🚀 ارسال", b"send"),
         Button.inline("➕ افزودن اکانت", b"addacc")],
        [Button.inline("👤 اکانت‌های من", b"accs:0"),
         Button.inline("✍️ محتوا", b"content")],
        [Button.inline("🧲 ساخت مخاطب", b"cb:0"),
         Button.inline("🖼 ایمپورت تصاویر", b"px:0")],
        [Button.inline("📊 آمار من", b"stats"),
         Button.inline("📖 راهنما", b"help")],
        [Button.url("🆘 پشتیبانی", f"tg://user?id={config.OWNER_ID}")],
    ]


@bot.on(events.NewMessage(pattern=r"^/start", func=lambda e: e.is_private))
async def on_start(event):
    uid = int(event.sender_id)
    # کاربر مسدود: قبل از هر کوئری، از کش حافظه. صفر هزینه.
    if ratelimit.is_blocked(uid):
        ratelimit.note_blocked_attempt(uid)
        return
    if db.maintenance_on():
        await event.respond(logbus.card("🛠 در حال تعمیر",
                                        ["• ربات موقتاً در حال تعمیر است."]))
        return

    user = await event.get_sender()
    fresh = db.get_customer(uid) is None
    db.ensure_customer(uid, getattr(user, "first_name", "") or "",
                       getattr(user, "username", "") or "")
    state.pop(uid, None)

    if fresh:
        await logbus.emit(kind="start", title="🟢 استارت · مشتری تازه",
                          rows=[f"• زبان: {getattr(user, 'lang_code', '') or '—'}"],
                          customer_id=uid, customer_label=gate.label_of(user),
                          log_label="استارت (تازه)", counted=False)
    else:
        t = db.customer_totals(uid)
        cust = db.get_customer(uid) or {}
        await logbus.emit(
            kind="start", title="🟢 استارت · بازگشت",
            rows=[f"• اکانت‌ها: {t['accounts']} · کل ارسال: {_fa(t['sent'])}",
                  f"• آخرین فعالیت: {gate._age_words(cust.get('last_seen'))}"],
            customer_id=uid, customer_label=gate.label_of(user),
            log_label="استارت (بازگشت)", counted=False)

    await event.respond(home_text(uid), buttons=home_kb())


@bot.on(events.CallbackQuery(data=b"home"))
async def on_home(event):
    if not await gate.gate(event, counted=False):
        return
    state.pop(int(event.sender_id), None)
    await _respond(event, home_text(event.sender_id), buttons=home_kb())


@bot.on(events.CallbackQuery(data=b"noop"))
async def on_noop(event):
    try:
        await event.answer()
    except Exception:  # noqa: BLE001
        pass


@bot.on(events.CallbackQuery(data=b"help"))
async def on_help(event):
    if not await gate.gate(event, counted=False):
        return
    await _respond(event, "📖 راهنما\n" + LINE + "\n\n" + "\n".join([
        "• ۱. «افزودن اکانت» → شماره بده → کد ایتا را بفرست",
        "• ۲. «محتوا» → متن یا فایلی که می‌خواهی بفرستی",
        "• ۳. «تست ارسال» → اول برای خودت، مطمئن شو",
        "• ۴. «ارسال» → اکانت را انتخاب کن و شروع کن",
        "",
        f"• روزی {config.DAILY_ADD_QUOTA} اکانت، تا سقف {config.ACCOUNT_CAP}",
        "• ارسال محدودیت روزانه ندارد",
        f"• فایل باید کمتر از {config.FILE_MAX_MB} مگابایت باشد",
        f"• هر ارسال تا {config.SEND_MAX_ACCOUNTS} اکانت",
        "",
        "• ⚡ آماده‌ی ارسال سریع",
        "• 🌐 فقط موتور مرورگر (یک بار «بروزرسانی مخاطبین» بزن)",
        "• ⚠️ بدون مخاطب",
    ]) + "\n\n" + LINE + "\n▪ مشکلی بود؟ کد پیگیری را به پشتیبانی بده",
        buttons=[[Button.inline("🏠 خانه", b"home")]])


@bot.on(events.CallbackQuery(data=b"stats"))
async def on_stats(event):
    if not await gate.gate(event, counted=False):
        return
    uid = int(event.sender_id)
    t = db.customer_totals(uid)
    q = gate.add_quota(uid)
    cust = db.get_customer(uid) or {}
    rows = [
        f"• اکانت‌ها: {t['accounts']} از {config.ACCOUNT_CAP}",
        f"• مخاطبین: {_fa(t['contacts'])}",
        f"• سهمیه امروز: {q['used']} از {config.DAILY_ADD_QUOTA}",
    ]
    if q["next_in"]:
        rows.append(f"• سهمیه بعدی: {gate.human_wait(q['next_in'])} دیگر")
    await _respond(event, "📊 آمار من\n" + LINE + "\n\n" + "\n".join(rows) +
                   "\n\n" + LINE + f"\n▪ کل ارسال: {_fa(t['sent'])} · عضویت "
                   f"{gate._age_words(cust.get('created_at'))}",
                   buttons=[[Button.inline("🏠 خانه", b"home")]])


# =========================================================================== #
# اکانت‌های من
# =========================================================================== #
@bot.on(events.CallbackQuery(pattern=rb"^accs:(\d+)$"))
async def on_accounts(event):
    if not await gate.gate(event, counted=False):
        return
    uid = int(event.sender_id)
    accs = db.list_accounts(uid)
    page = int(event.pattern_match.group(1))
    shown, page, pages = shared.page_slice(accs, page)

    rows = [f"• {len(accs)} اکانت · صفحه {page + 1} از {pages}"] if accs else \
           ["• هنوز اکانتی اضافه نکردی."]
    kb = [[Button.inline(f"{_acc_mark(a)} {_mask(a['phone'])} · "
                         f"{_fa(a.get('contacts') or 0)}",
                         f"acc:{a['id']}".encode())] for a in shown]
    nav = shared.pager_row("accs:", page, pages)
    if nav:
        kb.append(nav)
    kb.append([Button.inline("➕ افزودن اکانت", b"addacc"),
               Button.inline("🏠 خانه", b"home")])
    await _respond(event, "👤 اکانت‌های من\n" + LINE + "\n\n" + "\n".join(rows) +
                   "\n\n" + LINE + "\n▪ ⚡ آماده · 🌐 فقط مرورگر · ⚠️ بی‌مخاطب",
                   buttons=kb)


@bot.on(events.CallbackQuery(pattern=rb"^acc:(\d+)$"))
async def on_account(event):
    if not await gate.gate(event, counted=False):
        return
    uid = int(event.sender_id)
    aid = int(event.pattern_match.group(1))
    a = db.get_account(uid, aid)          # scope‌شده: مال او نباشد None است
    if not a:
        await event.answer("اکانت پیدا نشد.", alert=True)
        return
    key = str(aid)
    busy = manager.is_busy(key)
    saved = contacts_store.count(key)
    c, h = int(a.get("contacts") or 0), int(a.get("with_hash") or 0)
    fast = bool(c and h >= c)

    rows = [
        f"• وضعیت: {'⏳ مشغول' if busy else ('⚡ آماده‌ی ارسال سریع' if fast else ('🌐 فقط موتور مرورگر' if c else '⚠️ بدون مخاطب'))}",
        f"• مخاطبین: {_fa(c)}" + (f" · چت‌ها: {_fa(a.get('pvs'))}" if a.get("pvs") else ""),
        f"• ذخیره‌شده: {_fa(saved)}" if saved else None,
    ]
    rows = [r for r in rows if r]
    kb = [
        [Button.inline("🚀 ارسال", f"send1:{aid}".encode()),
         Button.inline("🧪 تست ارسال", f"dry:{aid}".encode())],
        [Button.inline("🔄 بروزرسانی مخاطبین", f"upd:{aid}".encode()),
         Button.inline("🧲 ساخت مخاطب", f"cbacc:{aid}".encode())],
        [Button.inline("🖼 ایمپورت تصاویر", f"pxacc:{aid}".encode())],
        [Button.inline("🗑 حذف اکانت", f"del:{aid}".encode())],
        [Button.inline("🔙 اکانت‌ها", b"accs:0"),
         Button.inline("🏠 خانه", b"home")],
    ]
    if busy:
        kb.insert(0, [Button.inline("⛔ توقف", f"stop:{aid}".encode())])
    await _respond(event, f"👤 {_mask(a['phone'])}\n" + LINE + "\n\n" +
                   "\n".join(rows) + "\n\n" + LINE +
                   f"\n▪ کل ارسال این اکانت: {_fa(a.get('total_sent') or 0)}",
                   buttons=kb)


@bot.on(events.CallbackQuery(pattern=rb"^del:(\d+)$"))
async def on_delete_ask(event):
    if not await gate.gate(event, action="حذف اکانت"):
        return
    uid, aid = int(event.sender_id), int(event.pattern_match.group(1))
    a = db.get_account(uid, aid)
    if not a:
        await event.answer("اکانت پیدا نشد.", alert=True)
        return
    await _respond(event, logbus.card("🗑 حذف اکانت", [
        f"• شماره: {_mask(a['phone'])}",
        "",
        "• پروفایل مرورگر، مخاطبین و سشنش پاک می‌شود.",
        "• این کار برگشت‌پذیر نیست.",
    ]), buttons=[[Button.inline("✅ بله، حذف کن", f"delx:{aid}".encode())],
                 [Button.inline("❌ لغو", f"acc:{aid}".encode())]])


@bot.on(events.CallbackQuery(pattern=rb"^delx:(\d+)$"))
async def on_delete_do(event):
    if not await gate.gate(event, action="تأیید حذف اکانت"):
        return
    uid, aid = int(event.sender_id), int(event.pattern_match.group(1))
    a = db.get_account(uid, aid)
    if not a:
        await event.answer("اکانت پیدا نشد.", alert=True)
        return
    if manager.is_busy(str(aid)):
        await event.answer("این اکانت الان مشغول است.", alert=True)
        return
    removed = shared.delete_account_profile(str(aid))
    db.delete_account(uid, aid)
    n = db.count_accounts(uid)
    await logbus.emit(
        kind="account_deleted", title="🗑 اکانت حذف شد",
        rows=[f"• حذف‌شده: {'، '.join(removed) if removed else 'چیزی پیدا نشد'}",
              f"• اکانت‌ها: {n} از {config.ACCOUNT_CAP}"],
        customer_id=uid, customer_label=await _label(uid), phone=a["phone"],
        log_label="حذف اکانت",
        safe_title="🗑 اکانت حذف شد",
        safe_rows=[f"• شماره: {_mask(a['phone'])}"],
        safe_footer=f"▪ اکانت‌های تو: {n} از {config.ACCOUNT_CAP}")
    await _respond(event, home_text(uid), buttons=home_kb())


# =========================================================================== #
# افزودن اکانت — با پروفایل موقت
# =========================================================================== #
@bot.on(events.CallbackQuery(data=b"addacc"))
async def on_add_account(event):
    if not await gate.gate(event, action="افزودن اکانت"):
        return
    uid = int(event.sender_id)

    if gate.account_cap_reached(uid):
        await _respond(event, logbus.card("🚫 به سقف اکانت رسیدی", [
            f"• اکانت‌های تو: {db.count_accounts(uid)} از {config.ACCOUNT_CAP}",
            "",
            "• برای اکانت جدید، اول یکی را حذف کن.",
        ]), buttons=[[Button.inline("👤 اکانت‌های من", b"accs:0")],
                     [Button.inline("🏠 خانه", b"home")]])
        await logbus.emit(kind="cap_hit", title="🚫 سقف اکانت خورد",
                          rows=[f"• اکانت‌ها: {db.count_accounts(uid)} از {config.ACCOUNT_CAP}"],
                          customer_id=uid, customer_label=await _label(uid),
                          log_label="سقف اکانت", counted=False)
        return

    q = gate.add_quota(uid)
    if q["left"] <= 0:
        # توضیح بده، نه اینکه فقط رد کنی — و عدد واقعیِ آزادسازی را بگو.
        await _respond(event, logbus.card("⏳ سهمیه امروزت پر شد", [
            f"• امروز: {q['used']} از {config.DAILY_ADD_QUOTA} اکانت",
            f"• سهمیه بعدی: {gate.human_wait(q['next_in'])} دیگر",
            f"• اکانت‌های تو: {db.count_accounts(uid)} از {config.ACCOUNT_CAP}",
            "",
            "• تا اون موقع با اکانت‌های فعلیت کار کن.",
        ]), buttons=[[Button.inline("🏠 خانه", b"home")]])
        await logbus.emit(
            kind="quota_hit", title="⏳ سهمیه پر · افزودن اکانت",
            rows=[f"• سهمیه: {q['used']} از {config.DAILY_ADD_QUOTA} در ۲۴ ساعت",
                  f"• آزادسازی: {gate.human_wait(q['next_in'])}",
                  f"• اکانت‌ها: {db.count_accounts(uid)} از {config.ACCOUNT_CAP}"],
            customer_id=uid, customer_label=await _label(uid),
            log_label="سهمیه پر", counted=False)
        return

    state[uid] = {"step": "phone"}
    await _respond(event, logbus.card("➕ افزودن اکانت", [
        "• شماره‌ی اکانت ایتا را بفرست.",
        "• مثال: 09121234567",
        "",
        f"• سهمیه امروز: {q['used']} از {config.DAILY_ADD_QUOTA}",
    ]), buttons=[[Button.inline("❌ لغو", b"home")]])


async def _do_login(uid: int, phone: str) -> None:
    """لاگین با پروفایل موقت؛ در موفقیت rename، در شکست پاک.

    مرورگر تا `config.LOGIN_TTL` ثانیه (پیش‌فرض ۳۲۰) منتظر کد می‌ماند و آن انتظار
    **داخل** اجاره‌ی pool است، پس تایم‌اوت مرورگر را آزاد می‌کند.
    """
    global _waiting
    trace = logbus.new_trace()
    label = await _label(uid)
    staging = f"_pending_{phone}_{uuid.uuid4().hex[:6]}"
    live = shared.LiveCard(uid)
    report = shared.make_report(bot, uid)

    _waiting += 1
    try:
        if _slots.locked():
            await bot.send_message(uid, logbus.card("⏳ در نوبت", [
                f"• شماره: {_mask(phone)}",
                f"• تخمین: حدود {max(1, _waiting)} دقیقه",
                "",
                "• همین‌جا منتظر بمان، خودم خبر می‌دهم.",
            ]))
            await logbus.emit(
                kind="queued", title="⏳ در نوبت · افزودن اکانت",
                rows=[f"• در انتظار: {_waiting}",
                      f"• اسلات‌ها: {config.BROWSER_SLOTS}"],
                customer_id=uid, customer_label=label, phone=phone, trace=trace,
                log_label="در نوبت", counted=False)
        async with _slots:
            state[uid] = {"step": "code", "phone": phone, "acct": staging,
                          "trace": trace}
            ok = await manager.start_bridge_login(
                staging, phone, report, live=live)
            if not ok:
                raise RuntimeError("busy")
            # جاب لاگین خودش کارت‌هایش را می‌فرستد. اینجا فقط منتظر نتیجه‌اش
            # می‌مانیم تا بتوانیم پروفایل را ارتقا یا پاک کنیم.
            deadline = time.time() + config.LOGIN_TTL + 60
            while time.time() < deadline:
                await asyncio.sleep(2)
                if not manager.is_busy(staging):
                    break
            logged_in = config.profile_dir(staging).is_dir() and \
                manager.login_stage(staging) in (None, "done")
    except Exception as exc:  # noqa: BLE001
        logged_in = False
        await logbus.emit_error(where="login", customer_id=uid,
                                customer_label=label, phone=phone, err=exc,
                                phase="start", safe_kind="generic", trace=trace)
    finally:
        _waiting = max(0, _waiting - 1)
        state.pop(uid, None)

    if logged_in:
        aid = db.add_account(uid, phone)
        try:
            src, dst = config.profile_dir(staging), config.profile_dir(str(aid))
            if dst.is_dir():
                shutil.rmtree(dst)
            os.rename(src, dst)          # اتمی: هیچ‌وقت پروفایل نیمه‌ساخته با نام واقعی
        except OSError as exc:
            print(f"[login promote] {exc}", flush=True)
        saved = contacts_store.count(staging) or 0
        items = contacts_store.items(staging) if saved else []
        with_hash = sum(1 for c in items if c.get("access_hash") and c.get("peer_id"))
        # فایل‌های per-account با نام موقت ساخته شده‌اند؛ با کلید واقعی دوباره بنویس.
        if items:
            contacts_store.save(str(aid), items)
            contacts_store.forget(staging)
        db.set_account_meta(aid, contacts=saved, with_hash=with_hash)
        n = db.count_accounts(uid)
        q = gate.add_quota(uid)
        fast = bool(saved and with_hash >= saved)
        await logbus.emit(
            kind="account_added", title="✅ اکانت اضافه شد",
            rows=[f"• مخاطبین: {_fa(saved)}" +
                  (" · همه با access_hash ✅" if fast else
                   f" ({_fa(with_hash)} با access_hash ⚠️)" if saved else ""),
                  f"• موتور: {'⚡ سریع (آماده)' if fast else '🌐 فقط مرورگر'}",
                  f"• اکانت‌ها: {n} از {config.ACCOUNT_CAP} · "
                  f"سهمیه: {q['used']} از {config.DAILY_ADD_QUOTA}"],
            customer_id=uid, customer_label=label, phone=phone, trace=trace,
            log_label="اکانت اضافه شد",
            safe_title="✅ اکانت اضافه شد",
            safe_rows=[f"• شماره: {_mask(phone)}",
                       f"• مخاطبین: {_fa(saved)}"],
            safe_footer=f"▪ آماده‌ی ارسال · {n} از {config.ACCOUNT_CAP} اکانت")
        await bot.send_message(uid, home_text(uid), buttons=home_kb())
    else:
        try:
            shutil.rmtree(config.profile_dir(staging), ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
        await logbus.emit(
            kind="login_failed", title="⚠️ لاگین ناموفق",
            rows=[f"• مرحله: {manager.login_stage(staging) or 'نامعلوم'}",
                  f"• پروفایل: {staging} پاک شد ✅"],
            customer_id=uid, customer_label=label, phone=phone, trace=trace,
            log_label="لاگین ناموفق", counted=False,
            safe_title="❌ اکانت اضافه نشد",
            safe_rows=[f"• شماره: {_mask(phone)}",
                       "• علت: لاگین کامل نشد",
                       "• اقدام: دوباره امتحان کن",
                       "",
                       "• چیزی نیمه‌کاره نموند، همه‌چیز پاک شد."],
            safe_footer=f"▪ 🔖 {trace}")
        await bot.send_message(uid, home_text(uid), buttons=home_kb())


# =========================================================================== #
# محتوا
# =========================================================================== #
@bot.on(events.CallbackQuery(data=b"content"))
async def on_content(event):
    if not await gate.gate(event, counted=False):
        return
    uid = int(event.sender_id)
    s = db.customer_settings(uid)
    rows = []
    if s.get("content_kind") == "text" and s.get("content_text"):
        rows.append(f"• الان: 📝 متن · {len(s['content_text'])} کاراکتر")
    elif s.get("content_kind") == "file" and s.get("content_name"):
        mb = (int(s.get("content_size") or 0)) / 1048576
        rows.append(f"• الان: 📎 {s['content_name']} · {mb:.1f} مگابایت")
        if s.get("content_caption"):
            rows.append(f"• کپشن: {len(s['content_caption'])} کاراکتر")
    else:
        rows.append("• هنوز محتوایی تنظیم نشده")
    await _respond(event, "✍️ محتوا\n" + LINE + "\n\n" + "\n".join(rows) +
                   "\n\n" + LINE + "\n▪ همین محتوا برای همه‌ی ارسال‌هایت استفاده می‌شود",
                   buttons=[[Button.inline("📝 متن", b"c:text"),
                             Button.inline("📎 فایل", b"c:file")],
                            [Button.inline("🗑 پاک کردن", b"c:clear")],
                            [Button.inline("🏠 خانه", b"home")]])


@bot.on(events.CallbackQuery(pattern=rb"^c:(text|file|clear)$"))
async def on_content_pick(event):
    kind = event.pattern_match.group(1).decode()
    if not await gate.gate(event, action=f"محتوا · {kind}"):
        return
    uid = int(event.sender_id)
    if kind == "clear":
        s = db.customer_settings(uid)
        old = s.get("content_path") or ""
        if old and os.path.exists(old):
            try:
                os.remove(old)
            except OSError:
                pass
        db.set_customer_setting(uid, content_kind="", content_text="",
                                content_path="", content_name="",
                                content_caption="", content_sha256="",
                                content_size=0)
        await logbus.emit(kind="content_cleared", title="🗑 محتوا پاک شد", rows=[],
                          customer_id=uid, customer_label=await _label(uid),
                          log_label="محتوا پاک شد")
        await _respond(event, home_text(uid), buttons=home_kb())
        return
    state[uid] = {"step": f"await_{kind}"}
    if kind == "text":
        await _respond(event, logbus.card("📝 متن", [
            "• متنی که می‌خواهی فرستاده شود را بفرست."]),
            buttons=[[Button.inline("❌ لغو", b"content")]])
    else:
        await _respond(event, logbus.card("📎 فایل", [
            "• فایل را بفرست (می‌توانی کپشن هم بگذاری).",
            f"• حداکثر {config.FILE_MAX_MB} مگابایت.",
        ]), buttons=[[Button.inline("❌ لغو", b"content")]])


# =========================================================================== #
# روتر پیام‌های متنی / فایل
# =========================================================================== #
@bot.on(events.NewMessage(func=lambda e: e.is_private))
async def on_message(event):
    uid = int(event.sender_id)
    if ratelimit.is_blocked(uid):
        ratelimit.note_blocked_attempt(uid)
        return
    txt = (event.raw_text or "").strip()
    if txt.startswith("/"):
        return                      # /start هندلر خودش را دارد

    st = state.get(uid)
    if not st:
        # پیام غیرمنتظره: ارزش لاگ‌کردن دارد — اگر چند مشتری پیام آزاد می‌فرستند،
        # یعنی پنل گیج‌کننده است و راهنما باید بهتر شود.
        if txt and await gate.gate(event, action="پیام غیرمنتظره"):
            await logbus.emit(kind="stray", title="❓ پیام غیرمنتظره",
                              rows=[f"• متن: {cards.sanitize(txt, 200)}"],
                              customer_id=uid, customer_label=await _label(uid),
                              log_label="پیام غیرمنتظره", counted=False)
            await event.respond(home_text(uid), buttons=home_kb())
        return

    step = st.get("step")

    if step == "phone":
        if not await gate.gate(event, action="ارسال شماره"):
            return
        phone = shared.account_name_for_phone(txt)
        if len(phone) < 10:
            await event.respond("شماره درست نیست. مثال: 09121234567")
            return
        if db.phone_taken(uid, phone):
            await event.respond("این شماره را قبلاً اضافه کردی.")
            state.pop(uid, None)
            return
        await logbus.emit(kind="phone_given", title="📱 شماره داد · افزودن اکانت",
                          rows=[f"• اکانت‌ها: {db.count_accounts(uid)} از {config.ACCOUNT_CAP}"],
                          customer_id=uid, customer_label=await _label(uid),
                          phone=phone, log_label="شماره داد", counted=False)
        asyncio.create_task(_do_login(uid, phone))
        return

    if step == "code":
        if not await gate.gate(event, action="ارسال کد"):
            return
        code = "".join(ch for ch in txt if ch.isdigit())
        if not code:
            await event.respond("فقط عدد بفرست.")
            return
        res = manager.submit_login_code(st.get("acct", ""), code)
        if res != "ok":
            await event.respond("الان منتظر کد نیستم. دوباره «افزودن اکانت» را بزن.")
        return

    if step == "await_text":
        if not await gate.gate(event, action="ست متن"):
            return
        s = db.customer_settings(uid)
        old = (s.get("content_text") or "")[:60]
        db.set_customer_setting(uid, content_kind="text", content_text=txt,
                                content_path="", content_name="",
                                content_size=0, content_sha256="")
        state.pop(uid, None)
        await logbus.emit(
            kind="text_set", title="✍️ متن ست شد",
            rows=[f"• طول: {len(txt)} کاراکتر",
                  f"• متن قبلی: «{old}…»" if old else None,
                  LINE, cards.sanitize(txt, 400)],
            customer_id=uid, customer_label=await _label(uid),
            log_label="ست متن", summary=txt[:200],
            safe_title="✅ متن ذخیره شد",
            safe_rows=[f"• طول: {len(txt)} کاراکتر"],
            safe_footer="▪ با 🧪 تست ارسال امتحانش کن")
        await event.respond(home_text(uid), buttons=home_kb())
        return

    if step == "await_file":
        if not event.file:
            await event.respond("فایل بفرست.")
            return
        if not await gate.gate(event, action="ست فایل"):
            return
        size = int(event.file.size or 0)
        name = event.file.name or "file"
        if size > config.FILE_MAX_MB * 1048576:
            await event.respond(logbus.card("🚫 فایل قبول نشد", [
                f"• نام: {name}",
                f"• حجم: {size / 1048576:.1f} مگابایت",
                f"• سقف: {config.FILE_MAX_MB} مگابایت",
                "",
                "• فایل کوچک‌تری بفرست.",
            ]))
            await logbus.emit(
                kind="file_rejected", title="🚫 فایل رد شد · بزرگ‌تر از سقف",
                rows=[f"• نام: {name}",
                      f"• حجم: {size / 1048576:.1f} مگابایت · سقف {config.FILE_MAX_MB}"],
                customer_id=uid, customer_label=await _label(uid),
                log_label="فایل رد شد", counted=False)
            return

        s = db.customer_settings(uid)
        old_path, old_name = s.get("content_path") or "", s.get("content_name") or ""
        cdir = config.DATA_DIR / "content" / str(uid)
        cdir.mkdir(parents=True, exist_ok=True)
        path = str(cdir / name)
        await event.download_media(file=path)
        if old_path and old_path != path and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass
        sha = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                sha.update(chunk)
        digest = sha.hexdigest()
        caption = (event.raw_text or "").strip()
        db.set_customer_setting(uid, content_kind="file", content_path=path,
                                content_name=name, content_caption=caption,
                                content_sha256=digest, content_size=size,
                                content_text="")
        state.pop(uid, None)

        trace = await logbus.emit(
            kind="file_set", title="📎 فایل عوض شد",
            rows=[f"• نام: {name}",
                  f"• حجم: {size / 1048576:.1f} مگابایت · {_fa(size)} بایت",
                  f"• sha256: {digest[:16]}…",
                  f"• کپشن: {len(caption)} کاراکتر" if caption else None,
                  f"• فایل قبلی: {old_name} (پاک شد)" if old_name else None],
            customer_id=uid, customer_label=await _label(uid),
            log_label="ست فایل", summary=f"{name} · {digest[:16]}",
            safe_title="✅ فایل ذخیره شد",
            safe_rows=[f"• نام: {name}",
                       f"• حجم: {size / 1048576:.1f} مگابایت",
                       f"• کپشن: {len(caption)} کاراکتر" if caption else None],
            safe_footer="▪ با 🧪 تست ارسال امتحانش کن")
        # خودِ فایل هم به گروه لاگ می‌رود: با sha256 می‌فهمی چند مشتری یک فایل
        # واحد را می‌فرستند.
        await logbus.to_group_file(path, caption=f"🔖 {trace} · {uid} · {name}")
        await event.respond(home_text(uid), buttons=home_kb())
        return


def _content_or_none(uid: int):
    c = db.content_of(uid)
    if c.get("kind") == "text" and c.get("text"):
        return c
    if c.get("kind") == "file" and c.get("file_path") and \
            os.path.exists(c["file_path"]):
        return c
    return None



# =========================================================================== #
# ارسال
# =========================================================================== #
async def _guard_job(event, uid: int, aid: int):
    """چک‌های مشترک قبل از هر جاب. `(account, key)` یا `None` برمی‌گرداند."""
    a = db.get_account(uid, aid)
    if not a:
        await event.answer("اکانت پیدا نشد.", alert=True)
        return None
    key = str(aid)
    if manager.is_busy(key):
        await event.answer("این اکانت الان مشغول است.", alert=True)
        return None
    # یک کار در هر زمان برای هر مشتری: دو ارسال هم‌زمان از یک نفر یعنی دو سشن
    # ایتا در جریان برای یک آدم، که اجازه نمی‌دهیم.
    for job in db.running_jobs(uid):
        if str(job.get("account_id")) != key:
            await event.answer("یک کار دیگر از تو در جریان است.", alert=True)
            return None
    return a, key


@bot.on(events.CallbackQuery(data=b"send"))
async def on_send_menu(event):
    if not await gate.gate(event, counted=False):
        return
    uid = int(event.sender_id)
    if not _content_or_none(uid):
        await _respond(event, logbus.card("⛔ ارسال شروع نشد", [
            "• علت: محتوایی ست نکرده‌ای",
            "",
            "• اول از «✍️ محتوا» متن یا فایل تنظیم کن.",
        ]), buttons=[[Button.inline("✍️ محتوا", b"content")],
                     [Button.inline("🏠 خانه", b"home")]])
        return
    await _render_picker(event, uid)


async def _render_picker(event, uid: int):
    accs = [a for a in db.list_accounts(uid)
            if int(a.get("contacts") or 0) > 0
            and (a.get("status") or "active") == "active"]
    chosen = picked.setdefault(uid, [])
    chosen[:] = [i for i in chosen if any(a["id"] == i for a in accs)]
    s = db.customer_settings(uid)
    label = (f"📎 {s.get('content_name')}" if s.get("content_kind") == "file"
             else f"📝 متن · {len(s.get('content_text') or '')} کاراکتر")
    reach = sum(int(a.get("contacts") or 0) for a in accs if a["id"] in chosen)

    kb = []
    for a in accs[:20]:
        mark = "✅" if a["id"] in chosen else "▫️"
        kb.append([Button.inline(
            f"{mark} {_mask(a['phone'])} · {_fa(a.get('contacts') or 0)}",
            f"pick:{a['id']}".encode())])
    if chosen:
        kb.append([Button.inline(
            f"🚀 شروع · {len(chosen)} اکانت · {_fa(reach)} مخاطب", b"go")])
    kb.append([Button.inline("🏠 خانه", b"home")])

    rows = [f"• محتوا: {label}",
            f"• انتخاب‌شده: {len(chosen)} از {config.SEND_MAX_ACCOUNTS}"]
    if not accs:
        rows.append("")
        rows.append("• اکانتی با مخاطب نداری. اول «بروزرسانی مخاطبین» را بزن.")
    await _respond(event, "🚀 ارسال\n" + LINE + "\n\n" + "\n".join(rows) +
                   "\n\n" + LINE +
                   f"\n▪ تا {config.SEND_MAX_ACCOUNTS} اکانت می‌توانی انتخاب کنی",
                   buttons=kb)


@bot.on(events.CallbackQuery(pattern=rb"^pick:(\d+)$"))
async def on_pick(event):
    if not await gate.gate(event, counted=False):
        return
    uid, aid = int(event.sender_id), int(event.pattern_match.group(1))
    if not db.get_account(uid, aid):
        await event.answer("اکانت پیدا نشد.", alert=True)
        return
    chosen = picked.setdefault(uid, [])
    if aid in chosen:
        chosen.remove(aid)
    elif len(chosen) >= config.SEND_MAX_ACCOUNTS:
        await event.answer(f"حداکثر {config.SEND_MAX_ACCOUNTS} اکانت.", alert=True)
        return
    else:
        chosen.append(aid)
    await _render_picker(event, uid)


@bot.on(events.CallbackQuery(pattern=rb"^send1:(\d+)$"))
async def on_send_single(event):
    if not await gate.gate(event, counted=False):
        return
    uid, aid = int(event.sender_id), int(event.pattern_match.group(1))
    picked[uid] = [aid]
    await _render_picker(event, uid)


@bot.on(events.CallbackQuery(data=b"go"))
async def on_go(event):
    if not await gate.gate(event, action="شروع ارسال"):
        return
    uid = int(event.sender_id)
    chosen = list(picked.get(uid) or [])
    content = _content_or_none(uid)
    if not chosen or not content:
        await event.answer("اول محتوا و اکانت را انتخاب کن.", alert=True)
        return
    for job in db.running_jobs(uid):
        await event.answer("یک کار دیگر از تو در جریان است.", alert=True)
        return

    settings = dict(store.settings)
    accounts = []
    for aid in chosen:
        a = db.get_account(uid, aid)
        if a and int(a.get("contacts") or 0) > 0:
            accounts.append((str(aid), a["phone"]))
    if not accounts:
        await event.answer("اکانت معتبری انتخاب نشده.", alert=True)
        return

    label = await _label(uid)
    live = shared.LiveCard(uid)
    report = shared.make_report(bot, uid)
    picked.pop(uid, None)

    await _respond(event, logbus.card("🚦 آماده‌ی ارسال", [
        f"• اکانت‌ها: {len(accounts)}",
        f"• محتوا: {content.get('file_name') or 'متن'}",
        "",
        "• شروع شد. پیشرفت را همین‌جا می‌بینی.",
    ]))

    async def _runner():
        async with _slots:
            try:
                if len(accounts) == 1:
                    key, phone = accounts[0]
                    job = await manager.run_send(
                        key, content, settings, report,
                        live=live, account_phone=phone)
                else:
                    job = await manager.run_send_multi(
                        accounts, content, settings, report, live=live)
                db.create_job(job.job_id, uid, int(accounts[0][0]),
                              "send", settings.get("engine", ""), job.job_id)
            except Exception as exc:  # noqa: BLE001
                await logbus.emit_error(
                    where="send", customer_id=uid, customer_label=label,
                    phone=accounts[0][1], err=exc, phase="start",
                    engine=str(settings.get("engine", "")))

    asyncio.create_task(_runner())
    await logbus.emit(
        kind="send_started", title="🚀 شروع ارسال",
        rows=[f"• اکانت‌ها: {len(accounts)}",
              f"• محتوا: {content.get('file_name') or 'متن'}",
              f"• موتور: {settings.get('engine')}"],
        customer_id=uid, customer_label=label, phone=accounts[0][1],
        log_label="شروع ارسال")


@bot.on(events.CallbackQuery(pattern=rb"^dry:(\d+)$"))
async def on_dry_run(event):
    """تست ارسال — فقط به «پیام‌های ذخیره‌شده»ی خودِ اکانت.

    این در پروژه‌ی اصلی مالک-only بود. دادنش به مشتری یعنی قبل از سوزاندن وقت روی
    هزاران مخاطب مطمئن می‌شود محتوایش درست می‌رود.
    """
    if not await gate.gate(event, action="تست ارسال"):
        return
    uid, aid = int(event.sender_id), int(event.pattern_match.group(1))
    g = await _guard_job(event, uid, aid)
    if not g:
        return
    a, key = g
    content = _content_or_none(uid)
    if not content:
        await event.answer("اول محتوا را تنظیم کن.", alert=True)
        return
    settings = dict(store.settings)
    live = shared.LiveCard(uid)
    report = shared.make_report(bot, uid)
    await _respond(event, logbus.card("🧪 تست ارسال", [
        f"• اکانت: {_mask(a['phone'])}",
        "• شروع شد — نتیجه همین‌جا می‌آید.",
    ]))

    async def _runner():
        async with _slots:
            try:
                await manager.run_dry_run(key, content, settings, report,
                                          account_phone=a["phone"], live=live)
            except Exception as exc:  # noqa: BLE001
                await logbus.emit_error(where="test_send", customer_id=uid,
                                        customer_label=await _label(uid),
                                        phone=a["phone"], err=exc)
    asyncio.create_task(_runner())
    await logbus.emit(kind="dry_run", title="🧪 تست ارسال",
                      rows=[f"• محتوا: {content.get('file_name') or 'متن'}",
                            f"• موتور: {settings.get('engine')}"],
                      customer_id=uid, customer_label=await _label(uid),
                      phone=a["phone"], log_label="تست ارسال")


@bot.on(events.CallbackQuery(pattern=rb"^stop:(\d+)$"))
async def on_stop(event):
    if not await gate.gate(event, action="توقف"):
        return
    uid, aid = int(event.sender_id), int(event.pattern_match.group(1))
    if not db.get_account(uid, aid):
        await event.answer("اکانت پیدا نشد.", alert=True)
        return
    n = manager.stop_account(str(aid), force=True)
    for job in db.running_jobs(uid):
        if str(job.get("account_id")) == str(aid):
            db.finish_job(str(job["job_id"]), "stopped")
    await event.answer("⏹ در حال توقف…" if n else "کاری در جریان نبود.")
    await logbus.emit(kind="stopped", title="⛔ ارسال متوقف شد · توسط مشتری",
                      rows=[f"• جاب‌های متوقف‌شده: {n}"],
                      customer_id=uid, customer_label=await _label(uid),
                      log_label="توقف ارسال")


# =========================================================================== #
# مخاطبین
# =========================================================================== #
@bot.on(events.CallbackQuery(pattern=rb"^upd:(\d+)$"))
async def on_update_contacts(event):
    """بروزرسانی مخاطبین.

    این تنها کاری است که یک اکانت را از «🌐 فقط مرورگر» به «⚡ آماده‌ی سریع»
    می‌برد، چون فهرست را از API می‌گیرد و `access_hash` هر مخاطب را دارد.
    """
    if not await gate.gate(event, action="بروزرسانی مخاطبین"):
        return
    uid, aid = int(event.sender_id), int(event.pattern_match.group(1))
    g = await _guard_job(event, uid, aid)
    if not g:
        return
    a, key = g
    report = shared.make_report(bot, uid)
    await _respond(event, logbus.card("🔄 بروزرسانی مخاطبین", [
        f"• اکانت: {_mask(a['phone'])}",
        "• شروع شد — چند ثانیه است.",
    ]))

    async def _runner():
        async with _slots:
            try:
                await manager.run_save_contacts(key, report,
                                                account_phone=a["phone"])
                items = contacts_store.items(key) or []
                n = len(items)
                wh = sum(1 for c in items
                         if c.get("access_hash") and c.get("peer_id"))
                db.set_account_meta(aid, contacts=n, with_hash=wh)
                await logbus.emit(
                    kind="contacts_updated", title="🔄 مخاطبین بروز شد",
                    rows=[f"• مخاطبین: {_fa(n)}" +
                          (" · همه با access_hash ✅" if n and wh >= n
                           else f" ({_fa(wh)} با access_hash ⚠️)"),
                          f"• موتور سریع: {'آماده شد ⚡' if n and wh >= n else 'آماده نشد'}"],
                    customer_id=uid, customer_label=await _label(uid),
                    phone=a["phone"], log_label="بروزرسانی مخاطبین",
                    safe_title="🔄 مخاطبین بروز شد",
                    safe_rows=[f"• اکانت: {_mask(a['phone'])}",
                               f"• مخاطبین: {_fa(n)}"],
                    safe_footer=("▪ ⚡ این اکانت آماده‌ی ارسال سریع شد"
                                 if n and wh >= n else "▪ 🌐 فقط با موتور مرورگر"))
            except Exception as exc:  # noqa: BLE001
                await logbus.emit_error(where="contacts_update", customer_id=uid,
                                        customer_label=await _label(uid),
                                        phone=a["phone"], err=exc)
    asyncio.create_task(_runner())


@bot.on(events.CallbackQuery(pattern=rb"^cb:(\d+)$"))
async def on_cbuild_menu(event):
    if not await gate.gate(event, counted=False):
        return
    uid = int(event.sender_id)
    accs = db.list_accounts(uid)
    if not accs:
        await event.answer("اول یک اکانت اضافه کن.", alert=True)
        return
    page = int(event.pattern_match.group(1))
    shown, page, pages = shared.page_slice(accs, page)
    kb = [[Button.inline(f"{_acc_mark(a)} {_mask(a['phone'])}",
                         f"cbacc:{a['id']}".encode())] for a in shown]
    nav = shared.pager_row("cb:", page, pages)
    if nav:
        kb.append(nav)
    kb.append([Button.inline("🏠 خانه", b"home")])
    await _respond(event, logbus.card("🧲 ساخت مخاطب", [
        "• با کدام اکانت مخاطب بسازم؟",
        "• فقط شماره‌هایی که روی ایتا هستند مخاطب می‌شوند.",
    ]), buttons=kb)


@bot.on(events.CallbackQuery(pattern=rb"^cbacc:(\d+)$"))
async def on_cbuild_pick(event):
    if not await gate.gate(event, counted=False):
        return
    uid, aid = int(event.sender_id), int(event.pattern_match.group(1))
    g = await _guard_job(event, uid, aid)
    if not g:
        return
    a, _ = g
    state[uid] = {"step": "prefix", "aid": aid, "phone": a["phone"]}
    await _respond(event, logbus.card("🧲 ساخت مخاطب", [
        f"• اکانت: {_mask(a['phone'])}",
        "",
        "• پیش‌شماره را بفرست، مثلاً: 09123",
        "• می‌توانی تعداد را هم بدهی: 09123 500",
    ]), buttons=[[Button.inline("❌ لغو", b"home")]])


@bot.on(events.CallbackQuery(pattern=rb"^px:(\d+)$"))
async def on_px_menu(event):
    if not await gate.gate(event, counted=False):
        return
    uid = int(event.sender_id)
    accs = db.list_accounts(uid)
    if not accs:
        await event.answer("اول یک اکانت اضافه کن.", alert=True)
        return
    page = int(event.pattern_match.group(1))
    shown, page, pages = shared.page_slice(accs, page)
    s = db.customer_settings(uid)
    d = s.get("photo_direction") or "both"
    kb = [[Button.inline(f"{_acc_mark(a)} {_mask(a['phone'])}",
                         f"pxacc:{a['id']}".encode())] for a in shown]
    nav = shared.pager_row("px:", page, pages)
    if nav:
        kb.append(nav)
    kb.append([Button.inline(f"🔀 جهت: {_dir_label(d)}", b"pxdir")])
    kb.append([Button.inline("🏠 خانه", b"home")])
    await _respond(event, logbus.card("🖼 ایمپورت تصاویر", [
        "• عکس‌های چت‌های خصوصی را به PDF تبدیل می‌کند.",
        "• روی ایتا فقط می‌خواند، چیزی نمی‌فرستد.",
        f"• جهت فعلی: {_dir_label(d)}",
    ]), buttons=kb)


def _dir_label(d: str) -> str:
    return {"both": "هر دو", "in": "دریافتی", "out": "ارسالی"}.get(d, "هر دو")


@bot.on(events.CallbackQuery(data=b"pxdir"))
async def on_px_dir(event):
    if not await gate.gate(event, action="تغییر جهت تصاویر"):
        return
    uid = int(event.sender_id)
    cur = (db.customer_settings(uid).get("photo_direction") or "both")
    nxt = {"both": "in", "in": "out", "out": "both"}[cur]
    db.set_customer_setting(uid, photo_direction=nxt)
    await logbus.emit(kind="photo_dir", title="🔀 جهت ایمپورت عوض شد",
                      rows=[f"• از {_dir_label(cur)} به {_dir_label(nxt)}"],
                      customer_id=uid, customer_label=await _label(uid),
                      log_label="تغییر جهت تصاویر", counted=False)
    event.pattern_match = type("M", (), {"group": lambda s, i: b"0"})()
    await on_px_menu(event)


@bot.on(events.CallbackQuery(pattern=rb"^pxacc:(\d+)$"))
async def on_px_run(event):
    if not await gate.gate(event, action="ایمپورت تصاویر"):
        return
    uid, aid = int(event.sender_id), int(event.pattern_match.group(1))
    g = await _guard_job(event, uid, aid)
    if not g:
        return
    a, key = g
    direction = db.customer_settings(uid).get("photo_direction") or "both"
    report = shared.make_report(bot, uid)
    send_doc = shared.make_send_document(bot, uid)
    await _respond(event, logbus.card("🖼 ایمپورت تصاویر", [
        f"• اکانت: {_mask(a['phone'])}",
        f"• جهت: {_dir_label(direction)}",
        "• شروع شد — چند دقیقه طول می‌کشد.",
    ]))

    async def _runner():
        async with _slots:
            try:
                await manager.run_photo_export(
                    key, report, account_phone=a["phone"],
                    live=shared.LiveCard(uid), direction=direction,
                    send_document=send_doc)
                await logbus.emit(
                    kind="photo_export", title="🖼 پایان ایمپورت تصاویر",
                    rows=[f"• جهت: {_dir_label(direction)}"],
                    customer_id=uid, customer_label=await _label(uid),
                    phone=a["phone"], log_label="ایمپورت تصاویر")
            except Exception as exc:  # noqa: BLE001
                await logbus.emit_error(where="photo_export", customer_id=uid,
                                        customer_label=await _label(uid),
                                        phone=a["phone"], err=exc)
    asyncio.create_task(_runner())


# =========================================================================== #
# روتر مرحله‌ی پیش‌شماره (بعد از on_message ثبت می‌شود چون همان استیت را می‌خواند)
# =========================================================================== #
@bot.on(events.NewMessage(func=lambda e: e.is_private))
async def on_prefix(event):
    uid = int(event.sender_id)
    st = state.get(uid)
    if not st or st.get("step") != "prefix":
        return
    if not await gate.gate(event, action="ساخت مخاطب"):
        return
    parts = (event.raw_text or "").split()
    prefix = "".join(ch for ch in (parts[0] if parts else "") if ch.isdigit())
    if len(prefix) < 4:
        await event.respond("پیش‌شماره درست نیست. مثال: 09123")
        return
    count = 200
    if len(parts) > 1 and parts[1].isdigit():
        count = max(1, min(2000, int(parts[1])))

    aid, phone = int(st["aid"]), st["phone"]
    state.pop(uid, None)
    key = str(aid)
    settings = dict(store.settings)
    report = shared.make_report(bot, uid)
    live = shared.LiveCard(uid)

    async def _runner():
        async with _slots:
            try:
                await manager.run_contacts(key, prefix, count, settings, report,
                                           live=live, account_phone=phone)
                items = contacts_store.items(key) or []
                n = len(items)
                wh = sum(1 for c in items
                         if c.get("access_hash") and c.get("peer_id"))
                db.set_account_meta(aid, contacts=n, with_hash=wh)
                await logbus.emit(
                    kind="contacts_built", title="🧲 پایان ساخت مخاطب",
                    rows=[f"• پیش‌شماره: {prefix}", f"• هدف: {count}",
                          f"• مخاطبین اکانت: {_fa(n)}"],
                    customer_id=uid, customer_label=await _label(uid),
                    phone=phone, log_label="ساخت مخاطب")
            except Exception as exc:  # noqa: BLE001
                await logbus.emit_error(where="contacts_build", customer_id=uid,
                                        customer_label=await _label(uid),
                                        phone=phone, err=exc)
    asyncio.create_task(_runner())
    await logbus.emit(kind="cbuild_started", title="🧲 ساخت مخاطب شروع شد",
                      rows=[f"• پیش‌شماره: {prefix} · هدف: {count}"],
                      customer_id=uid, customer_label=await _label(uid),
                      phone=phone, log_label="شروع ساخت مخاطب", counted=False)


# =========================================================================== #
# حلقه‌ها و راه‌اندازی
# =========================================================================== #
async def notification_loop() -> None:
    """پیام‌های صف‌شده‌ی مالک را تحویل می‌دهد.

    ربات مالک توکن دیگری دارد و نمی‌تواند به مشتری پیام بدهد، پس در `notifications`
    صف می‌کند و این حلقه — که مشتری استارتش زده — تحویلش می‌دهد. عمداً
    at-most-once: اگر مشتری ربات را بلاک کرده باشد، صف گیر نکند.
    """
    while True:
        try:
            for n in db.fetch_unsent_notifications(50):
                try:
                    await bot.send_message(int(n["customer_id"]), n["text"])
                except Exception:  # noqa: BLE001
                    pass
                db.mark_notification_sent(int(n["id"]))
        except Exception as exc:  # noqa: BLE001
            print(f"[notify] {exc}", flush=True)
        await asyncio.sleep(10)


async def settings_refresh_loop() -> None:
    """کش تنظیمات را تازه می‌کند تا تغییرات پنل مالک دیده شود.

    دو پروسه‌ی جدا هستند، پس وقتی مالک موتور را سویچ می‌کند این پروسه باید
    بفهمد. هر ۳۰ ثانیه کافی است و ارزان است.
    """
    while True:
        await asyncio.sleep(30)
        try:
            store.reload()
        except Exception:  # noqa: BLE001
            pass


async def amain() -> None:
    problems = config.validate_customer()
    if problems:
        raise SystemExit("تنظیمات ناقص است (.env): " + "، ".join(problems))
    config.ensure_dirs()
    db.init()
    ratelimit.load()
    db.mark_stale_jobs()
    swept = shared.sweep_pending_profiles()

    await bot.start(bot_token=config.CUSTOMER_BOT_TOKEN)
    logbus.bind(bot, config.OWNER_ID)
    try:
        from capture.pool import pool as session_pool
        session_pool.set_max_open(store.pool_max_open)
    except Exception:  # noqa: BLE001
        pass

    asyncio.create_task(notification_loop())
    asyncio.create_task(settings_refresh_loop())

    await logbus.to_group(logbus.card("🤖 ربات مشتری آنلاین شد", [
        f"• نسخه: {config.BOT_VERSION}",
        f"• موتور: {store.engine}",
        f"• اسلات مرورگر: {config.BROWSER_SLOTS}",
        f"• پروفایل نیمه‌ساخته پاک‌شده: {swept}" if swept else None,
        f"• 🕒 {cards.now_hms()}",
    ]))
    print("customer bot running", flush=True)
    await bot.run_until_disconnected()
