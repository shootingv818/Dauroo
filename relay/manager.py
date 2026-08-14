"""
relay/manager.py — مدیرِ تونلِ SSH به تلگرام، با failover هوشمند.
=================================================================

یک نمونه در هر پروسه‌ی ربات زندگی می‌کند (مالک و مشتری جدا)، یک relay را از
لیستِ فعال انتخاب می‌کند، با آن یک اتصال SSH برقرار می‌کند و روی
`127.0.0.1:<local_port>` یک شنونده‌ی SOCKS5 باز می‌کند که ترافیک را از داخلِ همان
اتصال SSH به بیرون هدایت می‌کند. **فقط** کلاینت Telethon به این پورت اشاره می‌کند،
پس تنها ترافیک تلگرام از تونل رد می‌شود — این یک پروکسیِ سیستمی نیست.

چرا asyncssh، نه spawn کردن `ssh -D`/autossh؟
    * تونل داخلِ همان event loopِ ربات است؛ نه پروسه‌ی جدا، نه وابستگی به
      systemd/autossh روی هاست. یعنی «مدیریت از داخل ربات» که خواسته شده.
    * سلامت و قطعیِ اتصال را مستقیم می‌بینیم (رویداد connection_lost و
      keepalive)، نه اینکه حدس بزنیم زیرپردازه زنده است یا نه.
    * failover و backoff را خودمان کنترل می‌کنیم، هماهنگ با health-checkِ واقعیِ
      تلگرام.
    ضررش: یک وابستگی می‌خواهد (`asyncssh`) و کمی کدِ بیشتر. اگر روی هاست نصب
    نباشد، مدیر با پیام روشن شکست می‌خورد و متوقف نمی‌شود (import آن تنبل است).

چرا SOCKS5 (شبیه `ssh -D`)، نه یک کانالِ مستقیم به `api.telegram.org:443`؟
    Telethon با MTProto به **چند** دیتاسنترِ تلگرام (IPهای مختلف، در زمان اجرا
    انتخاب می‌شوند) وصل می‌شود، نه به یک میزبانِ ثابت. یک forwardِ مستقیم (`-L`)
    فقط یک مقصدِ ثابت را می‌بندد و با این مدل جور نیست. SOCKS5 پویا هر مقصدی که
    Telethon بخواهد را باز می‌کند، و چون شنونده روی `127.0.0.1` است فقط پروسه‌ی
    محلی (کلاینتِ ما) از آن استفاده می‌کند. پس SOCKS5 هم درست‌تر است هم همان
    «فقط برای تلگرام» را می‌دهد.

TOFU (trust-on-first-use) برای کلید میزبان:
    اعتبارسنجیِ خودِ asyncssh خاموش است (`known_hosts=None`) و در عوض **ما**
    کلیدِ میزبان را در اولین اتصالِ موفق ذخیره می‌کنیم. از آن به بعد اگر کلید
    عوض شد، اتصال **رد** می‌شود و relay خراب علامت می‌خورد و هشدارِ بلند به گروه
    لاگ می‌رود (نشانه‌ی MITM یا بازنصبِ سرور). این یعنی اولین اتصال تأییدنشده است
    (تعریفِ TOFU) و این را صریح می‌پذیریم.

جدا نگه‌داشتنِ منابع:
    حلقه‌ی نگهبان و تونل I/O-bound و سبک‌اند؛ کارِ سنگینِ مرورگر در پروسه‌ی
    مشتری به‌صورت زیرپردازه‌ی Chromium اجرا می‌شود و با event loop رقابت نمی‌کند.
    هر health-check با `wait_for` تایم‌اوت دارد تا یک probeِ کند حلقه را قفل
    نکند، و حلقه هر استثنا را می‌بلعد تا هیچ‌وقت نمیرد. keepalive روی SSH روشن
    است تا relayِ نیمه‌مرده بدون انتظار برای probe بعدی دیده شود.
"""
from __future__ import annotations

import asyncio
import base64
import random
import time

import db
from config import config
from relay import crypto


# --------------------------------------------------------------------------- #
# رابطِ تونل و کانکتورِ پیش‌فرض (asyncssh). کانکتور تزریق‌پذیر است تا تست بدون
# asyncssh کار کند.
# --------------------------------------------------------------------------- #
class _AsyncsshTunnel:
    """یک اتصال SSH زنده‌ی asyncssh + شنونده‌ی SOCKS. رابطِ کمینه‌ای که مدیر می‌خواهد."""

    def __init__(self, conn, host_key_b64: str):
        self._conn = conn
        self._listener = None
        self.host_key_b64 = host_key_b64

    async def forward_socks(self, local_host: str, local_port: int) -> None:
        self._listener = await self._conn.forward_socks(local_host, local_port)

    def is_closed(self) -> bool:
        try:
            return bool(self._conn.is_closed())
        except Exception:  # noqa: BLE001
            return True

    async def wait_closed(self) -> None:
        try:
            await self._conn.wait_closed()
        except Exception:  # noqa: BLE001
            pass

    async def close(self) -> None:
        try:
            if self._listener is not None:
                self._listener.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass


def asyncssh_available() -> bool:
    try:
        import asyncssh  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


async def _asyncssh_connect(*, host: str, port: int, username: str,
                            password: str, keepalive: int) -> _AsyncsshTunnel:
    """کانکتورِ واقعی: با asyncssh وصل می‌شود و کلیدِ میزبان را برمی‌گرداند.

    forwardِ SOCKS اینجا شروع **نمی‌شود** — اول مدیر باید TOFU را چک کند، بعد
    خودش `forward_socks` را صدا می‌زند. `known_hosts=None` یعنی asyncssh کلید را
    چک نمی‌کند؛ چکِ ما در مدیر است.
    """
    import asyncssh
    conn = await asyncssh.connect(
        host, port=int(port), username=username, password=password,
        known_hosts=None, keepalive_interval=int(keepalive),
        keepalive_count_max=3,
    )
    host_key_b64 = ""
    try:
        key = conn.get_server_host_key()
        if key is not None:
            host_key_b64 = base64.b64encode(key.public_data).decode("ascii")
    except Exception:  # noqa: BLE001
        host_key_b64 = ""
    return _AsyncsshTunnel(conn, host_key_b64)


# --------------------------------------------------------------------------- #
# مدیر
# --------------------------------------------------------------------------- #
class RelayManager:
    """تونلِ فعال را نگه می‌دارد و هوشمندانه بین relayها سویچ می‌کند."""

    def __init__(self, *, local_port: int | None = None,
                 connector=None, health_probe=None, on_event=None):
        self.local_port = int(local_port if local_port is not None
                              else config.relay_local_port())
        #: کانکتور: async (*, host, port, username, password, keepalive) -> Tunnel
        self._connect_fn = connector or _asyncssh_connect
        #: probeِ سلامت: async () -> bool  (یک getMeِ واقعیِ تلگرام از داخل تونل)
        self._health_probe = health_probe
        #: async (dict) -> None برای لاگ‌کردن رویدادها (به logbus وصل می‌شود)
        self._on_event = on_event

        self._tunnel = None
        self.current_id: int | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._fail_streak = 0
        self._ping_high_streak = 0
        self._reconnect_attempt = 0
        self._started_at = 0.0
        #: چند بررسیِ اولِ پس از اتصال، قضاوتِ تأخیر نخورند (گرمایش).
        self._warmup_left = 0
        #: قفلِ گذارها. **تنبل** ساخته می‌شود، نه اینجا: این کلاس یک تک‌نمونه‌ی
        #: سطح-ماژول دارد (`manager`)، و یک `asyncio.Lock` در لحظه‌ی ساخت به لوپِ
        #: جاری می‌چسبد؛ ساختنش در import یعنی چسبیدن به لوپی که ربات رویش اجرا
        #: نمی‌شود (و روی پایتون ۳.۹ همان‌جا کرش).
        self._lock_obj = None

    # ---- تنظیمات (از config، تا تست بتواند override کند) ------------------- #
    @property
    def interval(self) -> int:
        return max(5, int(config.RELAY_HEALTH_INTERVAL))

    @property
    def probe_timeout(self) -> int:
        return max(1, int(config.RELAY_PROBE_TIMEOUT))

    @property
    def fail_threshold(self) -> int:
        return max(1, int(config.RELAY_FAIL_THRESHOLD))

    @property
    def max_ping_ms(self) -> int:
        return int(config.RELAY_MAX_PING_MS)

    @property
    def ping_high_max(self) -> int:
        return max(1, int(config.RELAY_PING_HIGH_STREAK))

    def _lock(self) -> asyncio.Lock:
        """قفلِ گذارِ تونل، در لوپِ در حال اجرا ساخته می‌شود.

        **همه‌ی** گذارها (اتصال، قطع، failover، سویچ) باید از این رد شوند، وگرنه
        دو گذارِ هم‌زمان — مثلاً failoverِ نگهبان و سویچِ دستیِ مالک از پنل — هر
        دو تونل می‌سازند و یکی **رهاشده** می‌ماند: یک سشنِ SSH بازِ بی‌استفاده روی
        relay و یک پورتِ SOCKS اشغال‌شده که هیچ‌کس نمی‌بنددش.

        قاعده‌ی جلوگیری از deadlock: قفل فقط در مرزهای **بیرونی** گرفته می‌شود
        (`start`/`stop`/`switch`/`reconnect` و یک دورِ نگهبان). متدهای درونی
        (`_connect`، `_failover`، `_teardown_tunnel`، `_select_and_connect`،
        `_health_cycle`، `_reconnect_cycle`) هرگز آن را نمی‌گیرند.
        """
        if self._lock_obj is None:
            self._lock_obj = asyncio.Lock()
        return self._lock_obj

    # ---- رویداد/لاگ ------------------------------------------------------- #
    async def _emit(self, **kw) -> None:
        if self._on_event is None:
            return
        try:
            res = self._on_event(kw)
            if asyncio.iscoroutine(res):
                await res
        except Exception:  # noqa: BLE001
            pass

    # ---- انتخابِ relay ---------------------------------------------------- #
    def _preferred_id(self):
        try:
            return db.owner_setting("relay_preferred", None)
        except Exception:  # noqa: BLE001
            return None

    def _candidates(self, exclude: int | None = None) -> list:
        """relayهای قابل‌امتحان، به ترتیبِ اولویت.

        اول سالم‌ها (status != broken)، بعد خراب‌ها — چون «هیچ‌وقت کاملاً تسلیم
        نشو»: اگر همه خراب علامت خورده‌اند، باز هم دوباره امتحانشان کن. relayِ
        ترجیحیِ مالک جلوی صف می‌آید.
        """
        preferred = self._preferred_id()
        relays = db.list_relays(include_disabled=False)
        if exclude is not None:
            relays = [r for r in relays if int(r["id"]) != int(exclude)]

        def sort_key(r):
            pri = r.get("priority")
            # `or 100` غلط بود: priority=0 (همان بوت‌استرپ) را به 100 می‌برد چون
            # 0 falsy است. باید فقط وقتی واقعاً None است پیش‌فرض بگذاریم.
            pri = 100 if pri is None else int(pri)
            return (
                0 if (preferred and int(r["id"]) == int(preferred)) else 1,
                0 if r.get("status") != "broken" else 1,
                pri,
                int(r["id"]),
            )

        return sorted(relays, key=sort_key)

    def _has_alternative(self) -> bool:
        """آیا relayِ دیگری جز فعلی برای رفتن هست؟

        اگر نه، failoverِ «کندی» بی‌معنی است: تونلی که تلگرام از آن جواب می‌دهد
        بسته می‌شود تا دوباره به **همان** relay وصل شویم.
        """
        if self.current_id is None:
            return bool(self._candidates())
        return any(int(r["id"]) != int(self.current_id)
                   for r in db.list_relays(include_disabled=False))

    # ---- اتصال به یک relayِ مشخص ----------------------------------------- #
    async def _connect(self, relay: dict) -> bool:
        rid = int(relay["id"])
        host = relay["host"]
        port = int(relay["ssh_port"] or 22)

        # رمز را رمزگشایی کن (هرگز plaintext ذخیره/لاگ نمی‌شود).
        password = ""
        if relay.get("secret_enc"):
            try:
                password = crypto.decrypt(relay["secret_enc"], config.relay_secret_key())
            except Exception as exc:  # noqa: BLE001
                db.set_relay_fields(rid, status="broken",
                                    last_error=f"رمزگشاییِ رمز شکست خورد: {exc}")
                await self._emit(kind="relay_error", relay=relay,
                                 reason="رمزگشاییِ رمز عبور شکست خورد")
                return False

        try:
            tunnel = await asyncio.wait_for(
                self._connect_fn(host=host, port=port,
                                 username=relay.get("username") or "root",
                                 password=password,
                                 keepalive=int(config.RELAY_KEEPALIVE)),
                timeout=self.probe_timeout + 8)
        except Exception as exc:  # noqa: BLE001
            db.set_relay_fields(rid, status="broken", last_check=time.time(),
                                last_error=f"اتصال شکست خورد: {_short(exc)}")
            await self._emit(kind="relay_error", relay=relay,
                             reason=f"اتصال به {host} شکست خورد", detail=_short(exc))
            return False

        # ---- TOFU: کلیدِ میزبان ----
        presented = getattr(tunnel, "host_key_b64", "") or ""
        stored = (relay.get("host_key") or "").strip()
        if stored and presented and stored != presented:
            await tunnel.close()
            db.set_relay_fields(rid, status="broken", last_check=time.time(),
                                last_error="کلیدِ میزبانِ SSH عوض شده (احتمال MITM)")
            await self._emit(kind="relay_hostkey", relay=relay,
                             reason="⚠️ کلیدِ میزبانِ SSH عوض شده — اتصال رد شد")
            return False
        if not stored and presented:
            db.set_relay_fields(rid, host_key=presented)
            await self._emit(kind="relay_tofu", relay=relay,
                             reason="کلیدِ میزبان برای اولین بار ذخیره شد (TOFU)",
                             detail=_fp(presented))

        # ---- فقط حالا SOCKS را باز کن ----
        try:
            await tunnel.forward_socks("127.0.0.1", self.local_port)
        except Exception as exc:  # noqa: BLE001
            await tunnel.close()
            db.set_relay_fields(rid, status="broken", last_check=time.time(),
                                last_error=f"بازکردنِ SOCKS شکست خورد: {_short(exc)}")
            await self._emit(kind="relay_error", relay=relay,
                             reason="بازکردنِ پورت SOCKS شکست خورد", detail=_short(exc))
            return False

        # دفاعی: اگر مسیری تونلِ قبلی را نبسته بود، اینجا ببند تا جایگزینی‌اش
        # تونلِ بازِ رهاشده به‌جا نگذارد.
        if self._tunnel is not None and self._tunnel is not tunnel:
            try:
                await self._tunnel.close()
            except Exception:  # noqa: BLE001
                pass
        self._tunnel = tunnel
        self.current_id = rid
        self._fail_streak = 0
        self._ping_high_streak = 0
        self._reconnect_attempt = 0
        self._warmup_left = 1      # اولین بررسی، قضاوتِ تأخیر نمی‌خورد
        db.set_relay_fields(rid, status="active", last_ok=time.time(),
                            last_check=time.time(), fail_count=0, last_error="")
        await self._emit(kind="relay_up", relay=relay,
                         reason=f"تونل به {host} برقرار شد",
                         detail=f"SOCKS5 روی 127.0.0.1:{self.local_port}")
        return True

    async def _select_and_connect(self, exclude: int | None = None) -> bool:
        """همه‌ی کاندیداها را یک‌بار امتحان می‌کند تا یکی وصل شود."""
        for relay in self._candidates(exclude=exclude):
            if self._stopping:
                return False
            if await self._connect(relay):
                return True
        return False

    # ---- backoff ---------------------------------------------------------- #
    def _backoff(self, attempt: int) -> float:
        base = float(config.RELAY_BACKOFF_BASE)
        cap = float(config.RELAY_BACKOFF_CAP)
        d = min(cap, base * (2 ** min(attempt, 16)))
        # full jitter روی نیمِ بالایی، تا هم پراکنده باشد هم صفر نشود
        return d / 2 + random.uniform(0, d / 2)

    # ---- probeِ سلامت ----------------------------------------------------- #
    async def _run_probe(self) -> tuple[bool, int]:
        """(سالم؟, تأخیرِ تونل به میلی‌ثانیه).

        **سلامت و تأخیر دو چیزِ متفاوت‌اند و از دو جا می‌آیند:**

        * سلامت از `health_probe` (یک `getMe`ِ واقعیِ تلگرام) — همان
          «بررسیِ واقعی، نه فقط TCP».
        * تأخیر از یک CONNECTِ SOCKS داخلِ تونل (`relay/probe.py`).

        قبلاً تأخیر هم از زمانِ `getMe` گرفته می‌شد و این یک باگِ واقعی بود:
        اولین `getMe` روی تونلِ تازه، دست‌دادنِ MTProto را هم شامل می‌شود
        (روی سرور ۵۴۶۶ms اندازه‌گیری شد) در حالی که خودِ تونل ۳ms بود؛ نتیجه‌اش
        failoverِ بی‌دلیل و خراب علامت‌خوردنِ یک relayِ سالم بود.
        """
        healthy = True
        if self._health_probe is not None:
            try:
                healthy = bool(await asyncio.wait_for(
                    self._health_probe(), timeout=self.probe_timeout))
            except Exception:  # noqa: BLE001
                healthy = False

        ping = 0
        try:
            from relay.probe import tunnel_ping_ms
            got = await tunnel_ping_ms(self.local_port, timeout=self.probe_timeout)
            ping = int(got) if got is not None else 0
        except Exception:  # noqa: BLE001
            ping = 0
        return healthy, ping

    # ---- failover --------------------------------------------------------- #
    async def _failover(self, reason: str) -> None:
        old = self.current_id
        if old is not None:
            row = db.get_relay(old)
            db.set_relay_fields(old, status="broken", last_error=reason,
                                disconnects=int((row or {}).get("disconnects", 0)) + 1)
            await self._emit(kind="relay_down", relay=row or {"id": old},
                             reason=f"relayِ فعال از کار افتاد: {reason}")
        await self._teardown_tunnel()

        ok = await self._select_and_connect(exclude=old)
        if not ok:
            # هیچ relayِ دیگری وصل نشد → خودِ قبلی را هم دوباره امتحان کن
            ok = await self._select_and_connect(exclude=None)
        if not ok:
            self._reconnect_attempt += 1
            await self._emit(kind="relay_none",
                             reason="هیچ relayی در دسترس نیست — تلاشِ مجدد")

    async def _teardown_tunnel(self) -> None:
        if self._tunnel is not None:
            try:
                await self._tunnel.close()
            except Exception:  # noqa: BLE001
                pass
        self._tunnel = None
        self.current_id = None

    # ---- یک دور از نگهبان (برای تست مستقیم قابل‌فراخوانی) ------------------ #
    async def _reconnect_cycle(self) -> None:
        """وقتی تونل مُرده: قطعیِ ناگهانی → failover، وگرنه یک اتصالِ تازه."""
        if self.current_id is not None:
            await self._failover(reason="اتصال قطع شد")
        else:
            ok = await self._select_and_connect()
            if not ok:
                self._reconnect_attempt += 1

    async def _health_cycle(self) -> str:
        """یک probeِ سلامت + تصمیم. رشته‌ی اقدام را برمی‌گرداند (برای تست).

        اقدام‌ها: ok | slow | slow_failover | fail | failover
        """
        healthy, ping = await self._run_probe()
        now = time.time()
        if self.current_id is not None:
            db.set_relay_fields(self.current_id, last_check=now, last_ping_ms=ping)

        # گرمایش: اولین بررسی پس از اتصال، قضاوتِ تأخیر نمی‌خورد. تونلِ تازه
        # هنوز دست‌دادنِ MTProto/DNS را در خودش دارد و «کند» به نظر می‌رسد؛
        # یک relayِ سالم نباید به‌خاطر گرم‌نشدن خراب علامت بخورد.
        if self._warmup_left > 0:
            self._warmup_left -= 1
            if healthy:
                self._fail_streak = 0
                self._ping_high_streak = 0
                if self.current_id is not None:
                    db.set_relay_fields(self.current_id, last_ok=now,
                                        fail_count=0, status="active", last_error="")
                return "warmup"

        if healthy and (self.max_ping_ms <= 0 or ping <= self.max_ping_ms):
            self._fail_streak = 0
            self._ping_high_streak = 0
            if self.current_id is not None:
                db.set_relay_fields(self.current_id, last_ok=now,
                                    fail_count=0, status="active", last_error="")
            return "ok"

        if healthy and self.max_ping_ms > 0 and ping > self.max_ping_ms:
            # نگهبانِ تأخیر: پینگ بالا رفته.
            self._ping_high_streak += 1
            await self._emit(kind="relay_slow", reason=f"پینگ بالا: {ping}ms",
                             relay=db.get_relay(self.current_id) or {})
            if self._ping_high_streak >= self.ping_high_max:
                # اگر relayِ دیگری نیست، **تونلِ سالم را خراب نکن**: تلگرام
                # جواب می‌دهد (healthy است) و failover فقط همین تونل را می‌بندد
                # و دوباره به همان relay وصل می‌شود — یعنی یک قطعیِ بی‌فایده.
                # کندی را گزارش می‌کنیم و می‌گذاریم کار کند.
                if not self._has_alternative():
                    self._ping_high_streak = 0
                    await self._emit(
                        kind="relay_slow",
                        reason="کند است ولی relayِ جانشینی نیست — تونل حفظ شد",
                        relay=db.get_relay(self.current_id) or {})
                    return "slow_kept"
                await self._failover(reason=f"پینگِ مداوماً بالا ({ping}ms)")
                return "slow_failover"
            return "slow"

        self._fail_streak += 1
        if self.current_id is not None:
            db.set_relay_fields(self.current_id, fail_count=self._fail_streak,
                                last_error="health-check شکست خورد")
        if self._fail_streak >= self.fail_threshold:
            await self._failover(reason="چند شکستِ پیاپیِ سلامت")
            return "failover"
        return "fail"

    # ---- حلقه‌ی نگهبان ----------------------------------------------------- #
    async def _guardian(self) -> None:
        """تا وقتی stop نشده، تونل را زنده نگه می‌دارد.

        هر گذار زیرِ قفل انجام می‌شود، ولی **خواب هرگز زیرِ قفل نیست** — وگرنه یک
        سویچِ دستی از پنل تا `interval` ثانیه معطل می‌ماند.
        """
        while not self._stopping:
            # تونل مُرده؟ فوراً وصل مجدد با backoff.
            if self._tunnel is None or self._tunnel.is_closed():
                async with self._lock():
                    # دوباره چک کن: ممکن است سویچِ دستی همین حالا درستش کرده باشد.
                    if not self._stopping and (self._tunnel is None
                                               or self._tunnel.is_closed()):
                        await self._reconnect_cycle()
                    need_backoff = self._tunnel is None
                if need_backoff and not self._stopping:
                    await asyncio.sleep(self._backoff(self._reconnect_attempt or 1))
                continue

            await asyncio.sleep(self.interval)
            if self._stopping:
                break

            async with self._lock():
                if self._stopping or self._tunnel is None or self._tunnel.is_closed():
                    continue
                await self._health_cycle()

    # ---- API عمومی -------------------------------------------------------- #
    def configure(self, *, health_probe=None, on_event=None, connector=None) -> None:
        """probeِ سلامت / sinkِ لاگ / کانکتور را بعد از ساخت وصل می‌کند."""
        if health_probe is not None:
            self._health_probe = health_probe
        if on_event is not None:
            self._on_event = on_event
        if connector is not None:
            self._connect_fn = connector

    async def start(self) -> bool:
        """اتصالِ اولیه + راه‌اندازیِ نگهبان. اگر غیرفعال است، no-op."""
        if not config.RELAY_ENABLED:
            return False
        if self._task is not None and not self._task.done():
            return self.current_id is not None
        self._stopping = False
        self._started_at = time.time()
        if not asyncssh_available() and self._connect_fn is _asyncssh_connect:
            await self._emit(
                kind="relay_error",
                reason="کتابخانه‌ی asyncssh نصب نیست — تونل برقرار نمی‌شود",
                detail="pip install asyncssh")
        async with self._lock():
            ok = await self._select_and_connect()
        self._task = asyncio.create_task(self._guardian())
        return ok

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        # قفل بعد از cancel گرفته می‌شود: اگر نگهبان وسطِ یک گذار بود، cancel
        # قفلش را آزاد می‌کند و ما تونل را تمیز می‌بندیم.
        async with self._lock():
            await self._teardown_tunnel()

    async def switch(self, relay_id: int | None = None) -> bool:
        """سویچِ دستی. relayِ داده‌شده را ترجیحی می‌کند و فوراً به آن می‌رود."""
        target = None
        if relay_id is not None:
            target = db.get_relay(int(relay_id))
            # ترجیح را فقط برای relayی که **واقعاً هست** ذخیره کن، وگرنه یک آیدیِ
            # مرده برای همیشه در تنظیمات می‌ماند.
            if target:
                db.set_owner_setting("relay_preferred", int(relay_id))
                # شانسِ تازه، اگر قبلاً خراب علامت خورده بود.
                if target.get("status") == "broken":
                    db.set_relay_fields(int(relay_id), status="idle", fail_count=0)
                    target = db.get_relay(int(relay_id))
        async with self._lock():
            old = self.current_id
            await self._teardown_tunnel()
            if target and await self._connect(target):
                return True
            return await self._select_and_connect(exclude=old)

    async def reconnect(self) -> bool:
        """تونل را می‌بندد و دوباره بهترین relay را انتخاب و وصل می‌کند.

        هیچ relayی را کنار نمی‌گذارد: هدف «همین حالا دوباره وصل شو» است، حتی اگر
        بهترین انتخاب همان قبلی باشد.
        """
        async with self._lock():
            await self._teardown_tunnel()
            return await self._select_and_connect()

    def status(self) -> dict:
        current = db.get_relay(self.current_id) if self.current_id else None
        return {
            "enabled": bool(config.RELAY_ENABLED),
            "running": bool(self._task is not None and not self._task.done()),
            "connected": self._tunnel is not None and not self._tunnel.is_closed(),
            "current_id": self.current_id,
            "current": current,
            "local_port": self.local_port,
            "have_asyncssh": asyncssh_available(),
            "preferred": self._preferred_id(),
            "relays": db.list_relays(include_disabled=True),
            "fail_streak": self._fail_streak,
        }


def _short(exc: object, n: int = 160) -> str:
    s = repr(exc)
    return s if len(s) <= n else s[:n] + "…"


def _fp(host_key_b64: str) -> str:
    """اثرِانگشتِ کوتاهِ کلیدِ میزبان برای نمایش."""
    try:
        import hashlib
        raw = base64.b64decode(host_key_b64)
        return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode()[:20]
    except Exception:  # noqa: BLE001
        return "?"


#: تک‌نمونه‌ی هر پروسه — مثل `store`/`pool`.
manager = RelayManager()
