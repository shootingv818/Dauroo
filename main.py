"""
main.py — نقطه‌ی ورود. نقش از **آرگومان** انتخاب می‌شود.
=======================================================

یک کدبیس، دو پروسه، دو توکن:

    python main.py owner       # پنل خصوصی مالک
    python main.py customer    # رباتی که مشتری‌ها استارت می‌زنند

**چرا آرگومان و نه فقط متغیرِ محیطی `MODE`:**
یک بار روی سرورِ واقعی هر دو سرویس، ربات **مالک** را اجرا کردند. علتش این بود که
یونیتِ systemd هم `EnvironmentFile=/opt/dauroo/.env` داشت (که `MODE=owner` در آن
بود) و هم `Environment=MODE=customer`؛ و مقدارِ فایل برنده شد. نتیجه‌اش دو
خرابیِ پشت‌سرهم بود:

  * پروسه‌ی «مشتری» پورتِ SOCKS مالک (۱۰۸۰) را می‌گرفت،
  * و هر دو یک فایلِ سشنِ Telethon را باز می‌کردند، که به
    `sqlite3.OperationalError: database is locked` و حلقه‌ی ری‌استارت رسید.

نقش، **هویتِ پروسه** است؛ نباید در فایلِ تنظیماتِ مشترکی بنشیند که هر دو سرویس
می‌خوانند. آرگومان صریح است و هیچ فایل محیطی نمی‌تواند بازنویسی‌اش کند.

`MODE` در محیط هنوز به‌عنوان پشتیبان کار می‌کند (برای اجرای دستی)، ولی اگر
آرگومان بدهی، آرگومان برنده است.

هر دو پروسه یک فایل SQLite را با WAL و busy_timeout به اشتراک می‌گذارند، و حالت
تعمیر از طریق یک فایل flag رد می‌شود تا پروسه‌ی مشتری لازم نباشد چیزی از سمت مالک
import کند.
"""
from __future__ import annotations

import asyncio
import os
import sys

_VALID = ("owner", "customer")


def _pick_mode(argv) -> str:
    """نقش را از argv بگیر، وگرنه از `MODE`، وگرنه owner.

    **قبل از import کردنِ config** صدا زده می‌شود و `os.environ["MODE"]` را ست
    می‌کند، چون `config.MODE` هنگامِ ساختِ کلاس از محیط خوانده می‌شود و
    `config.relay_local_port()` هم بر اساسِ همان، پورتِ SOCKS این پروسه را
    انتخاب می‌کند. اگر دیرتر ست شود، پروسه با پورتِ نقشِ دیگر بالا می‌آید.
    """
    args = [a for a in argv[1:] if not a.startswith("-")]
    mode = (args[0] if args else os.environ.get("MODE", "owner") or "owner")
    mode = mode.strip().lower()
    if mode not in _VALID:
        print(f"نقشِ نامعتبر: {mode!r} — یکی از {' یا '.join(_VALID)}",
              file=sys.stderr)
        raise SystemExit(2)
    # محیط را هم‌راستا کن تا هر کسی که بعداً `MODE` را می‌خواند همین را ببیند.
    os.environ["MODE"] = mode
    return mode


def main() -> None:
    mode = _pick_mode(sys.argv)
    from config import config
    # اگر config پیش‌تر (به هر دلیلی) import شده بود، مقدارِ کلاس را هم بچرخان.
    config.MODE = mode
    print(f"نقش: {mode} · پورتِ SOCKS: {config.relay_local_port()}", flush=True)

    if mode == "customer":
        import customer_bot
        asyncio.run(customer_bot.amain())
    else:
        import owner_bot
        asyncio.run(owner_bot.amain())


if __name__ == "__main__":
    main()
