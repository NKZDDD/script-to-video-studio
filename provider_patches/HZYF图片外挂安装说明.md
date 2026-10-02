# HZYF hzyfai.com 图片服务商外挂

版本：20261002.1。支持 Studio 的 providers 外挂机制；不含 API Key，无需安装 Python。

## 安装

1. 等当前生产结束，退出 Studio。
2. 在设置或启动窗口确认实际数据目录，将 `providers/zz_hzyfai_image.py` 放到该数据目录的 `providers` 文件夹。不是项目素材文件夹。
3. 重启 Studio，在服务商设置中选择 **HZYF hzyfai.com**，填写自己的 API Key 并保存。
4. 基址可填 `https://hzyfai.com` 或 `https://hzyfai.com/v1`，不会重复拼接 v1。
5. 在图片生产行选择 HZYF 和图片模型，重新查看生产计划后开始。

例如数据目录为 `D:\stv-data`，文件路径应为 `D:\stv-data\providers\zz_hzyfai_image.py`。
若服务商未出现，检查实际数据目录、.py 后缀和设置页的外挂加载错误。

## 模型与参数

- `gpt-image-2`
- `gpt-image-2.5`
- `gpt-image-2.5-flare`
- `gpt-image-2.5-sunburst`

本次接入图片能力，不声明视频或聊天能力。
尺寸选项：`1024x1024`（方图）、`1536x1024`（横图）、`1024x1536`（竖图）。
使用像素尺寸传给 size；尚未验证独立 1K/2K/4K 参数，不把档位转换成猜测的请求参数。
旧任务若带有 resolution 参数，请在任务参数中去掉该字段，并清空生产行的清晰度覆盖，再选像素尺寸。

文生图：POST `/v1/images/generations`，JSON。
图生图：POST `/v1/images/edits`，multipart 重复 `image[]` 字段。
本地图片直接上传，不需要对象存储；上传前检查完整性，以实际图片内容决定 MIME。
不在本地按 9 张截断或拦截，服务端的实际素材限制仍以响应为准。
支持同步返回 URL 和 Base64，结果经下载与文件检查后才标记完成。

## 错误与兼容边界

- `insufficient_user_quota` / HTTP 403：所属账户余额不足。
- `system_cpu_overloaded` / HTTP 503：服务商网关负载过高，稍后再试。
- 当前实测图片请求同步返回结果；request_id 是追踪编号，不当作可轮询 task ID。未验证异步任务查询接口，不承诺按 request_id 恢复历史任务。
- 只扩展 HZYF，不修改其他服务商、全局任务管理或用户密钥。
- 主程序源码已并入；旧 EXE 可先使用外挂。换用包含 HZYF 的新版 EXE 后，可移走本外挂以使用内置版本。

撤销：退出 Studio 后将 zz_hzyfai_image.py 移出 providers 并重启，保留项目和配置。

## 本次验证

2026-10-02 使用用户授权的测试 Key 实测，所有结果均完整下载并解码：

| 模型 | 测试 | 实际输出 |
| --- | --- | --- |
| gpt-image-2 | 文生图 / 双参考图编辑 | 1024x1024 / 1536x1024 PNG |
| gpt-image-2.5 | 通过 Studio 服务商代码文生图 | 1024x1536 PNG |
| gpt-image-2.5-flare | 通过 Studio 服务商代码文生图 | 1024x1024 PNG |
| gpt-image-2.5-sunburst | 通过 Studio 服务商代码文生图 | 1024x1024 PNG |

全量回归 3009 项通过；2026-09-30 旧版 EXE 在隔离数据目录成功加载此外挂及 4 个模型。
确认的是上述 OpenAI 兼容图片接口，不代表已验证所有 OpenAI 协议端点或所有模型参数组合。
