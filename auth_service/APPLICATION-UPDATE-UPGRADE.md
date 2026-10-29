# 双应用版本管理（认证服务 1.10.2）

## 1.10.3 后台布局修正

修正客户端版本页的应用选择框缩进，以及用量对账中日期输入框重叠、筛选按钮错位。宽窗口下筛选分为三列两行，窄窗口自动排列为两列或一列；筛选条件与 CSV 导出逻辑不变。更新后的静态资源带新版本参数，避免继续加载浏览器中的旧样式。

覆盖升级认证服务并重启即可，不需要重新安装两个客户端。账号仍共用，没有按应用单独授权；设备上限也由账号共用。两款客户端分别生成设备标识，因此同一台电脑同时登录两款客户端需要两个设备名额（上限设为 0 表示不限）。

Studio 与对外批量版共用账号、Key 库和用量记录，各自管理推荐版本、最低版本、构建记录与版本校验开关。

保留服务器 `.env`、`data/`、`.venv/`，不要删除原目录。升级 ZIP 内已有 `respect-studio-auth/`，解压目标选 `/www/wwwroot/`，覆盖原程序后执行：

```bash
cd /www/wwwroot/respect-studio-auth
.venv/bin/python -m pip install -r requirements.txt
systemctl restart respect-studio-auth
curl --retry 5 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:8781/health
```

健康检查应显示 1.10.2。首次升级将原校验开关状态复制给两个应用，之后互不影响。旧 Studio 客户端和已有发布记录继续兼容，无需重装 Studio。

打开 https://login.respect.chat/admin/ → 客户端版本，先选“Respect Studio”或“对外批量版”，再登记测试构建或正式发布。导入清单必须属于所选应用。

批量版 2026.10.29.2 起带更新器。先登记 `batch-registration.json`，安装本次 `Respect-Batch-Setup-2026.10.29.2.exe`。上一版 2026.10.29.1 没有更新器，首次切换须手动运行安装器并关闭旧程序。

后续更新：构建新的批量安装器 → 使用批量项目的 `tools/upload_update.py --release .../release.json --config .../config.json` 上传并校验 R2 → 选择“对外批量版”，导入生成的 `release.r2.json` → 设置推荐/最低版本并发布。上传文件不会自动推更。没有上传时 release.json 的 URL 留空，不能直接正式发布。

批量更新使用 `batch-updates/batch/`，Studio 继续使用 `studio-updates/`。批量安装目录和注册表独立，用户项目与用量数据不在安装器替换范围中。客户端更新入口登录前也能访问；正在运行的任务需完成收尾后再重启更新。

命令行开关现在按应用操作，未写 application 默认为 Studio：

```bash
.venv/bin/python -m studio_auth.manage client-version-check status --application batch
.venv/bin/python -m studio_auth.manage client-version-check pause --application batch
.venv/bin/python -m studio_auth.manage client-version-check resume --application batch
```

Key 池仍共用。同账号跨两种应用的用量仍按账号合并。批量模型根据账号绑定、Key 实际可见模型以及批量已适配协议决定；本次不包含执行 Studio 远程适配包的兼容层。
