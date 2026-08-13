"""
relay/crypto.py — رمزنگاری «در حالت سکون» برای رمز عبور relayها.
================================================================

قاعده‌ی سفت: **رمز SSH هیچ‌وقت به‌صورت متن ساده در دیتابیس یا لاگ نوشته نمی‌شود.**
رمز فقط رمزنگاری‌شده ذخیره می‌شود و کلیدش از متغیر محیطی می‌آید
(`RELAY_SECRET_KEY`، و اگر نبود `RAW_ENCRYPTION_KEY`). اگر هیچ کلیدی ست نشده
باشد، ذخیره‌ی رمز **رد** می‌شود تا هرگز چیزی محافظت‌نشده ننشیند.

چرا با کتابخانه‌ی استاندارد، نه `cryptography`/Fernet؟
    این سرویس روی هاستی نصب می‌شود که ممکن است دسترسی به PyPI نداشته باشد
    (همین محیط ساخت هم نداشت)، و افزودن یک وابستگی که کامپایل C می‌خواهد یعنی
    ریسک «نصب نمی‌شود سرِ استقرار». پس یک ساختِ استانداردِ ثابت‌شده با
    `hashlib`/`hmac` پیاده شده که هیچ وابستگی‌ای ندارد:

        encrypt-then-MAC، با کلیدجریانِ HMAC-SHA256 در حالت شمارنده (CTR).

    * از کلیدِ خام، دو زیرکلید مشتق می‌شود (یکی برای رمزنگاری، یکی برای MAC)،
      تا کلیدِ رمزنگاری و کلیدِ اصالت هیچ‌وقت یکی نباشند.
    * یک nonce تصادفی ۱۶ بایتی برای هر رمزنگاری ساخته می‌شود، پس دو بار
      رمزکردنِ یک رمز، دو خروجی متفاوت می‌دهد.
    * کلیدجریان = زنجیره‌ی HMAC(enc_key, nonce || counter) و متنِ رمز با آن
      XOR می‌شود (CTR).
    * برچسبِ اصالت = HMAC(mac_key, nonce || ciphertext) و **قبل** از رمزگشایی
      با مقایسه‌ی زمان‌ثابت بررسی می‌شود (encrypt-then-MAC، جلوگیری از دستکاری).

    اگر روزی `cryptography` روی هاست موجود شد، جایگزینی با Fernet بی‌دردسر است:
    فقط `encrypt`/`decrypt` را عوض کن؛ بقیه‌ی کد این ماژول را نمی‌شناسد. توکن‌ها
    با پیشوند نسخه (`v1:`) ذخیره می‌شوند تا مهاجرت قابل‌تشخیص باشد.

این ماژول به `config`/`db` وابسته نیست تا مستقل و آسان‌تست بماند؛ کلید را
فراخواننده می‌دهد.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

_VERSION = b"v1"
_NONCE_LEN = 16
_TAG_LEN = 32  # HMAC-SHA256
_BLOCK = 32    # اندازه‌ی هر بلوکِ کلیدجریان (خروجی SHA256)


class DecryptError(Exception):
    """توکن دستکاری شده، خراب است، یا با کلید دیگری رمز شده."""


def _derive(key: str) -> tuple[bytes, bytes]:
    """کلیدِ خام (رشته) → (enc_key, mac_key) با جداسازی دامنه.

    خودِ کلیدِ خام مستقیم استفاده نمی‌شود؛ دو زیرکلیدِ مستقل از آن مشتق می‌شود تا
    نقشِ رمزنگاری و نقشِ اصالت درهم نروند.
    """
    root = (key or "").encode("utf-8")
    enc = hashlib.sha256(b"dauroo-relay-enc\x00" + root).digest()
    mac = hashlib.sha256(b"dauroo-relay-mac\x00" + root).digest()
    return enc, mac


def _keystream(enc_key: bytes, nonce: bytes, length: int) -> bytes:
    """کلیدجریانِ CTR: HMAC(enc_key, nonce || counter) را تا طول لازم زنجیر می‌کند."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(
            enc_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def is_key_set(key: str) -> bool:
    return bool((key or "").strip())


def encrypt(plaintext: str, key: str) -> str:
    """`plaintext` را با `key` رمز می‌کند و یک توکنِ base64 برمی‌گرداند.

    اگر کلید خالی باشد `ValueError` می‌دهد — عمداً، تا هیچ‌وقت رمز محافظت‌نشده
    ذخیره نشود. فراخواننده باید قبلش `is_key_set` را چک کند.
    """
    if not is_key_set(key):
        raise ValueError("کلید رمزنگاری ست نشده — رمز relay ذخیره نمی‌شود")
    enc_key, mac_key = _derive(key)
    data = (plaintext or "").encode("utf-8")
    nonce = os.urandom(_NONCE_LEN)
    ct = bytes(b ^ k for b, k in zip(data, _keystream(enc_key, nonce, len(data))))
    tag = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(nonce + ct + tag).decode("ascii")
    return _VERSION.decode("ascii") + ":" + token


def decrypt(token: str, key: str) -> str:
    """توکن را رمزگشایی می‌کند. اگر دستکاری/خراب/کلیدِ اشتباه بود `DecryptError`."""
    if not is_key_set(key):
        raise DecryptError("کلید رمزنگاری ست نشده")
    if not token or ":" not in token:
        raise DecryptError("قالب توکن نامعتبر است")
    ver, _, body = token.partition(":")
    if ver.encode("ascii") != _VERSION:
        raise DecryptError(f"نسخه‌ی ناشناخته‌ی توکن: {ver}")
    try:
        blob = base64.urlsafe_b64decode(body.encode("ascii"))
    except Exception as exc:  # noqa: BLE001
        raise DecryptError("base64 نامعتبر") from exc
    if len(blob) < _NONCE_LEN + _TAG_LEN:
        raise DecryptError("توکن کوتاه‌تر از حد لازم")
    nonce = blob[:_NONCE_LEN]
    tag = blob[-_TAG_LEN:]
    ct = blob[_NONCE_LEN:-_TAG_LEN]
    enc_key, mac_key = _derive(key)
    expected = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    # مقایسه‌ی زمان‌ثابت: قبل از هر رمزگشایی، اصالت را تأیید کن.
    if not hmac.compare_digest(tag, expected):
        raise DecryptError("برچسب اصالت نمی‌خواند (دستکاری یا کلید اشتباه)")
    pt = bytes(b ^ k for b, k in zip(ct, _keystream(enc_key, nonce, len(ct))))
    return pt.decode("utf-8", errors="strict")


def mask(secret: str) -> str:
    """برای نمایش/لاگ: هرگز رمز کامل، فقط طول را نشان بده."""
    n = len(secret or "")
    if n == 0:
        return "—"
    return "•" * min(n, 8) + (f" ({n} کاراکتر)" if n > 8 else "")
