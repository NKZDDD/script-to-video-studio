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
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from core import paths  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="剧本→AI视频 自动化生产台")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770)
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

    srv = serve(args.host, args.port)
    url = f"http://{args.host}:{args.port}/"
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
