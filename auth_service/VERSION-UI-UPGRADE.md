# 服务端 1.6.3：按功能模块管理客户端版本

版本窗口由一张长表单改为四个模块。采用接近 iOS 设置界面的浅灰背景、白色分组卡片、统一圆角和间距；顶部导航与提交区固定，只滚动当前模块的内容。

| 模块 | 操作 |
|---|---|
| 版本概览 | 查看校验状态、暂停/恢复校验，以及各体系的推荐版本和最低版本 |
| 测试登记 | 导入测试包清单，只登记构建，不推送更新 |
| 正式发布 | 导入已上传清单，设置推荐版本、最低版本和更新说明 |
| 构建记录 | 按体系筛选已登记版本，查看当前推荐标记 |

下载地址、SHA256 等信息在「正式发布」中展开查看。切换模块会保留本次输入；成功与失败提示显示在对应操作区。支持窄屏与键盘切换模块。

本次只更新服务端网页和静态资源，保留原校验、账号、Key、用量与发布规则。无需重新安装 Studio EXE，也不会改变已暂停的状态或自动发布新版本。

## 部署

将 `respect-studio-auth-1.6.3.zip` 中 `respect-studio-auth/` **里面的内容**合并到：

```text
/www/wwwroot/respect-studio-auth/
```

保留原 `data/`、`.venv/`、`.env`、`deploy/runtime.env`，不要先删除整个目录。

宝塔终端执行：

```bash
systemctl restart respect-studio-auth
```

检查版本：

```bash
curl --retry 5 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:8781/health
```

应显示 `1.6.3`。然后在 `https://login.respect.chat/admin/` 按 **Ctrl+F5** 刷新，打开「客户端版本」。如果原来使用宝塔 Python 项目管理进程，在原管理入口重启，不要重复启动第二个进程。

新增 `studio_auth/web/releases.css` 必须一并上传；不要只替换 JavaScript。页面对新 CSS 和 JavaScript 使用了带版本号的资源地址，避免旧缓存导致布局错乱。
