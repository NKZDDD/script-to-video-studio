# -*- coding: utf-8 -*-
"""打包成不用装 Python 就能跑的 exe。

    python 打包exe.py            # 单文件（一个 exe 拿了就走，启动慢几秒）
    python 打包exe.py --onedir   # 一个文件夹（启动快，杀软误报少）

产物在 dist/ 下，连同使用手册一起，整个文件夹拷到别的机器就能用。

要点：
  · web/ 和 prompts/ 必须打进去 —— 它们是运行时读的资源，不是代码
  · 可选依赖（boto3/Pillow/pypdf/imageio-ffmpeg）装了才打得进去。
    缺哪个，exe 就缺对应能力（见下面的清单）
  · 打包机器的位数决定 exe 的位数；64 位机上打的 exe 不能在 32 位机上跑
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "script-to-video-studio"        # exe 文件名，改成中文也行，但英文最省事

# 可选依赖 → 缺了会少什么能力。打包时逐个检查并如实报告，
# 别让人拿到一个「拼不了片」的 exe 还不知道为什么。
OPTIONAL = {
    "boto3": "参考图上传到对象存储（只收公网链接的模型会用不了）",
    "PIL": "参考图压缩（不压直接转 base64，请求体会很大）",
    "pypdf": "读 PDF 剧本（docx / txt 不受影响）",
    "imageio_ffmpeg": "环节12 拼接成片（目标机装了系统 ffmpeg 也行）",
}
# 这些包 PyInstaller 有时扫不出来（运行时才 import 的），显式点名
HIDDEN = ["boto3", "botocore", "PIL", "pypdf", "imageio_ffmpeg"]


def main() -> int:
    onedir = "--onedir" in sys.argv
    if importlib.util.find_spec("PyInstaller") is None:
        print("没装 PyInstaller。先跑：pip install pyinstaller")
        return 1

    print(f"Python {sys.version.split()[0]} "
          f"{'64 位' if sys.maxsize > 2 ** 32 else '32 位'}"
          f"　→ 打出来的 exe 也是这个位数\n")

    have, miss = [], []
    for mod, what in OPTIONAL.items():
        (have if importlib.util.find_spec(mod) else miss).append((mod, what))
    print("可选依赖：")
    for mod, what in have:
        print(f"  ✓ {mod:<16}{what}")
    for mod, what in miss:
        print(f"  ✗ {mod:<16}{what}　← exe 里会缺这个能力")
    if miss:
        print(f"\n  想补齐：pip install {' '.join(m for m, _ in miss)}")
        if input("  现在就这样打包？(y/N) ").strip().lower() != "y":
            return 1
    print()

    for d in ("build", "dist"):
        shutil.rmtree(os.path.join(HERE, d), ignore_errors=True)

    sep = ";" if os.name == "nt" else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir" if onedir else "--onefile",
        # 保留控制台：启动时打的四行路径、跑批时的进度和报错都在那儿。
        # 关掉控制台等于把出错原因藏起来。
        "--console",
        "--name", NAME,
        # 运行时读的资源，必须打进去
        "--add-data", f"{os.path.join(HERE, 'web')}{sep}web",
        "--add-data", f"{os.path.join(HERE, 'prompts')}{sep}prompts",
    ]
    for h in HIDDEN:
        if importlib.util.find_spec(h):
            cmd += ["--hidden-import", h]
    cmd.append(os.path.join(HERE, "run.py"))

    print("执行：\n  " + " ".join(cmd) + "\n")
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode:
        print("\n打包失败，看上面的 PyInstaller 输出")
        return r.returncode

    # 把手册一起放进 dist，拿到的人不用回来找文档
    out = os.path.join(HERE, "dist")
    for doc in ("使用手册.md", "FRAMEWORK.md"):
        src = os.path.join(HERE, doc)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(out, doc))

    exe = os.path.join(out, NAME + (".exe" if os.name == "nt" else ""))
    if onedir:
        exe = os.path.join(out, NAME, NAME + (".exe" if os.name == "nt" else ""))
    size = os.path.getsize(exe) / 1024 / 1024 if os.path.isfile(exe) else 0
    print(f"\n完成 → {exe}　({size:.0f} MB)")
    print(f"       dist/ 整个文件夹拷走就能用（手册也在里面）")
    print()
    print("目标机器上怎么跑：")
    print(f"  双击 {NAME}.exe，或在命令行里：")
    print(f"    {NAME}.exe                      配置和产物放 exe 旁边（绿色版）")
    print(f"    {NAME}.exe --data D:\\stv-data   配置和产物放指定目录")
    print(f"    {NAME}.exe --port 8888          换端口")
    print()
    print("注意：")
    print("  · 第一次启动杀软可能拦一下（PyInstaller 打的包常被误报），加信任即可")
    if not onedir:
        print("  · 单文件模式每次启动要解压，慢几秒；嫌慢用 --onedir 重打")
        print("  · 正常 Ctrl+C 退出会自己清临时解压目录；被强杀（任务管理器结束进程）")
        print("    则会在 %TEMP% 留下 _MEI* 文件夹，偶尔手动清一下")
    print("  · 目标机器不用装 Python，但拼接成片要么靠打包进去的 imageio-ffmpeg，"
          "要么目标机自己有 ffmpeg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
