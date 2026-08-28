# -*- coding: utf-8 -*-
"""字幕：videocaptioner 打进包里，从 `<本程序>.exe caption ...` 进去。

用户原话（2026-08-27）：「我需要直接 pip install videocaptioner，连同我最新的
修改一起打包 exe，videocaptioner 要确保可以正常使用」。

三件查出来的事实（都影响能不能用）：

1. 它声明 `Requires-Python <3.13`，而本机是 3.13.0。真正的原因是
   **`audioop` 在 3.13 被从标准库删了**（PEP 594）而 pydub 导它 ——
   装官方 backport `audioop-lts` 之后 3.13 跑得通（实测 CLI 全部子命令）。
2. `pyqt5` / `pyqt-fluent-widgets` 是它的**无条件依赖**（它自己的 `gui` extra
   是空摆设）。我们只用 CLI，所以打包时把 Qt 那一堆排掉 —— 不排的话
   PyInstaller 顺着 `caption gui` 那个分支把整个 Qt 拖进来。
3. 它靠 PATH 找 ffmpeg，而 imageio-ffmpeg 带的二进制**叫
   `ffmpeg-win-x86_64-v7.1.exe`** —— 光把目录塞进 PATH 没用，名字对不上。
   所以按标准名做一份影子副本。
"""
import inspect
import unittest

from core.store import read_text


class PassthroughTests(unittest.TestCase):

    def _src(self):
        return read_text("run.py")

    def test_caption_is_intercepted_before_argparse(self):
        """★ 后面的参数是它的 —— 交给我们的 parser 只会被判成「不认识的参数」。"""
        src = self._src()
        i = src.index("def main() -> int:")
        j = src.index("ArgumentParser", i)
        self.assertIn('sys.argv[1] == "caption"', src[i:j])

    def test_it_rewrites_sys_argv(self):
        """★ videocaptioner 的 main() 按 sys.argv 解析，直接传参数它不认。"""
        src = self._src()
        i = src.index("def _caption")
        self.assertIn('sys.argv = ["videocaptioner"]', src[i:i + 2600])

    def test_systemexit_is_not_an_error(self):
        """★ argparse 的正常退出走 SystemExit —— 不接住的话
        `caption --help` 会被当成崩了。"""
        src = self._src()
        i = src.index("def _caption")
        self.assertIn("except SystemExit", src[i:i + 2600])

    def test_it_says_what_to_do_when_missing(self):
        """★ 源码方式跑时没装它 —— 报「没有这个模块」不如直接给装的命令。"""
        src = self._src()
        i = src.index("def _caption")
        blk = src[i:i + 2600]
        self.assertIn("ignore-requires-python", blk)
        self.assertIn("audioop-lts", blk)


class FfmpegShimTests(unittest.TestCase):

    def test_it_copies_to_the_standard_name(self):
        """★ imageio-ffmpeg 那个二进制不叫 ffmpeg.exe，而它用 which('ffmpeg')
        找 —— 只把目录塞进 PATH 是找不到的（实测：doctor 照旧报 not found）。

        实现挪到了 `core/captions.ensure_ffmpeg`（原来只长在 caption 直通里，
        源码方式和字幕环节都享受不到）。
        """
        import inspect
        from core import captions
        src = inspect.getsource(captions.ensure_ffmpeg)
        self.assertIn('"ffmpeg.exe"', src)
        self.assertIn("shutil.copy2", src)

    def test_it_does_not_recopy_when_the_name_already_matches(self):
        """机器上装的是真 ffmpeg 时别多拷一份。"""
        import inspect
        from core import captions
        self.assertIn("os.path.basename(exe).lower() != want",
                      inspect.getsource(captions.ensure_ffmpeg))

    def test_missing_ffprobe_is_named_explicitly(self):
        """★ imageio-ffmpeg **只带 ffmpeg 不带 ffprobe**。
        不说清就是让它自己报一句没头没尾的 not found。"""
        src = read_text("run.py")
        i = src.index("def _caption")
        self.assertIn("ffprobe", src[i:i + 2600])


class PackagingTests(unittest.TestCase):

    def _src(self):
        return read_text("打包exe.py")

    def test_it_is_a_hard_requirement(self):
        src = self._src()
        self.assertIn('"videocaptioner"', src)
        self.assertIn('"audioop"', src)

    def test_the_cli_module_is_smoke_checked(self):
        """★ 「模块在包里」和「跑得起来」是两件事：它在 import 阶段就碰
        pydub → audioop，少了 backport 直接崩，而 /api/modules 只证明
        import 成功。所以自检里真跑一次 `caption --version`。"""
        src = self._src()
        self.assertIn('"videocaptioner.cli.main"', src)
        self.assertIn('"caption", "--version"', src)

    def test_the_gui_stack_is_excluded(self):
        """★ pyqt5 是它的无条件依赖，而我们是网页界面，用不上。"""
        src = self._src()
        self.assertIn("EXCLUDE", src)
        for m in ("PyQt5", "qfluentwidgets"):
            self.assertIn(f'"{m}"', src)

    def test_submodules_are_collected(self):
        """★ 它按子命令动态 import，静态分析扫不全。"""
        self.assertIn('"--collect-submodules", "videocaptioner"', self._src())

    def test_no_local_subprocess_import_in_selfcheck(self):
        """★ 函数里再 `import subprocess` 会把这个名字变成局部变量，
        于是函数开头那处 subprocess.Popen 直接 UnboundLocalError ——
        exe 构建完了、自检还没开始就崩（刚踩过）。"""
        # 用 AST 判，**别匹配源码文本** —— 注释里解释这件事的那句话本身就含
        # 「import subprocess」，按文本断言会撞上自己的注释（这个项目里
        # 同一类误伤已经出现过三次：撞变量声明、撞注释）。
        import ast
        tree = ast.parse(read_text("打包exe.py"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "selfcheck")
        got = [a.name for n in ast.walk(fn)
               if isinstance(n, ast.Import) for a in n.names]
        self.assertNotIn("subprocess", got)


class FindCliInFrozenTests(unittest.TestCase):
    """★ 打包之后字幕这一环**永远报「没装 VideoCaptioner」**，而它就在包里。

    用户实遇（2026-08-27）：「字幕功能加在哪里了我为什么没看见」。
    功能其实早就齐了 —— 设置页有字幕那一节、流程里有字幕这一步、
    `core/subtitle.py` 负责调它。断的是 `find_cli()`：

      · `shutil.which("videocaptioner")` —— 目标机器上没有 Scripts 目录
      · 回落分支要求 `sys.executable` 的名字以 python 开头 ——
        exe 里它是 `Respect短剧制作平台.exe`

    两条路都走不通 → 返回空 → 整环显示没装。现在加一支：打包状态下，
    只要 `import videocaptioner` 成功，就用 `<自己> caption`（run.py 的直通）。
    """

    def test_the_frozen_branch_comes_first(self):
        """★ 必须在 which 之前 —— 机器上如果另装了一个不同版本的
        videocaptioner，用它而不用包里那个，就是两套行为。"""
        import inspect
        from core import subtitle
        src = inspect.getsource(subtitle.find_cli)
        self.assertLess(src.index("paths.FROZEN"),
                        src.index('shutil.which("videocaptioner")'))

    def test_it_uses_the_passthrough_subcommand(self):
        import inspect
        from core import subtitle
        src = inspect.getsource(subtitle.find_cli)
        self.assertIn('[sys.executable, "caption"]', src)

    def test_it_still_checks_the_module_is_really_there(self):
        """★ 光看 FROZEN 就返回命令的话，没打进去的包会给出一个假候选，
        然后在真跑字幕那一刻才失败 —— 那时候成片已经出了。"""
        import inspect
        from core import subtitle
        src = inspect.getsource(subtitle.find_cli)
        self.assertIn("import videocaptioner", src)

    def test_the_frozen_message_does_not_tell_users_to_pip_install(self):
        """★ exe 用户装不了 pip 包。让他去 pip install 是把包的问题
        推给他 —— 而这时候「没调起来」一定是我们打包漏了。"""
        from core import subtitle
        self.assertNotIn("pip install", subtitle.NOT_INSTALLED_FROZEN)
        self.assertIn("caption --version", subtitle.NOT_INSTALLED_FROZEN)

    def test_both_call_sites_pick_the_right_message(self):
        import inspect
        from core import subtitle
        for fn in (subtitle.selftest, subtitle.run):
            src = inspect.getsource(fn)
            if "NOT_INSTALLED" in src:
                self.assertIn("NOT_INSTALLED_FROZEN", src, fn.__name__)


class SelftestVerdictTests(unittest.TestCase):
    """★ 自检要按「我们用的那两步」判，不照抄它 doctor 的总判决。

    实遇（2026-08-27）：包里装好了、`installed=True`，而自检仍然报
    「装是装了，但它自检没过」—— 因为它的 doctor 报了 4 个 ERROR。
    逐项查过它的源码，其中三项和我们无关：

      ffprobe  只在 doctor 自己和 `core/dubbing/audio.py`（配音）里用 ——
               `transcribe`（出 srt）和 `synthesize`（压制）一次都不碰
      yt-dlp   只给 `download`（从网上下视频）用
      python   它声明 <3.13 的真原因是 pydub 要 audioop（3.13 删了标准库那个），
               装 audioop-lts 之后实测全部子命令正常

    误报比漏报更贵：人会去装一堆用不上的东西，或者干脆以为这功能坏了。
    """

    def _split(self, text):
        from core import subtitle
        return subtitle._split_doctor(text)

    def test_the_three_asides_do_not_block(self):
        stop, aside = self._split(
            "ERROR python: Python 3.13.0 is unsupported\n"
            "ERROR ffprobe: ffprobe not found. Required for duration checks.\n"
            "ERROR yt-dlp: yt-dlp not found. Required by download.\n")
        self.assertEqual(stop, [])
        self.assertEqual(len(aside), 3)

    def test_a_real_blocker_still_blocks(self):
        """★ ffmpeg 是真要的（抽音轨、压制都靠它）—— 不能一起放过。"""
        stop, _ = self._split("ERROR ffmpeg: ffmpeg not found.\n")
        self.assertEqual(len(stop), 1)

    def test_the_summary_line_is_not_a_finding(self):
        """★ 「Doctor found 4 error(s)」是汇总行 —— 算进去的话永远至少有
        一项「挡住字幕」，自检永远红着，而那行什么信息都没多给。"""
        stop, aside = self._split(
            "ERROR ffprobe: ffprobe not found.\n"
            "ERROR Doctor found 1 error(s) and 4 warning(s)\n")
        self.assertEqual(stop, [])
        self.assertEqual(len(aside), 1)

    def test_warnings_do_not_turn_it_red(self):
        """WARN 是「没配 key」这类 —— 配了才用得上某些功能。"""
        stop, aside = self._split("WARN llm.api_key: missing\n")
        self.assertEqual((stop, aside), ([], []))


class StdoutHygieneTests(unittest.TestCase):
    """★ 直通路上的提示一律走 stderr。

    实遇（2026-08-27）：那两句 ffmpeg 提示打在 stdout 上，而
    `core/subtitle.version()` 是抓 stdout 解析版本号的 —— 于是版本号变成
    「⚠ 有 ffmpeg 没有 ffprobe…\nvideocaptioner 1.4.2」，页面照原样显示。
    """

    def test_the_passthrough_prints_notices_to_stderr(self):
        src = read_text("run.py")
        i = src.index("def _caption")
        blk = src[i:src.index("def main()", i)]
        self.assertIn("file=sys.stderr", blk)
        # 直通里不许有**打到 stdout 的** print —— 一个就够污染 --version。
        # `_note` 自己那行是 print(..., file=sys.stderr)，放过。
        bare = [l.strip() for l in blk.splitlines()
                if l.strip().startswith("print(")
                and "sys.stderr" not in l]
        self.assertEqual(bare, [], bare)


class FfmpegShimIsSharedTests(unittest.TestCase):
    """★ 影子副本原来只长在 caption 直通里 —— 源码方式跑、或字幕环节自己
    调 doctor 时照旧报「ffmpeg not found」，而机器上明明有一个。
    一处实现、两处调用。"""

    def test_it_lives_in_one_place(self):
        from core import captions
        self.assertTrue(callable(getattr(captions, "ensure_ffmpeg", None)))

    def test_both_callers_use_it(self):
        self.assertIn("captions.ensure_ffmpeg", read_text("run.py"))
        self.assertIn("captions.ensure_ffmpeg", read_text("core/subtitle.py"))
