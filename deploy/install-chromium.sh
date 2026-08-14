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

# **حتماً** داخلِ APP_DIR کار کن، نه جایی که کاربر اسکریپت را از آن زده.
# چرا: اگر از `~/Dauroo` اجرا شود، `sudo -u dauroo` همان مسیر را به‌عنوان cwd
# ارث می‌برد و کاربرِ سرویس اجازه‌ی خواندنِ `/root/...` را ندارد، پس
# `python -m relay.fetch` با «No module named 'relay'» می‌مرد — یعنی بهترین
# مسیرِ نصب بی‌صدا رد می‌شد. PYTHONPATH هم صریح ست می‌شود.
cd "$APP_DIR" || { echo "به $APP_DIR نمی‌توانم بروم" >&2; exit 1; }
export PYTHONPATH="$APP_DIR"

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

#: آخرین دلیلِ شکستِ لانچ را از لاگ بیرون بکش و نشان بده.
#: بدونِ این، پیامِ «بالا نیامد» هیچ سرنخی نمی‌دهد و باید دستی لاگ را بخوانی.
why_failed() {
    local line
    line="$(grep -a 'browser launch FAILED' "$LOG" | tail -1 | cut -c1-300 || true)"
    if [ -n "$line" ]; then
        echo "     ${C}دلیل: ${line#*FAILED: }${N}"
    fi
    # اگر باینری یک stubِ snap باشد (روی اوبونتو chromium-browser همین است)،
    # معمولاً برای کاربرِ سرویس بالا نمی‌آید — این را صریح بگو.
    if [ -n "${CHROME_PATH:-}" ] && [ -e "${CHROME_PATH}" ]; then
        if file -b "${CHROME_PATH}" 2>/dev/null | grep -qi 'text\|script' \
           || grep -qai 'snap' "${CHROME_PATH}" 2>/dev/null; then
            echo "     ${Y}توجه: ${CHROME_PATH} یک اسکریپت/stubِ snap است، نه باینریِ واقعی.${N}"
            echo "     ${Y}روی اوبونتو، chromium-browser به snap اشاره می‌کند و برای${N}"
            echo "     ${Y}کاربرِ سرویس معمولاً بالا نمی‌آید. راهِ ۲ (تونل) بهتر است.${N}"
        fi
    fi
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
CHROME_PATH="$EXISTING_CHROME" why_failed

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
    # آدرس‌های جایگزینی که خودِ playwright فهرست می‌کند (میکروسافت/آژور).
    mapfile -t FALLBACKS < <(printf '%s\n' "$DRY" | grep -oP 'Download fallback \d+:\s*\K\S+' || true)

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

            # چند آدرس را به ترتیب امتحان کن.
            #
            # چرا لازم شد: `--dry-run` آدرسِ **خام** می‌دهد
            #   https://cdn.playwright.dev/builds/cft/<ver>/linux64/chrome-linux64.zip
            # ولی آنچه خودِ playwright هنگام دانلود می‌زند این است:
            #   https://cdn.playwright.dev/dbazure/download/playwright/builds/cft/...
            # روی سرور، ffmpeg (که آدرسِ dry-runش از قبل شکلِ dbazure داشت) موفق
            # شد و chrome شکست خورد — همین تفاوت. پس شکلِ dbazure را هم می‌سازیم،
            # و آدرس‌های جایگزینِ خودِ playwright را هم اضافه می‌کنیم.
            CANDS=()
            case "$url" in
                *"/dbazure/download/playwright/"*) CANDS+=("$url") ;;
                https://cdn.playwright.dev/builds/*)
                    CANDS+=("${url/https:\/\/cdn.playwright.dev\/builds\//https://cdn.playwright.dev/dbazure/download/playwright/builds/}")
                    CANDS+=("$url") ;;
                *) CANDS+=("$url") ;;
            esac
            for fb in "${FALLBACKS[@]:-}"; do
                [ -n "$fb" ] && [ "$(basename "$fb")" = "$name" ] && CANDS+=("$fb")
            done

            got=0
            for cand in "${CANDS[@]}"; do
                logf "  try $cand"
                # `-f` عمداً **نیست**: با -f کرل بی‌صدا شکست می‌خورد و فقط کدِ
                # خروج می‌دهد، پس «۴۰۳ جغرافیایی» از «قطعِ وسطِ دانلود» قابلِ
                # تشخیص نبود. اینجا کدِ HTTP و حجمِ دریافتی و خطای کرل را
                # می‌گیریم و نشان می‌دهیم.
                #
                # --retry/-C: فایلِ کروم حدود ۱۷۰ مگ است و از داخلِ یک تونلِ SSH
                # می‌آید؛ یک قطعیِ کوتاه نباید کلِ دانلود را دور بریزد. ffmpeg
                # (۲ مگ) موفق شد و کروم نشد، که خودش نشانه‌ی همین است.
                # --speed-limit/--speed-time: اگر ۳۰ ثانیه زیرِ ۱ کیلوبایت شد،
                # یعنی عملاً متوقف شده؛ ببند و برو سراغِ آدرسِ بعدی.
                cerr="/tmp/pw_curl_err.$$"
                code="$(curl -sSL --socks5-hostname "127.0.0.1:$SOCKS_PORT" \
                        --connect-timeout 20 --max-time 3600 \
                        --retry 5 --retry-delay 5 --retry-all-errors \
                        --speed-limit 1024 --speed-time 30 \
                        -C - -w '%{http_code}' \
                        -o "$tmp" "$cand" 2>"$cerr" || true)"
                rc=$?
                cmsg="$(tr -d '\r' <"$cerr" | tail -2 | tr '\n' ' ')"
                rm -f "$cerr"
                size="$(stat -c%s "$tmp" 2>/dev/null || echo 0)"
                logf "  http=$code rc=$rc size=$size err=$cmsg"
                # ۲۰۰ یا ۴۱۶ (یعنی از قبل کامل دانلود شده) قبول است.
                if { [ "$code" = "200" ] || [ "$code" = "206" ] || [ "$code" = "416" ]; } \
                   && [ "$size" -gt 100000 ]; then
                    got=1
                    break
                fi
                warn "نشد (HTTP $code · ${size} بایت): ${cand:0:60}…"
                [ -n "$cmsg" ] && echo "        ${C}کرل: $cmsg${N}"
                # فایلِ نیمه‌کاره را نگه ندار، وگرنه -C - دفعه‌ی بعد گیج می‌شود.
                [ "$size" -lt 100000 ] && rm -f "$tmp"
            done

            if [ "$got" -eq 1 ]; then
                mkdir -p "$dir"
                if unzip -qo "$tmp" -d "$dir" >>"$LOG" 2>&1; then
                    ok "$name نصب شد در $dir"
                else
                    bad "بازکردنِ $name شکست خورد"; FAILED=1
                fi
                rm -f "$tmp"
            else
                bad "هیچ‌کدام از ${#CANDS[@]} آدرسِ $name جواب نداد"; FAILED=1
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
        why_failed
    fi
fi

# --------------------------------------------------------------------------- #
step "۲b/۴ راهِ دوم-ب: دانلود روی خودِ relay، بعد آوردن با SFTP"
# --------------------------------------------------------------------------- #
# اگر دانلودِ SOCKS برای فایلِ بزرگ نشد، این مسیر مشکل را دور می‌زند: دانلود
# **روی relay** انجام می‌شود (آنجا فیلتری نیست) و فایل با SFTP از همان اتصالِ SSH
# می‌آید. اعتبارنامه از جدولِ relays خوانده می‌شود، پس رمز جایی تکرار نمی‌شود.
# تأییدِ قابلِ‌import بودن، قبل از تکیه بر آن — تا اگر مسیر خراب بود، پیامِ روشن
# بدهد نه یک ModuleNotFoundError در عمقِ لاگ.
if ! sudo -u "$APP_USER" env HOME="/home/$APP_USER" PYTHONPATH="$APP_DIR" \
        "$PY" -c "import relay.fetch" >>"$LOG" 2>&1; then
    bad "relay.fetch قابلِ import نیست (مسیر/دسترسی) — این راه رد شد"
    tail -3 "$LOG" | sed 's/^/        /'
elif sudo -u "$APP_USER" env HOME="/home/$APP_USER" PYTHONPATH="$APP_DIR" \
        "$PY" -m relay.fetch --playwright >>"$LOG" 2>&1; then
    chown -R "$APP_USER:$APP_USER" "/home/$APP_USER/.cache" 2>/dev/null || true
    if browser_works; then
        ok "از طریقِ relay نصب شد"
        echo
        echo "${G}${B}تمام. سرویس‌ها را ری‌استارت کن:${N}"
        echo "  sudo systemctl restart dauroo-owner dauroo-customer"
        exit 0
    fi
    warn "دانلود انجام شد ولی مرورگر بالا نیامد"
    why_failed
else
    warn "مسیرِ relay هم جواب نداد (جزئیات در لاگ)"
    tail -6 "$LOG" | sed 's/^/        /'
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
    bad "کرومیومِ سیستم هم بالا نیامد"
    CHROME_PATH="$CAND" why_failed
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
