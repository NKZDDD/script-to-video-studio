# 超模 TH / Native 分组 Key 外挂

版本：20260930.1。供支持 providers 外挂的 Studio EXE 使用，不含密钥，无需安装 Python。

## 安装

1. 等当前生产结束，退出 Studio。
2. 在 Studio 设置或启动窗口确认实际数据目录。将压缩包内 `providers/zz_chaomo_groups.py` 放到该数据目录的 `providers` 文件夹。
3. 其他旧超模外挂（例如 `chaomo_image_refs_hotfix.py`）先移出 providers 备份，避免多个超模文件互相覆盖。其他服务商外挂保留。
4. 重新启动，在设置中确认超模来源是 `zz_chaomo_groups.py`。“覆盖已有同名服务商”是正常提示。
5. 配置两把 Key，保存后在生产行选择对应模型和清晰度。

例如数据目录为 `D:\stv-data`，最终文件为 `D:\stv-data\providers\zz_chaomo_groups.py`。不是项目素材目录；只有实际数据目录就在 EXE 旁时，才放在 EXE 旁的 providers 中。

## 两把 Key 怎么填、怎么选

| 旧 EXE 中的字段 | 填写的 Key | 对应模型 |
| --- | --- | --- |
| 图片 1K Key | TH 分组 Key | 下列 5 个 TH 模型 |
| 图片 4K Key | Native 原生分组 Key | 下列 4 个统一 Native 模型，以及原有固定档位 Native 模型 |

原来两个字段已有正确 Key 时不用重新填写。外挂不合并、复制或改写密钥，也不会在某组缺 Key 时偷偷改用另一组。

TH 模型：

- `gpt-image-1k-th`：仅 1K。
- `gpt-image-2-th`
- `gpt-image-2.5-th`
- `gpt-image-2.5-flare-th`
- `gpt-image-2.5-sunburst-th`

统一 Native 模型：

- `gpt-image-2-Native`
- `gpt-image-2.5-Native`
- `gpt-image-2.5-flare-Native`
- `gpt-image-2.5-sunburst-Native`

除 `gpt-image-1k-th` 外，上述模型都可以选 1K、2K、4K，实际通过请求的 `size` 参数传递，画幅由 `ratio` 传递。
**模型分组决定 Key；清晰度不会切换 Key。** 例如 TH 模型选 4K 仍使用 TH Key；Native 模型选 1K 仍使用 Native Key。

旧 EXE 的字段标签不会被此外挂改名。主程序源码已改为“TH 分组 Key（原图片 1K）”和“Native 原生 Key（原图片 4K）”。

## 范围与兼容

- 包含完整超模服务商实现、旧 EXE 后端 Key 路由兼容、模型清单保留规则；只替换请求代码不足以修正旧 EXE 的选 Key 逻辑。
- 图片参考图不在本地按 9 张限制或截断；按顺序全部提交。服务商自身限制仍以接口响应为准。
- 保留参考图完整解码检查、图片异步任务轮询等原有处理。
- 不改变全局任务 ID 管理和其他服务商。已提交任务仍须使用原提交账号取回，换 Key 不会把已有任务转到另一账号。
- 参考图“点击放大、另设查看任务入口”是主程序前端改动，**不包含在服务商外挂内**；旧 EXE 要获得这一界面改动，需要后续新版 EXE。
- 已用 2026-09-29 的旧 EXE、隔离数据目录、测试 Key 验证 9 个模型的加载、分组路由和清晰度声明。没有进行付费生成；不代表对用户真实账户的授权验证。

模型依据：用户提供的 TH / 原生分组截图和 2026-09-30 读取的官方模型页 https://zntcode.net/models 。

撤销：退出 Studio，移走 `zz_chaomo_groups.py`，再启动。以后换用包含本次改动的新 EXE 时可移除此外挂，避免覆盖更新的内置实现。
