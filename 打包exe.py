# -*- coding: utf-8 -*-
"""打包成不用装 Python 就能跑的 exe。

    python 打包exe.py            # 单文件（一个 exe 拿了就走，启动慢几秒）
    python 打包exe.py --onedir   # 一个文件夹（启动快，杀软误报少）

产物在 dist/ 下，连同使用手册一起，整个文件夹拷到别的机器就能用。

要点：
  · web/ 和 prompts/ 必须打进去 —— 它们是运行时读的资源，不是代码
  · 发布依赖会完整收集子模块、数据和二进制文件；缺任一项都会停止打包
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
NAME = "Respect短剧制作平台"                  # exe 文件名 = 显示名（用户双击的就是它）
# 数据目录名**不跟着这个改** —— 那个在 core/paths.APP_NAME，是 ASCII，
# 而且留了老名字兜底：改名不能让人的配置凭空消失。

# 分体系打包：`--system v34` / `--system v61`。
#
# 两套体系的代码和模板**始终都打进去** —— 限制的只是「新建项目时能选哪套」。
# 真裁掉另一套的话，拿错包打开老项目会把产物全判成「还没做」，重跑花第二份钱；
# 而且引擎层是共用的，裁不干净。包大小也省不下多少：大头是 Python 运行时。
FLAVORS = {
    "v34": ("Respect短剧制作平台-电影级十七章", "电影级十七章"),
    "v61": ("Respect短剧制作平台-通用十二环节", "通用十二环节"),
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
# **这几个缺一个就别打。** 不是「可选增强」——缺了是功能没了，
# 而且没了不会报错，只会在用到的那一刻失败，报一句 exe 用户照不了的话。
#
# 实测踩过：一份 81MB 的包里没有 pypdf、也没有 fitz 兜底 ——
# 传 PDF 剧本直接失败，报「未安装 pypdf，可 pip install pypdf」。
# 而当时自检是绿的（它只查服务商数、模板数、页面字符数）。
REQUIRED = {
    "requests": "所有服务商的 HTTP 请求",
    "certifi": "HTTPS 根证书（否则换电脑后所有 HTTPS 接口会失败）",
    "urllib3": "HTTP 连接池和重试",
    "boto3": "参考图上传到对象存储 —— 只收公网链接的模型"
             "（HVTALD、seedance 那几条）缺了它一条都出不了",
    "botocore": "对象存储请求签名和连接配置",
    "s3transfer": "对象存储文件传输",
    "PIL": "参考图压缩 —— 缺了不报错，改成原样转 base64，"
           "请求体大几倍，更容易撞网关体积上限（踩过）",
    "pypdf": "读 PDF 剧本 —— 缺了传 PDF 直接失败，"
             "而报错写的是「pip install pypdf」，exe 用户照不了",
    "imageio_ffmpeg": "拼接成片的兜底 ffmpeg —— 目标机器装了系统 ffmpeg "
                      "也行，但不能赌它装了",
    # ---- 字幕：v34-cinematic 这条线加的 ----
    "videocaptioner": "字幕（转写 / 优化 / 翻译 / 压制）—— "
                      "从 `<本程序>.exe caption ...` 进去。本机装法："
                      "pip install --ignore-requires-python "
                      "videocaptioner audioop-lts",
    # audioop 在 Python 3.13 被从标准库删了（PEP 594），而 pydub 导它 ——
    # 这就是 videocaptioner 声明 Requires-Python <3.13 的真正原因。
    # audioop-lts 是官方 backport，装上之后 3.13 跑得通（实测）。
    # **缺了不是报缺模块，是 videocaptioner 一上来就崩在 import 上。**
    "audioop": "Python 3.13 删掉的标准库模块，pydub 要它 —— "
               "装 audioop-lts 提供（缺了 videocaptioner 直接崩）",
}

# 这几个缺了只是少一点体验，不挡功能。
OPTIONAL = {
    "psutil": "CPU / 内存占用统计和并发建议（缺了页面上显示「占用未知」）",
}

# 这些包 PyInstaller 有时扫不出来（运行时才 import 的），显式点名
HIDDEN = ["boto3", "botocore", "PIL", "pypdf", "imageio_ffmpeg", "psutil",
          # videocaptioner 那条链上运行时才 import 的几个
          "audioop", "pydub", "langdetect", "json_repair", "diskcache",
          "tenacity", "platformdirs", "GPUtil"]

# **排掉桌面 GUI 那一堆。** videocaptioner 把 pyqt5 / pyqt-fluent-widgets
# 写成了无条件依赖（它自己的 `gui` extra 是空摆设），而我们只用它的 CLI ——
# 不排的话 PyInstaller 顺着 `caption gui` 那个分支把整个 Qt 拖进来，
# 包大一倍多，而那个子命令在我们这儿压根用不上（我们是网页界面）。
# --hidden-import 只带入口模块，不保证子模块、证书、Pillow 插件或 ffmpeg.exe。
# 这些发布库必须用 --collect-all 做完整收集。
#
# 这个常量在合并 main 时**定义丢了、用法留着** —— 下面 435 行还在 for 它。
# 表现是打包脚本自检全绿、依赖全 ✓，走到拼命令那一行才 NameError。
COLLECT_ALL = ["requests", "certifi", "urllib3", "charset_normalizer",
               "boto3", "botocore", "s3transfer", "PIL",
               "pypdf", "imageio_ffmpeg",
               # 字幕这条线：videocaptioner 自带模型配置和资源文件，
               # 光 --collect-submodules 只收 .py，配置读不到就崩在运行时。
               "videocaptioner"]

EXCLUDE = ["PyQt5", "qfluentwidgets", "qframelesswindow", "PyQt5.QtWebEngine"]

# 打完之后要**在 exe 里**确认这几个模块真的能 import。
#
# 「打包机器上装了」和「进了包」是两件事：PyInstaller 扫不到的运行时
# import 会被漏掉，而漏掉不报错。所以必须让 exe 自己回答。
MUST_IMPORT = ["pypdf", "PIL.Image", "imageio_ffmpeg", "boto3", "botocore",
               # 这两个是「打包漏了不会报错、只会缺」的典型：
               # 字幕那条路平时不走，缺了要到有人点字幕才发现
               "videocaptioner.cli.main", "audioop"]


def check_page() -> bool:
    """打包**之前**先验页面里的 JS 能不能解析。

    语法错的后果不是「某个功能坏了」，是**一行 JS 都不执行**：
    页面停在初始占位符、按钮全没反应，而且**什么都不报** ——
    连页面自己的错误捕获都装不上，因为它们就在这段脚本里。

    这次真的打进去了：用户在两台机器上各撞了一次，两边控制台都干净。

    判断逻辑在 core/pagecheck.py，**不在这里另写一份** ——
    第一版就是各写一份正则，这边那份转义写坏了、findall 返回空列表，
    于是它「检查了 0 段脚本」然后报成功：闸门装上了，但永远不会拦。
    """
    sys.path.insert(0, HERE)
    from core import pagecheck                             # noqa: PLC0415
    with io.open(os.path.join(HERE, "web", "index.html"), encoding="utf-8") as f:
        ok, why = pagecheck.check(f.read())
    print(f"  {'✓' if ok else '✗'} 页面 JS：{why}")
    if not ok:
        print("     打包中止 —— 页面坏了打出去，用户看到的是"
              "「什么都不动、什么都不报」，比崩掉还难查。")
    return ok


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

    print("\n自检：从 exe 内部实际调用发布库，并对比源码方式认得的资源")
    check_env = os.environ.copy()
    # 不允许本机环境变量把系统 ffmpeg 冒充成包内置的 ffmpeg。
    check_env.pop("IMAGEIO_FFMPEG_EXE", None)
    try:
        dep = subprocess.run([exe, "--package-selfcheck"], capture_output=True,
                             encoding="utf-8", errors="replace", timeout=120,
                             env=check_env)
    except subprocess.TimeoutExpired:
        print("  ✗ 运行库自检 120 秒未完成")
        return False
    if dep.stdout:
        print(dep.stdout.rstrip())
    if dep.returncode:
        if dep.stderr:
            print(dep.stderr[-2000:])
        print(f"  ✗ exe 内部运行库自检失败，退出码 {dep.returncode}")
        return False

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

        # 2. 功能模块：**「打包机器上装了」和「进了包」是两件事。**
        #    PyInstaller 扫不到的运行时 import 会被漏掉，而漏掉不报错 ——
        #    只在用到的那一刻失败（一份包里没有 pypdf，传 PDF 直接失败，
        #    而当时自检是绿的）。所以让 exe 自己 import 一次。
        try:
            mods = json.loads(get("/api/modules?names="
                                  + ",".join(MUST_IMPORT)))["modules"]
        except Exception as exc:                         # noqa: BLE001
            print(f"  ✗ 问不到模块清单（{exc}）—— 这个包连自检都做不了")
            return False
        gone = [m for m in MUST_IMPORT if not mods.get(m)]
        print(f"  {'✓' if not gone else '✗'} 功能模块 "
              f"{len(MUST_IMPORT) - len(gone)}/{len(MUST_IMPORT)}"
              + (f"　缺：{', '.join(gone)}" if gone else ""))
        if gone:
            print("     → 这几个缺了不会报错，只在用到的那一刻失败："
                  "pypdf=传 PDF 读不了、PIL=参考图不压缩容易撞体积上限、"
                  "imageio_ffmpeg=目标机没装 ffmpeg 就拼不了成片、"
                  "boto3/botocore=只收公网链接的模型一条都出不了。")
            print("     → 先确认这台机器 pip 装了它们，再看 HIDDEN 里有没有点名。")
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

        # 4b. 自带字幕样式：打进去了没有。**漏了不报错** ——
        #     字幕照出，只是用的是 videocaptioner 的默认样子。
        try:
            r = subprocess.run([str(exe), "caption", "style", "--help"],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=180)
            txt = (r.stdout or "") + (r.stderr or "")
            hit = "已装入" in txt or "样式" in txt
            print(f"  {'✓' if r.returncode == 0 else '✗'} 字幕样式 "
                  + ("自带 4 份已随包" if r.returncode == 0 else "跑不起来"))
            ok = ok and r.returncode == 0
        except Exception as exc:                         # noqa: BLE001
            print(f"  ✗ 字幕样式检查失败：{exc}")
            ok = False

        # 4. 字幕直通：**「模块在包里」和「跑得起来」是两件事。**
        #    videocaptioner 在 import 阶段就会碰 pydub → audioop，
        #    3.13 上少了 backport 就直接崩 —— 而 /api/modules 那一关
        #    只证明 import 成功，证明不了它的 CLI 能解析参数、能起来。
        #    所以真跑一次 `caption --version`。
        # 别在这儿 `import subprocess` —— 模块级已经导过了，函数里再导一次
        # 会把整个函数里的这个名字变成局部变量，于是**函数开头那处
        # subprocess.Popen 直接 UnboundLocalError**（刚踩过：exe 构建完了、
        # 自检还没开始就崩）。
        try:
            r = subprocess.run([str(exe), "caption", "--version"],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=180)
            line = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
            hit = next((l for l in line if "videocaptioner" in l.lower()), "")
            print(f"  {'✓' if hit else '✗'} 字幕直通 "
                  + (hit.strip() if hit else "跑不起来"))
            if not hit:
                print("     → 看看 EXCLUDE 是不是把它真正要的东西一起排掉了，"
                      "或者这台机器没装 audioop-lts")
                print((r.stdout or "")[-600:], (r.stderr or "")[-600:])
                ok = False
        except Exception as exc:                         # noqa: BLE001
            print(f"  ✗ 字幕直通跑不起来：{exc}")
            ok = False
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

    # **必须有的：缺了直接停。** 不给 y/N ——
    # 那个 y/N 就是上次打出一个读不了 PDF 的包的原因。
    lack = [(m, w) for m, w in REQUIRED.items()
            if not importlib.util.find_spec(m)]
    print("必须有的依赖：")
    for mod, what in REQUIRED.items():
        ok = importlib.util.find_spec(mod) is not None
        print(f"  {'✓' if ok else '✗'} {mod:<16}{what}")
    if lack:
        print(f"\n打不了：这台机器上缺 {'、'.join(m for m, _ in lack)}。")
        print(f"  pip install {' '.join(m for m, _ in lack)}")
        print("  这几个不是可选增强 —— 缺了打出来的包会在用到的那一刻失败，"
              "而且自检是绿的（踩过一次）。")
        return 1

    have, miss = [], []
    for mod, what in REQUIRED.items():
        (have if importlib.util.find_spec(mod) else miss).append((mod, what))
    print("发布依赖：")
    for mod, what in have:
        print(f"  ✓ {mod:<16}{what}")
    for mod, what in miss:
        print(f"  ✗ {mod:<16}{what}　← 不能生成完整发布包")
    if miss:
        print(f"\n  想补齐：pip install {' '.join(m for m, _ in miss)}")
        print("  已停止：不再允许生成缺少运行库的小包。")
        return 1
    print()
    if not check_page():
        return 1

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
        "--name", NAME,
        # 生成的临时 spec 放 build/，不要覆盖仓库里可供人工打包的 spec。
        "--specpath", os.path.join(HERE, "build"),
        # 运行时读的资源，必须打进去
        "--add-data", f"{os.path.join(HERE, 'web')}{sep}web",
        "--add-data", f"{os.path.join(HERE, 'prompts')}{sep}prompts",
        # 自带字幕样式（ASS V4+ 块）。**必须打进去** —— 目标机器上
        # videocaptioner 的样式目录第一次跑才建，里面一份都没有，
        # 而缺样式的表现不是报错，是「字幕出来是它的默认样子」。
        "--add-data", f"{os.path.join(HERE, '字幕样式')}{sep}字幕样式",
    ]
    for package in COLLECT_ALL:
        cmd += ["--collect-all", package]
    # 服务商是运行时按目录扫出来 import 的，没有任何一处静态 import ——
    # PyInstaller 的静态分析看不见它们，不点名就一个都不打进去。
    # 后果不是报错，是 exe 里「一家服务商都没有」，页面下拉框空白。
    cmd += ["--collect-submodules", "core.providers"]
    # videocaptioner 内部按子命令动态 import，静态分析扫不全
    cmd += ["--collect-submodules", "videocaptioner"]
    for m in EXCLUDE:
        cmd += ["--exclude-module", m]
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
    if not onedir:
        print("       单文件会压缩内部资源；体积小于 ffmpeg 解压后的大小不代表遗漏，"
              "以上面的 exe 内部自检为准")
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
