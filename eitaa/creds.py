"""
eitaa/creds.py — بیرون‌کشیدنِ api_id/api_hash ایتا از بسته‌ی JS خودش.
=====================================================================

**این api_id ایتاست، نه تلگرام.** دو چیزِ کاملاً متفاوت‌اند: `API_ID` در `.env`
مالِ Telethon است (برای ربات‌ها)، و این یکی مالِ MTProto ایتاست (برای لاگینِ
اکانت‌های ایتا).

چرا لازم شد: `eitaa/login_flow.resolve_api_creds()` این مقادیر را از سه جا
می‌خواهد و روی سرور هر سه خالی بود، پس لاگین با این خطا می‌مرد:

    missing api_id/api_hash (set EITAA_API_ID/EITAA_API_HASH or run a capture first)

سه منبعش:
  ۱. متغیرِ محیطی `EITAA_API_ID` / `EITAA_API_HASH`
  ۲. `artifacts/**/params.json` از یک capture قبلی
  ۳. از داخلِ صفحه: `window.Config.App` / `window.App` / `window.appConfig`
     — که در بیلدِ فشرده‌ی امروزِ ایتا در دسترس نیست و `null` می‌دهد.

این اسکریپت منبعِ ۲ را می‌سازد: بسته‌های JS ایتا را می‌گیرد و همان الگوهایی را
که `capture/extract_params.py` پروژه‌ی مرجع استفاده می‌کرد روی آن‌ها می‌زند
(عیناً همان دو رجکس)، بعد نتیجه را در `artifacts/params.json` می‌نویسد — همان
مسیری که `login_flow` خودش می‌گردد، پس هیچ تنظیمِ دستی لازم نیست.

این مقادیر **ثابت‌های عمومیِ کلاینت**اند و در هر مرورگری که ایتا را باز کند
وجود دارند؛ رمز یا اطلاعاتِ شخصی نیستند.

بدونِ مرورگر و بدونِ وابستگیِ جدید (فقط کتابخانه‌ی استاندارد).

اجرا روی سرور:

    cd /opt/dauroo
    sudo -u dauroo venv/bin/python -m eitaa.creds

    # اگر ایتا فقط از داخلِ تونل در دسترس بود (معمولاً نیست، ایتا ایرانی است):
    sudo -u dauroo venv/bin/python -m eitaa.creds --socks 1080
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin

from config import config

#: **عیناً** دو الگوی `capture/extract_params.py` پروژه‌ی مرجع. تغییرشان نده:
#: ایتا وب همان Telegram Web K است و ساختارِ `{id: N, hash: '…'}` از آنجا می‌آید.
RE_API = re.compile(r"""id\s*:\s*(\d{3,9})\s*,\s*hash\s*:\s*['"]([0-9a-fA-F]{32})['"]""")
RE_API_REV = re.compile(r"""hash\s*:\s*['"]([0-9a-fA-F]{32})['"]\s*,\s*id\s*:\s*(\d{3,9})""")

#: آدرسِ اسکریپت‌ها در HTML، و chunkهایی که خودِ بسته‌ها به هم ارجاع می‌دهند.
RE_SCRIPT_SRC = re.compile(r"""<script[^>]+src\s*=\s*['"]([^'"]+)['"]""", re.I)
RE_JS_CHUNK = re.compile(r"""['"]([\w./-]+\.js)['"]""")

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def _p(msg: str = "") -> None:
    print(msg, flush=True)


def _opener(socks_port: int | None):
    """opener معمولی، یا از داخلِ تونلِ SOCKS اگر خواسته شود.

    ایتا ایرانی است و از سرورِ ایران مستقیم در دسترس است، پس معمولاً تونل لازم
    نیست — گزینه‌اش فقط برای وقتی است که سرور جای دیگری باشد.
    """
    if not socks_port:
        return urllib.request.build_opener()
    try:
        import socks  # type: ignore
        import sockshandler  # type: ignore
        return urllib.request.build_opener(
            sockshandler.SocksiPyHandler(socks.SOCKS5, "127.0.0.1", int(socks_port)))
    except Exception:
        _p("⚠️  کتابخانه‌ی SOCKS برای urllib نیست؛ مستقیم امتحان می‌کنم.")
        return urllib.request.build_opener()


def _get(opener, url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with opener.open(req, timeout=timeout) as r:
        raw = r.read()
    return raw.decode("utf-8", errors="replace")


def extract_from_text(text: str) -> list:
    """کاندیداهای (api_id, api_hash) در یک متن. همان منطقِ پروژه‌ی مرجع."""
    out = []
    for m in RE_API.finditer(text):
        out.append((int(m.group(1)), m.group(2)))
    for m in RE_API_REV.finditer(text):
        out.append((int(m.group(2)), m.group(1)))
    return out


def find_creds(base: str, socks_port: int | None = None,
               max_assets: int = 40) -> tuple:
    """(api_id, api_hash, جزئیات) — یا (0, "", جزئیات) اگر پیدا نشد."""
    opener = _opener(socks_port)
    detail = {"assets": 0, "candidates": [], "errors": []}

    _p(f"• گرفتنِ صفحه‌ی اصلی: {base}")
    try:
        html = _get(opener, base)
    except Exception as exc:  # noqa: BLE001
        return 0, "", {**detail, "errors": [f"صفحه‌ی اصلی نیامد: {exc!r}"]}
    _p(f"  ✅ {len(html)} بایت")

    # آدرسِ همه‌ی اسکریپت‌ها را جمع کن (از تگ‌های script و ارجاع‌های داخلی).
    urls, seen = [], set()
    for rel in RE_SCRIPT_SRC.findall(html):
        u = urljoin(base, rel)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    if not urls:
        # بعضی بیلدها اسکریپت را با import پویا می‌آورند؛ نامِ chunkها را از
        # خودِ HTML بردار.
        for rel in RE_JS_CHUNK.findall(html):
            u = urljoin(base, rel)
            if u not in seen:
                seen.add(u)
                urls.append(u)
    _p(f"• {len(urls)} اسکریپت در صفحه")

    counter: Counter = Counter()
    queue = list(urls[:max_assets])
    visited = set()
    while queue:
        u = queue.pop(0)
        if u in visited or len(visited) >= max_assets:
            continue
        visited.add(u)
        try:
            js = _get(opener, u)
        except Exception as exc:  # noqa: BLE001
            detail["errors"].append(f"{u.rsplit('/', 1)[-1]}: {exc!r}")
            continue
        detail["assets"] += 1
        found = extract_from_text(js)
        for pair in found:
            counter[pair] += 1
        if found:
            _p(f"  ✅ {u.rsplit('/', 1)[-1]} → {len(found)} کاندیدا")
        # chunkهای ارجاع‌داده‌شده را هم دنبال کن (بسته‌ی اصلی معمولاً بقیه را
        # نام می‌برد و مقادیر ممکن است در یکی از آن‌ها باشد).
        if len(visited) < max_assets:
            for rel in RE_JS_CHUNK.findall(js)[:60]:
                nu = urljoin(u, rel)
                if nu not in visited and nu not in queue:
                    queue.append(nu)

    detail["candidates"] = [{"id": i, "hash": h, "seen": n}
                            for (i, h), n in counter.most_common()]
    if not counter:
        return 0, "", detail
    (aid, ah), _n = counter.most_common(1)[0]
    return aid, ah, detail


def write_params(api_id: int, api_hash: str) -> Path:
    """در `artifacts/params.json` می‌نویسد — همان جایی که login_flow می‌گردد."""
    out = Path(config.ARTIFACTS_DIR) / "params.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {}
    if out.exists():
        try:
            payload = json.loads(out.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            payload = {}
    payload["api_id"] = int(api_id)
    payload["api_hash"] = str(api_hash)
    payload["source"] = "eitaa.creds"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out


def main() -> int:
    args = sys.argv[1:]
    socks_port = None
    if "--socks" in args:
        i = args.index("--socks")
        try:
            socks_port = int(args[i + 1])
        except (IndexError, ValueError):
            _p("استفاده: --socks <پورت>")
            return 2
    base = config.EITAA_WEB_URL.rstrip("/") + "/"

    _p("استخراجِ api_id/api_hash ایتا")
    _p("=" * 46)
    aid, ah, detail = find_creds(base, socks_port)
    _p("")
    _p(f"• اسکریپت‌های خوانده‌شده: {detail['assets']}")
    if detail["errors"]:
        _p(f"• خطاها: {len(detail['errors'])}")
        for e in detail["errors"][:5]:
            _p(f"    {e}")

    if not aid:
        _p("")
        _p("❌ پیدا نشد.")
        _p("   یعنی ایتا مقادیر را در بسته پنهان/تقسیم کرده. راهِ مطمئن:")
        _p("   همین دو مقدار را از سرورِ ربات شخصی خودت بردار:")
        _p("     grep EITAA_API /path/to/Mkwlsoso/.env")
        _p("     grep -rh api_hash /path/to/Mkwlsoso/artifacts/*/params.json")
        _p("   بعد اینجا بگذار:")
        _p("     EITAA_API_ID=…  و  EITAA_API_HASH=…  در /opt/dauroo/.env")
        return 1

    _p("")
    _p(f"✅ api_id  : {aid}")
    _p(f"✅ api_hash: {ah}")
    if len(detail["candidates"]) > 1:
        _p("")
        _p("• کاندیداهای دیگر (اگر لاگین نشد، این‌ها را امتحان کن):")
        for c in detail["candidates"][1:4]:
            _p(f"    id={c['id']} hash={c['hash']} (×{c['seen']})")

    path = write_params(aid, ah)
    _p("")
    _p(f"✅ نوشته شد در {path}")
    _p("   `login_flow` خودش همین‌جا را می‌گردد، پس تنظیمِ دستی لازم نیست.")
    _p("")
    _p("حالا سرویس‌ها را ری‌استارت کن و لاگین را امتحان کن:")
    _p("   sudo systemctl restart dauroo-owner dauroo-customer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
