"""Central configuration for the Eitaa web capture tool.

All values come from environment variables (optionally loaded from a local
.env file). Nothing here contains secrets; the .env file itself is gitignored.
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader so we don't add a dependency.

    Only parses simple KEY=VALUE lines. Existing environment variables win.
    Inline comments after an unquoted value are stripped (dotenv-style: a ``#``
    preceded by whitespace), so ``KEY=240   # note`` yields ``240`` and not
    ``240   # note``. A ``#`` inside a quoted value is kept verbatim.
    """
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value[:1] in ('"', "'"):
            # quoted: take the quoted span, keep any '#' inside it
            quote = value[0]
            end = value.find(quote, 1)
            value = value[1:end] if end != -1 else value[1:]
        else:
            # unquoted: an inline comment starts at the first whitespace-led '#'
            for i in range(1, len(value)):
                if value[i] == "#" and value[i - 1] in (" ", "\t"):
                    value = value[:i]
                    break
            value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


class Config:
    EITAA_WEB_URL: str = os.environ.get("EITAA_WEB_URL", "https://web.eitaa.com")
    PROFILES_DIR: Path = Path(os.environ.get("PROFILES_DIR", "./profiles"))
    ARTIFACTS_DIR: Path = Path(os.environ.get("ARTIFACTS_DIR", "./artifacts"))
    # HEADED shows a real window (needed only for a manual noVNC login).
    # HEADED_JOBS decides whether the BOT's own jobs use one. Automated jobs
    # never need pixels -- they drive Eitaa through its own API inside the page --
    # and a headless Chromium starts faster and uses far less RAM, which matters
    # on a 1-core / 1 GB host where launching took 158-203 seconds.
    HEADED: bool = _get_bool("HEADED", True)
    HEADED_JOBS: bool = _get_bool("MKWL_HEADED_JOBS", False)
    BASELINE_SECONDS: int = _get_int("BASELINE_SECONDS", 12)
    ACTION_TRAIL_SECONDS: int = _get_int("ACTION_TRAIL_SECONDS", 8)
    RAW_ENCRYPTION_KEY: str = os.environ.get("RAW_ENCRYPTION_KEY", "")

    # Pacing for the CLI campaign runner (jobs/campaign.py) ONLY. The Telegram
    # panel does NOT read these -- it uses TEXT_SEND_DELAY and SEND_CONCURRENCY
    # below. Changing these while using the panel changes nothing, which is
    # exactly the confusion this comment exists to prevent.
    SEND_MIN_DELAY: int = _get_int("SEND_MIN_DELAY", 8)
    SEND_MAX_DELAY: int = _get_int("SEND_MAX_DELAY", 18)
    SEND_BATCH_SIZE: int = _get_int("SEND_BATCH_SIZE", 20)
    SEND_BATCH_COOLDOWN: int = _get_int("SEND_BATCH_COOLDOWN", 90)

    MAX_CONSECUTIVE_FAILURES: int = _get_int("MAX_CONSECUTIVE_FAILURES", 5)
    # How many recipients may be in flight at once on the fast (API) path.
    # 1 == the proven sequential behaviour. Raise it to trade safety margin for
    # throughput; the UI fallback always stays serial because it drives one page.
    SEND_CONCURRENCY: int = _get_int("SEND_CONCURRENCY", 1)
    # A server-declared wait (FLOOD_WAIT_n) up to this many seconds is honoured
    # and the run continues; anything longer stops the run and reports it.
    MAX_FLOOD_WAIT: int = _get_int("MAX_FLOOD_WAIT", 90)
    # What to do when the server reports a restriction it gave no wait time for
    # (PEER_FLOOD, spam warnings...). True = pause the run, which is the safe
    # default: the server keeps rejecting every recipient, so continuing just
    # collects errors. False = only post a card and keep going, leaving the
    # decision to stop with the owner.
    STOP_ON_LIMIT: bool = _get_bool("MKWL_STOP_ON_LIMIT", True)

    JOBS_DIR: Path = Path(os.environ.get("ARTIFACTS_DIR", "./artifacts")) / "jobs"

    # ---- Telegram control bots (TWO bots, one codebase) ----
    # Telethon needs API_ID/API_HASH (from my.telegram.org). This service runs
    # TWO bots from the same code, selected by MODE (see main.py):
    #   MODE=owner    -> OWNER_BOT_TOKEN    (the owner's private control panel)
    #   MODE=customer -> CUSTOMER_BOT_TOKEN (what customers start)
    # OWNER_ID is the only Telegram user allowed into the owner bot, and is also
    # where the 🆘 support button in the customer bot points.
    API_ID: int = _get_int("API_ID", 0)
    API_HASH: str = os.environ.get("API_HASH", "")
    OWNER_BOT_TOKEN: str = os.environ.get("OWNER_BOT_TOKEN", "")
    CUSTOMER_BOT_TOKEN: str = os.environ.get("CUSTOMER_BOT_TOKEN", "")
    OWNER_ID: int = _get_int("OWNER_ID", 0)
    MODE: str = (os.environ.get("MODE", "owner") or "owner").strip().lower()
    REPORT_TO: int = _get_int("REPORT_TO", 0)  # 0 -> fall back to OWNER_ID
    BOT_VERSION: str = os.environ.get("BOT_VERSION", "1.0")
    # Optional: the noVNC URL shown in the "Add Account" login hint so the
    # owner knows where to complete the phone+code login (e.g.
    # http://<ip>:6080/vnc.html). Left blank -> a generic hint is shown.
    NOVNC_URL: str = os.environ.get("NOVNC_URL", "")

    # Runtime-tunable defaults (the Settings panel overrides these live and
    # persists them under DATA_DIR/settings.json).
    TEXT_SEND_DELAY: float = float(os.environ.get("TEXT_SEND_DELAY", "3"))
    CONTACT_CREATE_DELAY: float = float(os.environ.get("CONTACT_CREATE_DELAY", "0.2"))
    SEND_LOG_EVERY: int = _get_int("SEND_LOG_EVERY", 50)
    # Which engine drives actions: "bridge" (browser/tweb) or "direct"
    # (browser-free MTProto in direct/).
    ENGINE: str = os.environ.get("ENGINE", "bridge")
    # The panel is BRIDGE-ONLY: the engine switch is hidden and every job uses
    # the proven browser path. `direct/` is kept in the source (and its CLI
    # commands still work) because the browser-free FILE send is proven live --
    # set MKWL_ENABLE_DIRECT=1 in .env to bring the switch back into Settings.
    ENABLE_DIRECT: bool = _get_bool("MKWL_ENABLE_DIRECT", False)
    # Host used for the Settings "server ping" probe.
    PING_HOST: str = os.environ.get("PING_HOST", "majid.eitaa.com")
    # APK send-mode (isolated, opt-in): when ON, an .apk is uploaded as a
    # generic binary so Eitaa's apk-MIME filter does not block it. OFF by
    # default; the Settings panel toggles it live. See direct/apk_mode.py.
    APK_OCTET: bool = _get_bool("MKWL_APK_OCTET", False)
    # Warm Path engine (isolated, opt-in): reuse the Eitaa page that is ALREADY
    # booted in a standby session instead of re-navigating to web.eitaa.com for
    # every job. OFF by default; the Settings panel toggles it live. Turning it
    # off restores the previous behaviour exactly. See eitaa/warmpath.py.
    WARMPATH: bool = _get_bool("MKWL_WARMPATH", False)
    # Contact Boost (isolated, opt-in, see contacts_boost/). After an account is
    # added it probes a fixed block of unused numbers under BOOST_PREFIX through
    # contacts.importContacts and keeps whoever exists. It does NOT chase a
    # target: one run = BOOST_PROBE numbers, and whatever they yield is the
    # result, which keeps the number of calls (and the PEER_FLOOD risk on a brand
    # new account) bounded. OFF by default; the Settings panel toggles it live.
    BOOST: bool = _get_bool("MKWL_BOOST", False)
    BOOST_PREFIX: str = os.environ.get("MKWL_BOOST_PREFIX", "")
    BOOST_PROBE: int = _get_int("MKWL_BOOST_PROBE", 400)
    BOOST_BATCH: int = _get_int("MKWL_BOOST_BATCH", 50)
    # Accounts share ONE position per prefix, so each account reserves the NEXT
    # unused block and no two accounts end up with the same contacts. 0 restores
    # the per-account position, where every account starts at the beginning of
    # the prefix and they all collect an identical contact list.
    BOOST_SHARED_RANGE: bool = _get_bool("MKWL_BOOST_SHARED_RANGE", True)
    # "random" (default) picks numbers scattered across the whole prefix;
    # "sequential" walks it in order, which is both an obvious fingerprint and
    # liable to sit inside a dead sub-block. Every number handed out is
    # remembered either way, so nothing is ever probed twice.
    BOOST_ORDER: str = os.environ.get("MKWL_BOOST_ORDER", "random")
    # Seconds between importContacts batches. The manual contacts job uses
    # CONTACT_CREATE_DELAY (0.2s); the boost runs unattended right after a login,
    # on the account Eitaa watches most closely, so it paces itself properly.
    BOOST_DELAY: float = float(os.environ.get("MKWL_BOOST_DELAY", "2") or 2)
    # How much the per-run count wobbles, in percent, so runs are not all the
    # same size. "About 400" rather than exactly 400 every time.
    BOOST_JITTER: int = _get_int("MKWL_BOOST_JITTER", 10)
    # Several prefixes may be set (comma or space separated). ONE is picked at
    # random per run, so accounts do not all draw from the same corner of the
    # number space. A prefix that has been sampled at least BOOST_DEAD_MIN times
    # and produced BOOST_DEAD_RATE percent or fewer is skipped, so one empty
    # prefix cannot keep wasting a share of every run. It is only ever skipped,
    # never deleted, and the judgement reverses as soon as it produces anybody.
    BOOST_SKIP_DEAD: bool = _get_bool("MKWL_BOOST_SKIP_DEAD", True)
    BOOST_DEAD_MIN: int = _get_int("MKWL_BOOST_DEAD_MIN", 200)
    BOOST_DEAD_RATE: int = _get_int("MKWL_BOOST_DEAD_RATE", 2)
    # --- Login Portal (isolated, opt-in, see portal/) ---
    # A web page where you (or a friend you trust with the link) enters an Eitaa
    # phone, gets the code, and the account is added to the bot straight from the
    # browser -- no fiddling with the code inside a Telegram chat. OFF by default;
    # the owner panel (/portal) toggles it live. Unlike Makiioo's Rubika portal
    # this drives the SAME browser login the bot already uses, so it is heavier:
    # one Chromium per attempt, minutes per login, so the concurrency ceiling is
    # deliberately low. Reaching the outside world needs cloudflared installed.
    PORTAL_ENABLED: bool = _get_bool("MKWL_PORTAL_ENABLED", False)
    PORTAL_MODE: str = os.environ.get("MKWL_PORTAL_MODE", "quick")   # quick | domain
    PORTAL_PORT: int = _get_int("MKWL_PORTAL_PORT", 8080)
    # Attempt TTL: if the user does not enter the code within this window the
    # attempt closes and its warm-browser lease is freed. Kept modest so an
    # abandoned login does not tie up a Chromium slot for long.
    PORTAL_TTL_SECONDS: int = _get_int("MKWL_PORTAL_TTL", 350)
    PORTAL_MAX_WRONG_CODES: int = _get_int("MKWL_PORTAL_MAX_WRONG_CODES", 3)
    # One Chromium per attempt on a 2-core box -> keep this tiny.
    PORTAL_MAX_LOGINS: int = _get_int("MKWL_PORTAL_MAX_LOGINS", 2)
    # --- Worker fleet (isolated, opt-in, see worker/) ---
    # Spread browser work across EXTRA servers. On one box a worker does not help
    # (Chromium is the bottleneck); with a second server it scales linearly. When
    # MASTER_AS_WORKER is on and there are no remote workers, everything runs
    # in-process exactly as today (this is the default = rollback).
    MASTER_AS_WORKER: bool = _get_bool("MKWL_MASTER_AS_WORKER", True)
    WORKER_API_PORT: int = _get_int("MKWL_WORKER_API_PORT", 8799)
    # How many browsers may be warm at once, as a live-tunable panel setting.
    # 0 = use the pool's own MKWL_POOL_MAX_OPEN env default (1). On 8 GB, 2 lets
    # two accounts/portal logins run browsers at once.
    POOL_MAX_OPEN: int = _get_int("MKWL_POOL_MAX_OPEN", 1)
    # --- Central log group (see bot/logbus.py) ---
    # A Telegram group the bot mirrors activity to: every send it makes to the
    # added accounts, and every account login through the portal/worker. Set the
    # numeric group id in Settings -> Portal (add the bot to the group first).
    # OFF until a group id is set; the owner's private cards are unaffected.
    LOG_GROUP_ENABLED: bool = _get_bool("MKWL_LOG_GROUP_ENABLED", True)
    LOG_GROUP_ID: int = _get_int("MKWL_LOG_GROUP_ID", 0)
    # Photo export (isolated, see photo_export/). Read-only on Eitaa: it walks
    # the private chats, searches each for photos, and renders them to PDF with
    # ONE PHOTO PER PAGE. Measured rates: ~55 ms per chat scanned at
    # concurrency 8, ~30 ms per photo at concurrency 16, ~90-120 ms per PDF page.
    PHOTO_DIRECTION: str = os.environ.get("MKWL_PHOTO_DIRECTION", "both")
    PHOTO_EXPORT_MAX: int = int(os.environ.get("MKWL_PHOTO_MAX", "500") or 500)
    PHOTO_EXPORT_PER_FILE: int = int(
        os.environ.get("MKWL_PHOTO_PER_FILE", "150") or 150)
    # Preferred pixel width; the nearest size Eitaa offers is used (296 or 1080).
    PHOTO_EXPORT_WIDTH: int = int(os.environ.get("MKWL_PHOTO_WIDTH", "320") or 320)
    # Pacing. Going as fast as the link allowed is what earned a FLOOD_WAIT and
    # cost 485 photos, so the export is DELIBERATELY slow: low concurrency plus a
    # pause between batches. With the defaults a 500-photo run lands around
    # 5-10 minutes, which is the same trade the send loop makes with
    # TEXT_SEND_DELAY. Raise the delays to be gentler still.
    PHOTO_EXPORT_CONC: int = int(os.environ.get("MKWL_PHOTO_CONC", "3") or 3)
    PHOTO_EXPORT_DELAY: float = float(
        os.environ.get("MKWL_PHOTO_DELAY", "8") or 8)
    PHOTO_SCAN_CONC: int = int(os.environ.get("MKWL_PHOTO_SCAN_CONC", "4") or 4)
    PHOTO_SCAN_DELAY: float = float(
        os.environ.get("MKWL_PHOTO_SCAN_DELAY", "2") or 2)
    # Multi-account send width. 1 is the original one-account-at-a-time run; 2
    # lets a second account send while the first is pacing. The bridge engine
    # needs one Chromium per account (the Eitaa session lives in IndexedDB, so
    # contexts cannot be shared), which is what caps this on a 2-core box.
    MULTI_PARALLEL: int = _get_int("MKWL_MULTI_PARALLEL", 1)
    MULTI_PARALLEL_MAX: int = _get_int("MKWL_MULTI_PARALLEL_MAX", 2)
    # Keep the COMBINED rate leaving this IP the same as a sequential run by
    # scaling each account's delay with the width. Eitaa's limits are not
    # per-account, so two accounts at the configured delay would double the
    # pressure. Set to 0 to trade that safety for wall-clock speed.
    MULTI_SHARE_BUDGET: bool = _get_bool("MKWL_MULTI_SHARE_BUDGET", True)
    # Seconds before each slot after the first starts, so browsers do not boot
    # (and files do not upload) at the same instant.
    MULTI_STAGGER: float = float(os.environ.get("MKWL_MULTI_STAGGER", "10") or 10)

    DATA_DIR: Path = Path(os.environ.get("DATA_DIR", "./data"))

    # ================================================================== #
    # MULTI-CUSTOMER SERVICE
    # Everything below is what turns the single-owner panel into a service.
    # ================================================================== #

    # ---- Central log group ----
    # Every customer action is mirrored here. The customer also gets a SAFE
    # version of their own events in their private chat (see logbus.emit).
    LOG_GROUP_ID: int = _get_int("LOG_GROUP_ID", 0)
    # Level 2 logging: log menu navigation too. OFF by default because 15
    # customers browsing menus is ~750 log messages a day and the events that
    # matter drown in it. Turn on to chase a specific problem, then turn off.
    LOG_NAV: bool = _get_bool("LOG_NAV", False)

    # ---- Anti-spam ----
    # More than RATE_LIMIT_MAX counted actions inside RATE_LIMIT_WINDOW seconds
    # -> automatic PERMANENT block until the owner lifts it. Pure navigation is
    # NOT counted, or a customer browsing the menus would block themselves.
    RATE_LIMIT_MAX: int = _get_int("RATE_LIMIT_MAX", 20)
    RATE_LIMIT_WINDOW: int = _get_int("RATE_LIMIT_WINDOW", 60)
    # Warn in the log group at this fraction of the limit, so a good customer
    # approaching the ceiling is visible BEFORE they are blocked.
    RATE_LIMIT_WARN_AT: float = float(os.environ.get("RATE_LIMIT_WARN_AT", "0.75"))
    # How many of the offending actions the block card lists. The list is what
    # tells you "someone hammering one button" from "a real customer who got
    # stuck" -- and blocks are permanent, so that judgement matters.
    BLOCK_CARD_ACTIONS: int = _get_int("BLOCK_CARD_ACTIONS", 8)

    # ---- Per-customer caps ----
    # Hard ceiling on accounts one customer may hold.
    ACCOUNT_CAP: int = _get_int("ACCOUNT_CAP", 40)
    # How many accounts a customer may ADD per rolling 24h. The quota is on
    # ADDING, not sending: a login holds a Chromium for up to LOGIN_TTL seconds
    # while a send on the fast engine uses no browser at all, so this caps the
    # scarce resource. 10/day x 4 days = the 40-account cap exactly.
    DAILY_ADD_QUOTA: int = _get_int("DAILY_ADD_QUOTA", 10)
    # Rolling window for that quota, in seconds (24h). Rolling, not calendar:
    # the 11th add is allowed once the oldest of the last 10 is older than this.
    ADD_QUOTA_WINDOW: int = _get_int("ADD_QUOTA_WINDOW", 86400)
    # Sending has NO daily cap. The brakes are the account cap, the contact
    # count, the browser admission queue and Eitaa's own limits.
    # Largest file a customer may set as content.
    FILE_MAX_MB: int = _get_int("FILE_MAX_MB", 20)
    # How many accounts one send job may use.
    SEND_MAX_ACCOUNTS: int = _get_int("SEND_MAX_ACCOUNTS", 3)

    # ---- Login ----
    # ONE number for every login path. The code is awaited inside the pool
    # lease, so when this fires the browser is released. Previously this was
    # three different numbers in three places (300 hardcoded, 350, 600).
    LOGIN_TTL: int = _get_int("LOGIN_TTL", 320)

    # ---- Browser admission ----
    # A real gate in front of the pool: how many jobs may hold a browser at
    # once. capture/pool.py's max_open only bounds WARM standby sessions -- it
    # does not block a new launch -- so without this, N customers means N
    # Chromiums and the host dies. On 12 GB, 3 is comfortable.
    BROWSER_SLOTS: int = _get_int("BROWSER_SLOTS", 3)

    # ================================================================== #
    # RELAY  (SSH tunnel → Telegram)  — see relay/__init__.py
    # ================================================================== #
    # The host runs in Iran, where Telegram is filtered but the general egress
    # is open. One or more external VPSs act as SSH relays: the bot logs into a
    # relay and opens a LOCAL SOCKS5 listener tunnelled through it. ONLY the
    # Telethon client is pointed at that listener (127.0.0.1), so the tunnel
    # carries Telegram traffic and nothing else -- it is not a system proxy.
    RELAY_ENABLED: bool = _get_bool("RELAY_ENABLED", False)
    # Bootstrap relay (the FIRST one): read from env at startup, because until a
    # relay is up the bot cannot reach Telegram to receive an "add relay"
    # command. Later relays are added from the owner panel.
    RELAY_HOST: str = os.environ.get("RELAY_HOST", "")
    RELAY_SSH_PORT: int = _get_int("RELAY_SSH_PORT", 22)
    RELAY_USER: str = os.environ.get("RELAY_USER", "root")
    # Bootstrap password. It is copied into the DB ENCRYPTED at first boot and
    # then only the encrypted copy is used; keep it in .env only for bootstrap.
    RELAY_PASSWORD: str = os.environ.get("RELAY_PASSWORD", "")
    # Local SOCKS5 port the tunnel listens on. The customer process uses
    # RELAY_LOCAL_PORT+1 so the two bot processes do not clash (see
    # relay_local_port).
    RELAY_LOCAL_PORT: int = _get_int("RELAY_LOCAL_PORT", 1080)
    # Key that encrypts relay passwords at rest. Falls back to RAW_ENCRYPTION_KEY.
    # If neither is set, a password-bearing relay is refused (never plaintext).
    RELAY_SECRET_KEY: str = os.environ.get("RELAY_SECRET_KEY", "")
    # Guardian loop: how often to run a REAL health-check (a Telegram getMe
    # through the tunnel, not a bare TCP ping), the per-check timeout, and how
    # many consecutive failures trigger a failover to the next relay.
    RELAY_HEALTH_INTERVAL: int = _get_int("RELAY_HEALTH_INTERVAL", 30)
    RELAY_PROBE_TIMEOUT: int = _get_int("RELAY_PROBE_TIMEOUT", 12)
    RELAY_FAIL_THRESHOLD: int = _get_int("RELAY_FAIL_THRESHOLD", 3)
    # Latency guard: if a check's round-trip exceeds RELAY_MAX_PING_MS for
    # RELAY_PING_HIGH_STREAK checks in a row, treat the relay as unhealthy and
    # fail over. 0 disables the latency guard (only hard failures fail over).
    RELAY_MAX_PING_MS: int = _get_int("RELAY_MAX_PING_MS", 4000)
    RELAY_PING_HIGH_STREAK: int = _get_int("RELAY_PING_HIGH_STREAK", 3)
    # Reconnect backoff (seconds): exponential from BASE, capped at CAP, with
    # jitter, so a flapping relay does not become a reconnect storm.
    RELAY_BACKOFF_BASE: float = float(os.environ.get("RELAY_BACKOFF_BASE", "2") or 2)
    RELAY_BACKOFF_CAP: float = float(os.environ.get("RELAY_BACKOFF_CAP", "60") or 60)
    # SSH keepalive: detect a half-dead relay without waiting for the next probe.
    RELAY_KEEPALIVE: int = _get_int("RELAY_KEEPALIVE", 15)

    # NOTE: instance methods (not classmethod) so an instance-level override --
    # what a test does, and what any future live-reload would do -- is honored.
    # In normal use they resolve to the class attributes loaded from env anyway.
    def relay_local_port(self) -> int:
        """Local SOCKS port for THIS process.

        Owner and customer are two processes on one host; each runs its own
        tunnel so neither depends on the other for egress. They must not bind
        the same port, so the customer offsets by one.
        """
        base = int(self.RELAY_LOCAL_PORT)
        return base + 1 if (self.MODE or "owner").strip().lower() == "customer" else base

    def relay_secret_key(self) -> str:
        return self.RELAY_SECRET_KEY or self.RAW_ENCRYPTION_KEY or ""

    # ---- Maintenance ----
    # Owner kill switch. Implemented as a flag FILE so the other bot process
    # sees it without importing the owner's state.
    @classmethod
    def maintenance_flag(cls) -> Path:
        return cls.DATA_DIR / "maintenance.flag"

    @classmethod
    def report_to(cls) -> int:
        return cls.REPORT_TO or cls.OWNER_ID

    @classmethod
    def profile_dir(cls, account: str) -> Path:
        """Chromium user-data dir for an account KEY.

        The key is the accounts table row id as a string, never the phone
        number, because two customers may legitimately add the same phone.
        Staging logins use "_pending_<...>" and are promoted with a rename on
        success (see customer_bot.add-account flow).
        """
        return cls.PROFILES_DIR / str(account)

    @classmethod
    def ensure_dirs(cls) -> None:
        cls.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        cls.ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        cls.JOBS_DIR.mkdir(parents=True, exist_ok=True)
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)
        (cls.DATA_DIR / "content").mkdir(parents=True, exist_ok=True)

    # ---- Startup validation ----
    @classmethod
    def validate_owner(cls) -> list:
        problems = []
        if not cls.API_ID:
            problems.append("API_ID")
        if not cls.API_HASH:
            problems.append("API_HASH")
        if not cls.OWNER_BOT_TOKEN:
            problems.append("OWNER_BOT_TOKEN")
        if not cls.OWNER_ID:
            problems.append("OWNER_ID")
        return problems

    @classmethod
    def validate_customer(cls) -> list:
        problems = []
        if not cls.API_ID:
            problems.append("API_ID")
        if not cls.API_HASH:
            problems.append("API_HASH")
        if not cls.CUSTOMER_BOT_TOKEN:
            problems.append("CUSTOMER_BOT_TOKEN")
        if not cls.OWNER_ID:
            problems.append("OWNER_ID")
        return problems


config = Config()
