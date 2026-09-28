# 巨轮新时长接入 Studio，并提供旧机器外挂

日期：2026-09-28

用户要求：「按照最新的更新一下巨轮在studio内但是也需要单独出个服务商配置我额外用于其他机器外挂」。

## 定性及范围

`core/providers/julun.py` 的 SPEC 保留了旧时长：`SD 2.0-933` 为 4–15，`sd2.5` 固定 30。`fit_duration` 按该表修正实际提交时长。而 `/v1/models` 只返回模型 ID，设置页拉取名字不更新这些规则，所以旧规则能长期保留。Agent UI/生产计划读取 `model_options`，此前巨轮只提供 specs 和全局 4–30 候选，提交规则与页面并非同一来源。

范围为巨轮视频规格、对应能力选项、独立服务商外挂；不改密钥、批量程序、其他服务商、项目材料或正在运行的任务。不重打完整 EXE。

## 依据与取舍

2026-09-28 只读查询 `https://julun.cc/v1/models`、`https://julun.cc/api/pricing`、`https://julun.cc/api/status`。模型清单 37 个 ID，pricing 23 条，status 版本 20260927-localfirst。未创建付费任务。

- pricing 明确 SD 2.0-933、sd2.5、SD 2.5、sd2.5_30、seedance-2.5-720p、pro-480、pro-720 支持 4–30 秒，deal-720 支持 5–30 秒。
- 无后缀 deal 描述称 30 秒，结构化 video_api 为 4–25 秒、480p。采用结构化值并在能力备注、安装说明中标明冲突。
- 没有新请求协议文档，不凭同族名字更换已有格式。新型号保留原本未知模型使用的 url_media 形状；未知数量用 None，避免凭空指定禁用或上限。
- seedance_v2.x 再次出现在清单，移出退役名单但不假设与空格名称完全一致。
- sd2.5 的参考视频/音频新字段缺少正式依据，仍只提供现有 image_refs 实现；HM 旧媒体/face 协议不在此次重写范围。边界已写入安装说明。

## 实现与避免的问题

1. 更新规格并从 SPEC 生成 model_options，页面、生产计划与实际请求时长一致；30 秒不再被旧的 933 上限改成 15，sd2.5 的 20 秒也不再被强制改成 30。
2. 已知清晰度不匹配时在提交前提示去修改视频清晰度，避免 deal 继续带旧 720p 排队后失败。
3. 同一份适配器使用绝对导入，任务恢复模块可选，发布为 `provider_patches/zz_julun_latest.py`。插件 ID 仍为 julun，沿用已配置 Key，仅覆盖巨轮；包含旧结果查询修复，不依赖旧 EXE 自带的过期规格。
4. 明确卸载旧巨轮外挂及手填时长覆盖的处理方法，避免安装后仍被旧插件/旧手填规则覆盖。

## 验证

测试覆盖实际生成请求中 20/30 秒与全部参考图保留、模型拉取合并后保留新选项、deal 冲突规则、旧版缺模块加载、插件只替换巨轮及卸载恢复。所有网络请求均模拟，不花费生成额度。

- 巨轮专项：112 passed in 5.09s。
- 全量：`py -m pytest -o addopts='' -q -p no:cacheprovider --basetemp=outputs/julun-update-20260928/test-full-1`，2786 passed in 343.37s。最初沙箱执行遇到 pytest 临时目录 WinError 5，改用正常 Windows 权限后全部通过。
- `git diff --check` 通过；ZIP 完整性及其中插件与源码字节一致性校验通过。
- 交付：`outputs/julun-update-20260928/Respect-Studio-Julun-20260928.1.zip`，16,537 字节，包含适配器与安装说明，不含 Key/config/项目材料。
- ZIP SHA-256：`6603ee6839d45e9217b320633d9c31c65c459d21249f3cbadc82f30fb4b53645`。

## 20260928.2：按用户要求将无后缀 deal 放开至 30 秒

用户明确要求：「seedance-2.5-deal 先开放到30秒」。这覆盖上一版优先采用结构化 25 秒上限的取舍。

原因：上一版 SPEC 的 `(4, 25)` 同时生成页面选项并用于 `fit_duration`，因此提交 30 秒会被压成 25 秒。现改为 `(4, 30)`，页面和实际请求同步；最低 4 秒、480p、WAV 及其他媒体参数保持不变。能力备注和说明保留官方描述与结构化数据冲突的事实，明确此次由用户要求放行。

内置及独立外挂版本同时升为 20260928.2。测试验证 deal 的 20/30 秒、全部参考图、结果取回、模型拉取后选项保留，并断言 30 秒不触发时长修正日志。巨轮专项 114 passed in 1.69s。

全量结果：2787 passed、1 failed in 262.80s。唯一失败为 `test_agent_handoff.py::test_revision_not_newer_than_active_is_refused_before_extracting`，`staging.replace(target)` 遇到 Windows WinError 5 拒绝访问，并非时长断言失败。该用例单独复测 1 passed in 1.10s；未为此修改交接包代码。`git diff --check` 通过。

新包 `outputs/julun-update-20260928/Respect-Studio-Julun-20260928.2.zip`，16,576 字节；完整性及插件源码一致性通过。SHA-256：`9920324accb0cf2df891cbfc807f4278edd71fe6eff25ec4c5a1f7a7189d4f01`。不包含密钥，未创建付费生成任务。
