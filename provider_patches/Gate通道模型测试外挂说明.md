# Gate 通道模型测试外挂 · 20261019.1

按用户要求，以下 8 个新模型名沿用对应无后缀模型的现有参数。属于待用户实测的通道映射，不代表服务商已验证这些通道的上限。本包不含 Key，没有发起付费生成测试。

## 安装

1. 等正在运行的任务结束。在 Studio「设置 → 服务商与密钥」点击「导入服务商文件」，选择包内 `providers/zz_gate_formoss.py`。
2. 或退出 Studio，将该文件放入实际数据目录的 `providers` 文件夹，再启动。数据目录以「设置 → 数据与路径」的 `data_dir` 为准，不一定是 EXE 所在目录。不要放到项目材料目录或 `_MEI` 临时目录。
3. 如有其他覆盖 Gate 的外挂，先移到 providers 文件夹外备份，避免重复覆盖。其他服务商文件不动。
4. 点击「重新扫描服务商」或重启，Gate 来源应为 `zz_gate_formoss.py`。“覆盖已有同名服务商”属于预期提示，不应有加载错误。
5. 生产页选择 Gate，再选择下面的完整模型名。原 Gate Key 和地址继续沿用。

工作室模型：

```text
seedance-2.0-fast-tobpersonal-formoss
seedance-2.0-mini-tobpersonal-formoss
seedance-2.0-standard-tobpersonal-formoss
seedance-2.5-tobpersonal-formoss
```

中转模型：

```text
seedance-2.0-fast-tobpersonal-formoss-zhongzhuan
seedance-2.0-mini-tobpersonal-formoss-zhongzhuan
seedance-2.0-standard-tobpersonal-formoss-zhongzhuan
seedance-2.5-tobpersonal-formoss-zhongzhuan
```

## 后台分配 Key 的账号

如果使用个人 Key，在 Studio 本地填写即可。如果 Gate 使用后台分配 Key，在管理后台对应 Key 的「绑定模型」中加入实际授权的完整新模型名，保存后在客户端点击「刷新后台配置」。原有模型绑定需要继续使用的应保留。

目前公开目录未列出这 8 个名称，后台自动拉取可能看不到它们。外挂提供接口能力，后台绑定决定该账号使用哪把 Key；外挂不会自动授予权限，也不会修改后台。

## 本包沿用的参数

| 对应原模型 | 时长 | 清晰度 | 参考图片 / 视频 / 音频上限 |
|---|---|---|---|
| seedance-2.0-fast | 4–15 秒 | 480p、720p、1080p、4k | 9 / 3 / 3 |
| seedance-2.0-mini | 4–15 秒 | 480p、720p | 9 / 3 / 3 |
| seedance-2.0-standard | 4–15 秒 | 480p、720p、1080p、4k | 9 / 3 / 3 |
| seedance-2.5 | 4–30 秒 | 480p、720p | 30 / 10 / 10 |

因此新 2.5 名称不会再误套 mini 的 9 图 / 15 秒限制。原模型支持的比例、音频依赖和禁用参数也保持一致；本包不改用户素材和提示词。

创建使用 `POST /api/multimodal/create_task`，查询使用 `POST /api/multimodal/get_result`；两者保留相同完整模型名，续取旧 ID 也不去掉后缀、不重新创建任务。

建议先按服务商示例测试 4 秒、480p、16:9 的文生视频，再测试实际需要的素材和规格；实际能否生成取决于该 Key 的通道权限和服务商接受的参数。已启动批次保留原配置，请在安装后创建新批次测试。

## 回退

退出 Studio，将 `zz_gate_formoss.py` 移出 providers 文件夹，恢复之前备份的 Gate 外挂（如有），重启即可。本包不修改 config.json、账号授权或项目文件；源码内置 Gate 已同步加入相同映射，后续重新打包时可并入 EXE。
