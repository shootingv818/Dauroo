"""
relay/fetch.py — دانلود روی سرورِ relay، بعد آوردنش با SFTP.
============================================================

چرا این وجود دارد: CDN پلی‌رایت از ایران با ۴۰۳ جغرافیایی رد می‌شود، و دانلودِ
مستقیم از داخلِ تونلِ SOCKS برای فایلِ کوچک (ffmpeg ~۲ مگ) کار کرد ولی برای
کرومیوم (~۱۷۰ مگ) نه. این مسیر مشکل را از ریشه دور می‌زند:

    ۱) روی خودِ relay (که خارج است) با curl دانلود کن — آنجا فیلتری نیست.
    ۲) فایل را با SFTP از همان اتصالِ SSH بیاور.

هیچ نرم‌افزار تازه‌ای لازم نیست: `asyncssh` از قبل برای تونل نصب است و SFTP
دارد؛ `curl` هم روی هر VPS معمولی هست (و اگر نبود، نصبش را امتحان می‌کنیم).

اعتبارنامه‌ی relay از همان جدولِ `relays` خوانده و رمزگشایی می‌شود، پس رمز
جایی تکرار نمی‌شود.

اجرا:
    python -m relay.fetch <url> <مسیر-مقصدِ-محلی>
    python -m relay.fetch --playwright     # همه‌ی بسته‌های پلی‌رایت
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile

import db
from config import config
from relay import crypto


def _p(msg: str = "") -> None:
    print(msg, flush=True)


def _pick_relay():
    """اولین relayِ فعال، با رمزِ رمزگشایی‌شده."""
    relays = db.list_relays(include_disabled=False)
    if not relays:
        return None, "هیچ relayی در دیتابیس نیست"
    # همان ترتیبی که مدیر استفاده می‌کند: اولویت صعودی.
    r = relays[0]
    pw = ""
    if r.get("secret_enc"):
        try:
            pw = crypto.decrypt(r["secret_enc"], config.relay_secret_key())
        except Exception as exc:  # noqa: BLE001
            return None, f"رمزگشاییِ رمز شکست خورد: {exc}"
    return (r, pw), ""


async def fetch_via_relay(url: str, dest: str) -> bool:
    """`url` را روی relay دانلود و به `dest` منتقل می‌کند."""
    try:
        import asyncssh
    except Exception:  # noqa: BLE001
        _p("❌ asyncssh نصب نیست")
        return False

    picked, err = _pick_relay()
    if not picked:
        _p(f"❌ {err}")
        return False
    r, pw = picked
    host, port, user = r["host"], int(r["ssh_port"] or 22), r.get("username") or "root"
    _p(f"• relay: {user}@{host}:{port}")

    remote = "/tmp/pw_" + os.path.basename(url)
    try:
        async with asyncssh.connect(host, port=port, username=user, password=pw,
                                    known_hosts=None) as conn:
            _p("✅ به relay وصل شد")

            # curl روی relay هست؟ اگر نه، نصبش کن (بی‌صدا، و اگر نشد بی‌خیال).
            res = await conn.run("command -v curl || command -v wget", check=False)
            if not (res.stdout or "").strip():
                _p("• curl/wget روی relay نیست؛ نصبش می‌کنم…")
                await conn.run(
                    "apt-get update -qq && apt-get install -y -qq curl", check=False)

            _p(f"↓ دانلود روی relay: {os.path.basename(url)}")
            cmd = (f"curl -fsSL --retry 5 --retry-delay 3 -o {remote!r} {url!r} "
                   f"|| wget -q -O {remote!r} {url!r}")
            res = await conn.run(cmd, check=False)
            if res.exit_status != 0:
                _p(f"❌ دانلود روی relay شکست خورد: "
                   f"{(res.stderr or '')[:300]}")
                return False

            # حجم را چک کن — یک صفحه‌ی خطای HTML هم «موفق» به‌نظر می‌رسد.
            res = await conn.run(f"stat -c%s {remote!r}", check=False)
            try:
                size = int((res.stdout or "0").strip())
            except ValueError:
                size = 0
            if size < 100000:
                _p(f"❌ فایلِ دانلودشده خیلی کوچک است ({size} بایت) — "
                   f"احتمالاً صفحه‌ی خطا، نه فایلِ واقعی")
                await conn.run(f"rm -f {remote!r}", check=False)
                return False
            _p(f"✅ روی relay دانلود شد ({size / 2**20:.1f} مگ)")

            _p("↓ انتقال با SFTP…")
            async with conn.start_sftp_client() as sftp:
                await sftp.get(remote, dest)
            await conn.run(f"rm -f {remote!r}", check=False)

        local = os.path.getsize(dest) if os.path.exists(dest) else 0
        if local != size:
            _p(f"❌ حجمِ منتقل‌شده نمی‌خواند ({local} != {size})")
            return False
        _p(f"✅ منتقل شد: {dest}")
        return True
    except Exception as exc:  # noqa: BLE001
        _p(f"❌ {exc!r}")
        return False


def _playwright_targets():
    """[(url, install_dir)] از خودِ `playwright install --dry-run`."""
    try:
        out = subprocess.run([sys.executable, "-m", "playwright", "install",
                              "--dry-run", "chromium"],
                             capture_output=True, text=True, timeout=120).stdout
    except Exception as exc:  # noqa: BLE001
        _p(f"❌ اجرای playwright --dry-run نشد: {exc}")
        return []
    urls, dirs = [], []
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("Download url:"):
            urls.append(s.split(":", 1)[1].strip())
        elif s.startswith("Install location:"):
            dirs.append(s.split(":", 1)[1].strip())
    # همان بازنویسیِ آدرس که در install-chromium.sh هست: آدرسی که --dry-run
    # می‌دهد خام است، ولی آنچه واقعاً سرو می‌شود شکلِ dbazure است.
    fixed = []
    for u in urls:
        if "/dbazure/download/playwright/" not in u:
            u = u.replace("https://cdn.playwright.dev/builds/",
                          "https://cdn.playwright.dev/dbazure/download/playwright/builds/")
        fixed.append(u)
    return list(zip(fixed, dirs))


async def _playwright_all() -> int:
    targets = _playwright_targets()
    if not targets:
        _p("❌ چیزی برای دانلود پیدا نشد")
        return 1
    _p(f"• {len(targets)} بسته")
    if not shutil.which("unzip"):
        _p("⚠️  unzip نیست؛ با python باز می‌کنیم")
    failed = 0
    for url, dirn in targets:
        name = os.path.basename(url)
        exe_found = False
        # اگر از قبل باز شده، رد کن.
        for root, _d, files in os.walk(dirn if os.path.isdir(dirn) else "."):
            if "chrome" in files or "chrome-headless-shell" in files:
                exe_found = True
                break
        if exe_found:
            _p(f"✅ {name} از قبل هست")
            continue
        _p("")
        _p(f"── {name} ──")
        tmp = os.path.join(tempfile.gettempdir(), name)
        if not await fetch_via_relay(url, tmp):
            failed += 1
            continue
        os.makedirs(dirn, exist_ok=True)
        try:
            import zipfile
            with zipfile.ZipFile(tmp) as z:
                z.extractall(dirn)
            # بیتِ اجرا: zipfile مجوزها را نگه نمی‌دارد و بدونِ آن پلی‌رایت
            # می‌گوید فایل نیست/اجرا نمی‌شود.
            for root, _d, files in os.walk(dirn):
                for f in files:
                    if f in ("chrome", "chrome-headless-shell", "ffmpeg-linux") \
                       or f.endswith(".sh"):
                        os.chmod(os.path.join(root, f), 0o755)
            _p(f"✅ باز شد در {dirn}")
        except Exception as exc:  # noqa: BLE001
            _p(f"❌ بازکردن شکست خورد: {exc}")
            failed += 1
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    return 1 if failed else 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        _p(__doc__ or "")
        return 2
    if args[0] == "--playwright":
        return asyncio.run(_playwright_all())
    if len(args) < 2:
        _p("استفاده: python -m relay.fetch <url> <مقصد>")
        return 2
    return 0 if asyncio.run(fetch_via_relay(args[0], args[1])) else 1


if __name__ == "__main__":
    raise SystemExit(main())
