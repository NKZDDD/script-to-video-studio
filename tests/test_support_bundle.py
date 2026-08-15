# -*- coding: utf-8 -*-
"""一键打包排错资料。

「出问题该发哪个文件」用户猜不出来 —— 猜错的代价是来回好几轮：
发截图看不出模型回了什么，发产物看不出是哪一步断的。真正有用的那几份
（失败原文、诊断记录、用量账本、当时的配置）散在四个地方。

**脱敏是这个模块存在的主要理由。** config.json 里有 API key 和对象存储
密钥，而它恰恰是最该发的一份（用的哪家、哪个模型、流式开没开）。
人工删 key 迟早漏一次，而外发的东西收不回来。
"""
import io
import json
import os
import shutil
import unittest
import zipfile

from core import support
from test_v34_run import new_project

CFG = {
    "llm": {"base_url": "https://api.paisio.online", "model": "gpt-5.6-sol",
            "api_key": "sk-realkey1234567890", "max_tokens": 16000,
            "stream": True},
    "providers": {"lingganya": {"api_key": "sk-anotherrealkey", "base_url": "x"},
                  "kunji": {"api_key": ""}},
    "upload": {"access_key": "AKIAREAL", "secret_key": "s3cr3t",
               "endpoint": "https://xxx.r2.cloudflarestorage.com",
               "bucket": "respect-data-nong"},
}


class RedactTests(unittest.TestCase):

    def test_every_kind_of_key_is_removed(self):
        """★ 漏一个就得换所有的 key。"""
        blob = json.dumps(support.redact(CFG), ensure_ascii=False)
        for secret in ("sk-realkey1234567890", "sk-anotherrealkey",
                       "AKIAREAL", "s3cr3t"):
            self.assertNotIn(secret, blob, f"{secret} 泄漏了")

    def test_the_useful_parts_survive(self):
        """★ 全删等于没发 —— 用的哪家、哪个模型才是排错要看的。"""
        r = support.redact(CFG)
        self.assertEqual(r["llm"]["model"], "gpt-5.6-sol")
        self.assertEqual(r["llm"]["base_url"], "https://api.paisio.online")
        self.assertEqual(r["llm"]["max_tokens"], 16000)
        self.assertIs(r["llm"]["stream"], True)
        self.assertEqual(r["upload"]["bucket"], "respect-data-nong")

    def test_filled_and_empty_keys_stay_distinguishable(self):
        """★ 整个删掉的话，「没配 key」和「key 配错了」就分不出来了。"""
        r = support.redact(CFG)
        self.assertIn(f"{len(CFG['llm']['api_key'])} 位", r["llm"]["api_key"])
        self.assertEqual(r["providers"]["kunji"]["api_key"], "（空）")

    def test_it_does_not_wreck_the_structure(self):
        r = support.redact(CFG)
        self.assertEqual(set(r), set(CFG))
        self.assertEqual(set(r["providers"]), set(CFG["providers"]))


class BundleTests(unittest.TestCase):

    def setUp(self):
        self.pj = new_project()
        d = self.pj.p("07_检查与记录", "失败原文")
        os.makedirs(d, exist_ok=True)
        io.open(os.path.join(d, "n4b_01.txt"), "w", encoding="utf-8").write(
            "环节 n4b　全剧\n原因：JSON 校验重试第 1/2 次\n---\n{\"asset_prompts\":[")
        io.open(self.pj.p("07_检查与记录", "usage.jsonl"), "w",
                encoding="utf-8").write('{"stage":"n1","seconds":302}\n')
        self.dest = self.pj.p("07_检查与记录", "b.zip")

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _names(self):
        """返回 (文件名列表, 打开着的 zip)。别用 with —— 关掉之后 read 不了。"""
        support.bundle(self.pj.root, CFG, self.dest)
        z = zipfile.ZipFile(self.dest)
        self.addCleanup(z.close)
        return z.namelist(), z

    def test_the_raw_model_output_is_included(self):
        """★ 排「JSON 解析不了」只能靠这个 —— 少了它这个包就白发了。"""
        names, _ = self._names()
        self.assertIn("07_检查与记录/失败原文/n4b_01.txt", names)

    def test_the_config_goes_in_redacted(self):
        names, z = self._names()
        self.assertIn("配置（已脱敏）.json", names)
        blob = z.read("配置（已脱敏）.json").decode("utf-8")
        self.assertNotIn("sk-realkey1234567890", blob)
        self.assertIn("gpt-5.6-sol", blob)

    def test_no_secret_appears_anywhere_in_the_zip(self):
        """★ 逐个文件扫一遍，不只看配置那一份。"""
        _, z = self._names()
        for n in z.namelist():
            blob = z.read(n).decode("utf-8", "replace")
            for secret in ("sk-realkey1234567890", "AKIAREAL", "s3cr3t"):
                self.assertNotIn(secret, blob, f"{secret} 出现在 {n} 里")

    def test_the_environment_is_recorded(self):
        """版本对不上是常见原因，而人一般不会主动说。"""
        _, z = self._names()
        env = json.loads(z.read("环境.json"))
        self.assertIn("程序版本", env)
        self.assertIn(env["运行方式"], ("exe", "源码"))

    def test_it_explains_itself(self):
        """★ 收到包的人要知道里面有什么、以及**没有**什么。"""
        _, z = self._names()
        man = z.read("这个包里有什么.txt").decode("utf-8")
        self.assertIn("已隐去", man)
        self.assertIn("不在里面", man)

    def test_missing_parts_do_not_break_it(self):
        """还没跑到那一步时那几份不存在，不能因此打不出包。"""
        r = support.bundle(self.pj.root, CFG, self.dest)
        self.assertTrue(os.path.isfile(self.dest))
        self.assertTrue(r["files"])


class WiringTests(unittest.TestCase):
    """有按钮才用得上。"""

    def test_the_page_offers_it_where_the_errors_are(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html = io.open(os.path.join(root, "web", "index.html"),
                       encoding="utf-8").read()
        self.assertIn("/api/support/bundle", html)
        self.assertIn("打包排错资料", html)
        # ★ 得说清 key 不会外发，否则人不敢点
        self.assertIn("自动隐去", html)

    def test_the_endpoint_exists(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = io.open(os.path.join(root, "server", "app.py"),
                      encoding="utf-8").read()
        self.assertIn('"/api/support/bundle"', src)


if __name__ == "__main__":
    unittest.main()


class AttributionTests(unittest.TestCase):
    """一份原文单独发出去时，收到的人得知道是谁答的。

    这一份多半会脱离当时的日志被单独转发 —— 不写模型和线路的话，
    收到的人第一句话就得回问「你用的哪个模型」，一来一回半天。
    """

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    class _LLM:
        model = "gpt-5.6-sol"
        base_url = "https://api.paisio.online/v1"
        stream = True
        max_tokens = 16000

    def test_the_raw_file_header_names_the_model_and_route(self):
        from core import run_v34 as R
        R.keep_partial(self.pj, "n4b", "", "", llm=self._LLM())("{", "截断")
        head = io.open(self.pj.p("07_检查与记录", "失败原文", "n4b_01.txt"),
                       encoding="utf-8").read()[:400]
        for want in ("gpt-5.6-sol", "api.paisio.online", "流式：开",
                     "输出上限：16000", "时间："):
            self.assertIn(want, head, want)

    def test_the_header_never_leaks_the_key(self):
        """★ 这份是要外发的 —— 只记域名，不记完整 base_url 里可能带的东西。"""
        from core import run_v34 as R

        class Keyed:
            model = "m"
            base_url = "https://api.x.com/v1?key=sk-REALKEY"
            stream = False
            max_tokens = 1
        R.keep_partial(self.pj, "n1", "", "", llm=Keyed())("{", "截断")
        head = io.open(self.pj.p("07_检查与记录", "失败原文", "n1_01.txt"),
                       encoding="utf-8").read()
        self.assertNotIn("sk-REALKEY", head)

    def test_missing_llm_info_says_so_instead_of_lying(self):
        from core import run_v34 as R
        R.keep_partial(self.pj, "n1", "", "")("{", "截断")
        head = io.open(self.pj.p("07_检查与记录", "失败原文", "n1_01.txt"),
                       encoding="utf-8").read()
        self.assertIn("没记到", head)

    def test_llm_failures_record_which_model_answered(self):
        """★ 出图出片一直带着服务商，分析引擎这一层一直是空的 ——

        而分析环节恰恰是最常出问题的那一层。
        """
        import inspect

        from core import pipeline_v34 as P
        src = inspect.getsource(P)
        self.assertIn('"provider": _host(', src)
        self.assertIn("target=ep or \"全剧\", **who", src)


class BothSystemsTests(unittest.TestCase):
    """两套体系必须都有排错能力 —— 只做一边等于另一边照旧摸黑。

    实际发生过：失败原文、模型归属这些做在了电影级那条执行链上，
    而通用版（stages.py / pipeline.py）**一份原文都不存** ——
    JSON 解析不了时模型回了什么直接丢掉，人只能对着一句
    「未找到可解析的 JSON」猜。这类能力是体系无关的，
    做在一边就该同时接到另一边。
    """

    def _src(self, rel):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return io.open(os.path.join(root, rel), encoding="utf-8").read()

    def test_both_execution_layers_keep_the_raw_output(self):
        for rel in ("core/stages.py", "core/run_v34.py"):
            self.assertIn("keep_partial(", self._src(rel), rel)
            self.assertIn("on_partial=", self._src(rel), rel)

    def test_both_pipelines_record_who_answered(self):
        for rel in ("core/pipeline.py", "core/pipeline_v34.py"):
            src = self._src(rel)
            self.assertIn("provider=", src.replace('"provider":', "provider="), rel)
            self.assertIn("model", src, rel)

    def test_keep_partial_lives_in_the_shared_layer(self):
        """★ 放在某一套的执行层里，另一套就 import 不到（会绕成循环依赖）。"""
        from core import store
        self.assertTrue(callable(store.keep_partial))
