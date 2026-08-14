#!/usr/bin/env bash
#
# update.sh — گرفتنِ کدِ جدید و اعمالش روی سرویسِ در حال اجرا.
# ===========================================================
#
# چرا این وجود دارد: مخزنی که `git pull` می‌زنی (`~/Dauroo`) و کدی که سرویس
# اجرا می‌کند (`/opt/dauroo`) **دو نسخه‌ی جدا** هستند — نصب با tar کپی می‌شود تا
# `.env` و `data/` سرویس دستِ گیت نباشد. نتیجه‌اش این تله بود:
#
#     git pull      → فایلِ جدید در ~/Dauroo
#     cd /opt/dauroo && python -m eitaa.creds
#                   → No module named eitaa.creds     ← هنوز کپی نشده
#
# این اسکریپت هر بار همان ترتیبِ درست را می‌زند:
#     pull → کپی به APP_DIR → نصبِ وابستگی‌های تازه → ری‌استارت → گزارش
#
# اجرا (از داخلِ مخزن):
#     sudo bash deploy/update.sh
#
# `.env`، `data/`، `profiles/`، `artifacts/` هرگز لمس نمی‌شوند.

set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/dauroo}"
APP_USER="${APP_USER:-dauroo}"
LOG="/var/log/dauroo-update.log"

if [ -t 1 ]; then
    R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; C=$'\e[36m'; B=$'\e[1m'; N=$'\e[0m'
else R=""; G=""; Y=""; C=""; B=""; N=""; fi

ts()   { date '+%Y-%m-%d %H:%M:%S'; }
logf() { printf '[%s] %s\n' "$(ts)" "$*" >>"$LOG"; }
step() { echo; echo "${B}▶ $*${N}"; logf "STEP $*"; }
ok()   { echo "  ${G}✅${N} $*"; logf "OK $*"; }
warn() { echo "  ${Y}⚠️${N}  $*"; logf "WARN $*"; }
bad()  { echo "  ${R}❌${N} $*"; logf "FAIL $*"; }

[ "$(id -u)" -eq 0 ] || { echo "با sudo اجرا کن:  sudo bash deploy/update.sh" >&2; exit 1; }
mkdir -p "$(dirname "$LOG")"; : >>"$LOG"

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -d "$APP_DIR" ] || { echo "$APP_DIR نیست — اول deploy/install.sh را بزن" >&2; exit 1; }

echo "${B}به‌روزرسانی Dauroo${N}"
echo "───────────────────────────────"
echo "  مخزن  : $SRC_DIR"
echo "  سرویس : $APP_DIR"

# --------------------------------------------------------------------------- #
step "۱/۵ گرفتنِ کدِ جدید"
# --------------------------------------------------------------------------- #
if [ -d "$SRC_DIR/.git" ]; then
    BEFORE="$(git -C "$SRC_DIR" rev-parse --short HEAD 2>/dev/null || echo '?')"
    if git -C "$SRC_DIR" pull --ff-only >>"$LOG" 2>&1; then
        AFTER="$(git -C "$SRC_DIR" rev-parse --short HEAD 2>/dev/null || echo '?')"
        if [ "$BEFORE" = "$AFTER" ]; then
            ok "از قبل به‌روز بود ($AFTER)"
        else
            ok "به‌روز شد: $BEFORE → $AFTER"
        fi
    else
        warn "git pull نشد (شاید تغییرِ محلی داری) — با کدِ فعلی ادامه می‌دهم"
        tail -3 "$LOG" | sed 's/^/        /'
    fi
else
    warn "$SRC_DIR مخزنِ گیت نیست — فقط کپی می‌کنم"
fi

# --------------------------------------------------------------------------- #
step "۲/۵ کپیِ کد به $APP_DIR"
# --------------------------------------------------------------------------- #
# requirements را قبل و بعد مقایسه کن تا بدانیم نصبِ وابستگی لازم است یا نه.
REQ_BEFORE="$(sha256sum "$APP_DIR/requirements.txt" 2>/dev/null | cut -d' ' -f1 || echo none)"
if [ "$SRC_DIR" != "$APP_DIR" ]; then
    tar -C "$SRC_DIR" --exclude=.git --exclude=data --exclude=profiles \
        --exclude=artifacts --exclude=.env --exclude=__pycache__ \
        --exclude='*.pyc' --exclude=venv -cf - . 2>>"$LOG" \
        | tar -C "$APP_DIR" -xf - 2>>"$LOG" || { bad "کپی شکست خورد"; exit 1; }
    ok "کد کپی شد (.env و data/ دست‌نخورده)"
else
    ok "مخزن و مقصد یکی‌اند؛ کپی لازم نیست"
fi
REQ_AFTER="$(sha256sum "$APP_DIR/requirements.txt" 2>/dev/null | cut -d' ' -f1 || echo none)"
chown -R "$APP_USER:$APP_USER" "$APP_DIR" 2>/dev/null || true
# پایکشِ کهنه می‌تواند کدِ قدیمی را زنده نگه دارد.
find "$APP_DIR" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
ok "کشِ پایتون پاک شد"

# --------------------------------------------------------------------------- #
step "۳/۵ وابستگی‌ها"
# --------------------------------------------------------------------------- #
PY="$APP_DIR/venv/bin/python"
if [ "$REQ_BEFORE" != "$REQ_AFTER" ]; then
    warn "requirements.txt عوض شده — نصب می‌کنم"
    if "$PY" -m pip install -q -r "$APP_DIR/requirements.txt" >>"$LOG" 2>&1; then
        ok "وابستگی‌ها نصب شد"
    else
        bad "نصبِ وابستگی‌ها شکست خورد — ببین:  tail -30 $LOG"
    fi
else
    ok "requirements.txt تغییری نداشت"
fi
# پینِ playwright حیاتی است (نسخه‌ی جدید از ایران دانلود نمی‌شود)، پس چک کن.
PW="$("$PY" -c "import playwright;print(playwright.__version__)" 2>/dev/null || echo '?')"
PW_WANT="$(grep -oP '^playwright==\K\S+' "$APP_DIR/requirements.txt" 2>/dev/null || echo '')"
if [ -n "$PW_WANT" ] && [ "$PW" != "$PW_WANT" ]; then
    warn "playwright نصب‌شده $PW است ولی باید $PW_WANT باشد"
    warn "اگر مرورگر خراب شد:  $PY -m pip install 'playwright==$PW_WANT'"
else
    ok "playwright: $PW"
fi

# --------------------------------------------------------------------------- #
step "۴/۵ ری‌استارتِ سرویس‌ها"
# --------------------------------------------------------------------------- #
# یونیت‌ها ممکن است عوض شده باشند.
for unit in dauroo-owner dauroo-customer; do
    src="$APP_DIR/deploy/$unit.service"
    if [ -f "$src" ]; then
        sed -e "s|/opt/dauroo|$APP_DIR|g" -e "s|^User=.*|User=$APP_USER|" \
            -e "s|^Group=.*|Group=$APP_USER|" "$src" \
            > "/etc/systemd/system/$unit.service"
    fi
done
systemctl daemon-reload
# حالتِ «تسلیم‌شده» را پاک کن، وگرنه ری‌استارت بی‌اثر است.
systemctl reset-failed dauroo-owner dauroo-customer 2>/dev/null || true
systemctl restart dauroo-owner dauroo-customer
ok "ری‌استارت شد"

# --------------------------------------------------------------------------- #
step "۵/۵ بررسیِ سلامت"
# --------------------------------------------------------------------------- #
sleep 8
FAILED=0
for unit in dauroo-owner dauroo-customer; do
    st="$(systemctl is-active "$unit" 2>/dev/null || true)"
    if [ "$st" = "active" ]; then
        # نقشِ واقعیِ هر پروسه را نشان بده — یک بار هر دو سرویس ربات مالک را
        # اجرا می‌کردند و کسی نمی‌فهمید.
        role="$(journalctl -u "$unit" -n 40 --no-pager 2>/dev/null \
                | grep -oP 'نقش: \K\w+' | tail -1 || true)"
        ok "$unit: فعال${role:+ · نقش: $role}"
    else
        bad "$unit: $st"
        FAILED=1
        journalctl -u "$unit" -n 12 --no-pager 2>/dev/null | sed 's/^/        /'
    fi
done

echo
echo "───────────────────────────────"
if [ "$FAILED" -eq 0 ]; then
    echo "${G}${B}✅ به‌روزرسانی تمام شد.${N}"
else
    echo "${R}${B}⚠️ سرویس بالا نیامد — لاگِ بالا را ببین.${N}"
fi
echo "  لاگِ زنده:  journalctl -u dauroo-customer -f"
echo "───────────────────────────────"
exit "$FAILED"
