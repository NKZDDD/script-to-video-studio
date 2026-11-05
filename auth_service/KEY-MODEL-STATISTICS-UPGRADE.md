# Key 模型成功率卡片 · 1.13.1

配套客户端：Studio `2026.11.05.2`。

后台进入 **Key 库 → 对应 Key 的“模型与成功率”**，直接查看该 Key 的模型卡片。模型从此 Key 的接口读取；如果设置了绑定模型，按照绑定清单展示，与客户端的模型分配规则一致。新增或修改 Key 后先保存。

客户端在生产页、首选/备用模型或创建项目向导中点击模型输入框，即可看到带成功率的模型卡片，并选择完整模型 ID。

每张卡片显示成功率、绿色分段条、平均耗时、样本和统计窗口。统计仍来自服务商平台；Key 用于确定可显示的模型范围，并不是计算此 Key 的个人成功率。无统计显示灰色“暂无数据”，实际 0% 用红色区分，旧统计显示黄色。绿色分段条只表示成功比例，不是实时连接检测或逐条任务结果。

无需额外提供检测 Key，后台使用 Key 库中已保存的凭据读取模型。未绑定的 Key 需已发布适配包提供鉴权模型读取接口；只有公开模型/健康接口时，页面会说明无法确认 Key 的模型范围，需要绑定准确模型名。

需要更新服务端程序及静态资源、客户端 EXE 后两端才生效。无需数据库迁移，不改原有登录、生产授权和强更策略。此前 Studio 2026.11.05.1 / auth 1.13.0 包不包含此次修正。

## 已有部署升级（宝塔终端）

将 `respect-studio-auth-1.13.1.zip` 上传到 `/root/`，在宝塔终端以 root 执行。沿用原 systemd 服务及虚拟环境，不新建站点、数据库或登录服务。

```bash
set -e
umask 077
APP="$(systemctl show respect-studio-auth -p WorkingDirectory --value)"
test -n "$APP" && test -d "$APP/studio_auth" && test -x "$APP/.venv/bin/python"
printf '现有服务目录：%s\n' "$APP"
STAGING="$(mktemp -d /root/studio-auth-1.13.1.XXXXXX)"
unzip -q /root/respect-studio-auth-1.13.1.zip -d "$STAGING"
cd "$STAGING/respect-studio-auth"
sha256sum -c SHA256SUMS

# 1.13.0 已有此依赖；较早版本需补装。失败时不停止现有服务。
(umask 022; "$APP/.venv/bin/python" -m pip install certifi==2026.2.25)
BACKUP="$(mktemp -d /root/studio-auth-backup-1.13.1.XXXXXX)"
cp -a "$APP/studio_auth" "$BACKUP/studio_auth"
cp -a "$APP/requirements.txt" "$BACKUP/requirements.txt"
systemctl stop respect-studio-auth
cp -a "$APP/data" "$BACKUP/data"
cp -a "$STAGING/respect-studio-auth/studio_auth/." "$APP/studio_auth/"
chmod -R u=rwX,go=rX "$APP/studio_auth"
install -m 0644 "$STAGING/respect-studio-auth/requirements.txt" "$APP/requirements.txt"
install -m 0644 "$STAGING/respect-studio-auth/KEY-MODEL-STATISTICS-UPGRADE.md" "$APP/KEY-MODEL-STATISTICS-UPGRADE.md"
systemctl start respect-studio-auth
printf '备份目录：%s\n' "$BACKUP"
if ! curl --retry 10 --retry-connrefused --retry-delay 1 --connect-timeout 3 --max-time 5 -fsS http://127.0.0.1:8781/health; then
  systemctl status respect-studio-auth --no-pager -l
  journalctl -u respect-studio-auth -n 80 --no-pager
  exit 1
fi
```

备份命令适用于数据库位于现有安装目录 `data/`（包括 `/www/wwwroot/respect-studio-auth/data/auth.sqlite3`）的部署。如果服务另行指定了 `STUDIO_AUTH_DB`，停止服务后应备份其实际目录。保留原 `.venv`、服务配置、Key 和数据库，不覆盖用户配置。

健康检查应返回 `version: 1.13.1`。刚启动时短暂拒绝连接会自动重试；持续拒绝连接需要查看上述服务日志。网页 Ctrl+F5 后进入 **Key 库 → 对应 Key 的“模型与成功率”**。已有新版适配包提供模型读取声明时无需重新发适配包；若页面提示缺少读取接口，先发布含监测声明的适配包，或给该 Key 绑定准确模型名。

回退时停止服务，用打印出的 `$BACKUP/studio_auth/.` 覆盖 `$APP/studio_auth/` 并恢复原 requirements.txt，再启动服务。无需回退数据库，以免覆盖期间新数据。

## 客户端发布

在管理后台选择 Studio 的 **客户端版本 → 正式发布**，导入交付目录的 `release.r2.json`，推荐版本为 `2026.11.05.2`。清单 `mandatory=false`，最低可用版本可保持原值。登记新构建后用户可在线更新或安装 EXE；上传 R2 本身不会修改推荐版本和强制更新策略。
