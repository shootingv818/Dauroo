#!/usr/bin/env bash
#
# install.sh — نصبِ کاملِ Dauroo روی سرورِ خام (اوبونتو/دبیان).
# ============================================================
#
# فرض: سرورِ تازه، هیچ‌چیز نصب نیست.
#
#     sudo bash deploy/install.sh
#
# idempotent است: هر بار دوباره بزنی فقط چیزی که کم است ساخته می‌شود، و
# `.env` / `data/` / `profiles/` هرگز بازنویسی یا پاک نمی‌شوند.
#
# فقط تشخیص، بدونِ تغییر:   sudo bash deploy/install.sh --check
#
# ---- لاگ‌یابی ----
# هر گام (۱) قبل از اجرا پیش‌نیازش را چک می‌کند، (۲) بعد از اجرا **نتیجه‌اش را
# تأیید می‌کند** نه فقط کدِ خروج، و (۳) اگر شکست خورد می‌گوید کدام دستور در کدام
# خط بود و کجای لاگ را بخوانی. همه‌چیز با زمان در این فایل می‌نشیند:
#
#     /var/log/dauroo-install.log
#
# در پایان یک «کارنامه» چاپ می‌شود که نشان می‌دهد چه چیزی سالم است و چه چیزی نه،
# پس اگر وسطش چیزی خراب شد لازم نیست حدس بزنی.

set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/dauroo}"
APP_USER="${APP_USER:-dauroo}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LOG="/var/log/dauroo-install.log"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

if [ -t 1 ]; then
    R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; C=$'\e[36m'; B=$'\e[1m'; N=$'\e[0m'
else R=""; G=""; Y=""; C=""; B=""; N=""; fi

#: کارنامه: نامِ گام → وضعیت
declare -a REPORT=()
CURRENT_STEP="راه‌اندازی"
FAILED=0

ts()   { date '+%Y-%m-%d %H:%M:%S'; }
logf() { printf '[%s] %s\n' "$(ts)" "$*" >>"$LOG"; }
step() { CURRENT_STEP="$*"; echo; echo "${B}▶ $*${N}"; logf "STEP $*"; }
ok()   { echo "  ${G}✅${N} $*"; logf "  OK  $*"; REPORT+=("ok|$CURRENT_STEP|$*"); }
warn() { echo "  ${Y}⚠️${N}  $*"; logf "  WARN $*"; REPORT+=("warn|$CURRENT_STEP|$*"); }
bad()  { echo "  ${R}❌${N} $*"; logf "  FAIL $*"; REPORT+=("bad|$CURRENT_STEP|$*"); FAILED=1; }
die()  { echo; echo "${R}${B}متوقف شد: $*${N}" >&2; logf "DIE $*"; show_report; exit 1; }

# هر خطای غیرمنتظره: بگو کدام خط و کدام دستور، و کجا را بخوان.
on_err() {
    local code=$? line=$1 cmd=$2
    echo
    echo "${R}${B}✖ خطا در گام «$CURRENT_STEP»${N}"
    echo "${R}  خط $line — دستور: ${cmd}${N}"
    echo "${R}  کدِ خروج: $code${N}"
    echo "${C}  لاگِ کامل:  tail -40 $LOG${N}"
    logf "ERROR line=$line code=$code cmd=$cmd"
    show_report
    exit "$code"
}
trap 'on_err "$LINENO" "$BASH_COMMAND"' ERR

# اجرای یک دستور با لاگِ کامل؛ خروجی‌اش در لاگ می‌رود نه روی صفحه.
run() {
    logf "  \$ $*"
    if "$@" >>"$LOG" 2>&1; then return 0; fi
    return 1
}

show_report() {
    echo
    echo "${B}───────────── کارنامه ─────────────${N}"
    local o=0 w=0 b=0
    for row in "${REPORT[@]:-}"; do
        [ -n "$row" ] || continue
        # هر `local` جدا: bash همه‌ی سمت‌راست‌های یک `local` را **قبل** از
        # انتساب بسط می‌دهد، پس ارجاع به `rest` در همان خط زیرِ `set -u`
        # خطای «unbound variable» می‌دهد و کارنامه را خراب می‌کند.
        local st="${row%%|*}"
        local rest="${row#*|}"
        local stp="${rest%%|*}"
        local msg="${rest#*|}"
        case "$st" in
            ok)   echo "  ${G}✅${N} $msg"; o=$((o+1)) ;;
            warn) echo "  ${Y}⚠️ ${N} $msg  ${C}[$stp]${N}"; w=$((w+1)) ;;
            bad)  echo "  ${R}❌${N} $msg  ${C}[$stp]${N}"; b=$((b+1)) ;;
        esac
    done
    echo "${B}───────────────────────────────────${N}"
    echo "  سالم: ${G}$o${N} · هشدار: ${Y}$w${N} · خراب: ${R}$b${N}"
    echo "  لاگ: $LOG"
}

# --------------------------------------------------------------------------- #
[ "$(id -u)" -eq 0 ] || die "با sudo اجرا کن:  sudo bash deploy/install.sh"
mkdir -p "$(dirname "$LOG")"; : >>"$LOG"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "${B}نصبِ Dauroo${N}  $( [ $CHECK_ONLY = 1 ] && echo "${C}(حالتِ تشخیص، بدونِ تغییر)${N}" )"
echo "───────────────────────────────"
echo "  سورس : $SRC_DIR"
echo "  نصب  : $APP_DIR"
echo "  کاربر: $APP_USER"
echo "  لاگ  : $LOG"
logf "=== install start (check_only=$CHECK_ONLY) src=$SRC_DIR dst=$APP_DIR ==="

# --------------------------------------------------------------------------- #
step "۰/۹ پیش‌بررسی محیط"
# --------------------------------------------------------------------------- #
. /etc/os-release 2>/dev/null || true
ok "سیستم: ${PRETTY_NAME:-نامشخص}"
command -v apt-get >/dev/null 2>&1 || die "apt-get نیست — این اسکریپت برای اوبونتو/دبیان است"
# رم و دیسک: کروم روی رمِ کم بالا نمی‌آید و نصبِ کرومیوم ~۵۰۰ مگ دیسک می‌خواهد.
MEM_MB=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)
DISK_MB=$(df -Pm / | awk 'NR==2{print $4}')
[ "$MEM_MB" -ge 1800 ] && ok "رم: ${MEM_MB} مگ" || warn "رم کم است (${MEM_MB} مگ) — مرورگر ممکن است بالا نیاید"
[ "$DISK_MB" -ge 3000 ] && ok "دیسکِ آزاد: $((DISK_MB/1024)) گیگ" || warn "دیسکِ آزاد کم است ($((DISK_MB)) مگ)"
if ping -c1 -W3 deb.debian.org >/dev/null 2>&1 || ping -c1 -W3 archive.ubuntu.com >/dev/null 2>&1; then
    ok "دسترسی به مخزنِ بسته‌ها"
else warn "مخزنِ بسته‌ها پاسخ نداد — اگر apt شکست خورد، دلیلش همین است"; fi

if [ $CHECK_ONLY = 1 ]; then
    step "تشخیصِ نصبِ موجود"
    [ -d "$APP_DIR" ] && ok "$APP_DIR هست" || bad "$APP_DIR نیست"
    [ -x "$APP_DIR/venv/bin/python" ] && ok "venv هست" || bad "venv نیست"
    [ -f "$APP_DIR/.env" ] && ok ".env هست" || bad ".env نیست"
    for u in dauroo-owner dauroo-customer; do
        if systemctl list-unit-files | grep -q "^$u.service"; then
            st=$(systemctl is-active "$u" 2>/dev/null || true)
            [ "$st" = active ] && ok "$u: فعال" || bad "$u: $st"
        else bad "$u نصب نیست"; fi
    done
    if [ -x "$APP_DIR/venv/bin/python" ]; then
        for m in telethon asyncssh playwright python_socks; do
            "$APP_DIR/venv/bin/python" -c "import $m" 2>/dev/null && ok "$m import می‌شود" || bad "$m نیست"
        done
    fi
    show_report; exit $FAILED
fi

# --------------------------------------------------------------------------- #
step "۱/۹ بسته‌های سیستمی"
# --------------------------------------------------------------------------- #
export DEBIAN_FRONTEND=noninteractive
run apt-get update -qq || warn "apt-get update خطا داد (ادامه می‌دهیم)"
# python3-venv جداست و بدونش venv ساخته نمی‌شود؛ build-essential برای هر
# وابستگی‌ای که چرخِ آماده ندارد.
if run apt-get install -y -qq python3 python3-venv python3-pip python3-dev \
        build-essential ca-certificates curl git tzdata; then
    ok "بسته‌ها نصب شد"
else
    die "نصبِ بسته‌ها شکست خورد — ببین:  tail -40 $LOG"
fi
# تأیید: خودِ پایتون و venv واقعاً کار می‌کنند؟
"$PYTHON_BIN" -V >/dev/null 2>&1 || die "$PYTHON_BIN اجرا نمی‌شود"
ok "پایتون: $("$PYTHON_BIN" -V 2>&1)"
"$PYTHON_BIN" -c "import venv" 2>/dev/null && ok "ماژول venv موجود" \
    || die "python3-venv نصب نشد — بدونش venv ساخته نمی‌شود"

# --------------------------------------------------------------------------- #
step "۲/۹ کاربرِ سرویس"
# --------------------------------------------------------------------------- #
if id "$APP_USER" >/dev/null 2>&1; then
    ok "کاربر $APP_USER از قبل هست"
else
    run useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER" \
        || die "ساختِ کاربر شکست خورد"
    id "$APP_USER" >/dev/null 2>&1 && ok "کاربر $APP_USER ساخته شد" || die "کاربر ساخته نشد"
fi

# --------------------------------------------------------------------------- #
step "۳/۹ کپیِ کد به $APP_DIR"
# --------------------------------------------------------------------------- #
mkdir -p "$APP_DIR"
if [ "$SRC_DIR" != "$APP_DIR" ]; then
    # .env و داده‌ها استثنا می‌شوند تا اجرای دوباره چیزی را از دست ندهد.
    tar -C "$SRC_DIR" --exclude=.git --exclude=data --exclude=profiles \
        --exclude=artifacts --exclude=.env --exclude=__pycache__ \
        --exclude='*.pyc' --exclude=venv -cf - . 2>>"$LOG" \
        | tar -C "$APP_DIR" -xf - 2>>"$LOG" || die "کپیِ کد شکست خورد"
    ok "کد کپی شد"
else
    ok "سورس = مقصد، کپی لازم نیست"
fi
# تأیید: فایل‌های حیاتی رسیدند؟
for f in main.py requirements.txt .env.example config.py relay/manager.py; do
    [ -f "$APP_DIR/$f" ] || die "$f در مقصد نیست — کپی ناقص بوده"
done
ok "فایل‌های حیاتی موجودند"

# --------------------------------------------------------------------------- #
step "۴/۹ venv و وابستگی‌های پایتون"
# --------------------------------------------------------------------------- #
if [ ! -x "$APP_DIR/venv/bin/python" ]; then
    run "$PYTHON_BIN" -m venv "$APP_DIR/venv" || die "ساختِ venv شکست خورد"
    ok "venv ساخته شد"
else ok "venv از قبل هست"; fi
PY="$APP_DIR/venv/bin/python"
[ -x "$PY" ] || die "venv/bin/python اجرایی نیست"
run "$PY" -m pip install --upgrade pip -q || warn "ارتقاء pip نشد (مهم نیست)"
if run "$PY" -m pip install -q -r "$APP_DIR/requirements.txt"; then
    ok "وابستگی‌ها نصب شد"
else
    die "نصبِ وابستگی‌ها شکست خورد — ببین:  tail -40 $LOG"
fi
# تأیید تک‌تک، چون «pip موفق شد» با «import می‌شود» یکی نیست.
for m in telethon asyncssh; do
    if "$PY" -c "import $m" 2>>"$LOG"; then
        ok "$m: $("$PY" -c "import $m;print(getattr($m,'__version__','?'))" 2>/dev/null)"
    else
        bad "$m import نمی‌شود — بدونش $( [ $m = asyncssh ] && echo 'تونلِ relay' || echo 'ربات') کار نمی‌کند"
    fi
done
# python_socks جدا چک می‌شود چون نبودنش **بی‌صدا** خراب می‌کند: تونل سالم بالا
# می‌آید و selfcheck سبز است، ولی ربات با «No module named 'socks'» می‌میرد.
if "$PY" -c "import python_socks" 2>>"$LOG"; then
    ok "python-socks: $("$PY" -c "import python_socks;print(getattr(python_socks,'__version__','?'))" 2>/dev/null)  (پروکسیِ تلگرام)"
else
    bad "python-socks نیست — با relay روشن، ربات بالا نمی‌آید. رفع: $PY -m pip install 'python-socks[asyncio]'"
fi

# --------------------------------------------------------------------------- #
step "۵/۹ کرومیومِ Playwright"
# --------------------------------------------------------------------------- #
# `--with-deps` کتابخانه‌های سیستمیِ کروم را هم با apt نصب می‌کند؛ همان چیزی که
# روی سرورِ خام جا می‌افتد و بعد کروم بی‌دلیل بالا نمی‌آید.
if run "$PY" -m playwright install --with-deps chromium; then
    ok "کرومیوم نصب شد"
else
    warn "نصبِ کرومیوم شکست خورد — موتورِ «سریع» بدونِ مرورگر کار می‌کند"
    warn "بعداً:  $PY -m playwright install --with-deps chromium"
fi
# کروم در HOME کاربری می‌نشیند که نصبش کرده (اینجا root)، پس کاربرِ سرویس
# نمی‌بیندش. کپی‌اش کن، وگرنه هر جابِ مرورگری شکست می‌خورد.
PW_SRC="/root/.cache/ms-playwright"
PW_DST="/home/$APP_USER/.cache/ms-playwright"
if [ -d "$PW_SRC" ] && [ ! -d "$PW_DST" ]; then
    mkdir -p "/home/$APP_USER/.cache"
    cp -a "$PW_SRC" "$PW_DST" && ok "کرومیوم برای کاربرِ سرویس کپی شد"
fi
if [ -d "$PW_DST" ] || [ -d "$PW_SRC" ]; then
    ok "کرومیوم در دسترس است"
else warn "کرومیوم پیدا نشد — جاب‌های مرورگری کار نمی‌کنند"; fi

# --------------------------------------------------------------------------- #
step "۶/۹ پوشه‌ها و .env"
# --------------------------------------------------------------------------- #
mkdir -p "$APP_DIR"/{data,profiles,artifacts}
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    KEY="$(head -c 32 /dev/urandom | base64 | tr -d '\n=' | tr '+/' '-_')"
    # از ENVIRON، نه `-v`: هرچند این کلید base64 url-safe است و `\` ندارد، همان
    # الگوی امنِ configure.sh را نگه می‌داریم تا کسی بعداً کورکورانه کپی نکند.
    AWK_V="$KEY" awk 'BEGIN{FS=OFS="="} $1=="RELAY_SECRET_KEY"{print "RELAY_SECRET_KEY=" ENVIRON["AWK_V"]; next}{print}' \
        "$APP_DIR/.env" > "$APP_DIR/.env.t" && mv "$APP_DIR/.env.t" "$APP_DIR/.env"
    ok ".env ساخته شد + RELAY_SECRET_KEY تولید شد"
    NEED_ENV=1
else
    ok ".env از قبل هست (دست‌نخورده ماند)"
    NEED_ENV=0
fi
# مهاجرت: یک خطِ فعالِ `MODE=` در .env قدیمی باید غیرفعال شود.
# چون مقادیرِ EnvironmentFile بر Environment= می‌چربند، همان یک خط باعث می‌شد
# **هر دو** سرویس یک نقش را اجرا کنند (سرویسِ مشتری، ربات مالک) و بعد دو پروسه
# یک فایلِ سشن را باز کنند → «database is locked» و حلقه‌ی ری‌استارت.
# نقش الان با آرگومان به main.py داده می‌شود، پس این خط فقط مضر است.
if grep -qE '^[[:space:]]*MODE=' "$APP_DIR/.env"; then
    sed -i 's/^[[:space:]]*MODE=/# MODE=  (غیرفعال شد؛ نقش با آرگومان داده می‌شود) /' \
        "$APP_DIR/.env"
    ok "خطِ MODE در .env غیرفعال شد (نقش از آرگومان می‌آید)"
fi
chmod 600 "$APP_DIR/.env"
chown -R "$APP_USER:$APP_USER" "$APP_DIR" "/home/$APP_USER" 2>/dev/null || true
ok "دسترسی‌ها تنظیم شد"

# --------------------------------------------------------------------------- #
step "۷/۹ واحدهای systemd"
# --------------------------------------------------------------------------- #
for unit in dauroo-owner dauroo-customer; do
    src="$APP_DIR/deploy/$unit.service"
    [ -f "$src" ] || die "$src نیست"
    sed -e "s|/opt/dauroo|$APP_DIR|g" -e "s|^User=.*|User=$APP_USER|" \
        -e "s|^Group=.*|Group=$APP_USER|" "$src" > "/etc/systemd/system/$unit.service"
done
run systemctl daemon-reload || die "daemon-reload شکست خورد"
# always-on: هم فعال (بعد از ری‌بوت بالا بیاید) هم Restart=always در یونیت.
run systemctl enable dauroo-owner dauroo-customer || warn "enable شکست خورد"
for unit in dauroo-owner dauroo-customer; do
    systemctl is-enabled "$unit" >/dev/null 2>&1 \
        && ok "$unit نصب و برای بوت فعال شد" || bad "$unit فعال نشد"
done

# --------------------------------------------------------------------------- #
step "۸/۹ تستِ سلامتِ نصب"
# --------------------------------------------------------------------------- #
cd "$APP_DIR"
TMPD="/tmp/dauroo_check_$$"
if sudo -u "$APP_USER" env DATA_DIR="$TMPD" PROFILES_DIR="$TMPD/p" \
        ARTIFACTS_DIR="$TMPD/a" "$PY" -m bot.tests.test_relay >>"$LOG" 2>&1; then
    ok "تستِ داخلیِ relay سبز است"
else
    bad "تستِ داخلیِ relay رد نشد — ببین:  tail -60 $LOG"
fi
rm -rf "$TMPD" 2>/dev/null || true
# config واقعاً بارگذاری می‌شود؟ (خطای .env اینجا لو می‌رود، نه سرِ استارت)
if sudo -u "$APP_USER" "$PY" -c "from config import config; print(config.MODE)" >>"$LOG" 2>&1; then
    ok "config و .env بارگذاری می‌شوند"
else
    bad "بارگذاریِ config شکست خورد — .env را چک کن"
fi

# --------------------------------------------------------------------------- #
step "۹/۹ گام‌های بعدی"
# --------------------------------------------------------------------------- #
show_report
echo
echo "${B}───────────── ادامه ─────────────${N}"
if [ "${NEED_ENV:-0}" = "1" ]; then
    echo "${Y}${B}۱) تنظیمات را بنویس${N} (یا دستی: nano $APP_DIR/.env)"
    echo "     sudo API_ID=... API_HASH=... OWNER_BOT_TOKEN=... \\"
    echo "          CUSTOMER_BOT_TOKEN=... OWNER_ID=... LOG_GROUP_ID=... \\"
    echo "          RELAY_HOST=... RELAY_PASSWORD=... \\"
    echo "          bash $APP_DIR/deploy/configure.sh"
    echo
fi
echo "${B}۲) تونل را تست کن${N} (قبل از استارت — سرور ایران است)"
echo "     cd $APP_DIR && sudo -u $APP_USER venv/bin/python -m relay.selfcheck"
echo
echo "${B}۳) استارت${N}"
echo "     sudo systemctl start dauroo-owner dauroo-customer"
echo
echo "${B}۴) لاگِ زنده${N}"
echo "     journalctl -u dauroo-owner -f"
echo "     journalctl -u dauroo-customer -f"
echo
echo "${B}تشخیصِ بعدی هر وقت خواستی:${N}  sudo bash $APP_DIR/deploy/install.sh --check"
echo "${B}─────────────────────────────────${N}"
logf "=== install done failed=$FAILED ==="
exit "$FAILED"
