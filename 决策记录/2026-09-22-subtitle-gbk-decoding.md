# 字幕压制因 GBK 解码 FFmpeg 日志失败

日期：2026-09-22
起因：用户的独立字幕工具在“正在生成带字幕视频”后报告 `_readerthread` 的 GBK `UnicodeDecodeError`，接着报告 `expected string or bytes-like object, got 'NoneType'`。

## 定性与范围

VideoCaptioner 的 `ass_renderer._get_video_resolution` 使用 `subprocess.run(text=True)`，未指定编码，随后对 `result.stderr` 做正则匹配。在中文 Windows 默认 GBK 环境中，FFmpeg 的 UTF-8 文件名/元数据可能无法解码；Windows 管道读取线程失败后 stderr 为 None，正则再报第二个错误。

本程序的 `probe.run_text` 和主程序 stdout 已经指定 UTF-8，但这不会覆盖依赖内部新建的管道。此前短文件名样片验收通过，未强制 GBK 默认编码，也未验证容易触发解码错误的视频元数据，因此漏掉。

依赖中的 ASS 压制、圆角字幕渲染和其他视频工具调用存在同类文本管道。独立工具 `subtitle_app/main.py` 和兼容 Studio 的 `run.py` 都有 caption 子命令入口。

## 实施

在共享的 `core/captions.py` 提供仅包住 caption CLI 执行期间的子进程文本编码适配：未声明编码时使用 UTF-8，未声明错误策略时使用 replace。保留二进制管道、显式编码/错误策略、进程退出码；退出 CLI 时恢复原 Popen。两个 CLI 入口均接入，不在 Studio 服务线程中启用，也不修改已安装的第三方源码。

这样 UTF-8 日志可正常读取，其他无法解码的日志字节只显示替代字符，不会把 stderr 变成 None。真实 FFmpeg 错误仍按非零退出码报告。

## 验证与交付

回归测试强制默认 GBK，覆盖中文日志和异常字节、run/流式 Popen、二进制输出、显式 GBK、非零退出码与异常退出后的恢复；另生成含中文文件名与元数据的真实视频，确认其 FFmpeg 日志无法按 GBK 解码，并验证依赖的真实分辨率探测正常。

- 全量测试 2658 项通过（退出码 0），日志：`outputs/subtitle-encoding-tests.log`。
- 独立 EXE 完整重新构建成功；校验内嵌入口和共享字幕模块与最终源码一致。
- 成品 EXE 对中文文件名、GBK 不兼容元数据的样片完成硬字幕和软字幕合成；硬字幕画面已查看，软字幕轨已验证，真实缺失文件仍返回失败。
- 成品验收未调用识别或付费接口；报告：`outputs/subtitle-encoding-qa/20260922-153749/results.json`。
- 交付 `dist/Respect-Subtitles-20260922-UTF8-Fix.zip`。用户可指定失败那次已经生成的字幕跳过识别，原片与原字幕不覆盖。

下次遇到相同中文路径或视频元数据时，日志可正常读取、分辨率可正常解析；无法解码的日志字节显示替代字符，不再引发读取线程崩溃和随后的 NoneType 错误。此次未取得用户原片，使用相同文件名和可复现编码冲突的本地样片验证。
