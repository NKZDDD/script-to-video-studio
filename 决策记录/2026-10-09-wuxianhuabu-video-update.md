# 无限画布视频模型更新与旧 EXE 外挂

日期：2026-10-09
需求：用户提供 https://videogogo.top/models，要求增加无限画布视频，先交付外挂。

## 定性

已有无限画布适配器，但内置模型来自 2026-09-17（6 个型号），默认型号已不在当前公开目录。仅修改代码列表时，旧 EXE 的 model-snapshot 还会覆盖候选，造成装了外挂仍看不到新模型。当前 capability_schema 使用 max_images/max_videos/max_audios，通用目录读取原先只认 max_reference_*，会漏掉 0 或较小限制。

## 官方证据

- 官网模型页脚本调用 GET /api/model-plaza/v1/models；2026-10-09 返回 13 个视频模型和 1 个图片模型。本次只处理视频。
- 用户随后提供视频分组 Key，只读 GET https://videogogo.top/api/v1/models 返回 HTTP 200，13 个视频模型的 ID 和 capability_schema 与公开目录完全一致。
- 官网脚本内的 API 文档：POST /api/v1/assets 上传二进制素材；POST /api/v1/videos 创建；GET /api/v1/videos/{id} 查询；完成后公开 download_url 下载。三类参考为 URL 或 asset_id 字符串数组；网关层不限制数量，逐模型明确的限制另行处理。
- 请求没有创建付费任务。Key 只用于当次只读请求，没有写进源码、模型记录或发行包。
- 去除凭据后的模型证据：tests/fixtures/wuxianhuabu-20261009.json。

## 实现

- 内置 13 个最新视频型号及对应参数；默认 sd-2.5-ss-720p。旧型号已知约束仍保留给兼容调用，已有查询/结果下载流程继续使用。
- Dola 仅 5/10/30 秒，video-sd25-30s 仅 10/30 秒；其他声明连续时长的型号按实际范围提供，报错不再把离散档位写成连续范围。
- 逐模型处理图片/视频/音频数量，不偷偷截断；明确为 0 的类型在提交前报错；未公布数量不额外设上限。
- model_options 包含音频上限与比例/清晰度/时长；catalog_keep_models 防止旧包快照隐藏新候选。用户填写 Key 后刷新取得当前目录。
- 通用目录解析补齐三个短数量字段；服务商新声明的有限数量覆盖原先“未公布上限”标志，H3 等显式本地不限制策略保留优先级。
- 外挂与主程序同 ID、代码字节一致，绝对导入可在旧 EXE 的插件环境直接加载；不需要主程序新增模块。用户设置页导入 .py 或复制到数据目录/providers 后重启即可。
- 本次不重新打 EXE，只出外挂 ZIP；主程序源码同步更新，后续打包包含本次改动。

## 验证

- 初始专项 57 passed，1.40 秒；随后增加一项新目录数量覆盖的回归，纳入全量。
- 已在实际 Respect短剧制作平台-20261009-r2.exe 的隔离数据目录加载外挂并访问 bootstrap，确认 13 个新模型全部可见、默认型号正确，Dola、DL、SS 和 video-sd25 的参数正确。没有改动真实项目。报告 outputs/wxhb-plugin-exe-20261009.json。
- 全量回归 3190 passed，282.90 秒，日志 outputs/wxhb-full-20261009.log。
- ZIP 解压校验与内部文件 SHA256 校验通过；ZIP 内服务商文件和主程序源码字节一致。

## 交付与效果

dist/Respect-Studio-Wuxianhuabu-Video-20261009.zip，包含 providers/zz_wuxianhuabu_latest.py、README.md、models.json、manifest.json。

ZIP 为 10,390 字节；SHA256：9b6be3c521e2bd86ec8b274d339d79cb17d8bcf6bfaccfbb3fed9220d4bb8060。包内凭据扫描通过。

此前旧模型表和快照会隐藏新视频型号，且不同型号的参数不能统一套用；现在旧 EXE 导入外挂后即可选择新型号及各自参数，不必为新增候选重新分发 EXE。鉴权和目录读取已验证，未进行付费视频生成。
