#!/usr/bin/env bash
#
# install.sh — نصبِ کاملِ Dauroo روی سرورِ خام (اوبونتو/دبیان).
# ============================================================
#
# فرض: سرورِ تازه، هیچ‌چیز نصب نیست. این اسکریپت همه‌چیز را نصب و راه‌اندازی
# می‌کند: پایتون، venv، وابستگی‌ها، کرومِ Playwright با کتابخانه‌های سیستمی،
# کاربرِ سرویس، پوشه‌ها، و دو واحدِ systemd.
#
# اجرا (با root یا sudo):
#
#     sudo bash deploy/install.sh
#
# اسکریپت idempotent است: هر بار دوباره بزنی، فقط چیزهایی که کم است را می‌سازد.
# هیچ‌وقت .env موجود را بازنویسی نمی‌کند و هیچ‌وقت داده/پروفایل را پاک نمی‌کند.
#
# بعد از نصب: .env را پر کن، بعد
#     sudo systemctl enable --now dauroo-owner dauroo-customer

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/dauroo}"
APP_USER="${APP_USER:-dauroo}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# رنگ‌ها (اگر ترمینال پشتیبانی کند)
if [ -t 1 ]; then
    R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; B=$'\e[1m'; N=$'\e[0m'
else
    R=""; G=""; Y=""; B=""; N=""
fi

step() { echo; echo "${B}▶ $*${N}"; }
ok()   { echo "  ${G}✅${N} $*"; }
warn() { echo "  ${Y}⚠️${N}  $*"; }
die()  { echo "  ${R}❌ $*${N}" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "با sudo اجرا کن:  sudo bash deploy/install.sh"

# مسیرِ سورس = پوشه‌ی والدِ همین اسکریپت
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "${B}نصبِ Dauroo${N}"
echo "───────────────────────────────"
echo "  سورس : $SRC_DIR"
echo "  نصب  : $APP_DIR"
echo "  کاربر: $APP_USER"

# --------------------------------------------------------------------------- #
step "۱/۹ بسته‌های سیستمی"
# --------------------------------------------------------------------------- #
if ! command -v apt-get >/dev/null 2>&1; then
    die "این اسکریپت برای اوبونتو/دبیان است (apt-get پیدا نشد)."
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# python3-venv جداست و بدونش venv ساخته نمی‌شود؛ build-essential برای هر
# وابستگی‌ای که چرخِ آماده ندارد؛ ca-certificates برای TLS.
apt-get install -y -qq \
    python3 python3-venv python3-pip python3-dev \
    build-essential ca-certificates curl git tzdata >/dev/null
ok "پایتون و ابزارهای ساخت نصب شد ($("$PYTHON_BIN" -V 2>&1))"

# --------------------------------------------------------------------------- #
step "۲/۹ کاربرِ سرویس"
# --------------------------------------------------------------------------- #
if id "$APP_USER" >/dev/null 2>&1; then
    ok "کاربر $APP_USER از قبل هست"
else
    # کاربرِ سیستمی بدونِ لاگین: ربات نباید با root اجرا شود.
    useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
    ok "کاربر $APP_USER ساخته شد"
fi

# --------------------------------------------------------------------------- #
step "۳/۹ کپیِ کد به $APP_DIR"
# --------------------------------------------------------------------------- #
mkdir -p "$APP_DIR"
if [ "$SRC_DIR" != "$APP_DIR" ]; then
    # داده‌ها و .env هرگز بازنویسی نمی‌شوند.
    tar -C "$SRC_DIR" \
        --exclude=.git --exclude=data --exclude=profiles --exclude=artifacts \
        --exclude=.env --exclude=__pycache__ --exclude='*.pyc' \
        -cf - . | tar -C "$APP_DIR" -xf -
    ok "کد کپی شد"
else
    ok "همین‌جا نصب می‌شود (سورس = مقصد)"
fi

# --------------------------------------------------------------------------- #
step "۴/۹ venv و وابستگی‌های پایتون"
# --------------------------------------------------------------------------- #
if [ ! -x "$APP_DIR/venv/bin/python" ]; then
    "$PYTHON_BIN" -m venv "$APP_DIR/venv"
    ok "venv ساخته شد"
else
    ok "venv از قبل هست"
fi
PY="$APP_DIR/venv/bin/python"
"$PY" -m pip install --upgrade pip -q
# asyncssh در requirements.txt هست (برای تونلِ relay).
"$PY" -m pip install -q -r "$APP_DIR/requirements.txt"
ok "وابستگی‌ها نصب شد (telethon، playwright، asyncssh)"

# --------------------------------------------------------------------------- #
step "۵/۹ کرومِ Playwright + کتابخانه‌های سیستمی"
# --------------------------------------------------------------------------- #
# `--with-deps` خودش کتابخانه‌های سیستمیِ لازمِ کروم را با apt نصب می‌کند؛ این
# همان چیزی است که روی سرورِ خام معمولاً جا می‌افتد و بعد کروم بی‌دلیل بالا
# نمی‌آید. اگر شکست خورد، نصب را متوقف نمی‌کنیم: موتورِ سریع (بدون مرورگر) باز
# هم کار می‌کند و می‌شود بعداً درستش کرد.
if "$PY" -m playwright install --with-deps chromium >/tmp/pw.log 2>&1; then
    ok "کرومیوم نصب شد"
else
    warn "نصبِ کرومیوم شکست خورد (لاگ: /tmp/pw.log)"
    warn "موتورِ «سریع» بدونِ مرورگر کار می‌کند؛ بعداً این را اجرا کن:"
    warn "  $PY -m playwright install --with-deps chromium"
fi
# کروم را در HOME کاربرِ سرویس هم قابلِ دسترس کن (Playwright آن را در HOME
# کاربری که نصب کرده می‌گذارد؛ اگر با root نصب شده، مسیرش /root است و کاربرِ
# سرویس نمی‌بیند).
PW_CACHE="/home/$APP_USER/.cache/ms-playwright"
if [ -d /root/.cache/ms-playwright ] && [ ! -d "$PW_CACHE" ]; then
    mkdir -p "/home/$APP_USER/.cache"
    cp -a /root/.cache/ms-playwright "$PW_CACHE"
    ok "کرومیوم برای کاربرِ سرویس کپی شد"
fi

# --------------------------------------------------------------------------- #
step "۶/۹ پوشه‌ها و .env"
# --------------------------------------------------------------------------- #
mkdir -p "$APP_DIR"/{data,profiles,artifacts}
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    # کلیدِ رمزنگاریِ رمزِ relay را خودکار بساز، تا هیچ‌وقت رمز بی‌محافظت نماند.
    KEY="$(head -c 32 /dev/urandom | base64 | tr -d '\n=' | tr '+/' '-_')"
    if grep -q '^RELAY_SECRET_KEY=' "$APP_DIR/.env"; then
        sed -i "s|^RELAY_SECRET_KEY=.*|RELAY_SECRET_KEY=$KEY|" "$APP_DIR/.env"
    else
        echo "RELAY_SECRET_KEY=$KEY" >> "$APP_DIR/.env"
    fi
    ok ".env از نمونه ساخته شد + RELAY_SECRET_KEY تولید شد"
    NEED_ENV=1
else
    ok ".env از قبل هست (دست‌نخورده ماند)"
    NEED_ENV=0
fi
chmod 600 "$APP_DIR/.env"
chown -R "$APP_USER:$APP_USER" "$APP_DIR" "/home/$APP_USER" 2>/dev/null || true
ok "دسترسی‌ها تنظیم شد (.env فقط برای مالکش خواندنی)"

# --------------------------------------------------------------------------- #
step "۷/۹ واحدهای systemd"
# --------------------------------------------------------------------------- #
for unit in dauroo-owner dauroo-customer; do
    src="$APP_DIR/deploy/$unit.service"
    [ -f "$src" ] || die "$src پیدا نشد"
    # مسیرها/کاربر را با مقادیرِ واقعیِ همین نصب جایگزین کن.
    sed -e "s|/opt/dauroo|$APP_DIR|g" \
        -e "s|^User=.*|User=$APP_USER|" \
        -e "s|^Group=.*|Group=$APP_USER|" \
        "$src" > "/etc/systemd/system/$unit.service"
done
systemctl daemon-reload
ok "dauroo-owner و dauroo-customer نصب شدند"

# --------------------------------------------------------------------------- #
step "۸/۹ تستِ سلامتِ نصب"
# --------------------------------------------------------------------------- #
cd "$APP_DIR"
if sudo -u "$APP_USER" "$PY" -c "
import telethon, asyncssh
print('  telethon', telethon.__version__)
print('  asyncssh', asyncssh.__version__)
import playwright; print('  playwright ok')
" 2>/dev/null; then
    ok "کتابخانه‌ها درست import می‌شوند"
else
    warn "بعضی کتابخانه‌ها import نشدند — بالاتر را ببین"
fi
# تستِ آفلاینِ خودِ پروژه (بدونِ شبکه): اگر این سبز شد، کد سالم نصب شده.
if sudo -u "$APP_USER" env DATA_DIR=/tmp/dauroo_check_$$ \
        "$PY" -m bot.tests.test_relay >/tmp/dauroo_test.log 2>&1; then
    ok "تستِ داخلیِ relay سبز است ($(grep -c '  PASS' /tmp/dauroo_test.log) چک)"
else
    warn "تستِ داخلی رد نشد (لاگ: /tmp/dauroo_test.log)"
fi
rm -rf "/tmp/dauroo_check_$$" 2>/dev/null || true

# --------------------------------------------------------------------------- #
step "۹/۹ تمام"
# --------------------------------------------------------------------------- #
echo
echo "───────────────────────────────"
if [ "$NEED_ENV" = "1" ]; then
    echo "${Y}${B}گامِ بعدی: .env را پر کن${N}"
    echo
    echo "  sudo nano $APP_DIR/.env"
    echo
    echo "  اجباری‌ها:"
    echo "    API_ID، API_HASH        (از my.telegram.org)"
    echo "    OWNER_BOT_TOKEN         (از @BotFather)"
    echo "    CUSTOMER_BOT_TOKEN      (رباتِ دوم از @BotFather)"
    echo "    OWNER_ID                (آیدیِ عددیِ خودت)"
    echo "    LOG_GROUP_ID            (گروهِ لاگ، منفی؛ ربات‌ها را ادمین کن)"
    echo
    echo "  برای relay (چون سرور ایران است):"
    echo "    RELAY_ENABLED=1"
    echo "    RELAY_HOST، RELAY_USER، RELAY_PASSWORD    (VPSِ خارجی)"
    echo "    RELAY_SECRET_KEY  ← ${G}خودکار ساخته شد${N}"
    echo
fi
echo "${B}تستِ تونل قبل از استارت (توصیه می‌شود):${N}"
echo "  cd $APP_DIR && sudo -u $APP_USER venv/bin/python -m relay.selfcheck"
echo
echo "${B}استارت:${N}"
echo "  sudo systemctl enable --now dauroo-owner dauroo-customer"
echo
echo "${B}دیدنِ لاگ:${N}"
echo "  journalctl -u dauroo-owner -f"
echo "  journalctl -u dauroo-customer -f"
echo "───────────────────────────────"
