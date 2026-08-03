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

from server.app import serve  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="剧本→AI视频 自动化生产台")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

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
