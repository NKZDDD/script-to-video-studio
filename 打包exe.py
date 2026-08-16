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
import io
import os
import shutil
import subprocess
import sys

# Windows 的非 UTF-8 控制台无法直接输出自检使用的 ✓/✗，会在真正打包前
# 抛 UnicodeEncodeError。固定脚本自身的输出编码，避免依赖调用者先设置环境变量。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "script-to-video-studio"        # exe 文件名，改成中文也行，但英文最省事

# 分体系打包：`--system v34` / `--system v61`。
#
# 两套体系的代码和模板**始终都打进去** —— 限制的只是「新建项目时能选哪套」。
# 真裁掉另一套的话，拿错包打开老项目会把产物全判成「还没做」，重跑花第二份钱；
# 而且引擎层是共用的，裁不干净。包大小也省不下多少：大头是 Python 运行时。
FLAVORS = {
    "v34": ("script-to-video-studio-电影级十七章", "电影级十七章"),
    "v61": ("script-to-video-studio-通用十二环节", "通用十二环节"),
}


def _flavor() -> tuple:
    """(体系 id, exe 名, 显示名)。没给 --system 就是全体系包。"""
    if "--system" in sys.argv:
        i = sys.argv.index("--system")
        sid = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
        if sid not in FLAVORS:
            raise SystemExit(f"--system 只能是 {' / '.join(FLAVORS)}，给的是 {sid!r}")
        return (sid,) + FLAVORS[sid]
    return "", NAME, "全体系"


def _stamp(sid: str) -> None:
    """把体系写进 core/build_info.py。

    打包前改、打完**必须改回去** —— 留着的话源码方式跑也被限死，
    而那是最容易忽略的一种「我明明没改配置怎么少了一套」。
    """
    p = os.path.join(HERE, "core", "build_info.py")
    with io.open(p, encoding="utf-8") as f:
        src = f.read()
    import re
    src = re.sub(r'^SYSTEM = ".*?"$', f'SYSTEM = "{sid}"', src, flags=re.M)
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)

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


def selfcheck(exe: str) -> bool:
    """真把 exe 跑起来，比对「源码方式有什么」和「exe 里有什么」。

    为什么非做不可：打包会漏东西，而漏了**不报错**。运行时才 import 的模块
    （服务商）、当资源读的文件（web/、prompts/）—— 少了就是空下拉框、空模板列表，
    源码方式跑一辈子也复现不出来。人工验的话，只会在换机器用的那天才发现。

    用临时数据目录和随机端口，不碰本机配置。
    """
    import json
    import socket
    import tempfile
    import time
    import urllib.request

    print("\n自检：把 exe 跑起来，对比源码方式认得的东西 —— 打包漏了不会报错，只会缺")
    sys.path.insert(0, HERE)
    from core import providers as P                       # noqa: PLC0415
    want_prov = sorted(p["id"] for p in P.status()["providers"])
    want_tpl = sorted(os.path.splitext(f)[0] for f in os.listdir(os.path.join(HERE, "prompts"))
                      if f.endswith(".md") and not f.endswith("_adapter.md"))

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    data = tempfile.mkdtemp(prefix="stv-selfcheck-")
    # 单独开一个进程组：这样后面能像用户按 Ctrl+C 那样让它自己收尾
    # （单文件 exe 被强杀的话，引导程序来不及删 %TEMP%\_MEI*，会一直攒着）
    proc = subprocess.Popen([exe, "--data", data, "--port", str(port), "--no-browser"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            encoding="utf-8", errors="replace",
                            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                                           if os.name == "nt" else 0))

    def get(path):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.read().decode("utf-8")

    ok = True
    try:
        deadline = time.time() + 90
        while True:
            if proc.poll() is not None:
                print(f"  ✗ exe 起不来，退出码 {proc.returncode}")
                print((proc.stdout.read() or "")[-2000:])
                return False
            try:
                got_prov = sorted(p["id"] for p in json.loads(get("/api/providers/status"))["providers"])
                break
            except Exception:                            # noqa: BLE001, PERF203
                if time.time() > deadline:
                    print("  ✗ 90 秒还没起来（单文件首次启动要解压，但不该这么久）")
                    return False
                time.sleep(1)

        # 1. 服务商：运行时动态 import 的，最容易被打包漏掉
        miss = [x for x in want_prov if x not in got_prov]
        print(f"  {'✓' if not miss else '✗'} 服务商 {len(got_prov)}/{len(want_prov)} 家"
              + (f"　缺：{', '.join(miss)}" if miss else ""))
        if miss:
            print("     → 检查 --collect-submodules core.providers，"
                  "以及新加的内置有没有写进 _BUILTIN_ORDER")
            ok = False

        st = json.loads(get("/api/providers/status"))
        for w in st.get("warnings", []):
            print(f"  ⚠ {w.get('id')}：{'；'.join(w.get('problems', []))}")
        for e in st.get("errors", []):
            print(f"  ✗ {e.get('file')} 加载失败")
            ok = False

        # 2. 提示词模板：当资源打进去的，--add-data 漏了就是空列表
        got_tpl = sorted(t["name"] for t in json.loads(get("/api/prompts"))["items"])
        miss = [x for x in want_tpl if x not in got_tpl]
        print(f"  {'✓' if not miss else '✗'} 提示词模板 {len(got_tpl)}/{len(want_tpl)} 份"
              + (f"　缺：{', '.join(miss)}" if miss else ""))
        ok = ok and not miss

        # 3. 页面：同上
        html = get("/")
        print(f"  {'✓' if len(html) > 10000 else '✗'} 页面 {len(html):,} 字符")
        ok = ok and len(html) > 10000
    finally:
        _stop(proc)
        shutil.rmtree(data, ignore_errors=True)
    return ok


def _stop(proc) -> None:
    """把自检起的 exe 停干净。

    两个坑叠在一起：
      · 单文件 exe 是「引导程序 + 真正的子进程」两个进程，只 terminate 引导程序，
        子进程会变孤儿 —— 端口占着、进程列表里赖着
      · 强杀则轮到引导程序来不及删 %TEMP%\\_MEI*，每打一次包攒一个几百 MB 的目录

    所以先按 Ctrl+C 让它自己收尾（run.py 接住 KeyboardInterrupt 会 shutdown），
    不听话再连整棵进程树一起杀。
    """
    import signal
    import time

    if os.name == "nt":
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            for _ in range(30):
                if proc.poll() is not None:
                    return
                time.sleep(0.3)
        except Exception:                                # noqa: BLE001
            pass
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> int:
    onedir = "--onedir" in sys.argv
    sid, name, flavor = _flavor()
    _stamp(sid)                       # 打包前写标记；finally 里会改回去
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

    # **只清 build/，不清整个 dist/。**
    # 分体系打包是接连打两次的，把 dist/ 整个删掉会让先打的那个消失 ——
    # 而且不报错，你会以为「怎么只出来一个包」。只删这一次的目标产物。
    shutil.rmtree(os.path.join(HERE, "build"), ignore_errors=True)
    for stale in (os.path.join(HERE, "dist", name),
                  os.path.join(HERE, "dist", name + ".exe")):
        if os.path.isdir(stale):
            shutil.rmtree(stale, ignore_errors=True)
        elif os.path.isfile(stale):
            try:
                os.remove(stale)
            except OSError as exc:      # 多半是这个 exe 正开着
                print(f"  ✗ 删不掉旧的 {os.path.basename(stale)}：{exc}")
                print("     先把它关掉再打 —— 否则打出来的还是旧那个。")
                return 1

    sep = ";" if os.name == "nt" else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir" if onedir else "--onefile",
        # 保留控制台：启动时打的四行路径、跑批时的进度和报错都在那儿。
        # 关掉控制台等于把出错原因藏起来。
        "--console",
        "--name", name,
        # 运行时读的资源，必须打进去
        "--add-data", f"{os.path.join(HERE, 'web')}{sep}web",
        "--add-data", f"{os.path.join(HERE, 'prompts')}{sep}prompts",
    ]
    for h in HIDDEN:
        if importlib.util.find_spec(h):
            cmd += ["--hidden-import", h]
    # 服务商是运行时按目录扫出来 import 的，没有任何一处静态 import ——
    # PyInstaller 的静态分析看不见它们，不点名就一个都不打进去。
    # 后果不是报错，是 exe 里「一家服务商都没有」，页面下拉框空白。
    cmd += ["--collect-submodules", "core.providers"]
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

    exe = os.path.join(out, name + (".exe" if os.name == "nt" else ""))
    if onedir:
        exe = os.path.join(out, name, name + (".exe" if os.name == "nt" else ""))
    size = os.path.getsize(exe) / 1024 / 1024 if os.path.isfile(exe) else 0

    if not selfcheck(exe):
        print("\n打出来的 exe 没通过自检，别发出去。上面写了缺什么。")
        return 2

    print(f"\n完成 → {exe}　({size:.0f} MB)")
    print(f"       dist/ 整个文件夹拷走就能用（手册也在里面）")
    print()
    print("目标机器上怎么跑：")
    print(f"  双击 {name}.exe，或在命令行里：")
    print(f"    {name}.exe                      配置和产物放 exe 旁边（绿色版）")
    print(f"    {name}.exe --data D:\\stv-data   配置和产物放指定目录")
    print(f"    {name}.exe --port 8888          换端口")
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
    try:
        code = main()
    finally:
        # **必须还原。** 留着 v34/v61 标记的话，之后源码方式跑也被限死 ——
        # 而那是最容易忽略的一种「我明明没改配置，怎么少了一套体系」。
        _stamp("")
    sys.exit(code)
