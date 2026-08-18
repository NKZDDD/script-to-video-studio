# -*- coding: utf-8 -*-
"""网络路径要透明，失败的内容要留下来。

这两件事都是排「n2 老是断在中途」时缺的：
  · 请求实际走了系统代理，程序一声不吭 —— 报错指向服务商，查了很久才发现
  · 三次断流收到的两万多字全丢了 —— 断在哪个字段、模型有没有跑偏，无从查证
"""
import os
import shutil
import unittest

from core import run_v34 as R
from core.llm import LLM, mask_url
from test_v34_run import new_project


class ProxyResolutionTests(unittest.TestCase):
    """代理三态：跟随环境 / 强制直连 / 指定。"""

    def _llm(self, proxy):
        return LLM("k", "https://x", "m", proxy=proxy)

    def test_blank_follows_the_environment(self):
        proxies, trust_env, note = self._llm("").resolve_proxy()
        self.assertIsNone(proxies)
        self.assertTrue(trust_env, "空值要保持原行为：跟随系统与环境代理")
        self.assertTrue(note)

    def test_direct_really_ignores_the_environment(self):
        """★ 强制直连必须把 trust_env 也关掉。

        只给 proxies={} 是不够的 —— requests 会继续读 HTTPS_PROXY，
        「强制直连」就成了一句空话，而人已经以为自己排除了代理这个变量。
        """
        for word in ("direct", "直连", "DIRECT", "off"):
            proxies, trust_env, note = self._llm(word).resolve_proxy()
            self.assertFalse(trust_env, word)
            self.assertEqual(proxies, {"http": None, "https": None}, word)
            self.assertIn("直连", note)

    def test_explicit_proxy_is_used_and_does_not_trust_env(self):
        proxies, trust_env, note = self._llm("http://127.0.0.1:7890").resolve_proxy()
        self.assertEqual(proxies["https"], "http://127.0.0.1:7890")
        self.assertFalse(trust_env, "指定了代理就不该再被环境变量插一脚")
        self.assertIn("127.0.0.1:7890", note)

    def test_credentials_in_the_proxy_url_are_masked(self):
        """代理地址常带账号密码，日志和页面都会显示这句话。"""
        note = self._llm("http://me:pw@127.0.0.1:7890").resolve_proxy()[2]
        self.assertNotIn("pw", note)
        self.assertIn("***", note)
        self.assertNotIn("secret", mask_url("http://u:secret@h:1"))

    def test_session_carries_trust_env_not_bare_requests(self):
        """★ trust_env 只能在 Session 上设 —— 裸 requests.post 收不了这个参数。

        少了 Session，「强制直连」这一档是假的。
        """
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "core", "llm.py"), encoding="utf-8").read()
        self.assertNotIn("requests.post(", src,
                         "还有地方裸调 requests.post，trust_env 不生效")
        self.assertIn("s.trust_env = trust_env", src)
        self.assertIn("requests.Session()", src)

    def test_the_effective_route_is_logged(self):
        """不打日志的话代理就是隐形的，出问题查不到。"""
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "core", "llm.py"), encoding="utf-8").read()
        self.assertIn('log(f"网络：{proxy_note}")', src)


class MaxTokensTests(unittest.TestCase):
    """999999 这种值不许发出去。

    网关拿到不合法的上限行为不可预测，实测表现是流到中途连接被关，
    而报错指向「网络中断」—— 根本看不出是这个值的问题。
    页面上那个 input 标了 max="200000"，但 HTML 的 max 不阻止提交。
    """

    def test_absurd_values_are_clamped(self):
        from server.app import MAX_TOKENS_CEILING, _max_tokens
        self.assertEqual(_max_tokens(999999), MAX_TOKENS_CEILING)
        self.assertEqual(_max_tokens(300000), MAX_TOKENS_CEILING)
        self.assertEqual(_max_tokens(500), 1024, "太小会被截断，抬到下限")

    def test_zero_means_use_the_providers_default(self):
        from server.app import _max_tokens
        self.assertEqual(_max_tokens(0), 0)
        self.assertEqual(_max_tokens(-5), 0)

    def test_garbage_falls_back_instead_of_crashing(self):
        from server.app import _max_tokens
        for junk in (None, "abc", "", {}):
            self.assertEqual(_max_tokens(junk), 16000, repr(junk))

    def test_an_old_config_is_healed_on_load(self):
        """★ 存盘时夹住只管新存的。

        早先存进去的 9,999,999 会一直躺在 config.json 里：页面上显示着它，
        每次调用都在日志里刷一句「你填的是 9,999,999…」，而人不点保存
        就永远不会变。实跑里它一直跟到了提示词改写那一层的日志里。
        """
        import json
        import shutil
        import tempfile

        from core import paths
        from server.app import load_config
        prev, d = paths._forced["data"], tempfile.mkdtemp()
        paths.set_data_dir(d)
        try:
            with open(paths.config_path(), "w", encoding="utf-8") as f:
                json.dump({"llm": {"max_tokens": 9999999}}, f)
            self.assertLessEqual(load_config()["llm"]["max_tokens"], 200000)
        finally:
            paths.set_data_dir(prev)
            shutil.rmtree(d, ignore_errors=True)

    def test_clamped_on_save_not_only_on_use(self):
        """存的时候不夹住的话，config.json 里一直躺着 999999，页面回显也是它。

        直接调接口验行为，不去数源码的字符 —— 原来是截 1500 字看
        `_max_tokens` 在不在，那个处理函数一长就假失败（真出现过）。
        """
        import shutil
        import tempfile

        from core import paths
        from server.app import api_post, load_config
        prev, d = paths._forced["data"], tempfile.mkdtemp()
        paths.set_data_dir(d)
        try:
            api_post("/api/config", {"llm": {"max_tokens": 999999}})
            self.assertLessEqual(load_config()["llm"]["max_tokens"], 200000)
        finally:
            paths.set_data_dir(prev)
            shutil.rmtree(d, ignore_errors=True)


class KeepPartialTests(unittest.TestCase):
    """失败的模型输出要落盘。"""

    def setUp(self):
        self.pj = new_project()

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _files(self):
        d = self.pj.p("07_检查与记录", "失败原文")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def test_partial_is_written_with_reason_and_length(self):
        save = R.keep_partial(self.pj, "n2")
        save("这是模型吐了一半的内容", "流式连接中断：Response ended prematurely")
        self.assertEqual(self._files(), ["n2_01.txt"])
        body = open(self.pj.p("07_检查与记录", "失败原文", "n2_01.txt"),
                    encoding="utf-8").read()
        self.assertIn("Response ended prematurely", body)
        self.assertIn("这是模型吐了一半的内容", body)
        self.assertIn("收到 11 字", body)

    def test_repeated_failures_do_not_overwrite_each_other(self):
        """★ 一次跑断三遍就是三份证据，覆盖了等于只留最后一次。"""
        save = R.keep_partial(self.pj, "n2")
        for i in range(3):
            save(f"第{i}次", "断了")
        self.assertEqual(self._files(), ["n2_01.txt", "n2_02.txt", "n2_03.txt"])

    def test_segment_stages_get_their_own_file(self):
        R.keep_partial(self.pj, "n12", "EP01", "SEG03")("x", "断了")
        self.assertEqual(self._files(), ["n12_EP01_SEG03_01.txt"])

    def test_both_json_call_sites_pass_the_callback(self):
        """★ 只接一处的话，另一处的失败照样静默丢掉。"""
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "core", "run_v34.py"),
            encoding="utf-8").read()
        self.assertEqual(src.count("on_partial=keep_partial("), 2)

    def test_a_failing_writer_does_not_mask_the_real_error(self):
        """存盘失败不能盖掉真正的报错 —— 那才是人要看的东西。"""
        from core import llm as L

        class Boom(L.LLM):
            def _session(self, trust_env):
                raise RuntimeError("真正的错误")

        with self.assertRaises(RuntimeError) as cm:
            Boom("k", "https://x", "m").chat(
                "s", "u", log=lambda *_: None,
                on_partial=lambda *_: (_ for _ in ()).throw(OSError("盘满了")))
        self.assertIn("真正的错误", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
