# 批量客户端接入（服务端 1.10.1）

日期：2026-10-29。

原服务仅接受 Studio 的 all/v34/v61 版本类别。批量程序若复用这些类别，会混用最低版本、构建登记和更新地址。
本次仅增加独立 `batch` 类别及后台名称“对外批量版”。账号、Key 库、配置下发、R2 签名和用量上报协议保持兼容，不增加计费模块，不迁移或清空数据库。

保留服务器 `.env`、`data/` 和 `.venv/`，覆盖程序文件后执行：

```bash
cd /www/wwwroot/respect-studio-auth
.venv/bin/python -m pip install -r requirements.txt
systemctl restart respect-studio-auth
curl --retry 5 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:8781/health
```

健康检查应显示 1.10.1。后台刷新后在“客户端版本 → 测试登记”导入批量版构建生成的 `batch-registration.json`。不要登记到 Studio 的“全体系”，不要为了测试关闭全局版本校验。
然后沿用 Key 库为账号绑定模型和分配 Key；需要参考图公网链接时分配 R2 授权。
原 Studio 推荐版本和最低版本不改变，批量版本未登记或指纹不匹配时照常拒绝登录。

用量仍按账号与具体 Key 汇总，原 CSV 中包含模型、时间、任务 ID、状态。相同账号两种应用的用量会合并；如需分开核算，分配不同账号。客户端上报只作对账依据。
