#!/usr/bin/env bash
#
# 需要 root 的安裝步驟。這台 Pi 的 sudo 需要密碼且無法互動輸入，
# 所以這些事沒辦法自動代跑——請自己執行：
#
#     sudo bash ~/weien/diabetes_report/deploy/setup-root.sh
#
# 可重複執行。不會碰 Cloudflare 儀表板上的任何設定（見 README 的部署章節）。

set -euo pipefail

PROJECT=/home/ubuntu/weien/diabetes_report
SERVICE=diabetes-report

if [[ $EUID -ne 0 ]]; then
    echo "請用 sudo 執行：sudo bash $0" >&2
    exit 1
fi

echo "── 1/4 安裝 cloudflared ─────────────────────────────────────"
if command -v cloudflared >/dev/null; then
    echo "已安裝：$(cloudflared --version 2>&1 | head -1)"
else
    # 走 Cloudflare 的 apt repo 而非直接下載 .deb，之後才會跟著系統更新
    mkdir -p --mode=0755 /usr/share/keyrings
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
        > /usr/share/keyrings/cloudflare-main.gpg
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
        > /etc/apt/sources.list.d/cloudflared.list
    apt-get update -qq
    apt-get install -y cloudflared
    echo "已安裝：$(cloudflared --version 2>&1 | head -1)"
fi

echo
echo "── 2/4 檢查應用環境 ─────────────────────────────────────────"
if [[ ! -x "$PROJECT/.venv/bin/gunicorn" ]]; then
    echo "找不到 $PROJECT/.venv/bin/gunicorn" >&2
    echo "請先以 ubuntu 身分執行：cd $PROJECT && uv sync --extra web" >&2
    exit 1
fi
install -d -o ubuntu -g ubuntu -m 700 "$PROJECT/var"
echo "gunicorn 就緒，var/ 權限 700"

echo
echo "── 3/4 安裝 systemd 服務 ────────────────────────────────────"
install -m 644 "$PROJECT/deploy/$SERVICE.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable --now "$SERVICE"
sleep 2
systemctl --no-pager --lines=0 status "$SERVICE" | head -4

echo
echo "── 4/4 cloudflared 設定 ─────────────────────────────────────"
if [[ -f /etc/cloudflared/config.yml ]]; then
    echo "已有 /etc/cloudflared/config.yml，不覆蓋。"
    systemctl is-enabled cloudflared >/dev/null 2>&1 \
        && echo "cloudflared 服務已啟用" \
        || echo "提醒：cloudflared 服務尚未啟用（cloudflared service install）"
else
    mkdir -p /etc/cloudflared
    echo "尚未設定。接下來請照 README「部署」章節做這幾步（需要你的 Cloudflare 帳號）："
    echo "  cloudflared tunnel login          # 不要加 sudo"
    echo "  cloudflared tunnel create cgm     # 記下 UUID"
    echo "  # create 把憑證寫在 ~/.cloudflared/，服務讀的是 /etc/cloudflared/，要複製過去："
    echo "  sudo install -o root -g root -m 600 ~/.cloudflared/<UUID>.json /etc/cloudflared/"
    echo "  sudo cp $PROJECT/deploy/cloudflared-config.yml /etc/cloudflared/config.yml"
    echo "  # 編輯該檔，把兩處 TUNNEL_ID 換成那組 UUID"
    echo "  cloudflared tunnel route dns cgm cgm.whtwbrown.com   # 不要加 sudo"
    echo "  sudo cloudflared service install && sudo systemctl restart cloudflared"
fi

echo
echo "完成。本機自我檢查："
curl -sf -o /dev/null -w "  http://127.0.0.1:8090/ → HTTP %{http_code}\n" \
    http://127.0.0.1:8090/ || echo "  應用尚未回應，請看 journalctl -u $SERVICE -n 50"
