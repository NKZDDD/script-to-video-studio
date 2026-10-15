# 已部署服务器升级网页后台

适用目录：`/www/wwwroot/respect-studio-auth`；Python 环境沿用原 `.venv`。这次更新服务端，无需修改 Studio 的认证地址。

## 1. 先备份数据库

在宝塔 root 终端执行，备份文件名每次应不同：

```bash
cd /www/wwwroot/respect-studio-auth
runuser -u studioauth -- .venv/bin/python -m studio_auth.manage backup "data/auth-before-admin-$(date +%Y%m%d-%H%M%S).sqlite3"
systemctl stop respect-studio-auth
```

把备份下载保存。然后上传新版 ZIP，解压后将里面 `respect-studio-auth/` 的内容覆盖到当前部署目录。**保留原有 `data/`、`.venv/`、`deploy/runtime.env`，不要删除整个旧目录；不要多套一层同名目录。** 包内不含这些运行数据。

## 2. 创建后台管理员并启动

```bash
cd /www/wwwroot/respect-studio-auth
runuser -u studioauth -- .venv/bin/python -m studio_auth.manage add-admin operator
systemctl restart respect-studio-auth
curl http://127.0.0.1:8781/health
```

按提示设置两次密码（至少 12 位，输入不显示）。只需创建一次管理员；已存在时无需重复添加。健康检查应显示版本 `1.1.0`。

打开：**https://login.respect.chat/admin/**

使用 `operator` 和刚才设置的密码登录。点击“创建账号”创建给 Studio 使用的普通账号；后台管理员本身不是 Studio 普通账号。

## 3. 反向代理

原站点已代理整个 `/` 时，无需新增站点。确认现有代理包含：

```nginx
proxy_set_header Host $host;
proxy_set_header X-Forwarded-Proto $scheme;
```

浏览器须通过 HTTPS 域名访问；如果出现“请从本站管理页面操作”，核对 Host 是否仍错误地写成 `127.0.0.1`。完整配置见 `deploy/nginx-location.conf`。

## 4. 管理员忘记密码

```bash
cd /www/wwwroot/respect-studio-auth
runuser -u studioauth -- .venv/bin/python -m studio_auth.manage password-admin operator
```

重置后旧管理会话失效，使用新密码重新登录，不影响普通账号密码。

旧普通账号、有效期、密码及服务商客户端配置均保留。账号停用和撤销登录会在客户端下一次在线校验生效；已经启动的批次继续执行。
