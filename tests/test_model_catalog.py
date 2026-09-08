# -*- coding: utf-8 -*-
"""模型清单按实拉/快照来，不再只读代码里写死的那份。

用户原话（2026-09-02）：「批量工具都已经尝试使用拉取最新模型来更新服务商了，
现在 studio 也需要这样」。

为什么非这样不可 —— 这几天实拉的账：
  · 无限画布 **24 小时内**从 5 个变 11 个，头一天写进代码的 3 个已经下线
  · 鹤一次下线 9 个，其中 `sd2-720p` 还是它的 default_model 和代码兜底 ——
    也就是「没改过模型就点开始」必然失败
  · `sd2-1080p` 前一天实拉说没有、后一天又回来了

手改清单追不上，而追不上的表现是**跑到那一步才报「找不到模型」**，
那可能是几百步之后的事。
"""
import unittest

from core import model_catalog as MC
from core import providers as P


def _cap(pid="paisio"):
    return P.REGISTRY[pid]().capabilities()


class ModelCatalogTests(unittest.TestCase):
    def test_a_capability_never_loses_a_whole_kind(self):
        """★ 一类都拉不到时保留原来声明的那份。

        这是 studio 侧加的护栏（批量工具那边没有）：有的家的 `/v1/models`
        不是完整目录 —— 坤鸡按令牌分组过滤，实拉只回 `gpt-image-2` 一个
        （它自己代码注释里就写着）。照原样套上去，它的 video 会从 3 个变
        **0 个**，页面上那一类直接消失，而那几个模型其实是能用的。

        「多列一个已下线的」和「少列一个能用的」代价差很多：前者选中了会得到
        一句响的「找不到模型」，后者是**页面上根本没有**，人只会以为不支持。
        """
        cap = _cap("kunji")
        before = list((cap.get("video") or {}).get("models") or [])
        self.assertTrue(before, "坤鸡本来声明了视频模型，这条测试才有意义")
        got = MC.apply_catalog(cap, {"ok": True, "models": ["gpt-image-2"]}, "测试")
        self.assertEqual(list(got["video"]["models"]), before, "video 被清空了")
        self.assertIn("video", got["model_catalog"]["kept_declared"])
        # 拉到的那一类照常替换
        self.assertEqual(got["image"]["models"], ["gpt-image-2"])

    def test_unknown_kinds_still_make_the_list(self):
        """★ 认不出类型的**照样进清单** —— 拉取结果不是白名单。

        判不出是图还是片就都留着，让人自己选。漏掉一个能用的模型比多列一个贵。
        """
        cap = _cap()
        got = MC.apply_catalog(
            cap, {"ok": True, "models": ["某个全新的名字-480p"]}, "测试")
        self.assertIn("某个全新的名字-480p", got["model_catalog"]["unknown"])
        self.assertIn("某个全新的名字-480p", got["video"]["models"])

    def test_chat_models_stay_out_of_image_and_video(self):
        """★ 聊天模型不许进图/片的下拉。

        鹤的 `/v1/models` 里有 21 个 claude-* / gemini-* / deepseek-* ——
        它们是给 LLM 那条路用的。混进出图下拉，人选中了就是一次白花的调用。
        """
        cap = _cap()
        got = MC.apply_catalog(cap, {"ok": True, "models": [
            "claude-opus-4-6", "gemini-3.1-pro-preview", "deepseek-v4-pro",
            "paisio-seedance-2.5-720p"]}, "测试")
        for m in ("claude-opus-4-6", "gemini-3.1-pro-preview", "deepseek-v4-pro"):
            self.assertIn(m, got["model_catalog"]["other"], m)
            self.assertNotIn(m, got["image"]["models"], m)
            self.assertNotIn(m, got["video"]["models"], m)
        self.assertIn("paisio-seedance-2.5-720p", got["video"]["models"])

    def test_the_default_model_follows_the_pulled_list(self):
        """★ 默认模型不许指向一个拉不到的名字。

        鹤的 default_model 曾经是 `sd2-720p`，而那一族整个下线了 ——
        「没改过模型就点开始」必然失败，报「找不到模型」。
        """
        cap = _cap()
        got = MC.apply_catalog(cap, {"ok": True, "models": [
            "paisio-seedance-2.5-720p", "gpt-image2-1-high"]}, "测试")
        for kind in ("image", "video"):
            dm = got[kind]["default_model"]
            if got[kind]["models"]:
                self.assertIn(dm, got[kind]["models"], kind)

    def test_the_cache_is_keyed_on_the_credential(self):
        """★ 换了 Key 或接口地址就得重拉。

        不同 Key 的可见清单不一样（坤鸡按令牌分组过滤）。
        共用一份缓存的话，换了 Key 还在用上一把 Key 的清单 —— 不报错，
        只是选到一个这把 Key 看不见的模型。
        """
        a = MC.identity("paisio", {"api_key": "k1"})
        b = MC.identity("paisio", {"api_key": "k2"})
        c = MC.identity("paisio", {"api_key": "k1", "base_url": "https://x"})
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(a, MC.identity("paisio", {"api_key": "k1"}))

    def test_hvtald_is_skipped_not_failed(self):
        """HVTALD 是固定账号模型，没有清单接口 —— 说清原因，别报成网络错。"""
        r = MC.fetch("hvtald", {"api_key": "x"})
        self.assertFalse(r["ok"])
        self.assertIn("没有模型清单接口", r["msg"])

    def test_the_server_uses_the_catalog_not_the_hardcoded_list(self):
        """★ 页面拿到的必须是过了 catalog 的那份。

        原来五处都直接调 `providers.list_capabilities()` —— 也就是代码里写死的
        那份。漏掉一处的表现是「同一个页面上两个下拉不一样」。
        """
        import io
        import os
        src = io.open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "server", "app.py"), encoding="utf-8").read()
        # **按 AST 数，不按字符串数** —— docstring 和注释里会引用这个名字
        # 来解释「原来是怎样的」，用 count() 会把说明也算成调用（我刚踩了）。
        import ast
        tree = ast.parse(src)
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name)
                 and n.func.id == "list_capabilities"]
        self.assertEqual(len(calls), 1,
                         f"直接读写死清单的地方有 {len(calls)} 处 —— "
                         f"只该有 caps_of 里那一处兜底")
        self.assertIn('if path == "/api/models/refresh":', src)

    def test_the_snapshot_carries_no_credentials(self):
        """★ 打包快照里不许有凭据。

        它会被 --add-data 打进 exe，发出去的。生成工具当场核对过，
        这里再钉一遍：只有模型字段能进。
        """
        import inspect
        src = inspect.getsource(MC.fetch)
        # fetch 只挑白名单字段进 item，别把整行响应塞进去
        self.assertIn("item = {'id': mid}", src)
        self.assertNotIn("item = row", src)


if __name__ == "__main__":
    unittest.main()
