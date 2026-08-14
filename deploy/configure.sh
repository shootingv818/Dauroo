#!/usr/bin/env bash
#
# configure.sh — نوشتنِ .env از متغیرهای محیطی.
# ==============================================
#
# چرا جدا از install.sh: اعتبارنامه‌ها (توکنِ ربات، رمزِ SSH) **هرگز** نباید در
# فایلی باشند که در گیت کامیت می‌شود — این مخزن public است و توکنی که آنجا بنشیند
# در چند دقیقه برداشته می‌شود. پس این اسکریپت راز در خودش ندارد؛ مقادیر را از
# محیط می‌گیرد و در `.env` می‌نویسد که gitignore شده است.
#
# استفاده:
#
#     sudo API_ID=... API_HASH=... OWNER_BOT_TOKEN=... CUSTOMER_BOT_TOKEN=... \
#          OWNER_ID=... LOG_GROUP_ID=... RELAY_HOST=... RELAY_PASSWORD=... \
#          bash deploy/configure.sh
#
# فقط کلیدهایی که بدهی نوشته می‌شوند؛ بقیه از `.env.example` دست‌نخورده می‌مانند.
# اجرای دوباره فقط همان کلیدها را به‌روز می‌کند (idempotent).

set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/dauroo}"
APP_USER="${APP_USER:-dauroo}"
ENV_FILE="$APP_DIR/.env"

if [ -t 1 ]; then G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; B=$'\e[1m'; N=$'\e[0m'
else G=""; Y=""; R=""; B=""; N=""; fi

[ "$(id -u)" -eq 0 ] || { echo "${R}با sudo اجرا کن${N}" >&2; exit 1; }

if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$APP_DIR/.env.example" ]; then
        cp "$APP_DIR/.env.example" "$ENV_FILE"
        echo "${G}✅${N} .env از نمونه ساخته شد"
    else
        die() { echo "$1" >&2; exit 1; }
        die "${R}نه .env هست نه .env.example — اول install.sh را بزن${N}"
    fi
fi

# کلیدِ رمزنگاریِ رمزِ relay: اگر خالی است بساز. بدونِ آن، افزودنِ relay رد
# می‌شود (رمز هرگز بی‌رمزنگاری ذخیره نمی‌شود)، پس مسیرِ امن باید پیش‌فرض باشد.
if ! grep -qE '^RELAY_SECRET_KEY=.+' "$ENV_FILE" 2>/dev/null; then
    GEN="$(head -c 32 /dev/urandom | base64 | tr -d '\n=' | tr '+/' '-_')"
    RELAY_SECRET_KEY="${RELAY_SECRET_KEY:-$GEN}"
    echo "${G}✅${N} RELAY_SECRET_KEY تولید شد"
fi

set_kv() {
    local key="$1" val="$2"
    [ -n "$val" ] || return 0
    # مقدار از طریق **محیط** به awk می‌رسد، نه با `-v`.
    # چرا: در `awk -v v="$val"` رشته‌ی ورودی escape-processing می‌خورد، پس رمزی
    # که `\` دارد بی‌صدا خراب می‌شود (`a\db` → `adb`) و بعد «رمز غلط» می‌گیری
    # بدونِ هیچ سرنخی. `ENVIRON` این پردازش را ندارد.
    if grep -qE "^${key}=" "$ENV_FILE"; then
        AWK_K="$key" AWK_V="$val" awk \
            'BEGIN{FS=OFS="="} $1==ENVIRON["AWK_K"] {print ENVIRON["AWK_K"] "=" ENVIRON["AWK_V"]; next} {print}' \
            "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
    fi
    # مقدار چاپ نمی‌شود — رمز و توکن نباید در ترمینال/لاگ بنشیند.
    echo "  ${G}✅${N} $key"
}

echo "${B}نوشتنِ تنظیمات در $ENV_FILE${N}"

# تلگرام
set_kv API_ID              "${API_ID:-}"
set_kv API_HASH            "${API_HASH:-}"
set_kv OWNER_BOT_TOKEN     "${OWNER_BOT_TOKEN:-}"
set_kv CUSTOMER_BOT_TOKEN  "${CUSTOMER_BOT_TOKEN:-}"
set_kv OWNER_ID            "${OWNER_ID:-}"
set_kv LOG_GROUP_ID        "${LOG_GROUP_ID:-}"
# relay
set_kv RELAY_ENABLED       "${RELAY_ENABLED:-1}"
set_kv RELAY_HOST          "${RELAY_HOST:-}"
set_kv RELAY_SSH_PORT      "${RELAY_SSH_PORT:-22}"
set_kv RELAY_USER          "${RELAY_USER:-root}"
set_kv RELAY_PASSWORD      "${RELAY_PASSWORD:-}"
set_kv RELAY_SECRET_KEY    "${RELAY_SECRET_KEY:-}"

chmod 600 "$ENV_FILE"
chown "$APP_USER:$APP_USER" "$ENV_FILE" 2>/dev/null || true
echo "${G}✅${N} دسترسی 600 (فقط مالکش می‌خواند)"

# ---- اعتبارسنجی: چیزی که اجباری است و خالی مانده --------------------------- #
missing=()
for k in API_ID API_HASH OWNER_BOT_TOKEN CUSTOMER_BOT_TOKEN OWNER_ID; do
    grep -qE "^${k}=.+" "$ENV_FILE" || missing+=("$k")
done
echo
if [ ${#missing[@]} -gt 0 ]; then
    echo "${Y}⚠️  این‌ها خالی‌اند و ربات بالا نمی‌آید:${N} ${missing[*]}"
    exit 1
fi
echo "${G}${B}✅ تنظیماتِ اجباری کامل است.${N}"

# هشدارِ API_ID عمومی: 2040 مالِ Telegram Desktop است و بین همه مشترک؛ روی آن
# محدودیت و پرچمِ سوءاستفاده زیاد است. برای سرویسِ واقعی، مالِ خودت را بساز.
if grep -qE '^API_ID=(2040|1|4|6)$' "$ENV_FILE"; then
    echo "${Y}⚠️  API_ID عمومی/اشتراکی است. برای سرویسِ واقعی از my.telegram.org${N}"
    echo "${Y}   یک API_ID مخصوص خودت بساز، وگرنه ممکن است محدود شوی.${N}"
fi
