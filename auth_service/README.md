# Respect Studio 登录校验服务端 1.10.2

1.10.2 增加双应用选择和独立版本校验开关，批量客户端支持独立安装更新；账号和 Key 库继续共用，见 [双应用升级说明](APPLICATION-UPDATE-UPGRADE.md)。

1.10.1 增加“对外批量版”独立版本类别，复用原账号、Key 分配和用量对账接口。Studio 的更新策略保持独立，不增加计费功能，见[批量客户端接入说明](BATCH-CLIENT-UPGRADE.md)。

1.10.0 将提示词改写 LLM 接入 Key 库和账号分配。管理员填写 HTTPS 地址、Key 和一个模型，客户端登录或刷新后读取；客户端仍直接调用 LLM，不由认证服务器中转推理。配置导入同时支持顶层 `llm`。需要配套更新客户端，见 [LLM 配置升级说明](LLM-CONFIG-UPGRADE.md)。

1.9.0 新增按账号查看的“请求诊断”：区分服务端收到的请求与客户端上报的超时、连接错误及本地操作耗时。升级步骤、保存期限与判读边界见 [请求诊断升级说明](REQUEST-LOGS-UPGRADE.md)。客户端采集需要配套新版 EXE，旧版不会自动获得此功能。

1.8.1 修正反向代理模板遗留的 16 KB 上传限制，并在后台明确显示 HTTP 413 与处理方法。已部署站点需手动将原代理 `location` 内的 `client_max_body_size` 改为 `3m` 并重载 Nginx；只覆盖升级包不会修改宝塔站点配置。应用仍按接口限制请求大小，无需更新客户端。

1.8.0 新增服务商适配包统一发布，配套客户端 2026.10.22.2。导入、发布和已有站点上传限制修复见 [适配包升级说明](PROVIDER-ADAPTER-UPGRADE.md)。

1.7.0 新增后台 R2 配置与账号授权。客户端登录/刷新接收配置，图片和参考音频通过短期签名直接上传 R2，长期 Secret Key 留在服务端。需要安装新依赖和配套客户端 2026.10.21.1，见 [R2 升级说明](R2-UPGRADE.md)。原账号、Key 库、版本统计和更新策略保持不变。

1.6.4 新增「客户端版本 → 版本统计」：按账号、设备和最后上报版本查看升级进度，支持观察期、体系和账号筛选，供推荐/强制更新决策参考。请按 [版本统计升级说明](VERSION-STATS-UPGRADE.md) 部署；原账号、Key 与更新策略保持不变，无需重打 Studio EXE。数据从升级后开始采集，客户端版本声明不能作为未破解的证明。

给宝塔 Linux 部署的账号管理服务。后台可按账号分配图片/视频服务商 Key，登录后下发给客户端；未分配的服务商继续使用个人 Key。生成仍由客户端直接调用服务商。服务端接收管理员配置的 Key 和客户端用量记录，不接收项目内容、提示词或素材。

已部署服务器更新 1.6.3，请按 [版本管理界面升级说明](VERSION-UI-UPGRADE.md) 操作，保留原数据库和运行环境。版本管理按「版本概览、测试登记、正式发布、构建记录」分模块显示。支持 R2 下载地址，后台分别设置推荐版本和最低可用版本；保留启动检查、提交前检查与构建校验。可 [仅登记测试版本](TEST-BUILD-UPGRADE.md)，允许测试包通过校验而不向普通用户推送更新。新包尚未分发时，可按 [暂停版本校验说明](VERSION-CHECK-PAUSE.md) 暂停校验，仍要求账号登录和授权，发完新包再恢复。原有 Key 库与 config.json 导入功能保持兼容；相关配置见 [Key 库与模型对应升级说明](KEY-LIBRARY-UPGRADE.md)。

本包是**服务端**，不是 Studio EXE 更新。Studio 源码已接入 `https://login.respect.chat`；使用登录功能需要重新构建客户端，旧 EXE 不会自动改变。客户端行为见 [Studio 登录说明](../docs/studio-login.md)。`examples/studio_auth_client.py` 是独立对接示例。

## 包含什么

- 账号密码登录、在线校验、凭证续期、退出。
- 账号启停、有效期、密码重置、撤销全部登录。
- 可选登录设备数量限制，默认 0（不限）；设备 ID 是安装标识，不是不可伪造的硬件绑定。
- 密码使用 Argon2id 哈希；随机登录凭证只在数据库保存 SHA-256 哈希。
- SQLite SQL 数据库，WAL 模式，数据独立保存在 `data/`，无需另装 MySQL。
- 固定依赖、自动化测试、宝塔 Nginx 反向代理配置、systemd 开机启动及每日清理。
- 网页管理后台 `/admin/`：创建账号、搜索、修改期限与设备上限、启停、密码重置、查看登录设备、撤销全部登录。
- 后台管理员与普通账号独立，首次管理员通过服务器终端创建；不包含默认账号密码。
- 统一可命名 Key 库、账号勾选授权、服务商中文名称；每家可有多把 Key，客户端合并模型并按模型使用对应 Key。已保存的 Key 不回显；账号过期或停用后拒绝下发。
- 图片/视频生成尝试、任务 ID、结果状态上报；后台按来源、服务商和时间筛选，导出 CSV 对账。上报是客户端声明，不等于实际账单。

本地程序可以被修改，此方案用于正常客户端的账号管理，不保证防破解。服务端不会调用或转发生产请求。

## 1. 上传和安装

已经部署 1.0.0 的服务器，按 [网页后台升级说明](ADMIN-UPGRADE.md) 更新，保留原 `data/` 数据库及 `.venv/`。

环境：宝塔 Linux、Nginx、systemd、Python **3.11 或以上**（建议 3.12），Linux glibc 2.28 或以上，至少预留约 1 GB 可用内存。Argon2 每次密码校验约使用 64 MiB，单进程限制最多 4 次并行密码校验。旧 CentOS 7 不属于本包的部署目标；安装前可用 `ldd --version` 查看 glibc。

将压缩包解压，整个目录放到：

```text
/www/server/respect-studio-auth/
    requirements.txt
    studio_auth/
    deploy/
    examples/
    ...
```

**不要放到网站公开根目录，也不要配置 Nginx 静态访问这个目录。** 数据库和备份不应能从网页下载。

在宝塔终端以 root 运行：

```bash
cd /www/server/respect-studio-auth
python3 --version
bash deploy/install.sh /usr/bin/python3
```

如果 Python 安装在其他位置，将最后的路径改为该 Python 的实际路径。若提示缺少 venv，在 Debian/Ubuntu 安装与 Python 版本匹配的 `python3-venv` 软件包；也可使用宝塔安装的 Python 3.11+。不要用 Python 2 或过旧系统 Python。

脚本会创建无交互登录权限的 `studioauth` 系统用户、安装虚拟环境、初始化数据库，注册并启动服务。不会创建默认用户，不会开放公网 8781 端口，不会覆盖已有账号数据。

检查运行：

```bash
systemctl status respect-studio-auth --no-pager
curl http://127.0.0.1:8781/health
```

期望返回 `{"ok":true,"service":"respect-studio-auth",...}`。安装失败时查看：

```bash
journalctl -u respect-studio-auth -n 80 --no-pager
```

## 2. 在宝塔绑定域名和 HTTPS

1. 将专用子域名（例如 `auth.example.com`）解析到服务器。
2. 在宝塔添加该域名的网站，选择纯静态项目即可。站点目录使用一个新的空目录，**不能使用上面的服务代码目录**。
3. 在宝塔申请并启用 SSL 证书，打开“强制 HTTPS”。
4. 添加反向代理到 `http://127.0.0.1:8781`。参考 `deploy/nginx-location.conf`；在该站点 HTTPS 的 `server` 块中修改现有 `location /`，不要重复添加。
5. 确保传入 `X-Forwarded-Proto $scheme`，并将 `X-Forwarded-For` **覆盖为** `$remote_addr`，不要直接透传客户端提供的值。
6. 校验并重载 Nginx，在浏览器打开 `https://你的域名/health`。

应用只监听 127.0.0.1，Uvicorn 只信任本机反向代理的转发头；公网只开放 HTTPS。默认拒绝非 HTTPS 登录，不要为了消除错误关闭 HTTPS 检查。若报 `https_required`，先核对代理头和 SSL 配置。

本版说明按浏览器/客户端直接连接宝塔 Nginx 编写。若域名前再加 CDN，请按 CDN 官方地址范围正确配置 Nginx real_ip；否则限流看到的可能是 CDN 节点 IP。

## 3. 创建和管理账号

**始终在服务目录下、以 studioauth 身份运行管理命令**，避免生成另一份数据库或产生 root 所有的数据库日志文件：

```bash
cd /www/server/respect-studio-auth

# 创建有效期账号；会提示输入两次密码，密码不显示、不写入命令历史。
runuser -u studioauth -- .venv/bin/python -m studio_auth.manage add customer01 --expires '2027-01-01T00:00:00+08:00'

# 永久账号，最多两个同时登录的设备（0 表示不限）
runuser -u studioauth -- .venv/bin/python -m studio_auth.manage add customer02 --expires never --max-devices 2

# 查看账号
runuser -u studioauth -- .venv/bin/python -m studio_auth.manage list

# 续期（北京时间；示例到该时刻为止，不是包含当天全天）
runuser -u studioauth -- .venv/bin/python -m studio_auth.manage set customer01 --expires '2027-04-01T00:00:00+08:00'

# 停用 / 重新启用
runuser -u studioauth -- .venv/bin/python -m studio_auth.manage set customer01 --enabled no
runuser -u studioauth -- .venv/bin/python -m studio_auth.manage set customer01 --enabled yes

# 重置密码，同时撤销旧登录
runuser -u studioauth -- .venv/bin/python -m studio_auth.manage password customer01

# 查看登录设备名称和会话，或撤销全部登录
runuser -u studioauth -- .venv/bin/python -m studio_auth.manage sessions customer01
runuser -u studioauth -- .venv/bin/python -m studio_auth.manage revoke customer01

# 改设备上限会撤销当前登录，客户端需要重新登录
runuser -u studioauth -- .venv/bin/python -m studio_auth.manage set customer01 --max-devices 3
```

用户名为 3–64 位英文字母、数字或 `_ . @ + -`，统一转小写。密码长度 12–128 个字符。账号到期时间存储为 UTC 时间戳，管理命令输出 ISO UTC 时间；`+08:00` 用于输入北京时间。

默认访问凭证 15 分钟，登录会话最长 30 天。续期不会延长这 30 天的总期限，到期须重新登录。账号停用立即撤销会话，重新启用后仍需重新登录。账号到期后校验和续期均被拒绝；管理员续期后尚未过期且未撤销的会话可以继续校验。

数据库里的状态每次校验都会检查。Studio 在登录、开始新生产操作及手动刷新后台配置时在线校验；普通页面操作只检查本机会话和已知到期时间，不再每 60 秒强制联网。后台停用或修改配置在下一次在线校验生效；已经开始的批次保留原快照。临时网络故障会阻止新生产提交，但不强制退出仍有效的本地会话。

## 4. Studio 对接

### 网页管理后台

打开 `https://你的域名/admin/`。首次在部署目录执行：

```bash
runuser -u studioauth -- .venv/bin/python -m studio_auth.manage add-admin operator
```

交互输入两次至少 12 位的密码。`operator` 是后台管理员，与 Studio 使用账号分开；普通账号不能进入后台。登录后可在网页创建普通账号，无需再用终端逐个添加。

管理员登录最长 8 小时，退出立即失效。管理员忘记密码时在终端执行 `password-admin operator`，重置会撤销该管理员全部管理会话。当前不提供网页创建管理员或自助注册。

网页写操作要求同源 Origin 和 CSRF 校验；Cookie 使用 Secure、HttpOnly、SameSite=Strict。反向代理必须保留 `Host $host` 和 `X-Forwarded-Proto $scheme`，不要把 Host 写为 127.0.0.1。原 Studio Python 登录接口仍拒绝浏览器 Origin。

时间按浏览器所在电脑时区显示和输入；设备上限 0 为不限。数据库自动增加独立的管理员、管理会话及管理审计表，已有普通账号和登录会话保留。管理审计只记录操作人、动作、目标账号、时间，不记录密码；定时清理保留最近 90 天。

### 客户端调用

见 `API.md` 和 `examples/studio_auth_client.py`。

推荐由 Studio **本地 Python 后端**调用本服务，浏览器只与本地 Studio 通信。登录凭证不放到网页 localStorage；示例仅保存在进程内存，退出 Studio 后需重新登录。需要“记住登录”时应使用 Windows 系统凭据保护，不把明文凭证放入 `config.json`。

设备 ID 首次安装随机生成并保留，不要每次打开软件重新生成。多线程生产共享一个登录客户端，续期串行执行。同一设备重新登录会撤销该设备旧会话。

登录服务断网不等于账号已停用，也不等于授权成功。Studio 客户端断网校验失败时锁定页面并提供重试，阻止新操作；已启动的批次继续执行和取回，不因登录失败清空任务 ID 或重新提交。本服务不操作生产任务。

## 5. 备份、更新和维护

数据库文件：`data/auth.sqlite3`。**运行中不要只复制主文件而漏掉 WAL**，使用一致性备份命令：

```bash
cd /www/server/respect-studio-auth
runuser -u studioauth -- .venv/bin/python -m studio_auth.manage backup data/auth-backup-20261015.sqlite3
```

下载备份保存到安全位置。备份包含账号密码哈希和会话哈希，应按敏感文件保管；命令拒绝覆盖同名文件。

更新前备份，停止服务，覆盖代码及部署文件，保留 `data/`，再运行安装脚本。不要用“删除整个目录再解压”的方式更新。

恢复时停止服务和清理定时器，把**完整现有 data 目录改名保留**，重新创建同权限的 data 目录，将备份放成 `data/auth.sqlite3`，归属设为 `studioauth:studioauth`、目录权限 700、文件 600，再启动。不要混用旧 WAL 文件和恢复的主数据库。恢复备份会恢复备份时的账号状态；若不希望旧会话恢复，应对账号执行 `revoke`。

```bash
systemctl restart respect-studio-auth
systemctl list-timers respect-studio-auth-prune.timer
```

每日定时任务清除过期会话、限流计数和 90 天以前的审计记录；不删除账号。审计表只存事件、时间和内部用户/会话 ID，不存密码、Token、服务商 Key。

服务默认不记录 HTTP access log。不要在 Nginx、宝塔或外部监控里开启请求正文、Authorization 或响应正文日志。

## 6. 验证和限制

本地运行测试：

```bash
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/python -m pytest
```

此部署使用单机 SQLite，数据库需位于服务器本地磁盘，不能放网络共享盘。需要多台服务器共同使用账号库时，再迁移到 PostgreSQL/MySQL；不要直接把本版扩展成多机共享 SQLite。

本包不含你的域名、服务器凭据、服务商 Key、真实账号库或默认管理员密码。实际宝塔站点证书、DNS 和公网访问需由部署环境验证。

实现参考：[FastAPI 部署](https://fastapi.tiangolo.com/deployment/manually/)、[Argon2 密码哈希](https://argon2-cffi.readthedocs.io/en/stable/howto.html)、[OWASP 会话管理](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)。
