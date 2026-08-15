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
