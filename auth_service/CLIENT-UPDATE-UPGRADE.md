# Studio 安装版与在线更新（服务端 1.4.0）

本次升级的是 Studio 主程序和现有登录服务 `login.respect.chat`，不是 DA。

配套 Studio 安装包已移除内置字幕识别、翻译与烧录，保留成片合成。原有项目和字幕文件不会被升级或卸载删除。

## 1. 先升级登录服务

把服务端 ZIP 中 `respect-studio-auth/` **里面的内容**合并到 `/www/wwwroot/respect-studio-auth/`。保留 `.env`、`.venv/` 和 `data/`，不要删除目录后重新解压。新增 `client_releases` 表，不修改账号、Key 库、授权和用量记录。

在宝塔终端只复制以下命令（不要复制终端提示符）：

```bash
cd /www/wwwroot/respect-studio-auth
.venv/bin/python -m pip install -r requirements.txt
systemctl restart respect-studio-auth
curl -s http://127.0.0.1:8781/health
```

版本应为 `1.4.0`。如果你原来由宝塔 Python 项目管理进程，请在原管理入口重启，不要同时启动第二个进程。

## 2. 配置安装包下载目录

在服务器建立 `/www/wwwroot/respect-studio-downloads/`，把 `Respect-Studio-Setup-2026.10.20.0.exe` 上传进去。

在 `login.respect.chat` 的 Nginx **server 块内**添加独立 location，和原来的反向代理 location 并列：

```nginx
location ^~ /downloads/ {
    alias /www/wwwroot/respect-studio-downloads/;
    autoindex off;
    add_header X-Content-Type-Options nosniff always;
}
```

保留已经配置好的 HTTPS、Host 和 `X-Forwarded-Proto`。让 Nginx 直接发送较大的安装包，避免占用登录服务的 Python 工作进程。保存并重载 Nginx。

## 3. 在后台发布

1. 登录 `https://login.respect.chat/admin/`，点击「客户端版本」。
2. 导入本次构建附带的 `release.json`，自动填写版本、下载地址、文件大小、SHA256 和说明。
3. 确认安装包地址可以下载，再点「发布版本」。
4. 测试 `https://login.respect.chat/api/v1/updates/latest?flavor=all`。发布之前返回 `{"release":null}` 是正常的。

下载地址必须是本站 HTTPS `/downloads/` 下的英文文件名。客户端拒绝外站、重定向、体积不符、SHA256 不符和低版本安装包。版本信息公开，不包含任何账号、Key 或登录凭据。

## 4. 用户安装与后续更新

旧绿色 EXE 没有更新入口，**首次需要运行一次新安装器**。退出旧 Studio，运行安装器，可选择 D 盘安装。以后从开始菜单或桌面快捷方式打开独立窗口，无需命令行或外部浏览器。

设置 → 软件更新 → 检查更新 → 下载新版 → 退出并安装更新。安装器读取原安装目录；正在运行批次时拒绝安装，也不会强制停止工作。安装完成后可以勾选启动。

默认沿用 `%LOCALAPPDATA%\Respect-Studio` 的数据及已有路径指针。原来把 `config.json` 放在旧 EXE 旁边的用户，应先在旧版设置中将数据目录迁出程序目录，再安装。安装器不会查找并复制任意旧 EXE 旁的密钥文件。

安装和卸载不删除项目或数据目录。界面日志放在数据目录 `logs/desktop.log`（保留两份轮转文件）；WebView2 缓存在数据目录 `webview/`，下载包在 `updates/`。

安装器在缺少 WebView2 时通过随包的微软引导程序联网安装；没有 Python 也可使用。程序未附代码签名证书，Windows 可能显示未知发布者。

## 发布下一版

修改 `core/updates.py` 中四段数字版本号，以更高版本重新构建；上传新安装器，在后台导入对应 `release.json`。不要只改后台版本号而不重新打包程序。

构建命令（构建机需要发布依赖、PyInstaller、NSIS 3.13 和微软 WebView2 Bootstrapper）：

```powershell
py tools/build_desktop.py --build D:/Studio-build --makensis D:/tools/nsis/makensis.exe --bootstrapper D:/tools/MicrosoftEdgeWebview2Setup.exe
```

安装器是完整更新；本版本没有自动强制更新、补丁差分或后台静默重启。
