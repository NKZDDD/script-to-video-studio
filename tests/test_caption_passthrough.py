# -*- coding: utf-8 -*-
"""独立 subtitle_app 的 VideoCaptioner 运行库兼容性测试。

Studio 已移除字幕入口；其移除契约由 test_studio_subtitle_removed.py 覆盖。

独立工具仍需验证的运行库约束：

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




class FfmpegShimIsSharedTests(unittest.TestCase):
    """★ 影子副本原来只长在 caption 直通里 —— 源码方式跑、或字幕环节自己
    调 doctor 时照旧报「ffmpeg not found」，而机器上明明有一个。
    一处实现、两处调用。"""

    def test_it_lives_in_one_place(self):
        from core import captions
        self.assertTrue(callable(getattr(captions, "ensure_ffmpeg", None)))

    def test_both_callers_use_it(self):
        self.assertIn("captions.ensure_ffmpeg", read_text("subtitle_app/main.py"))
        self.assertIn("captions.ensure_ffmpeg", read_text("core/subtitle.py"))
