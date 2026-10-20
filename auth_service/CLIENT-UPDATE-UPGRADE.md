# Studio 安装版与 R2 在线更新（服务端 1.6.2）

本次升级的是 Studio 主程序和现有登录服务 `login.respect.chat`，不是 DA。

配套 Studio 安装包已移除内置字幕识别、翻译与烧录，保留成片合成。原有项目和字幕文件不会被升级或卸载删除。

## 1. 先升级登录服务

把服务端 ZIP 中 `respect-studio-auth/` **里面的内容**合并到 `/www/wwwroot/respect-studio-auth/`。保留 `.env`、`.venv/` 和 `data/`，不要删除目录后重新解压。新增 `client_releases`、`client_builds` 表，不修改账号、Key 库、授权和用量记录。

在宝塔终端只复制以下命令（不要复制终端提示符）：

```bash
cd /www/wwwroot/respect-studio-auth
.venv/bin/python -m pip install -r requirements.txt
systemctl restart respect-studio-auth
curl -s http://127.0.0.1:8781/health
```

版本应为 `1.6.2`。如果你原来由宝塔 Python 项目管理进程，请在原管理入口重启，不要同时启动第二个进程。

如果服务端已发布新版本、旧客户端尚未更换，可先按 [暂停版本校验说明](VERSION-CHECK-PAUSE.md) 暂停，发完新包再恢复。暂停不会删除版本记录，也不会关闭账号登录和 Key 授权。

## 2. 在构建电脑上传 R2

本地 Studio 的 `config.json → upload` 已有 R2 配置时，上传工具直接读取，不回显密钥，不修改参考图上传设置。R2 上传 Key 只留在构建电脑；不会写入 EXE、版本清单或登录服务器。

在 Studio 源码目录运行（用本次构建输出的实际路径）：

```powershell
py tools/upload_update.py --release D:/Studio-build-20261020-r2/release.json
```

如使用其他配置位置，加 `--config D:/Studio-data/config.json`。如已在 R2 绑定专门的下载域名，加 `--public-base-url https://你的下载域名`，无需修改参考图所用域名。

工具将安装包上传到 `studio-updates/all/Respect-Studio-Setup-版本号.exe`，再从公开下载地址读取完整文件并校验 SHA256；成功后生成同目录的 **`release.r2.json`**。重复运行会复用同一文件并重新验证，不覆盖同版本的不同文件。原 `release.json` 保留，后台应导入已验证的 `release.r2.json`。上传不会自动发布强制更新规则。

现有 R2 公共地址可用于首次验证。Cloudflare 将 `r2.dev` 定位为开发用途并施加速率限制，正式分发应在 R2 绑定自定义域名，而非将 CNAME 指向 `r2.dev`。参见 [R2 公开桶文档](https://developers.cloudflare.com/r2/buckets/public-buckets/)。

### 仍需兼容同域下载时

也可继续在服务器建立 `/www/wwwroot/respect-studio-downloads/`，把新安装器上传进去，沿用原 `release.json`。

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
2. 导入上传工具生成的 `release.r2.json`，自动填写推荐版本、R2 下载地址、文件大小、安装包 SHA256、程序构建校验码和说明。下载地址也可以在网页修改，但必须先验证新地址提供同一文件。
3. 选择「最低可用版本」，再点「发布更新设置」。推荐 V3、最低 V2 时，V2 可以继续使用，V1 必须更新；如最低也设 V3，则旧版全部必须更新。可在之后重新发布同一推荐版本的设置，提高最低版本，不必再次上传或打包。最低版本不允许降低。
4. 测试 `https://login.respect.chat/api/v1/updates/latest?flavor=all`。用浏览器直接访问时没有客户端版本头，`client.allowed` 为 false 不代表发布失败；正式客户端会带版本与校验码检查。尚未发布推荐时 `release` 为 null；未登记的新包会停在版本检查页。仅测试时，可在「登记测试版本（不推送更新）」导入打包生成的清单，允许测试包通过校验，同时保持原更新推荐不变，见 [测试发版说明](TEST-BUILD-UPGRADE.md)。

下载地址支持公网 HTTPS 的 R2 公开域名、自定义域名和原本站 `/downloads/`。客户端拒绝重定向、临时签名 URL、体积不符、SHA256 不符和低版本安装包。版本规则始终从固定登录域名获取，R2 只保存安装包。版本信息公开，不包含账号、Key 或登录凭据。规则在客户端下次启动、登录或提交新任务时检查生效，不是服务器向桌面发送即时弹窗。

## 4. 用户安装与后续更新

旧绿色 EXE 没有更新入口；安装版 **2026.10.20.1 及以前只接受登录服务同域名下载**。直接切到 R2 时，这些用户首次需要手动运行 `2026.10.20.2` 或更新安装器。也可先用原同域 `/downloads/` 发布过渡版，用户升级后再将下载地址切到 R2。退出旧 Studio，运行安装器，可选择 D 盘安装。以后从开始菜单或桌面快捷方式打开独立窗口，无需命令行或外部浏览器。

设置 → 软件更新 → 检查更新 → 下载新版 → 退出并安装更新。安装器读取原安装目录；正在运行批次时拒绝安装，也不会强制停止工作。安装完成后可以勾选启动。

默认沿用 `%LOCALAPPDATA%\Respect-Studio` 的数据及已有路径指针。原来把 `config.json` 放在旧 EXE 旁边的用户，应先在旧版设置中将数据目录迁出程序目录，再安装。安装器不会查找并复制任意旧 EXE 旁的密钥文件。

安装和卸载不删除项目或数据目录。界面日志放在数据目录 `logs/desktop.log`（保留两份轮转文件）；WebView2 缓存在数据目录 `webview/`，下载包在 `updates/`。

安装器在缺少 WebView2 时通过随包的微软引导程序联网安装；没有 Python 也可使用。程序未附代码签名证书，Windows 可能显示未知发布者。

## 发布下一版

修改 `core/client_build.py` 中四段数字版本号，以更高版本重新构建；用上传工具上传新安装器，在后台导入对应 `release.r2.json`。不要只改后台版本号而不重新打包程序。

构建命令（构建机需要发布依赖、PyInstaller、NSIS 3.13 和微软 WebView2 Bootstrapper）：

```powershell
py tools/build_desktop.py --build D:/Studio-build --makensis D:/tools/nsis/makensis.exe --bootstrapper D:/tools/MicrosoftEdgeWebview2Setup.exe
```

安装器是完整更新，支持强制更新；没有补丁差分或后台静默重启。


## 5. 强制更新与校验

部署顺序：**升级服务端到 1.6.2 → 过渡期间暂停版本校验 → 上传并校验安装器 → 后台导入 release.r2.json 并选择最低版本发布 → 分发客户端 → 恢复版本校验**。如没有仍需使用旧包的用户，可不暂停。
本次清单不自动提高最低版本，由管理员在后台选择。发布前确认下载地址可访问，首次迁移安排用户关闭旧程序。
旧绿色 EXE 没有更新页，需要手动安装一次；启用构建登记后，旧版会收到服务端拒绝登录/领取配置的提示。

- 启动时只显示版本检查页；通过后可登录。普通更新可稍后，强制更新没有跳过入口。
- 开始生产、重新生成、合成和刷新后台配置前实时查询，不使用上次登录缓存放行。
- 登录服务在登录、续期、验证并下发 Key 时也检查最低版本与构建登记，不只靠客户端 UI。
- 后台分别设置推荐版本和最低可用版本。后续普通发布保留最低版本，同版本不能换校验码；修改程序需要递增版本重新构建。
- 网络失败、服务端不支持或未登记构建时不能开始新任务。可在版本检查页重新检查连接、下载、安装。不会提供离线跳过强制更新的入口。
- 已开始的批次不会被强杀；更新页可停止当前批次（保留已提交的任务 ID），等任务退出后再安装。
- 损坏安装可以下载同一正式版本修复。安装前再次检查安装包体积和 SHA256。
- 客户端检查 studio-build.json 中列出的 EXE、运行库及内置资源的 SHA256；用户配置、项目和外置服务商插件不纳入程序指纹。不要手动修改安装目录中的内置资源。

后台「程序构建校验码」由 release.json 导入，不手填。安装包 SHA256 与程序构建校验码用途不同：前者验证下载文件，后者标识安装后的发行构建。

### 安全边界

SHA256 和客户端上报的版本不是秘密，也不是可信硬件证明。对程序解包修改后，攻击者仍能伪造版本/校验码请求；本方案不能保证「无法盗用」。服务端可以强制控制自己的账号、会话和 Key 配置接口，但服务商 Key 已下发客户端并直连服务商，无法阻止提取后绕过本登录服务调用。要进一步限制这类使用，需要服务商短期凭据或服务端代理，当前按用户选择保留客户端直连。

本次图标来自用户指定的 https://api.respect.chat/login?expired=true 页面实际 logo 配置，原始资源为 https://static-1318240866.cos.ap-guangzhou.myqcloud.com/doubao_1772088486145.png ，等比转换为 Windows ICO 和页面 PNG，未重新设计图案。
