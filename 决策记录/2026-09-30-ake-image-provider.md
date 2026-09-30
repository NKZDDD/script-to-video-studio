# 阿珂图片加入 Studio，先交付外挂

日期：2026-09-30
起因：用户要求“把阿珂图片加入studio 然后先出一个外挂”。

## 定性与范围

原 `core/providers/ake.py` 只声明 video 且只实现 generate_video，因此图片生产行不能选阿珂。
附件 `D:/新建 文本文档.md` 是视频协议，wan3.0-image 也是图生视频，不能当成生图接口。
进一步读取官网 `/api/status` 给出的文档入口，发现独立的图片文档：

- https://snumom.com/docs/gpt-image-2.html
- https://snumom.com/docs/grok-imagine-image-2.0.html

本次仅增加以上文档确认的两个图片模型，保留已有视频请求实现与参数。不打 EXE。

## 实现

- 文生图 POST /v1/images/generations；图生图 POST /v1/images/edits，multipart 的 image 文件字段。
- gpt-image-2 提供 2048x2048 与 3840x2160；Grok Imagine 2.0 提供 1024x1024。
- 图片走本机文件字节、视频仍走 URL；上传前按真实格式检查完整性并声明 MIME，避免把错误网页或截断文件上传。
- 多参考图按顺序使用重复 image 字段，不截断、不臆造文档未公布的张数上限；服务商是否支持多图需实机验证。
- 只映射文档明确的尺寸，尺寸与清晰度冲突时报明如何修改，避免静默改构图。
- 同步图片结果支持 URL 和 Base64，立即落盘；提交 HTTP 请求只尝试一次。没有文档化的图片任务查询协议，不猜异步轮询路径。
- 外挂是完整的同 ID 服务商实现，与主程序源文件一致；使用既有阿珂 Key 配置，不含凭据。

## 验证与交付

专项测试 23 passed；全量测试 2884 passed（250.79 秒）。覆盖原视频回归、图片 JSON/multipart 字段、参考图顺序与完整性、尺寸与清晰度校验、URL/Base64 结果，以及外挂加载与主程序实现一致性。

交付：`dist/Respect-Studio-Ake-Image-20260930.zip`，包含 `providers/zz_ake_image.py`、README.md、manifest.json。ZIP 完整性与内部文件 SHA256 已核验；主程序、外挂和 ZIP 内的服务商代码逐字节一致。

未进行付费生成；官网协议与模拟请求测试不能代替实际账号的模型权限、服务商多图行为与出图质量验证。

以前阿珂只有视频能力声明，没有图片请求实现。现在图片生产可选择阿珂，参考图以真实文件传入且不静默截断；尺寸不匹配或素材不可解码会在提交前明确报错，避免选了图片服务却发往视频接口或丢参考图。
