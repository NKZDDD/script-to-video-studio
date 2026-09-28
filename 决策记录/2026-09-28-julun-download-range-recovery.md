# 巨轮视频整文件下载断流，Range 分段成功取回

日期：2026-09-28。用户要求测试截图中任务结果是否正常拉取，未要求本轮修改生产程序。

## 任务识别

用户文字 ID `task_nMx6YRcvrK3aaLApXscajekg1RmJGTVs` 的 x 为小写，查询返回 task_not_exist。核对截图中大写 X 后，`task_nMX6YRcvrK3aaLApXscajekg1RmJGTVs` 查询成功。

只对这一已有任务进行 GET 查询与文件下载，没有提交生成任务、放弃旧任务或触发重复生成。

## 查询与复现

- HTTP 200，模型 `sd2.5`，状态 `completed`，progress 95。状态识别正常，不需要等待进度到 100。
- 多个结果字段均指向 `http://199.30.89.126` 上同一个视频下载入口。
- 原始下载地址返回 video/mp4、Content-Length 20,611,006；普通流式下载收到 196,608 字节后出现 ChunkedEncodingError / IncompleteRead，剩余 20,414,398 字节。
- 截图显示收到 327,680 字节、尚缺 20,283,326 字节；两者对应相同的总大小 20,611,006，成功复现同类断流。
- r2_ready=false，r2_backup_url 下载返回 HTTP 404；因此本次没有可用的 R2 备份。
- 鉴权下载 `/v1/videos/{task_id}/content` 同样返回 HTTP 200 和相同大小，收到 327,680 字节后断流。

## 成功取回的方式

对原始结果地址分别测试 Range 0–65535 与 1048576–1114111，均得到 HTTP 206、正确 Content-Range 和完整 65,536 字节。

随后以 64 KiB 分段、4 路并发，完成 315 个区间，逐段核对 HTTP 206、Content-Range 和字节数，按偏移拼接；未向结果存储主机发送用户 API Key。

- 最终大小：20,611,006 字节，与服务器声明一致。
- 媒体时长：00:00:30.08。
- 完整音视频解码：FFmpeg -xerror，退出码 0，错误输出为空。
- SHA256：`c430c8882d9b0977d639cb03f728f3e607d9c1c436fbf2ec7104e02f5c9a99ec`。
- 下载及验证耗时约 47.72 秒。
- 取回文件：`outputs/julun-update-20260928/task_nMX6YRcvrK3aaLApXscajekg1RmJGTVs-recovered.mp4`。

## 机制与后续

服务商的结果下载链路在整文件长传输时中断，小段 Range 请求可成功。`core/apiutil.py` 当前 save_item 只会从头重试同一链接，`core/providers/julun.py` 选择结果地址后也没有 Range 取回逻辑，所以即便识别完成状态正确，仍会连续下载失败。保留“已提交待取回”并停止自动重新生成的行为正确，避免重复计费。

本轮只测试并取回这条视频，没有修改 Studio 或外挂。后续如实施修复，应补充有完整性校验的续传/分段下载（严格验证 Content-Range、总大小、文件版本一致性，失败不发布半截文件），并只在备份实际可用时切换地址。不能用重新提交生成来处理下载断流。

查询响应、诊断脚本及视频均保存在 outputs 下，未纳入版本控制；本文不保存 Key、提示词或完整视频下载地址。
