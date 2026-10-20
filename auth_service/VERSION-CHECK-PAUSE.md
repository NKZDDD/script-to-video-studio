# 新包尚未分发：临时暂停版本校验

需要服务端 **1.6.1 或更新版本**，客户端不用重新打包。默认仍开启校验，由管理员明确暂停；后续升级保留已选状态。

## 现在先解除旧包阻断

把本次服务端 ZIP 里 `respect-studio-auth/` 的内容合并覆盖到 `/www/wwwroot/respect-studio-auth/`，保留原 `data/`、`.venv/`、`.env`、`deploy/runtime.env`。已运行 1.6.1 或更新版本时无需重复覆盖，可直接执行暂停命令。

在宝塔终端复制下面整段（不含 `[root@...]` 提示符）：

```bash
cd /www/wwwroot/respect-studio-auth &&
.venv/bin/python -m studio_auth.manage --db /www/wwwroot/respect-studio-auth/data/auth.sqlite3 client-version-check pause &&
systemctl restart respect-studio-auth
```

数据库路径是标准部署位置。如果以前手工修改过服务的 `STUDIO_AUTH_DB`，请将 `--db` 改为服务实际使用的同一数据库。命令会显示操作的数据库路径，找不到文件会拒绝执行，不创建空数据库。

确认服务版本：

```bash
curl --retry 5 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:8781/health
```

应返回本次部署的版本（至少 `1.6.1`）。再确认暂停状态：

```bash
.venv/bin/python -m studio_auth.manage --db /www/wwwroot/respect-studio-auth/data/auth.sqlite3 client-version-check status
```

应显示“版本校验：已暂停”。旧客户端重新登录或点击重新检查即可；账号密码、账号有效期和 Key 分配仍按原规则检查。

## 以后在网页操作

打开 `https://login.respect.chat/admin/`，按 Ctrl+F5 刷新，进入“客户端版本”。顶部会显示当前状态，点击：

- **暂停版本校验**：暂时允许旧版本、未登记构建进入正常账号登录流程。
- **恢复版本校验**：立即恢复已保存的最低版本与构建校验，客户端下次检查时生效。

也可在终端恢复：

```bash
cd /www/wwwroot/respect-studio-auth &&
.venv/bin/python -m studio_auth.manage --db /www/wwwroot/respect-studio-auth/data/auth.sqlite3 client-version-check resume
```

已经运行 1.6.1 或更新版本时，切换开关无需重启。暂停会持久保存；重启、上传安装包、登记测试构建、发布新推荐版本都不会自动恢复。

## 暂停的范围

所有 Studio 体系共用这个开关。暂停期间隐藏更新推荐，避免旧安装版解析 R2 地址失败；后台仍保留推荐版本、最低版本和全部构建记录，可继续准备新版。账号、Key 库、用量记录不受影响。

并未关闭密码、会话、账号停用、有效期、设备数、Key 授权检查；也不支持离线登录。暂停只跳过服务端的版本/构建检查，不关闭客户端已有的本地文件完整性检查。

发完新包并确认用户能安装后，再恢复校验即可。无需删除发布信息、清空数据库或手工降低最低版本。
