"""
main.py — نقطه‌ی ورود. نقش با `MODE` انتخاب می‌شود.
=================================================

یک کدبیس، دو پروسه، دو توکن:

    MODE=owner    python main.py     # پنل خصوصی مالک
    MODE=customer python main.py     # رباتی که مشتری‌ها استارت می‌زنند

هر دو پروسه یک فایل SQLite را با WAL و busy_timeout به اشتراک می‌گذارند، و حالت
تعمیر از طریق یک فایل flag رد می‌شود تا پروسه‌ی مشتری لازم نباشد چیزی از سمت مالک
import کند.
"""
from __future__ import annotations

import asyncio
import sys

from config import config


def main() -> None:
    mode = (config.MODE or "owner").strip().lower()
    if mode == "customer":
        import customer_bot
        asyncio.run(customer_bot.amain())
    elif mode == "owner":
        import owner_bot
        asyncio.run(owner_bot.amain())
    else:
        print(f"MODE نامعتبر: {mode!r} — یکی از owner یا customer", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
