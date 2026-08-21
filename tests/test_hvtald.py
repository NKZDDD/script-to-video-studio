# -*- coding: utf-8 -*-
"""HVTALD：把「回调 + WebDAV + 比例藏在提示词里」这套怪癖包成普通 Provider。

这些断言锁的都是**发错不报错、只是结果不对**的地方。
"""
import unittest

from core.apiutil import ApiError, TASK_FATAL
from core.providers import REGISTRY, resolve_id
from core.providers.base import VideoTask
from core.providers.hvtald import (FIXED_DURATION, MAX_REFS, HvtaldProvider,
                                   parse_creds)

FULL = ('{"deviceId":"MMTVTCALD","token":"TOK","webDavUrl":"http://fo/webdav/x",'
        '"user":"U","password":"P"}')
KV = "deviceId=MMTVTCALD;token=TOK;webdav=http://fo/webdav/x;user=U;password=P"
URLS = ["https://cdn/a.png", "https://cdn/b.png"]


class HvtaldTests(unittest.TestCase):
    def test_registered_and_aliased(self):
        self.assertIn("hvtald", REGISTRY)
        for alias in ("z988", "即梦国际", "hv"):
            self.assertEqual(resolve_id(alias), "hvtald")

    # -- 凭据：用户把客服给的东西原样粘进 API Key 就该能用 ----------------
    def test_creds_accept_json_and_kv(self):
        for raw in (FULL, KV):
            c = parse_creds(raw)
            self.assertEqual(c["device_id"], "MMTVTCALD")
            self.assertEqual(c["token"], "TOK")
            self.assertEqual(c["webdav_url"], "http://fo/webdav/x")
            self.assertEqual(c["user"], "U")
            self.assertEqual(c["password"], "P")

    def test_creds_fall_back_to_env(self):
        import os
        os.environ["HVTALD_TOKEN"] = "FROM_ENV"
        try:
            self.assertEqual(parse_creds("")["token"], "FROM_ENV")
        finally:
            del os.environ["HVTALD_TOKEN"]

    def test_missing_creds_fail_before_paying(self):
        p = HvtaldProvider(api_key="deviceId=only")
        ok, missing = p.ready()
        self.assertFalse(ok)
        with self.assertRaises(ApiError) as raised:
            p.generate_video(VideoTask(prompt="x", refs=URLS), "o.mp4")
        self.assertEqual(raised.exception.kind, TASK_FATAL)

    # -- 核心适配：比例要写在 prompt 最前面 -----------------------------
    def test_ratio_is_prepended_to_prompt(self):
        """这家没有比例字段。调用方照常设 ratio，本类替他拼到提示词开头 ——
        不然每个上层都得记住这条怪癖，迟早有人忘。"""
        p = HvtaldProvider(api_key=FULL)
        b = p.build_body(VideoTask(prompt="跳女团舞", ratio="16:9", refs=URLS))
        self.assertTrue(b["prompt"].startswith("16:9 "))
        self.assertNotIn("ratio", b)             # 确实没有这个字段
        self.assertNotIn("aspect_ratio", b)

    def test_ratio_not_doubled_if_user_already_wrote_it(self):
        p = HvtaldProvider(api_key=FULL)
        b = p.build_body(VideoTask(prompt="9:16 已经写了", ratio="16:9", refs=URLS))
        self.assertEqual(b["prompt"], "9:16 已经写了")

    # -- body 形状 -----------------------------------------------------
    def test_body_shape(self):
        p = HvtaldProvider(api_key=FULL)
        b = p.build_body(VideoTask(prompt="x", refs=URLS), action_id="a" * 24)
        self.assertEqual(b["deviceId"], "MMTVTCALD")
        self.assertEqual(b["token"], "TOK")
        self.assertEqual(b["imgs"], URLS)        # 字符串数组
        self.assertEqual(b["webDavUrl"], "http://fo/webdav/x")
        self.assertEqual(b["user"], "U")
        self.assertEqual(b["password"], "P")
        self.assertEqual(len(b["actionId"]), 24)

    def test_action_id_is_24_lowercase(self):
        """文档请求表写 32 位、回调表写 24 位，自相矛盾；示例是 24 位小写。"""
        p = HvtaldProvider(api_key=FULL)
        aid = p.build_body(VideoTask(prompt="x", refs=URLS))["actionId"]
        self.assertEqual(len(aid), 24)
        self.assertTrue(aid.isalpha() and aid.islower())

    def test_refs_capped_at_nine(self):
        p = HvtaldProvider(api_key=FULL)
        b = p.build_body(VideoTask(prompt="x", refs=[f"https://cdn/{i}.png" for i in range(15)]))
        self.assertEqual(len(b["imgs"]), MAX_REFS)

    def test_local_refs_rejected_before_paying(self):
        p = HvtaldProvider(api_key=FULL)
        with self.assertRaises(ApiError) as raised:
            p.build_body(VideoTask(prompt="x", refs=["data:image/png;base64,AAA"]))
        self.assertEqual(raised.exception.kind, TASK_FATAL)

    def test_no_refs_rejected(self):
        p = HvtaldProvider(api_key=FULL)
        with self.assertRaises(ApiError):
            p.build_body(VideoTask(prompt="x", refs=[]))

    # -- 能力声明：别让前端给出这家做不到的选项 --------------------------
    def test_capabilities_report_only_fifteen_seconds(self):
        cap = HvtaldProvider().capabilities()["video"]
        self.assertEqual(cap["durations"], [FIXED_DURATION])
        self.assertEqual(cap["max_refs"], MAX_REFS)
        self.assertEqual(cap["ref_mode"], "url")

    def test_url_only(self):
        p = HvtaldProvider()
        self.assertTrue(p.needs_url("", "video"))
        self.assertFalse(p.needs_bytes(""))

    # -- 取片：只认以 actionId 开头的 mp4 --------------------------------
    def test_wait_picks_matching_mp4_only(self):
        aid = "jdbamfupzohjmbsnxsombhip"
        p = HvtaldProvider(api_key=FULL)
        # `_wait` 先找成片目录（`_find_outs`），再按**绝对地址**列 ——
        # 桩要跟着真签名走，不跟就是「测试挡住了调用形状的改动」。
        p._find_outs = lambda log=None: "http://dav/outs"
        p._list_url = lambda url, missing_ok=True, sub="": [
            ("other.mp4", "u1"),
            (f"{aid}.txt", "u2"),               # 同名 txt 不能当成成片
            (f"{aid}_ab-4785.mp4", "u3"),
        ]
        self.assertEqual(p._wait(aid, 1, 5, log=lambda *a: None), "u3")

    def test_wait_timeout_is_retryable_not_fatal(self):
        """排队等超时不是「参数错了」，任务还在，标 retryable 让上层可以再来取。"""
        p = HvtaldProvider(api_key=FULL)
        p._find_outs = lambda log=None: "http://dav/outs"
        p._list_url = lambda url, missing_ok=True, sub="": []
        with self.assertRaises(ApiError) as raised:
            p._wait("zzz", 1, 1, log=lambda *a: None)
        self.assertNotEqual(raised.exception.kind, TASK_FATAL)


if __name__ == "__main__":
    unittest.main()


class OutsDirTests(unittest.TestCase):
    """成片目录不存在，要**当场说**，不是等到超时。

    用户问（2026-08-21）：「为什么我发起了 HVTALD 任务却在 used 里面空空如也」。
    截图里的路径是 `conf/sd2_HVTALD_0818/5051/used` —— 那是「投文件式」用法
    里服务端取走配置后挪过去的地方。我们走的是 HTTP 接口，那个目录永远是空的。

    但查它的时候撞到一个真问题：`_list` 把 404 当成「目录是空的」。
    于是 webdav 地址填到错的层级时（填成 `.../5051` 而 `outs` 在别处），
    我们会一直轮询一个不存在的目录直到超时，然后报「等了 N 秒没看到成片」——
    而那句话指向完全错的方向（人会去查线路余量）。
    """

    KEY = ("deviceId=d;token=t;"
           "webdav=http://dav.x/project/pro_test/conf/sd2_HVTALD_0818/5051;"
           "user=u;password=p")

    def _prov(self, status):
        import requests
        p = HvtaldProvider(api_key=self.KEY)

        class R:
            status_code = status
            content = b"<d:multistatus xmlns:d='DAV:'></d:multistatus>"
        self._old = requests.request
        requests.request = lambda *a, **k: R()
        self.addCleanup(lambda: setattr(requests, "request", self._old))
        return p

    def test_a_missing_outs_dir_is_reported_at_once(self):
        """★ 以前要等满 poll_timeout（默认 2400 秒）才说话，而且说错。

        现在先把整个空间找一遍（往上剪到根 + 往下看一层），
        真的哪儿都没有才报 —— 并且把试过的路径列出来。
        """
        p = self._prov(404)
        with self.assertRaises(ApiError) as e:
            p._wait("abc123", 1, 5, log=lambda m: None)
        msg = str(e.exception)
        self.assertIn("找不到成片目录", msg)
        self.assertIn("已经试过", msg)
        self.assertIn("5051", msg, "得把它填的那一层也列出来")

    def test_it_is_task_fatal_not_a_retry(self):
        """★ 重试一个不存在的目录只会把同一件事重复几遍。"""
        p = self._prov(404)
        with self.assertRaises(ApiError) as e:
            p._wait("abc", 1, 5, log=lambda m: None)
        self.assertEqual(getattr(e.exception, "kind", ""), "task_fatal")

    def test_later_rounds_tolerate_a_hiccup(self):
        """★ 第一圈严格、之后宽松：目录被临时挪走或网络抖一下不该判死这一条。"""
        p = self._prov(404)
        self.assertEqual(p._list("outs", missing_ok=True), [])

    def test_the_timeout_message_says_used_is_not_the_place(self):
        """★ 超时那句话要顺手挡住这个误解，否则人会去翻 used/。"""
        import requests
        p = HvtaldProvider(api_key=self.KEY)

        class R:
            status_code = 207
            content = b"<d:multistatus xmlns:d='DAV:'></d:multistatus>"
        old = requests.request
        requests.request = lambda *a, **k: R()
        self.addCleanup(lambda: setattr(requests, "request", old))
        with self.assertRaises(ApiError) as e:
            p._wait("abc", 1, 2, log=lambda m: None)
        msg = str(e.exception)
        self.assertIn("used/` 不是成片目录", msg)
        self.assertIn("abc", msg, "actionId 要留给人稍后取")


class FindOutsTests(unittest.TestCase):
    """`outs/` 在哪一层是**程序的活**，不是用户填的时候要数的。

    用户问（2026-08-21）：「这个生产的层级是我填写的时候要填的吗」。不该。
    客服给的地址可能指到线路目录（实遇 `/project/pro_test/conf/
    sd2_HVTALD_0818/5051`）、`conf` 上面、或者空间根 —— 而哪一层有 `outs`
    是一次 PROPFIND 就能查出来的事。

    **不靠 URL 里的 `..`**：那要服务端自己规范化，而不少自建 WebDAV 不做，
    结果是 404 —— 而我们会当成「这一层没有」，把一个本来找得到的目录
    判成找不到。自己裁路径是确定的。
    """

    BASE = "http://dav.x/project/pro_test/conf/sd2_HVTALD_0818/5051"
    KEY = ("deviceId=d;token=t;webdav={};user=u;password=p")

    def _prov(self, exists, dirs=b""):
        import requests
        from core.providers.hvtald import HvtaldProvider as H

        def req(method, url, **k):
            class R:
                status_code = 207 if url.rstrip("/") in exists else 404
                content = dirs or b'<d:multistatus xmlns:d="DAV:"/>'
            return R()
        old = requests.request
        requests.request = req
        self.addCleanup(lambda: setattr(requests, "request", old))
        return H(api_key=self.KEY.format(self.BASE))

    def test_outs_at_the_configured_level(self):
        p = self._prov({self.BASE + "/outs"})
        self.assertEqual(p._find_outs(lambda m: None), self.BASE + "/outs")

    def test_outs_three_levels_up(self):
        """★ 客服给的是线路目录，而 outs 在项目那一层 —— 这是实遇的形状。"""
        want = "http://dav.x/project/pro_test/outs"
        p = self._prov({want})
        self.assertEqual(p._find_outs(lambda m: None), want)

    def test_outs_at_the_space_root(self):
        """★ 写死「往上 4 层」正好差一层到不了根 —— 而根恰好可能是它。"""
        p = self._prov({"http://dav.x/outs"})
        self.assertEqual(p._find_outs(lambda m: None), "http://dav.x/outs")

    def test_it_says_where_it_found_it(self):
        """★ 不说的话，人不知道自己填的地址其实不对（只是被兜住了）。"""
        logs = []
        self._prov({"http://dav.x/project/pro_test/outs"})._find_outs(logs.append)
        self.assertTrue(any("上 3 层" in m for m in logs), logs)
        self.assertTrue(any("不用改" in m for m in logs), logs)

    def test_it_never_uses_dotdot_in_the_url(self):
        """★ URL 里的 `..` 要服务端规范化，不少自建 WebDAV 不做。"""
        p = self._prov(set())
        p._find_outs(lambda m: None)
        self.assertTrue(p._tried)
        for u in p._tried:
            self.assertNotIn("..", u)

    def test_it_stops_at_the_root(self):
        """★ 剪过头是同一个地址，白探 —— 而每一次 PROPFIND 都要等。"""
        p = self._prov(set())
        p._find_outs(lambda m: None)
        self.assertEqual(len(p._tried), len(set(p._tried)), "有重复的探测")
        self.assertEqual(p._tried[-1], "http://dav.x/outs")

    def test_it_lists_everything_it_tried_when_it_fails(self):
        """★ 只说「找不到」等于让人自己猜；列出来他一眼看得出该填哪个。"""
        p = self._prov(set())
        self.assertEqual(p._find_outs(lambda m: None), "")
        self.assertIn(self.BASE + "/outs", p._tried)

    def test_the_answer_is_cached(self):
        """★ 每轮轮询重找一遍 = 每 30 秒六次 PROPFIND，白等。"""
        p = self._prov({self.BASE + "/outs"})
        first = p._find_outs(lambda m: None)
        p._tried = ["哨兵"]
        self.assertEqual(p._find_outs(lambda m: None), first)
        self.assertEqual(p._tried, ["哨兵"], "又找了一遍")


class SelftestTests(unittest.TestCase):
    """「运行自检」对这一家以前是**假绿灯**。

    用户问（2026-08-21）：「我填写 webdav 是不是有问题应该填写空间地址吗」。
    而自检本来该能回答这个 —— 可它统一调 `list_models()`，
    这一家的模型是固定一个、不联网：WebDAV 地址全错、账号密码全错，
    自检照样显示「1 个模型」。

    **假绿灯比没有检查更糟**：人会信它，然后去查别的地方。
    """

    BASE = "http://dav.x/project/pro_test/conf/sd2_HVTALD_0818/5051"
    KEY = "deviceId=d;token=t;webdav={};user=u;password=p"

    def _run(self, key, codes):
        import requests
        from core.providers.hvtald import HvtaldProvider as H

        def req(method, url, **k):
            class R:
                status_code = codes.get(url.rstrip("/"), 404)
                content = b'<d:multistatus xmlns:d="DAV:"/>'
            return R()
        old = requests.request
        requests.request = req
        self.addCleanup(lambda: setattr(requests, "request", old))
        return H(api_key=key).selftest()

    def test_incomplete_creds_are_named(self):
        r = self._run("deviceId=d;token=t", {})
        self.assertFalse(r["ok"])
        self.assertIn("webdav_url", r["msg"])

    def test_a_404_suggests_the_web_ui_address_mistake(self):
        """★ 最常见的填错就是这个：网页版的地址不是 WebDAV 地址。"""
        r = self._run(self.KEY.format(self.BASE), {})
        self.assertFalse(r["ok"])
        self.assertIn("网页版", r["msg"])

    def test_a_401_says_it_is_the_password(self):
        """★ 401 和 404 混成一句的话，「地址错」和「密码错」看起来一样。"""
        r = self._run(self.KEY.format(self.BASE), {self.BASE: 401})
        self.assertFalse(r["ok"])
        self.assertIn("401", r["msg"])

    def test_success_reports_where_outs_is(self):
        r = self._run(self.KEY.format(self.BASE),
                      {self.BASE: 207, self.BASE + "/outs": 207})
        self.assertTrue(r["ok"])
        self.assertIn("/outs", r["msg"])

    def test_it_says_when_outs_is_not_where_you_typed(self):
        """★ 不说的话，人不知道自己填的其实不是那一层（只是被兜住了）。"""
        r = self._run(self.KEY.format(self.BASE),
                      {self.BASE: 207,
                       "http://dav.x/project/pro_test/outs": 207})
        self.assertTrue(r["ok"])
        self.assertIn("不在你填的那一层", r["msg"])

    def test_connected_but_no_outs_anywhere(self):
        r = self._run(self.KEY.format(self.BASE), {self.BASE: 207})
        self.assertFalse(r["ok"])
        self.assertIn("找不到 `outs/`", r["msg"])

    def test_the_endpoint_prefers_the_providers_own_selftest(self):
        """★ 不接的话后端改了也白改 —— 页面上按的还是那个假绿灯。"""
        import io as _io
        import os as _os
        src = _io.open(_os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
            "server", "app.py"), encoding="utf-8").read()
        i = src.index('"/api/selftest"')
        blk = src[i:i + 1400]
        self.assertIn("prov.selftest()", blk)
        self.assertLess(blk.index("prov.selftest()"), blk.index("list_models()"),
                        "先拉模型列表就轮不到专门自检了")

    def test_providers_without_one_fall_back(self):
        """别家没有专门自检 —— 回落到拉模型列表，别把它们弄坏。"""
        from core.providers.paisio import PaisioProvider
        self.assertIsNone(PaisioProvider(api_key="sk-x").selftest())
