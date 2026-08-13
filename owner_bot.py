"""
owner_bot.py — پنل خصوصی مالک.
==============================

**همه‌ی** تنظیمات اینجاست و هیچ‌کدام دست مشتری نیست: موتور، تأخیر، هم‌زمانی، سقف
مرورگر، گروه لاگ، جوین اجباری.

چیزهایی که فقط اینجا هست:

* **کلید موتور** — یک انتخاب برای همه‌ی مشتری‌ها، برای وقتی یک موتور خراب شد
  (`fail over`). مشتری این کلید را نمی‌بیند.
* **مشتری‌ها** — فهرست صفحه‌بندی‌شده با فیلتر SQL، جستجو با شماره‌ی هر اکانتشان،
  مسدود/آزاد، پیام به یک نفر، و **پیش‌نمایش پنل مشتری**.
* **پیام همگانی** — از طریق صف `notifications`، چون ربات مالک نمی‌تواند به مشتری
  پیام بدهد.
* **ریست سه‌پله** — چون «کش» و «سشن» دو چیز کاملاً متفاوتند (بخش `_reset_*`).
* **حالت تعمیر** — کلید قطع فوری.

مجوزدهی: هر هندلر با `if not is_owner(event): return` شروع می‌شود. گارد داخل
هندلر است، نه فقط روی دکمه — تا با replay کردن callback data قابل دسترس نباشد.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import time

from telethon import Button, TelegramClient, events

import db
import gate
import ratelimit
from bot import app as shared
from bot import cards, logbus
from bot.store import store
from config import config

LINE = cards.DIVIDER

config.DATA_DIR.mkdir(parents=True, exist_ok=True)
bot = TelegramClient(str(config.DATA_DIR / "owner_bot"),
                     config.API_ID, config.API_HASH)

#: وضعیت گفتگوی مالک
state: dict = {}

#: پوشه‌های داخل پروفایل کروم که **کش خالص**اند.
#: سشن ایتا در IndexedDB و Local Storage است، پس حذف این‌ها گیگابایت‌ها آزاد
#: می‌کند **بدون اینکه کسی لاگ‌اوت شود** — و همین تفکیک، کل طراحی ریست است.
_CACHE_DIRS = (
    "Cache", "Code Cache", "GPUCache", "DawnCache", "ShaderCache",
    "GrShaderCache", "GraphiteDawnCache", "component_crx_cache",
    "Service Worker/CacheStorage", "Service Worker/ScriptCache",
    "Default/Cache", "Default/Code Cache", "Default/GPUCache",
    "Default/Service Worker/CacheStorage",
)


def is_owner(event) -> bool:
    return bool(config.OWNER_ID) and int(event.sender_id) == int(config.OWNER_ID)


def _fa(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


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


def _engine_label(name: str) -> str:
    return {"bridge": "🌐 مرورگر", "hybrid": "⚡ سریع",
            "direct": "⚡ سریع (مستقیم)"}.get(str(name), str(name))


# =========================================================================== #
# منابع سرور
# =========================================================================== #
def _disk() -> tuple:
    try:
        t, u, f = shutil.disk_usage(str(config.DATA_DIR))
        return f / 2**30, t / 2**30
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


def _ram() -> tuple:
    """(آزاد، کل) به گیگابایت، از /proc/meminfo. بدون وابستگی جدید."""
    try:
        vals = {}
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                k, _, rest = line.partition(":")
                vals[k.strip()] = int(rest.split()[0])
        total = vals.get("MemTotal", 0) / 2**20
        avail = vals.get("MemAvailable", vals.get("MemFree", 0)) / 2**20
        return avail, total
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


def _profiles_size() -> tuple:
    """(کل، کش) به گیگابایت. کش همان بخشی است که پله‌ی ۱ پاک می‌کند."""
    total = cache = 0
    try:
        base = config.PROFILES_DIR
        if not base.is_dir():
            return 0.0, 0.0
        for root, _dirs, files in os.walk(base):
            is_cache = any(seg in root for seg in
                           ("Cache", "GPUCache", "ShaderCache", "CacheStorage",
                            "ScriptCache"))
            for f in files:
                try:
                    n = os.path.getsize(os.path.join(root, f))
                except OSError:
                    continue
                total += n
                if is_cache:
                    cache += n
    except Exception:  # noqa: BLE001
        pass
    return total / 2**30, cache / 2**30


def _pool_status() -> dict:
    try:
        from capture.pool import pool
        return pool.status()
    except Exception:  # noqa: BLE001
        return {}


# =========================================================================== #
# خانه
# =========================================================================== #
async def home_text() -> str:
    t = db.owner_totals()
    ram_f, ram_t = _ram()
    dk_f, dk_t = _disk()
    ps = _pool_status()
    ping = await shared.server_ping_ms()
    running = len(db.running_jobs())

    rows = [
        f"• مشتری‌ها: {t['customers']} · {t['customers'] - t['blocked']} فعال · "
        f"{t['blocked']} مسدود",
        f"• اکانت‌ها: {t['accounts']} · ⚡ {t['fast_ready']} آماده · "
        f"{t['accounts'] - t['ready']} بی‌مخاطب",
        f"• مخاطبین: {_fa(t['contacts'])}",
        "",
        f"• موتور: {_engine_label(store.engine)}"
        + ("  (بدون مرورگر)" if store.browserless else ""),
        f"• مرورگر: {ps.get('warm', 0)} گرم از {ps.get('max_open', '—')}",
        f"• جاب در حال اجرا: {running}",
        f"• رم: {ram_f:.1f} از {ram_t:.1f} گیگ آزاد" if ram_t else None,
        f"• دیسک: {dk_f:.0f} از {dk_t:.0f} گیگ آزاد" if dk_t else None,
        f"• ایتا: {ping}ms" if ping is not None else "• ایتا: 🔴 بی‌پاسخ",
        f"• لاگین‌های ۲۴ ساعت: {t['logins_today']}",
    ]
    if db.maintenance_on():
        rows.insert(0, "• 🛠 حالت تعمیر روشن است")
    rows = [r for r in rows if r is not None]
    return ("🤖 پنل مالک\n" + LINE + "\n\n" + "\n".join(rows) + "\n\n" + LINE +
            f"\n▪ کل ارسال: {_fa(t['sent'])} · 🕒 {cards.now_hms()[11:16]}")


def home_kb() -> list:
    return [
        [Button.inline("👥 مشتری‌ها", b"custs:all:0"),
         Button.inline("⚙️ تنظیمات", b"set")],
        [Button.inline("📢 پیام همگانی", b"bc"),
         Button.inline("🧹 ریست", b"reset")],
        [Button.inline("📊 منابع", b"res"),
         Button.inline("🛠 حالت تعمیر", b"maint")],
        [Button.inline("♻️ بروزرسانی", b"home")],
    ]


@bot.on(events.NewMessage(pattern=r"^/start", func=lambda e: e.is_private))
async def on_start(event):
    if not is_owner(event):
        return
    state.pop(int(event.sender_id), None)
    await event.respond(await home_text(), buttons=home_kb())


@bot.on(events.CallbackQuery(data=b"home"))
async def on_home(event):
    if not is_owner(event):
        return
    state.pop(int(event.sender_id), None)
    await _respond(event, await home_text(), buttons=home_kb())


@bot.on(events.CallbackQuery(data=b"noop"))
async def on_noop(event):
    try:
        await event.answer()
    except Exception:  # noqa: BLE001
        pass


# =========================================================================== #
# مشتری‌ها
# =========================================================================== #
_FILTER_LABEL = {"all": "همه", "active": "فعال", "blocked": "مسدود",
                 "withacc": "با اکانت", "noacc": "بی‌اکانت"}


@bot.on(events.CallbackQuery(pattern=rb"^custs:([a-z]+):(\d+)$"))
async def on_customers(event):
    if not is_owner(event):
        return
    filt = event.pattern_match.group(1).decode()
    page = int(event.pattern_match.group(2))
    per = 12
    total = db.count_customers(filt)
    pages = max(1, (total + per - 1) // per)
    page = max(0, min(page, pages - 1))
    rows_db = db.list_customers_page(page * per, per, filt)

    kb = []
    for c in rows_db:
        uid = int(c["telegram_id"])
        mark = "⛔" if c.get("blocked") else "🟢"
        t = db.customer_totals(uid)
        name = (c.get("name") or str(uid))[:16]
        kb.append([Button.inline(
            f"{mark} {name} · {t['accounts']} اکانت · {_fa(t['sent'])}",
            f"cust:{uid}".encode())])
    kb.append([Button.inline("همه", b"custs:all:0"),
               Button.inline("فعال", b"custs:active:0"),
               Button.inline("مسدود", b"custs:blocked:0")])
    kb.append([Button.inline("بی‌اکانت", b"custs:noacc:0"),
               Button.inline("🔍 جستجو", b"search")])
    if pages > 1:
        kb.append([Button.inline("◀", f"custs:{filt}:{(page - 1) % pages}".encode()),
                   Button.inline(f"{page + 1}/{pages}", b"noop"),
                   Button.inline("▶", f"custs:{filt}:{(page + 1) % pages}".encode())])
    kb.append([Button.inline("🏠 خانه", b"home")])

    body = [f"• {total} مشتری · صفحه {page + 1} از {pages}",
            f"• فیلتر: {_FILTER_LABEL.get(filt, filt)}"]
    if not rows_db:
        body.append("")
        body.append("• چیزی با این فیلتر پیدا نشد.")
    await _respond(event, "👥 مشتری‌ها\n" + LINE + "\n\n" + "\n".join(body) +
                   "\n\n" + LINE + "\n▪ 🟢 فعال · ⛔ مسدود", buttons=kb)


@bot.on(events.CallbackQuery(data=b"search"))
async def on_search(event):
    if not is_owner(event):
        return
    state[int(event.sender_id)] = {"step": "search"}
    await _respond(event, logbus.card("🔍 جستجوی مشتری", [
        "• آیدی، نام، یوزرنیم — یا **شماره‌ی یکی از اکانت‌هایش** را بفرست.",
        "",
        "• جستجو با شماره همان چیزی است که در عمل لازم می‌شود:",
        "  مشکل با شماره گزارش می‌شود، نه با آیدی تلگرام.",
    ]), buttons=[[Button.inline("🔙 مشتری‌ها", b"custs:all:0")]])


async def _customer_card(uid: int) -> tuple:
    c = db.get_customer(uid)
    if not c:
        return None, None
    t = db.customer_totals(uid)
    q = gate.add_quota(uid)
    s = db.customer_settings(uid)
    accs = db.list_accounts(uid)
    running = db.running_jobs(uid)
    last = db.last_jobs(uid, 1)

    content = "تنظیم نشده"
    if s.get("content_kind") == "text" and s.get("content_text"):
        content = f"📝 متن · {len(s['content_text'])} کاراکتر"
    elif s.get("content_kind") == "file":
        content = f"📎 {s.get('content_name')}"

    rows = [
        f"• آیدی: {uid}",
        f"• وضعیت: {'⛔ مسدود' if c.get('blocked') else '🟢 فعال'}"
        + (f" · {c.get('block_reason')}" if c.get("blocked") and c.get("block_reason") else ""),
        f"• عضویت: {gate._age_words(c.get('created_at'))}",
        f"• اکانت‌ها: {t['accounts']} از {config.ACCOUNT_CAP}"
        + (f" · ⚡ {t['fast']} آماده" if t["fast"] else ""),
        f"• مخاطبین: {_fa(t['contacts'])}",
        f"• سهمیه: {q['used']} از {config.DAILY_ADD_QUOTA}"
        + (f" · بعدی {gate.human_wait(q['next_in'])}" if q["next_in"] else ""),
        f"• محتوا: {content}",
        f"• sha256: {(s.get('content_sha256') or '')[:16]}…"
        if s.get("content_sha256") else None,
        f"• جاب در حال اجرا: {len(running)}" if running else None,
    ]
    if last:
        j = last[0]
        rows.append(f"• آخرین کار: {j.get('kind')} · {j.get('state')} · "
                    f"{_fa(j.get('sent'))} موفق")
    if c.get("note"):
        rows.append(f"• یادداشت: {c['note']}")
    rows = [r for r in rows if r is not None]

    kb = [
        [Button.inline("🔓 آزاد کن" if c.get("blocked") else "⛔ مسدود کن",
                       f"{'unblk' if c.get('blocked') else 'blk'}:{uid}".encode()),
         Button.inline("🔍 پیش‌نمایش پنلش", f"peek:{uid}".encode())],
        [Button.inline(f"👤 اکانت‌هایش ({len(accs)})", f"cacc:{uid}:0".encode()),
         Button.inline("📜 تاریخچه", f"hist:{uid}:0".encode())],
        [Button.inline("💬 پیام به او", f"msg:{uid}".encode()),
         Button.inline("📝 یادداشت", f"note:{uid}".encode())],
        [Button.inline("🔙 مشتری‌ها", b"custs:all:0"),
         Button.inline("🏠 خانه", b"home")],
    ]
    name = c.get("name") or str(uid)
    head = f"👤 {name}" + (f" · @{c['username']}" if c.get("username") else "")
    return (head + "\n" + LINE + "\n\n" + "\n".join(rows) + "\n\n" + LINE +
            f"\n▪ کل ارسال: {_fa(t['sent'])}"), kb


@bot.on(events.CallbackQuery(pattern=rb"^cust:(\d+)$"))
async def on_customer(event):
    if not is_owner(event):
        return
    uid = int(event.pattern_match.group(1))
    text, kb = await _customer_card(uid)
    if not text:
        await event.answer("مشتری پیدا نشد.", alert=True)
        return
    await _respond(event, text, buttons=kb)


@bot.on(events.CallbackQuery(pattern=rb"^peek:(\d+)$"))
async def on_peek(event):
    """پیش‌نمایش فقط-خواندنیِ پنل مشتری.

    جواب «پیش من این دکمه نیست» را چند ثانیه‌ای می‌دهد. دکمه‌ها رندر نمی‌شوند تا
    اشتباهی کاری از طرف مشتری انجام نشود.
    """
    if not is_owner(event):
        return
    uid = int(event.pattern_match.group(1))
    if not db.get_customer(uid):
        await event.answer("مشتری پیدا نشد.", alert=True)
        return
    import customer_bot
    await _respond(event,
                   "🔍 پیش‌نمایش\n" + LINE + "\nدقیقاً همین را می‌بیند:\n" + LINE +
                   "\n\n" + customer_bot.home_text(uid),
                   buttons=[[Button.inline("🔙 پروفایل", f"cust:{uid}".encode())]])


@bot.on(events.CallbackQuery(pattern=rb"^cacc:(\d+):(\d+)$"))
async def on_customer_accounts(event):
    if not is_owner(event):
        return
    uid = int(event.pattern_match.group(1))
    page = int(event.pattern_match.group(2))
    accs = db.list_accounts(uid)
    shown, page, pages = shared.page_slice(accs, page)
    rows = [f"• {len(accs)} اکانت · صفحه {page + 1} از {pages}"] if accs else \
           ["• اکانتی ندارد."]
    for a in shown:
        c, h = int(a.get("contacts") or 0), int(a.get("with_hash") or 0)
        mark = "⚡" if (c and h >= c) else ("🌐" if c else "⚠️")
        # مالک شماره‌ی کامل را می‌بیند؛ ماسک فقط برای مشتری و گروه لاگ است.
        rows.append(f"• {mark} {a['phone']} · {_fa(c)} مخاطب · "
                    f"{_fa(a.get('total_sent') or 0)} ارسال")
    kb = []
    nav = shared.pager_row(f"cacc:{uid}:", page, pages)
    if nav:
        kb.append(nav)
    kb.append([Button.inline("🔙 پروفایل", f"cust:{uid}".encode())])
    await _respond(event, "👤 اکانت‌های مشتری\n" + LINE + "\n\n" + "\n".join(rows) +
                   "\n\n" + LINE + "\n▪ ⚡ آماده · 🌐 فقط مرورگر · ⚠️ بی‌مخاطب",
                   buttons=kb)


@bot.on(events.CallbackQuery(pattern=rb"^hist:(\d+):(\d+)$"))
async def on_history(event):
    if not is_owner(event):
        return
    uid = int(event.pattern_match.group(1))
    page = int(event.pattern_match.group(2))
    per = 12
    total = db.count_timeline(uid)
    pages = max(1, (total + per - 1) // per)
    page = max(0, min(page, pages - 1))
    evs = db.timeline(uid, page * per, per)
    rows = []
    for e in evs:
        ts = time.strftime("%m-%d %H:%M", time.localtime(float(e["at"])))
        line = f"• {ts} {e.get('label') or e.get('kind')}"
        if e.get("trace_id"):
            line += f" · 🔖 {e['trace_id']}"
        rows.append(line)
    if not rows:
        rows = ["• رویدادی ثبت نشده."]
    kb = []
    if pages > 1:
        kb.append([Button.inline("◀", f"hist:{uid}:{(page - 1) % pages}".encode()),
                   Button.inline(f"{page + 1}/{pages}", b"noop"),
                   Button.inline("▶", f"hist:{uid}:{(page + 1) % pages}".encode())])
    kb.append([Button.inline("🔙 پروفایل", f"cust:{uid}".encode())])
    await _respond(event, "📜 تاریخچه\n" + LINE + "\n\n" + "\n".join(rows) +
                   "\n\n" + LINE + f"\n▪ {total} رویداد · صفحه {page + 1} از {pages}",
                   buttons=kb)


@bot.on(events.CallbackQuery(pattern=rb"^(blk|unblk):(\d+)$"))
async def on_block_toggle(event):
    if not is_owner(event):
        return
    what = event.pattern_match.group(1).decode()
    uid = int(event.pattern_match.group(2))
    if not db.get_customer(uid):
        await event.answer("مشتری پیدا نشد.", alert=True)
        return

    if what == "blk":
        stopped = gate.stop_customer_jobs(uid)   # مسدودی باید جاب جاری را بخواباند
        ratelimit.block(uid, "توسط مالک")
        db.enqueue_notification(uid, logbus.card("⛔ حساب تو مسدود شد", [
            "• دسترسی‌ات به ربات بسته شد.",
            "• برای رفع مسدودی با پشتیبانی تماس بگیر.",
        ]))
        await logbus.emit(kind="blocked", title="⛔ مسدود شد · توسط مالک",
                          rows=[f"• جاب‌ها: {stopped} متوقف شد",
                                f"• اکانت‌ها: {db.count_accounts(uid)} · نگه داشته شد"],
                          customer_id=uid, log_label="مسدودی دستی", counted=False)
        await event.answer("مسدود شد.")
    else:
        ratelimit.unblock(uid)
        db.enqueue_notification(uid, logbus.card("✅ حساب تو آزاد شد", [
            "• دسترسی‌ات برگشت. /start را بزن.",
        ]))
        await logbus.emit(kind="unblocked", title="🔓 آزاد شد · توسط مالک", rows=[],
                          customer_id=uid, log_label="آزادسازی", counted=False)
        await event.answer("آزاد شد.")

    text, kb = await _customer_card(uid)
    await _respond(event, text, buttons=kb)


@bot.on(events.CallbackQuery(pattern=rb"^(msg|note):(\d+)$"))
async def on_msg_or_note(event):
    if not is_owner(event):
        return
    what = event.pattern_match.group(1).decode()
    uid = int(event.pattern_match.group(2))
    state[int(event.sender_id)] = {"step": what, "uid": uid}
    title = "💬 پیام به مشتری" if what == "msg" else "📝 یادداشت"
    hint = ("متن پیام را بفرست. از طریق ربات مشتری تحویل داده می‌شود."
            if what == "msg" else "یادداشت را بفرست (فقط برای خودت).")
    await _respond(event, logbus.card(title, [f"• مشتری: {uid}", "", f"• {hint}"]),
                   buttons=[[Button.inline("❌ لغو", f"cust:{uid}".encode())]])


# =========================================================================== #
# تنظیمات
# =========================================================================== #
@bot.on(events.CallbackQuery(data=b"set"))
async def on_settings(event):
    if not is_owner(event):
        return
    s = store.settings
    rows = [
        f"• موتور: {_engine_label(s.get('engine'))}"
        + ("  (بدون مرورگر)" if s.get("browserless") else ""),
        f"• تأخیر ارسال: {s.get('text_send_delay')} ثانیه · "
        f"هم‌زمانی: {s.get('send_concurrency')}",
        f"• تأخیر ساخت مخاطب: {s.get('contact_create_delay')} ثانیه",
        f"• مرورگر گرم: {s.get('pool_max_open')}",
        f"• Warm Path: {'✅' if s.get('warmpath') else '❌'}",
        f"• حالت APK: {'✅' if s.get('apk_octet') else '❌'}",
        f"• توقف روی محدودیت: {'✅' if s.get('stop_on_limit') else '❌'}",
        f"• گروه لاگ: {'✅ ' + str(config.LOG_GROUP_ID) if config.LOG_GROUP_ID else '❌ تنظیم نشده'}",
    ]
    await _respond(event, "⚙️ تنظیمات\n" + LINE + "\n\n" + "\n".join(rows) +
                   "\n\n" + LINE + "\n▪ این تنظیمات برای همه‌ی مشتری‌ها اعمال می‌شود",
                   buttons=[
                       [Button.inline("⚡ موتور", b"eng")],
                       [Button.inline("⏱ تأخیر ارسال", b"s:delay"),
                        Button.inline("🔀 هم‌زمانی", b"s:conc")],
                       [Button.inline("🖥 مرورگر گرم", b"s:pool"),
                        Button.inline("🔥 Warm Path", b"s:warm")],
                       [Button.inline("📦 حالت APK", b"s:apk"),
                        Button.inline("🛑 توقف روی محدودیت", b"s:stop")],
                       [Button.inline("🏠 خانه", b"home")]])


@bot.on(events.CallbackQuery(data=b"eng"))
async def on_engine(event):
    """کلید failover موتور — یک انتخاب برای همه‌ی مشتری‌ها."""
    if not is_owner(event):
        return
    cur = store.engine
    bl = store.browserless
    fast = (cur != "bridge")
    rows = [
        f"• موتور فعال: {_engine_label(cur)}" + ("  (بدون مرورگر)" if bl else ""),
        "",
        "• ⚡ سریع — بدون مرورگر، روی HTTPS",
        "   سریع و بدون مصرف رم",
        "   ⚠️ پشتیبان ندارد؛ مخاطبی که نشد رد می‌شود",
        "",
        "• 🌐 مرورگر — از داخل وب ایتا",
        "   کندتر · هر کروم حدود ۱ گیگ رم",
        "   ✅ پشتیبان دارد؛ خطا دوباره تلاش می‌شود",
    ]
    kb = [
        [Button.inline("🌐 سویچ به مرورگر" if fast else "⚡ سویچ به سریع",
                       b"engsw")],
        [Button.inline(f"{'✅' if bl else '❌'} کاملاً بدون مرورگر", b"engbl")],
        [Button.inline("🔙 تنظیمات", b"set")],
    ]
    await _respond(event, "⚡ موتور ارسال\n" + LINE + "\n\n" + "\n".join(rows) +
                   "\n\n" + LINE +
                   "\n▪ جاب‌های در حال اجرا با موتور خودشان تمام می‌شوند"
                   "\n▪ ⚠️ تعویض موتور، مرورگرهای گرم را می‌بندد", buttons=kb)


@bot.on(events.CallbackQuery(data=b"engsw"))
async def on_engine_switch(event):
    if not is_owner(event):
        return
    old = store.engine
    new = "bridge" if old != "bridge" else "hybrid"
    store.set_setting("engine", new)
    if new == "bridge":
        store.set_setting("browserless", False)
    # کلید pool شامل مسیر init-script است، پس سشن‌های گرم دیگر سازگار نیستند.
    closed = 0
    try:
        from capture.pool import pool
        closed = await pool.close_all()
    except Exception:  # noqa: BLE001
        pass
    await logbus.emit(kind="engine_switch", title="⚡ موتور عوض شد",
                      rows=[f"• از {_engine_label(old)} به {_engine_label(new)}",
                            f"• مرورگرهای بسته‌شده: {closed}"],
                      customer_id=None, log_label="تعویض موتور", counted=False)
    await on_engine(event)


@bot.on(events.CallbackQuery(data=b"engbl"))
async def on_engine_browserless(event):
    if not is_owner(event):
        return
    new = not store.browserless
    if new and store.engine == "bridge":
        await event.answer("اول موتور را روی سریع بگذار.", alert=True)
        return
    store.set_setting("browserless", new)
    await event.answer("روشن شد." if new else "خاموش شد.")
    await on_engine(event)


_TOGGLES = {"warm": "warmpath", "apk": "apk_octet", "stop": "stop_on_limit"}
_NUMERIC = {"delay": ("text_send_delay", "تأخیر ارسال (ثانیه)", 0.0, 60.0),
            "conc": ("send_concurrency", "هم‌زمانی (۱ تا ۱۰)", 1, 10),
            "pool": ("pool_max_open", "مرورگر گرم هم‌زمان", 1, 8)}


@bot.on(events.CallbackQuery(pattern=rb"^s:([a-z]+)$"))
async def on_setting_change(event):
    if not is_owner(event):
        return
    key = event.pattern_match.group(1).decode()
    if key in _TOGGLES:
        name = _TOGGLES[key]
        store.set_setting(name, not bool(store.settings.get(name)))
        await event.answer("عوض شد.")
        await on_settings(event)
        return
    if key in _NUMERIC:
        name, label, lo, hi = _NUMERIC[key]
        state[int(event.sender_id)] = {"step": "num", "key": name,
                                       "lo": lo, "hi": hi}
        await _respond(event, logbus.card(f"✏️ {label}", [
            f"• مقدار فعلی: {store.settings.get(name)}",
            f"• عددی بین {lo} و {hi} بفرست.",
        ]), buttons=[[Button.inline("❌ لغو", b"set")]])


# =========================================================================== #
# پیام همگانی
# =========================================================================== #
@bot.on(events.CallbackQuery(data=b"bc"))
async def on_broadcast(event):
    if not is_owner(event):
        return
    n = db.count_customers("active")
    blocked = db.count_customers("blocked")
    state[int(event.sender_id)] = {"step": "bc"}
    await _respond(event, logbus.card("📢 پیام همگانی", [
        f"• گیرندگان: {n} مشتری فعال",
        f"• {blocked} مسدود پیام نمی‌گیرند" if blocked else None,
        "",
        "• متن پیام را بفرست.",
    ]), buttons=[[Button.inline("❌ لغو", b"home")]])


@bot.on(events.CallbackQuery(data=b"bcgo"))
async def on_broadcast_go(event):
    if not is_owner(event):
        return
    st = state.get(int(event.sender_id)) or {}
    text = st.get("text")
    if not text:
        await event.answer("متنی نیست.", alert=True)
        return
    state.pop(int(event.sender_id), None)
    n = db.broadcast(logbus.card("📢 اطلاعیه", [f"• {text}"]), only_active=True)
    await logbus.emit(kind="broadcast", title="📢 پیام همگانی فرستاده شد",
                      rows=[f"• گیرندگان: {n} مشتری", LINE,
                            cards.sanitize(text, 400)],
                      customer_id=None, log_label="پیام همگانی", counted=False)
    await _respond(event, logbus.card("✅ در صف قرار گرفت", [
        f"• {n} مشتری",
        "• ربات مشتری تحویلش می‌دهد (هر ۱۰ ثانیه یک دسته).",
    ]), buttons=[[Button.inline("🏠 خانه", b"home")]])


# =========================================================================== #
# ریست سه‌پله
# =========================================================================== #
@bot.on(events.CallbackQuery(data=b"reset"))
async def on_reset(event):
    if not is_owner(event):
        return
    ps = _pool_status()
    tot, cache = _profiles_size()
    dk_f, dk_t = _disk()
    running = len(db.running_jobs())
    rows = [
        f"• مرورگر گرم: {ps.get('warm', 0)}",
        f"• حجم پروفایل‌ها: {tot:.1f} گیگ",
        f"• از این مقدار کش: {cache:.1f} گیگ",
        f"• دیسک آزاد: {dk_f:.0f} از {dk_t:.0f} گیگ",
        f"• جاب در حال اجرا: {running}",
    ]
    await _respond(event, "🧹 ریست\n" + LINE + "\n\n" + "\n".join(rows) +
                   "\n\n" + LINE +
                   "\n▪ پاک‌سازی سریع و عمیق، کسی را لاگ‌اوت نمی‌کند",
                   buttons=[[Button.inline("🧹 پاک‌سازی سریع", b"rs:1")],
                            [Button.inline("🗂 پاک‌سازی عمیق", b"rs:2")],
                            [Button.inline("🔴 ریست کامل", b"rs:3")],
                            [Button.inline("🏠 خانه", b"home")]])


def _clear_caches() -> float:
    """پوشه‌های **کش خالص** هر پروفایل را پاک می‌کند. گیگابایت آزادشده را می‌دهد.

    سشن ایتا در `IndexedDB` و `Local Storage` است و به آن‌ها دست نمی‌زنیم، پس هیچ
    اکانتی لاگ‌اوت نمی‌شود. این تفکیک کل دلیل وجود پله‌ی ۱ است.
    """
    freed = 0
    base = config.PROFILES_DIR
    if not base.is_dir():
        return 0.0
    for prof in base.iterdir():
        if not prof.is_dir():
            continue
        for rel in _CACHE_DIRS:
            p = prof / rel
            if not p.is_dir():
                continue
            try:
                for root, _d, files in os.walk(p):
                    for f in files:
                        try:
                            freed += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
                shutil.rmtree(p, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
    return freed / 2**30


@bot.on(events.CallbackQuery(pattern=rb"^rs:([123])$"))
async def on_reset_run(event):
    if not is_owner(event):
        return
    tier = int(event.pattern_match.group(1))

    if tier == 3:
        state[int(event.sender_id)] = {"step": "reset3"}
        n = db.owner_totals()["accounts"]
        await _respond(event, logbus.card("🔴 ریست کامل", [
            f"• {n} اکانت کامل پاک می‌شود",
            "• همه‌ی مشتری‌ها باید از اول لاگین کنند",
            "",
            "• برای تأیید بنویس: ریست",
        ]), buttons=[[Button.inline("❌ لغو", b"reset")]])
        return

    running = db.running_jobs()
    if running:
        await event.answer(f"{len(running)} جاب در حال اجراست. اول متوقفشان کن.",
                           alert=True)
        return

    _, dk_before = _disk()
    free_before, _ = _disk()
    closed = 0
    try:
        from capture.pool import pool
        closed = await pool.close_all()
    except Exception:  # noqa: BLE001
        pass
    freed = _clear_caches()

    extra = []
    if tier == 2:
        # کپچرهای قدیمی و PDFها. ⚠️ جدیدترین کپچر هر اکانت باید بماند، وگرنه
        # موتور سریع خلع سلاح می‌شود و همه‌ی ارسال‌ها به مرورگر برمی‌گردند.
        try:
            sess = config.ARTIFACTS_DIR / "sessions"
            if sess.is_dir():
                by_acc: dict = {}
                for p in sess.glob("capall_*.json"):
                    acc = p.name.split("_")[1] if "_" in p.name else ""
                    by_acc.setdefault(acc, []).append(p)
                removed = 0
                for _acc, paths in by_acc.items():
                    paths.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                    for p in paths[1:]:      # جدیدترین را نگه دار
                        try:
                            p.unlink()
                            removed += 1
                        except OSError:
                            pass
                if removed:
                    extra.append(f"• کپچر قدیمی پاک‌شده: {removed}")
            px = config.ARTIFACTS_DIR / "photo_export"
            if px.is_dir():
                n = 0
                for p in px.glob("*.pdf"):
                    try:
                        p.unlink()
                        n += 1
                    except OSError:
                        pass
                if n:
                    extra.append(f"• PDF پاک‌شده: {n}")
        except Exception:  # noqa: BLE001
            pass
        pruned = db.prune_events()
        if pruned:
            extra.append(f"• رویداد قدیمی پاک‌شده: {pruned}")

    free_after, _ = _disk()
    title = "🧹 پاک‌سازی سریع" if tier == 1 else "🗂 پاک‌سازی عمیق"
    rows = [f"• مرورگر بسته‌شده: {closed}",
            f"• کش پاک‌شده: {freed:.1f} گیگ",
            f"• دیسک آزاد: {free_before:.0f} → {free_after:.0f} گیگ", *extra]
    await logbus.emit(kind="reset", title=f"{title} · توسط مالک",
                      rows=rows + ["• لاگ‌اوت: هیچ ✅"],
                      customer_id=None, log_label=title, counted=False)
    await _respond(event, title + "\n" + LINE + "\n\n" + "\n".join(rows) +
                   "\n\n" + LINE + "\n▪ هیچ اکانتی لاگ‌اوت نشد ✅",
                   buttons=[[Button.inline("🔙 ریست", b"reset")],
                            [Button.inline("🏠 خانه", b"home")]])


# =========================================================================== #
# منابع و حالت تعمیر
# =========================================================================== #
@bot.on(events.CallbackQuery(data=b"res"))
async def on_resources(event):
    if not is_owner(event):
        return
    ram_f, ram_t = _ram()
    dk_f, dk_t = _disk()
    tot, cache = _profiles_size()
    ps = _pool_status()
    t = db.owner_totals()
    ping = await shared.server_ping_ms()
    rows = [
        f"• رم: {ram_f:.1f} از {ram_t:.1f} گیگ آزاد" if ram_t else None,
        f"• دیسک: {dk_f:.0f} از {dk_t:.0f} گیگ آزاد" if dk_t else None,
        "",
        f"• مرورگر گرم: {ps.get('warm', 0)} از {ps.get('max_open', '—')}",
        f"• لانچ صرفه‌جویی‌شده: {ps.get('saved_launches', 0)}",
        f"• جاب در حال اجرا: {len(db.running_jobs())}",
        f"• حجم پروفایل‌ها: {tot:.1f} گیگ · کش {cache:.1f}",
        f"• لاگین‌های ۲۴ ساعت: {t['logins_today']}",
    ]
    rows = [r for r in rows if r is not None]
    await _respond(event, "📊 منابع سرور\n" + LINE + "\n\n" + "\n".join(rows) +
                   "\n\n" + LINE +
                   f"\n▪ ایتا: {ping}ms · 🕒 {cards.now_hms()[11:16]}"
                   if ping is not None else "\n▪ ایتا: 🔴 بی‌پاسخ",
                   buttons=[[Button.inline("♻️ بروزرسانی", b"res")],
                            [Button.inline("🏠 خانه", b"home")]])


@bot.on(events.CallbackQuery(data=b"maint"))
async def on_maintenance(event):
    if not is_owner(event):
        return
    on = db.maintenance_on()
    rows = [
        f"• وضعیت: {'🛠 روشن — سرویس بسته' if on else '🟢 خاموش — سرویس فعال'}",
        "",
        "• روشن که بشود:",
        "• هیچ مشتری‌ای کاری شروع نمی‌کند",
        "• جاب‌های در حال اجرا تمام می‌شوند",
        "• پنل مالک عادی کار می‌کند",
    ]
    await _respond(event, "🛠 حالت تعمیر\n" + LINE + "\n\n" + "\n".join(rows) +
                   "\n\n" + LINE + "\n▪ برای وقتی چیزی خراب شد یا حمله شدیم",
                   buttons=[[Button.inline("🟢 خاموش کن" if on else "🛠 روشن کن",
                                           b"maintx")],
                            [Button.inline("🏠 خانه", b"home")]])


@bot.on(events.CallbackQuery(data=b"maintx"))
async def on_maintenance_toggle(event):
    if not is_owner(event):
        return
    new = not db.maintenance_on()
    db.set_maintenance(new)
    await logbus.emit(kind="maintenance",
                      title=f"🛠 حالت تعمیر {'روشن' if new else 'خاموش'} شد",
                      rows=[], customer_id=None,
                      log_label="حالت تعمیر", counted=False)
    await on_maintenance(event)


# =========================================================================== #
# روتر پیام‌های مالک
# =========================================================================== #
@bot.on(events.NewMessage(func=lambda e: e.is_private))
async def on_message(event):
    if not is_owner(event):
        return
    txt = (event.raw_text or "").strip()
    if txt.startswith("/"):
        return
    oid = int(event.sender_id)
    st = state.get(oid)
    if not st:
        return
    step = st.get("step")

    if step == "search":
        state.pop(oid, None)
        found = db.search_customers(txt)
        if not found:
            await event.respond("چیزی پیدا نشد.",
                                buttons=[[Button.inline("🔙 مشتری‌ها", b"custs:all:0")]])
            return
        kb = [[Button.inline(
            f"{'⛔' if c.get('blocked') else '🟢'} {(c.get('name') or c['telegram_id'])} "
            f"· {c['telegram_id']}", f"cust:{c['telegram_id']}".encode())]
            for c in found[:20]]
        kb.append([Button.inline("🔙 مشتری‌ها", b"custs:all:0")])
        await event.respond(logbus.card("🔍 نتیجه‌ی جستجو",
                                        [f"• {len(found)} مورد"]), buttons=kb)
        return

    if step == "msg":
        uid = int(st["uid"])
        state.pop(oid, None)
        db.enqueue_notification(uid, logbus.card("💬 پیام از پشتیبانی", [f"• {txt}"]))
        await logbus.emit(kind="owner_msg", title="💬 پیام مالک به مشتری",
                          rows=[LINE, cards.sanitize(txt, 400)],
                          customer_id=uid, log_label="پیام مالک", counted=False)
        await event.respond("در صف قرار گرفت.",
                           buttons=[[Button.inline("🔙 پروفایل", f"cust:{uid}".encode())]])
        return

    if step == "note":
        uid = int(st["uid"])
        state.pop(oid, None)
        db.set_note(uid, txt)
        await event.respond("یادداشت ذخیره شد.",
                           buttons=[[Button.inline("🔙 پروفایل", f"cust:{uid}".encode())]])
        return

    if step == "num":
        name, lo, hi = st["key"], st["lo"], st["hi"]
        try:
            val = float(txt.replace("٫", ".")) if isinstance(lo, float) else int(txt)
        except ValueError:
            await event.respond("عدد بفرست.")
            return
        if not (lo <= val <= hi):
            await event.respond(f"باید بین {lo} و {hi} باشد.")
            return
        state.pop(oid, None)
        store.set_setting(name, val)
        if name == "pool_max_open":
            try:
                from capture.pool import pool
                pool.set_max_open(int(val))
            except Exception:  # noqa: BLE001
                pass
        await event.respond(f"ذخیره شد: {val}",
                           buttons=[[Button.inline("🔙 تنظیمات", b"set")]])
        return

    if step == "bc":
        st["text"] = txt
        st["step"] = "bc_confirm"
        n = db.count_customers("active")
        await event.respond("📢 تأیید پیام همگانی\n" + LINE + "\n\n" +
                            f"• گیرندگان: {n} مشتری\n\n" +
                            cards.sanitize(txt, 400) + "\n\n" + LINE +
                            "\n▪ بفرستم؟",
                            buttons=[[Button.inline("✅ بفرست", b"bcgo"),
                                      Button.inline("❌ لغو", b"home")]])
        return

    if step == "reset3":
        state.pop(oid, None)
        if txt.strip() != "ریست":
            await event.respond("تأیید نشد. چیزی پاک نشد.",
                                buttons=[[Button.inline("🔙 ریست", b"reset")]])
            return
        running = db.running_jobs()
        if running:
            await event.respond(f"{len(running)} جاب در حال اجراست. اول متوقفشان کن.")
            return
        n = 0
        try:
            from capture.pool import pool
            await pool.close_all()
        except Exception:  # noqa: BLE001
            pass
        for c in db.list_customers_page(0, 10000, "all"):
            uid = int(c["telegram_id"])
            for a in db.list_accounts(uid):
                shared.delete_account_profile(str(a["id"]))
                db.delete_account(uid, int(a["id"]))
                n += 1
        await logbus.emit(kind="reset_full", title="🔴 ریست کامل · توسط مالک",
                          rows=[f"• اکانت‌های پاک‌شده: {n}"],
                          customer_id=None, log_label="ریست کامل", counted=False)
        await event.respond(logbus.card("🔴 ریست کامل انجام شد", [
            f"• {n} اکانت پاک شد",
            "• همه‌ی مشتری‌ها باید از اول لاگین کنند.",
        ]), buttons=[[Button.inline("🏠 خانه", b"home")]])
        return


# =========================================================================== #
# حلقه‌ها و راه‌اندازی
# =========================================================================== #
async def janitor_loop() -> None:
    """نگهبان شبانه: پله‌ی ۱ خودکار + جاروی رویدادهای قدیمی.

    هدف این است که هیچ‌وقت لازم نشود دکمه‌ی ریست را دستی بزنی.
    """
    while True:
        await asyncio.sleep(3600)
        try:
            hour = time.localtime().tm_hour
            if hour != 3:
                continue
            if db.running_jobs():
                continue
            closed = 0
            try:
                from capture.pool import pool
                closed = await pool.close_all()
            except Exception:  # noqa: BLE001
                pass
            freed = _clear_caches()
            pruned = db.prune_events()
            if closed or freed > 0.1 or pruned:
                await logbus.emit(
                    kind="janitor", title="🧹 نگهبان شبانه",
                    rows=[f"• مرورگر بسته‌شده: {closed}",
                          f"• کش پاک‌شده: {freed:.1f} گیگ",
                          f"• رویداد قدیمی: {pruned}",
                          "• لاگ‌اوت: هیچ ✅"],
                    customer_id=None, log_label="نگهبان شبانه", counted=False)
        except Exception as exc:  # noqa: BLE001
            print(f"[janitor] {exc}", flush=True)


async def blocked_summary_loop() -> None:
    """خلاصه‌ی روزانه‌ی تلاش کاربران مسدود.

    شمارش در حافظه است، پس یک اسپمر با ۴۱ تلاش **یک** پیام تولید می‌کند نه ۴۱ تا —
    که همان چیزی است که «کاربر مسدود صفر بار روی سرور» را حفظ می‌کند.
    """
    while True:
        await asyncio.sleep(86400)
        try:
            snap = ratelimit.blocked_attempts_snapshot(reset=True)
            if not snap:
                continue
            rows = [f"• کاربران مسدود: {len(snap)}",
                    f"• کل تلاش: {sum(n for _u, n, _t in snap)}", ""]
            for uid, n, last in snap[:10]:
                rows.append(f"• {uid} · {n} تلاش · آخری "
                            f"{time.strftime('%H:%M', time.localtime(last))}")
            await logbus.emit(kind="blocked_summary",
                              title="🚫 تلاش کاربران مسدود · خلاصه‌ی روز",
                              rows=rows, customer_id=None,
                              log_label="خلاصه‌ی مسدودها", counted=False)
        except Exception as exc:  # noqa: BLE001
            print(f"[blocked summary] {exc}", flush=True)


async def amain() -> None:
    problems = config.validate_owner()
    if problems:
        raise SystemExit("تنظیمات ناقص است (.env): " + "، ".join(problems))
    config.ensure_dirs()
    db.init()
    ratelimit.load()

    await bot.start(bot_token=config.OWNER_BOT_TOKEN)
    logbus.bind(bot, config.OWNER_ID)

    asyncio.create_task(janitor_loop())
    asyncio.create_task(blocked_summary_loop())

    t = db.owner_totals()
    await logbus.to_group(logbus.card("🤖 پنل مالک آنلاین شد", [
        f"• نسخه: {config.BOT_VERSION}",
        f"• موتور: {_engine_label(store.engine)}",
        f"• مشتری‌ها: {t['customers']} · اکانت‌ها: {t['accounts']}",
        f"• 🕒 {cards.now_hms()}",
    ]))
    print("owner bot running", flush=True)
    await bot.run_until_disconnected()
