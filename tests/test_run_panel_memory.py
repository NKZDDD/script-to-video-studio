# -*- coding: utf-8 -*-
"""跑法要记住，服务商要按设置来 —— 两个都是「看着对其实不对」。

用户实遇两件事：

① 「一键跑到底」面板上改了并发，下次打开全弹回去。
   原因是单向绑定：只从 `config.defaults` 读，**从不写回**。
   人以为它记住了，下次按记忆点「开始」，实际跑的是旧并发 —— 不报错。

② 生产页每次都用「列表里第一家 + 那家的 default_model」，
   而不是「设置 → 出图出片优先级」里排的首选。
   于是同一个项目两条入口用不同的服务商，两边都不报错
   （那家也能出图，只是不是你排的那家）。

两条都只能靠自己发现，所以用机器检查钉住。
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _html() -> str:
    return io.open(os.path.join(ROOT, "web", "index.html"),
                   encoding="utf-8").read()


class WriteBackTests(unittest.TestCase):
    """① 面板上改过的跑法要写回。"""

    def setUp(self):
        self.html = _html()

    def test_the_panel_writes_back(self):
        """★ 这就是那个 bug —— 以前只有读没有写。"""
        self.assertIn("async function rememberAuto()", self.html)
        i = self.html.index("async function rememberAuto()")
        blk = self.html[i:i + 700]
        self.assertIn("api.post('/api/config', {defaults})", blk)

    def test_every_run_knob_is_remembered(self):
        """七个跑法旋钮都要在名单里，漏一个就是那一个记不住。"""
        i = self.html.index("const AUTO_REMEMBER")
        blk = self.html[i:i + 500]
        for id_, key in (("autoConc", "concurrency"),
                         ("autoEpConc", "llm_episodes"),
                         ("autoSegConc", "llm_segments"),
                         ("autoLlmConc", "llm_concurrency"),
                         ("autoSize", "image_size"),
                         ("autoRatio", "ratio"),
                         ("autoDur", "duration")):
            self.assertIn(id_, blk, f"{id_} 不在写回名单里")
            self.assertIn(key, blk, f"{key} 不在写回名单里")

    def test_the_scope_and_the_checkboxes_are_not_remembered(self):
        """★ 这两类**故意不记**，记了更危险。

        记住「分析这几集=EP01」或者「包含出图出片=关」的话，下次点
        「开始/继续」你以为在跑全剧/在出图，实际只跑了一集/只跑了文字 ——
        而这不报错。所以它们必须留在名单外。
        """
        i = self.html.index("const AUTO_REMEMBER")
        blk = self.html[i:i + 500]
        for never in ("autoOnly", "autoProd", "autoProduce", "autoDeliver"):
            self.assertNotIn(never, blk, f"{never} 不该被记住")

    def test_an_empty_box_is_not_written_back(self):
        """★ `+''` 是 0 —— 一个 0 并发存进去等于把这条路堵死。"""
        i = self.html.index("async function rememberAuto()")
        blk = self.html[i:i + 700]
        self.assertIn("=== ''", blk.replace("'", "'"))

    def test_numbers_stay_numbers_and_strings_stay_strings(self):
        """比例 `9:16` 走 `+` 会变 NaN，尺寸同理。"""
        i = self.html.index("const AUTO_NUM")
        blk = self.html[i:i + 220]
        for n in ("concurrency", "llm_episodes", "llm_segments",
                  "llm_concurrency", "duration"):
            self.assertIn(n, blk)
        for s in ("ratio", "image_size"):
            self.assertNotIn(f"'{s}'", blk)

    def test_it_listens_on_change_not_input(self):
        """数字框每敲一下发一次请求纯属浪费。"""
        i = self.html.index("Object.keys(AUTO_REMEMBER)")
        blk = self.html[i:i + 260]
        self.assertIn("'change'", blk)
        self.assertNotIn("'input'", blk)

    def test_the_three_llm_knobs_are_also_in_settings(self):
        """面板和「设置」两处都该有 —— 不然只能靠面板顺手改，看不到全貌。"""
        i = self.html.index("const RUN_FIELDS")
        blk = self.html[i:i + 420]
        for k in ("llm_episodes", "llm_segments", "llm_concurrency"):
            self.assertIn(k, blk)

    def test_the_defaults_table_covers_them(self):
        """★ RUN_DEFAULTS 漏一项，「设置」里那一格就是空的 ——

        而保存时空格是 0。这个坑改写重试已经踩过一次。
        """
        m = re.search(r"const RUN_DEFAULTS = \{(.*?)\};", _html(), re.S)
        self.assertIsNotNone(m)
        for k in ("concurrency", "llm_episodes", "llm_segments",
                  "llm_concurrency", "max_retry", "soften_rounds"):
            self.assertIn(k, m.group(1))


class ChainPickTests(unittest.TestCase):
    """② 生产页按设置里排的链预选。"""

    def setUp(self):
        self.html = _html()

    def test_the_provider_select_preselects_the_chain(self):
        """★ 这就是那个 bug —— 以前没有 selected，浏览器挑列表第一个。"""
        i = self.html.index("function providerOptions(")
        blk = self.html[i:i + 400]
        self.assertIn("selected", blk)
        self.assertIn("cur", blk)

    def test_the_row_carries_the_chain_choice(self):
        self.assertIn("data-chain-prov", self.html)
        self.assertIn("data-chain-model", self.html)
        i = self.html.index("const pick =")
        self.assertIn("BOOT.config.chains", self.html[i:i + 120])

    def test_the_model_follows_the_chain_too(self):
        """★ 服务商对了模型不对，等于还是没按设置来。"""
        i = self.html.index("const want = sel.value === tr.dataset.chainProv")
        blk = self.html[i:i + 320]
        self.assertIn("chainModel", blk)
        self.assertIn("default_model", blk)

    def test_switching_provider_falls_back_to_that_ones_default(self):
        """★ 别硬套：换成别家之后链上那个型号不适用了。

        硬套会选到一个这家不认的型号，然后吃 400。
        """
        i = self.html.index("const want = sel.value === tr.dataset.chainProv")
        blk = self.html[i:i + 320]
        self.assertIn("includes(want)", blk)


if __name__ == "__main__":
    unittest.main()
