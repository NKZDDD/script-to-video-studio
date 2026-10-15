#!/usr/bin/env bash
set -euo pipefail
umask 022
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
PYTHON="${1:-python3}"
if [[ "$EUID" -ne 0 ]]; then
    echo '请在宝塔 root 终端运行本脚本。' >&2
    exit 1
fi
if [[ ! "$APP_DIR" =~ ^/[a-zA-Z0-9_./-]+$ ]] || [[ "$APP_DIR" == /root/* ]] || [[ "$APP_DIR" == /home/* ]]; then
    echo '请部署到 /www/server/respect-studio-auth；路径不要含空格或中文，也不要在 /root 或 /home。' >&2
    exit 1
fi
command -v systemctl >/dev/null
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 11), "需要 Python 3.11 或以上版本"'
if ! getent passwd studioauth >/dev/null; then
    useradd --system --user-group --home-dir "$APP_DIR/data" --shell /usr/sbin/nologin studioauth
fi
if [[ "$(id -u studioauth)" == 0 ]]; then
    echo 'studioauth 不能是 root 账号。' >&2
    exit 1
fi
cd "$APP_DIR"
"$PYTHON" -m venv .venv
.venv/bin/python -m pip install --no-cache-dir -r requirements.txt
install -d -m 0700 -o studioauth -g studioauth "$APP_DIR/data"
runuser -u studioauth -- .venv/bin/python -m studio_auth.manage init
for unit in respect-studio-auth.service respect-studio-auth-prune.service respect-studio-auth-prune.timer; do
    sed "s|@APP_DIR@|$APP_DIR|g" "deploy/$unit" > "/etc/systemd/system/$unit"
    chmod 0644 "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable respect-studio-auth.service respect-studio-auth-prune.timer
systemctl restart respect-studio-auth.service
systemctl start respect-studio-auth-prune.timer
echo '服务已启动，仅监听 127.0.0.1:8781。下一步在宝塔配置 HTTPS 反向代理，再创建账号。'
echo '健康检查：curl http://127.0.0.1:8781/health'
