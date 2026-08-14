#!/usr/bin/env bash
#
# install-chromium.sh — نصبِ کرومیوم روی سرورِ ایران، با سه راهِ پشتِ سرِ هم.
# =======================================================================
#
# مسئله: CDN پلی‌رایت جغرافیایی مسدود است و دانلود با ۴۰۳ رد می‌شود:
#   AccessDenied — "this service is not available in your location"
# نتیجه‌اش این کارتِ خطا سرِ لاگین بود:
#   Executable doesn't exist at .../chromium_headless_shell-XXXX/...
#
# سه راه به ترتیبِ تلاش:
#   ۱) مستقیم       — شاید شبکه‌ی این سرور اجازه بدهد.
#   ۲) از تونلِ relay — با curl و SOCKS5ِ همان تونلی که ربات دارد. URLهای دقیق را
#                      از خودِ `playwright install --dry-run` می‌گیریم، پس نسخه و
#                      مسیرها حدس زده نمی‌شوند.
#   ۳) کرومیومِ سیستم — با apt، و بعد `CHROME_PATH` در .env ست می‌شود تا
#                      `capture/browser.py` همان را براند.
#
# اجرا:
#     sudo bash deploy/install-chromium.sh
#
# idempotent است؛ اگر کرومیوم از قبل سالم باشد کاری نمی‌کند.

set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/dauroo}"
APP_USER="${APP_USER:-dauroo}"
PY="$APP_DIR/venv/bin/python"
LOG="/var/log/dauroo-chromium.log"
# پورتِ SOCKS تونلِ ربات مالک (پیش‌فرضِ RELAY_LOCAL_PORT).
SOCKS_PORT="${SOCKS_PORT:-1080}"

if [ -t 1 ]; then
    R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; C=$'\e[36m'; B=$'\e[1m'; N=$'\e[0m'
else R=""; G=""; Y=""; C=""; B=""; N=""; fi

ts()   { date '+%Y-%m-%d %H:%M:%S'; }
logf() { printf '[%s] %s\n' "$(ts)" "$*" >>"$LOG"; }
step() { echo; echo "${B}▶ $*${N}"; logf "STEP $*"; }
ok()   { echo "  ${G}✅${N} $*"; logf "OK $*"; }
warn() { echo "  ${Y}⚠️${N}  $*"; logf "WARN $*"; }
bad()  { echo "  ${R}❌${N} $*"; logf "FAIL $*"; }

[ "$(id -u)" -eq 0 ] || { echo "با sudo اجرا کن" >&2; exit 1; }
[ -x "$PY" ] || { echo "venv پیدا نشد: $PY — اول install.sh را بزن" >&2; exit 1; }
mkdir -p "$(dirname "$LOG")"; : >>"$LOG"

echo "${B}نصبِ کرومیوم${N}"
echo "───────────────────────────────"
echo "  لاگ: $LOG"

# --------------------------------------------------------------------------- #
# آیا از قبل سالم است؟ تنها آزمونِ معتبر: مرورگر واقعاً بالا بیاید.
# --------------------------------------------------------------------------- #
browser_works() {
    # CHROME_PATH باید **صریح** به محیطِ sudo داده شود؛ `sudo -u ... env` متغیرهای
    # پوسته‌ی فعلی را خودش منتقل نمی‌کند، پس بدونِ این خط، آزمون همیشه کرومیومِ
    # پیش‌فرض را می‌سنجید و CHROME_PATH بی‌اثر به‌نظر می‌رسید.
    sudo -u "$APP_USER" env HOME="/home/$APP_USER" \
        CHROME_PATH="${CHROME_PATH:-}" "$PY" - <<'PYEOF' >>"$LOG" 2>&1
import os, sys
from playwright.sync_api import sync_playwright
exe = os.environ.get("CHROME_PATH", "").strip() or None
try:
    with sync_playwright() as p:
        kw = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        if exe:
            kw["executable_path"] = exe
        b = p.chromium.launch(**kw)
        b.close()
    print("browser launch OK")
except Exception as exc:
    print(f"browser launch FAILED: {exc}", file=sys.stderr)
    sys.exit(1)
PYEOF
}

step "۰/۴ آزمونِ وضعیتِ فعلی"
# CHROME_PATH موجود در .env را هم در آزمون لحاظ کن.
EXISTING_CHROME="$(grep -E '^CHROME_PATH=.+' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
if [ -n "$EXISTING_CHROME" ]; then
    echo "  ${C}CHROME_PATH فعلی: $EXISTING_CHROME${N}"
fi
if CHROME_PATH="$EXISTING_CHROME" browser_works; then
    ok "کرومیوم از قبل سالم است — کاری لازم نیست"
    exit 0
fi
warn "مرورگر بالا نمی‌آید؛ می‌رویم سرِ نصب"

# --------------------------------------------------------------------------- #
step "۱/۴ راهِ اول: دانلودِ مستقیم"
# --------------------------------------------------------------------------- #
if sudo -u "$APP_USER" env HOME="/home/$APP_USER" \
        "$PY" -m playwright install chromium >>"$LOG" 2>&1; then
    if browser_works; then ok "مستقیم نصب شد"; exit 0; fi
    warn "دانلود تمام شد ولی مرورگر بالا نیامد"
else
    warn "دانلودِ مستقیم شکست خورد (به‌احتمالِ زیاد ۴۰۳ جغرافیایی)"
fi

# --------------------------------------------------------------------------- #
step "۲/۴ راهِ دوم: دانلود از داخلِ تونلِ relay"
# --------------------------------------------------------------------------- #
# تونل باید بالا باشد (ربات مالک روشن). فقط ترافیکِ همین دانلود از آن می‌رود.
if ! ss -lnt 2>/dev/null | grep -q "127.0.0.1:$SOCKS_PORT"; then
    warn "تونلِ SOCKS روی 127.0.0.1:$SOCKS_PORT باز نیست"
    warn "ربات مالک را روشن کن:  systemctl start dauroo-owner"
else
    ok "تونلِ SOCKS باز است (127.0.0.1:$SOCKS_PORT)"
    command -v curl >/dev/null || apt-get install -y -qq curl >>"$LOG" 2>&1 || true
    command -v unzip >/dev/null || apt-get install -y -qq unzip >>"$LOG" 2>&1 || true

    # URLها و مسیرهای نصب را از خودِ playwright بگیر — حدس نزن.
    DRY="$(sudo -u "$APP_USER" env HOME="/home/$APP_USER" \
        "$PY" -m playwright install --dry-run chromium 2>>"$LOG" || true)"
    printf '%s\n' "$DRY" >>"$LOG"

    # خطوطِ «Download url:» و «Install location:» را جفت‌به‌جفت بردار.
    mapfile -t URLS < <(printf '%s\n' "$DRY" | grep -oP 'Download url:\s*\K\S+' || true)
    mapfile -t DIRS < <(printf '%s\n' "$DRY" | grep -oP 'Install location:\s*\K\S+' || true)

    if [ "${#URLS[@]}" -eq 0 ]; then
        warn "نتوانستم URL دانلود را از playwright بگیرم (لاگ را ببین)"
    else
        ok "${#URLS[@]} بسته برای دانلود پیدا شد"
        FAILED=0
        for i in "${!URLS[@]}"; do
            url="${URLS[$i]}"
            dir="${DIRS[$i]:-}"
            [ -n "$dir" ] || { warn "مسیرِ نصب برای $url پیدا نشد"; FAILED=1; continue; }
            name="$(basename "$url")"
            echo "  ${C}↓ $name${N}"
            tmp="/tmp/pw_$name"
            # --socks5-hostname: حلِ نام هم سمتِ relay انجام شود، نه اینجا.
            if curl -fsSL --socks5-hostname "127.0.0.1:$SOCKS_PORT" \
                    --connect-timeout 20 --max-time 900 -o "$tmp" "$url" >>"$LOG" 2>&1; then
                mkdir -p "$dir"
                if unzip -qo "$tmp" -d "$dir" >>"$LOG" 2>&1; then
                    ok "$name نصب شد در $dir"
                else
                    bad "بازکردنِ $name شکست خورد"; FAILED=1
                fi
                rm -f "$tmp"
            else
                bad "دانلودِ $name از تونل شکست خورد"; FAILED=1
            fi
        done
        # پلی‌رایت اجرایی‌بودن را چک می‌کند، پس بیتِ اجرا را ست کن.
        chown -R "$APP_USER:$APP_USER" "/home/$APP_USER/.cache" 2>/dev/null || true
        find "/home/$APP_USER/.cache/ms-playwright" -type f \
            \( -name 'chrome' -o -name 'chrome-headless-shell' -o -name '*.sh' \) \
            -exec chmod +x {} \; 2>/dev/null || true
        if [ "$FAILED" -eq 0 ] && browser_works; then
            ok "از داخلِ تونل نصب شد"
            exit 0
        fi
        warn "نصب از تونل کامل نشد"
    fi
fi

# --------------------------------------------------------------------------- #
step "۳/۴ راهِ سوم: کرومیومِ سیستم + CHROME_PATH"
# --------------------------------------------------------------------------- #
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >>"$LOG" 2>&1 || true
CAND=""
for pkg in chromium chromium-browser; do
    if apt-get install -y -qq "$pkg" >>"$LOG" 2>&1; then
        ok "بسته‌ی $pkg نصب شد"
        break
    else
        warn "نصبِ $pkg نشد"
    fi
done
for p in /usr/bin/chromium /usr/bin/chromium-browser /snap/bin/chromium \
         /usr/lib/chromium/chromium /usr/lib/chromium-browser/chromium-browser; do
    [ -x "$p" ] && { CAND="$p"; break; }
done
if [ -z "$CAND" ]; then
    bad "کرومیومِ سیستم پیدا نشد"
else
    ok "کرومیومِ سیستم: $CAND"
    # CHROME_PATH را در .env بنویس (با ENVIRON، نه sed، تا مسیر سالم بماند).
    if grep -qE '^CHROME_PATH=' "$APP_DIR/.env"; then
        AWK_V="$CAND" awk 'BEGIN{FS=OFS="="} $1=="CHROME_PATH"{print "CHROME_PATH=" ENVIRON["AWK_V"]; next}{print}' \
            "$APP_DIR/.env" > "$APP_DIR/.env.t" && mv "$APP_DIR/.env.t" "$APP_DIR/.env"
    else
        printf 'CHROME_PATH=%s\n' "$CAND" >> "$APP_DIR/.env"
    fi
    chmod 600 "$APP_DIR/.env"; chown "$APP_USER:$APP_USER" "$APP_DIR/.env" 2>/dev/null || true
    ok "CHROME_PATH در .env نوشته شد"
    if CHROME_PATH="$CAND" browser_works; then
        ok "کرومیومِ سیستم کار می‌کند"
        echo
        echo "${G}${B}تمام. سرویس‌ها را ری‌استارت کن:${N}"
        echo "  sudo systemctl restart dauroo-owner dauroo-customer"
        exit 0
    fi
    bad "کرومیومِ سیستم هم بالا نیامد (لاگ: $LOG)"
fi

# --------------------------------------------------------------------------- #
step "۴/۴ هیچ راهی جواب نداد"
# --------------------------------------------------------------------------- #
echo
echo "${R}${B}کرومیوم نصب نشد.${N}  موتورِ «سریع» بدونِ مرورگر کار می‌کند، ولی"
echo "لاگینِ ایتا مرورگر می‌خواهد."
echo
echo "${B}گامِ بعدیِ دستی (روی سرورِ relay که خارج است):${N}"
echo "  ۱) روی relay دانلود کن، بعد به این سرور کپی کن:"
echo "     ssh root@<relay> 'curl -fsSLO <URLهایی که در لاگ هست>'"
echo "     scp root@<relay>:'*.zip' /tmp/"
echo "  ۲) بعد در /home/$APP_USER/.cache/ms-playwright/<مسیرِ لاگ> باز کن."
echo
echo "URLها و مسیرهای دقیق در همین لاگ هستند:  grep -E 'Download url|Install location' $LOG"
exit 1
