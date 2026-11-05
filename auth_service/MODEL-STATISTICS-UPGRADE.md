# 服务商模型成功率显示 · 1.13.0

本文记录已发布的 1.13.0。后续源码已改为按 Key 展示绿色成功率卡片，尚未打包；新入口及范围见 [Key 模型成功率卡片](KEY-MODEL-STATISTICS-UPGRADE.md)。

配套客户端：Studio 2026.11.05.1。按「服务商 + 完整模型 ID」显示，保留服务商分组，不删模型后缀；供应商不发布统计的模型显示暂无数据。

客户端：生产页、首选/备用模型、创建项目向导的模型输入旁点击「查看模型成功率」。后台：Key 库新增或编辑 Key，选择服务商后，在绑定模型下方点击同名入口，选择「添加绑定」，最后保存 Key。

成功率来自供应商公开接口或公开任务样本，不是 Studio 用户的任务成功率。共接入纵横、阿珂、无限画布、沧元、章鱼哥、巨轮、坤鸡、零视、小白龙、Vale 10 家。请求统计与任务终态统计分开标注；不同窗口、样本大小不宜直接等同。统计读取失败不会拦住生产，有历史结果时标明旧数据。

## 兼容与更新范围

- 客户端与后台各自从公开接口读取，五分钟缓存，后台最多两个读取线程。无需额外 Key，不触发付费生成。
- 本次不需要数据库迁移，也不需要重新分配 Key 或更新适配包。
- 旧客户端连接新服务端仍可登录和生产，但没有客户端统计入口。新客户端的统计读取独立于后台此功能；后台显示需部署 1.13.0。
- R2 上传只发布安装文件。管理员导入 `release.r2.json` 后，才按所选推荐/最低版本策略通知用户。本次清单 `mandatory=false`，无需因这项展示功能提高最低可用版本。

## 已有 1.12.0 服务端升级

保留现有数据库、`.venv`、运行环境、R2 配置和 systemd 服务文件，仅替换程序与静态资源并安装新增证书依赖。下方通过服务的 WorkingDirectory 定位已有安装，不另建服务。

将 `respect-studio-auth-1.13.0.zip` 上传至 `/root/`，以 root 执行：

```bash
set -e
umask 077
APP="$(systemctl show respect-studio-auth -p WorkingDirectory --value)"
test -n "$APP" && test -d "$APP/studio_auth" && test -x "$APP/.venv/bin/python"
printf '现有服务目录：%s\n' "$APP"
STAGING="$(mktemp -d /root/studio-auth-1.13.0.XXXXXX)"
unzip -q /root/respect-studio-auth-1.13.0.zip -d "$STAGING"
cd "$STAGING/respect-studio-auth"
sha256sum -c SHA256SUMS

# 先安装新增依赖，失败时不停止现有服务。
(umask 022; "$APP/.venv/bin/python" -m pip install certifi==2026.2.25)
BACKUP="$(mktemp -d /root/studio-auth-backup-1.13.0.XXXXXX)"
cp -a "$APP/studio_auth" "$BACKUP/studio_auth"
cp -a "$APP/requirements.txt" "$BACKUP/requirements.txt"
systemctl stop respect-studio-auth
cp -a "$APP/data" "$BACKUP/data"

cp -a "$STAGING/respect-studio-auth/studio_auth/." "$APP/studio_auth/"
chmod -R u=rwX,go=rX "$APP/studio_auth"
install -m 0644 "$STAGING/respect-studio-auth/requirements.txt" "$APP/requirements.txt"
install -m 0644 "$STAGING/respect-studio-auth/MODEL-STATISTICS-UPGRADE.md" "$APP/MODEL-STATISTICS-UPGRADE.md"
systemctl start respect-studio-auth
printf '备份目录：%s\n' "$BACKUP"
curl --fail http://127.0.0.1:8781/health
```

适用于数据库仍在安装目录 `data/` 的现有部署（包括此前 `/www/wwwroot/respect-studio-auth/data/auth.sqlite3`）。如果运行环境另指定 `STUDIO_AUTH_DB`，停止服务后也应备份其实际目录。切勿把程序目录或备份配置成可公开下载的网站目录。

健康检查应显示 `version: 1.13.0`。网页按 Ctrl+F5 后进入 Key 库，选择有公开数据的服务商，等待第一次后台读取（页面约十秒刷新一次）。服务器到部分供应商连接失败时可显示暂无数据，不代表客户端生成接口失败。

如服务启动失败，先查看 `journalctl -u respect-studio-auth -n 80 --no-pager`；需要回退时停止服务，用 `$BACKUP/studio_auth/.` 覆盖 `$APP/studio_auth/`，恢复原 requirements.txt 后启动。此版本不改表结构，回退代码无需还原数据库，避免丢失期间新数据。
