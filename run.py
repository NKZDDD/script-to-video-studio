# -*- coding: utf-8 -*-
"""启动本地生产台：python run.py  →  浏览器打开 http://127.0.0.1:8770"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    # line_buffering：输出被重定向到文件时（打包成 exe 后常见，比如用
    # Start-Process 转存日志）Python 默认按块缓冲，进程被强杀就什么都没写进去，
    # 连启动横幅都看不到。逐行刷掉，慢一点但出事时有据可查。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from core import paths  # noqa: E402


def _caption(argv: list) -> int:
    """把参数原样交给 videocaptioner 的 CLI。

    为什么做成直通子命令，而不是让人另装一个 videocaptioner：
    打进这个 exe 之后目标机器上没有 Python，`videocaptioner` 那个命令不存在，
    只有从我们这里进得去。所以是 `<本程序>.exe caption transcribe a.mp4`。

    **动手前先把 ffmpeg 喂给它。** 它靠 PATH 找 ffmpeg / ffprobe，而我们的
    ffmpeg 是 imageio-ffmpeg 带进来的、埋在临时解压目录里，PATH 上没有 ——
    不喂的话它一上来就报「ffmpeg not found」，而机器上明明有一个。
    """
    # **这条路上的提示一律打 stderr。**
    # 打 stdout 会污染机器读的输出：`caption --version` 是被 core/subtitle
    # 抓 stdout 解析版本号的，我那两句 ffmpeg 提示混进去之后，版本号变成
    # 「⚠ 有 ffmpeg 没有 ffprobe…」那一句混进去之后，版本号变成了
    # 警告 + 版本号两行，页面上照原样显示，看着像程序疯了
    # （实遇 2026-08-27 的自检输出）。
    # 页面上照原样显示，看着像程序疯了（实遇 2026-08-27 自检输出）。
    def _note(*a):
        print(*a, file=sys.stderr)

    # ffmpeg 按标准名摆到 PATH 上（一处实现，见 core/captions）
    from core import captions                             # noqa: PLC0415
    ff, fp = captions.ensure_ffmpeg(_note)
    if ff and not fp:
        _note("  ⚠ 有 ffmpeg 没有 ffprobe（imageio-ffmpeg 只带前者）——"
              "配音那几步会失败，转写和压制不受影响。")
        _note("    要用配音就装一份完整的 ffmpeg，或者把 ffprobe.exe 放到 exe 旁边。")
    # 自带的四份字幕样式装进它的样式目录 —— 它只认 json，而我们带的是 ASS txt，
    # 所以要转一次（见 core/captions）。同名的不覆盖。
    from core import captions                             # noqa: PLC0415
    captions.install(_note)
    try:
        from videocaptioner.cli.main import main as vc_main   # noqa: PLC0415
    except Exception as exc:                                  # noqa: BLE001
        _note(f"  ✗ 这一版里没有 videocaptioner：{exc}")
        _note("    源码方式跑：pip install --ignore-requires-python videocaptioner "
              "audioop-lts")
        return 2
    # 它按 sys.argv 解析，所以这里替它铺好 —— 直接传参数它不认
    sys.argv = ["videocaptioner"] + list(argv)
    try:
        return int(vc_main() or 0)
    except SystemExit as e:            # argparse 的正常退出路径
        return int(e.code or 0)


def main() -> int:
    # `caption` 走直通，**在 argparse 之前拦掉** —— 后面的参数是它的，
    # 交给我们的 parser 只会被判成「不认识的参数」。
    if len(sys.argv) > 1 and sys.argv[1] == "caption":
        return _caption(sys.argv[2:])
    ap = argparse.ArgumentParser(description="Respect短剧制作平台")
    ap.add_argument("--host", default="127.0.0.1")
    # 默认端口按体系分开 —— 电影级 8770、通用 8771，**两个包要能同时开着**。
    # 0 = 用这一版自己的默认值（见 build_info.DEFAULT_PORTS）。
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--data", default="", metavar="目录",
                    help="数据目录（config.json 和默认 projects/ 放这儿）。"
                         "也可以用环境变量 STV_DATA_DIR。"
                         "放在程序目录之外，更新程序时就不会碰到配置和产物。")
    args = ap.parse_args()
    if args.data:
        paths.set_data_dir(args.data)

    # 先把路径打出来再起服务：配置到底读的哪一份、产物写到哪儿，
    # 换机器时这两行比什么文档都管用
    from server.app import load_config, serve            # noqa: PLC0415
    p = paths.snapshot()
    cfg = load_config()
    print()
    print(f"  程序目录  {p['program_dir']}")
    print(f"  数据目录  {p['data_dir']}   （{p['source']}）")
    print(f"  配置文件  {p['config_path']}"
          f"{'' if p['config_exists'] else '   ← 还没有，保存设置时会建'}")
    print(f"  产物目录  {cfg['projects_dir']}")
    if p["config_at_risk"]:
        print()
        print("  ⚠ 配置文件在程序目录里 —— 更新程序时整个覆盖会把 key 和优先级链一起弄丢。")
        print("    在「设置 → 数据与路径」点一下「把配置搬出程序目录」，或者启动时加")
        print("    --data D:\\stv-data （原件会保留成 config.json.已搬走，不删）")

    srv, port = serve(args.host, args.port)
    # 用**真实端口**拼 URL：端口被占时会顺延，拿参数拼会打开一个没人听的地址
    url = f"http://{args.host}:{port}/"
    if args.port and port != args.port:
        print(f"\n  ⚠ {args.port} 被占了，改用 {port}")
    print(f"\n  生产台已启动 → {url}")
    print("  Ctrl+C 停止\n")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
