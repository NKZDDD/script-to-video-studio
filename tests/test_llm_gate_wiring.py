# -*- coding: utf-8 -*-
"""页面上的旋钮必须真的接到线上。

实跑（电影级，烟火尽头05）：并发填 50 / 50 / 200，状态栏一直显示
**「分析 3/4」** —— 上限是 4，跟填的 200 毫无关系。

查下来：`LLM_GATE` 的构造默认值就是 4，而 `LLM_GATE.configure()`
**只有 pipeline.py（通用级）调过，pipeline_v34.py 一次都没调**；
server 那边给 v34 的 start 也没传 `llm_concurrency`。

这类漏接最难发现：不报错、不崩、页面上那个框好好地在那儿 ——
只是填什么都不生效。所以两头都钉：参数要收得到，闸门要配得上。
"""
import inspect
import unittest

from core import pipeline as P61, pipeline_v34 as P34
from core.executor import LlmGate


class BothPipelinesConfigureTheGate(unittest.TestCase):

    def test_v34_accepts_the_setting(self):
        """★ 以前 run() 压根没有这个参数，页面传了也没人收。"""
        self.assertIn("llm_concurrency", inspect.signature(P34.run).parameters)

    def test_v34_actually_configures_the_gate(self):
        """★ 收到了不配置，等于没收。"""
        self.assertIn("LLM_GATE.configure(", inspect.getsource(P34.run))

    def test_v61_still_does(self):
        self.assertIn("LLM_GATE.configure(", inspect.getsource(P61.run))

    def test_both_fall_back_the_same_way(self):
        """没填总上限时，退回「集并发和段并发里大的那个」—— 两边要一致。"""
        want = "llm_concurrency or max(1, ep_concurrency, seg_concurrency)"
        for fn in (P34.run, P61.run):
            self.assertIn(want, inspect.getsource(fn), fn.__module__)

    def test_the_server_passes_it_to_v34_too(self):
        """★ 中间少一棒，前面两条都白搭。"""
        import io
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = io.open(os.path.join(root, "server", "app.py"), encoding="utf-8").read()
        i = src.index("pipeline_v34.start(")
        chunk = src[i:i + 1600]
        self.assertIn("llm_concurrency=", chunk, "给 v34 的调用没传总上限")


class GateBehaviourTests(unittest.TestCase):
    """闸门本身。"""

    def test_the_default_is_only_four(self):
        """★ 这就是「分析 3/4」里那个 4 —— 不配置就一直是它。"""
        self.assertEqual(LlmGate().snapshot()["llm_limit"], 4)

    def test_configure_changes_it(self):
        g = LlmGate()
        g.configure(200)
        self.assertEqual(g.snapshot()["llm_limit"], 200)

    def test_garbage_does_not_drop_it_to_zero(self):
        """0 或空会让所有分析卡死 —— 至少留 1。"""
        for junk in (0, None, ""):
            g = LlmGate()
            g.configure(junk)
            self.assertGreaterEqual(g.snapshot()["llm_limit"], 1, repr(junk))

    def test_it_really_limits_concurrency(self):
        """★ 光改数字不算数，得真的挡住。"""
        import threading
        g = LlmGate()
        g.configure(2)
        peak, lock, hold = [0], threading.Lock(), threading.Event()
        live = [0]

        def one():
            with g.slot():
                with lock:
                    live[0] += 1
                    peak[0] = max(peak[0], live[0])
                hold.wait(0.5)
                with lock:
                    live[0] -= 1

        ts = [threading.Thread(target=one) for _ in range(6)]
        for t in ts:
            t.start()
        hold.set()
        for t in ts:
            t.join()
        self.assertLessEqual(peak[0], 2)


class ProviderModelTests(unittest.TestCase):
    """两边的模型清单要对得上 —— ComfyUI 那份是跟着服务商文档维护的。"""

    def _models(self, pid, media):
        from core.providers import build
        c = build(pid, "k", "", "").capabilities()
        return set((c.get(media) or {}).get("models") or [])

    def test_chaomo_has_the_native_tier(self):
        """2026-08 新增的 Native 三档。"""
        got = self._models("chaomo", "image")
        for m in ("gpt-image2-1K-Native", "gpt-image2-2K-Native",
                  "gpt-image2-4K-Native"):
            self.assertIn(m, got)

    def test_paisio_models_all_exist_upstream(self):
        """★ 2026-08-28 用真 Key 拉过 /v1/models 校正（上一次 08-19）。

        这条以前断言的是 `paisiodance2.0`、`video-2.0`、`官方稳定seedance-2.0-720p-fast`
        之类 —— **上游一个都没有**，是照着旧文档抄的。清单里留着假名字比缺名字更糟：
        页面上能选中，跑起来才 503，而失败记录里只写「生成失败」。
        所以这里改成断言「只列真实存在的」。

        08-28 这一轮：`sd2-ultra-720p`、`paisiodance2.0-fast-720p`、
        `grok-imagine-video-1.5(-fast)` 又下线了，从这条断言里撤掉 ——
        这份名单**本身就会过期**，改它的唯一依据是实拉结果。
        """
        got = self._models("paisio", "video")
        for m in ("paisiodance2.0-720p",
                  "seedance2.0-selfsur-720p", "seedance2.0-selfsur-fast-720p",
                  "sd2-720p", "sd2-fast-720p",
                  "sd3-720p", "sd3-fast-720p",
                  "paisio-seedance-2.0-720p", "paisio-seedance-2-mini-720p",
                  "seedance2.0-standard-720p", "doubao-seedance-2-0-720p",
                  "minimax-h3"):
            self.assertIn(m, got, m)

    def test_paisio_lists_no_retired_models(self):
        """实拉确认已下线的，别再出现在下拉里。"""
        got = set(self._models("paisio", "video"))
        for dead in ("sd2-pro-720p", "paisiodance2.0", "paisiodance933-720p",
                     "seedance2.0-official2-720p", "seedance-discount-720p",
                     "video-2.0", "seedance-2.5-720p",
                     # 2026-08-28 实拉确认这一批也下线了
                     "sd2-ultra-720p", "sd2-ultra-fast-720p",
                     "paisiodance2.0-fast-720p", "seedance2-4-8-720p",
                     "seedance2.5-00-720p", "seedance2.5-00-480p",
                     "sd2.5-ultra-720p",
                     "grok-imagine-video-1.5", "grok-imagine-video-1.5-fast"):
            self.assertNotIn(dead, got, f"{dead} 已下线，不该还在清单里")

    def test_paisio_has_the_real_seedance25_family(self):
        got = self._models("paisio", "video")
        for m in ("seedance2.5-4-1-720p", "seedance2.5-26-720p",
                  "paisiodance-2.5-720p",
                  # 08-28 鹤新增的写法。用户就是在这儿撞上的：选了它，
                  # 时长下拉只到 15 秒，因为它不在当时那份 2.5 名单里。
                  "paisio-seedance-2.5-480p", "paisio-seedance-2.5-720p",
                  "doubao-seedance-2-5-720p", "sd2.5-720p-standard"):
            self.assertIn(m, got, m)

    def test_paisio_image_models_are_the_new_naming(self):
        """★ 08-28 实拉：旧的 16 个图片模型**一个都不在线上了**。

        鹤当图片服务商时整家是假绿灯 —— 页面上选得到、跑起来全 503。
        当前图片链排的是章鱼哥，所以这一条一直没暴露。
        """
        got = self._models("paisio", "image")
        for m in ("gpt-image2-1-high", "gpt-image2-2-low",
                  "gemini-3-pro-image-preview1",
                  "gemini-3.1-flash-image-preview1"):
            self.assertIn(m, got, m)
        for dead in ("gpt-image-2-1k", "gpt-image-2-4k", "gpt-image2-high",
                     "nano-banana-2-1k", "nano-banana-pro-4k",
                     "image-2-1K", "gemini-3-pro-image-preview"):
            self.assertNotIn(dead, got, f"{dead} 已下线，不该还在清单里")

    def test_no_duplicates_crept_in(self):
        """★ 补清单时最容易的错：同一个写两遍，下拉里出现两条一样的。"""
        from core.providers import build
        for pid in ("paisio", "chaomo"):
            for media in ("image", "video"):
                c = build(pid, "k", "", "").capabilities()
                ms = (c.get(media) or {}).get("models") or []
                dup = [m for m in set(ms) if ms.count(m) > 1]
                self.assertEqual(dup, [], f"{pid}/{media} 重复：{dup}")


if __name__ == "__main__":
    unittest.main()
