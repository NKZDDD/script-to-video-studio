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

    def test_wait_timeout_does_not_resubmit(self):
        """★ 超时**不重投**（2026-08-26 改的判断，原来是 retryable）。

        用户原话：「有调整提示词这个操作才要重试，如果没有就不需要重试」。
        超时这条路上提示词一个字都没变，同参重投只是换一个 actionId 再撞一次
        同样的墙，而**每次都算一次钱**。更难看的是取片按 actionId 前缀找 ——
        重投之后前一次的成片如果晚到，没人认领（WebDAV 只存 48 小时）。

        提示词被审核拒绝那条不受影响：那条走 soften 改写后重发，
        发出去的东西真的变了，才值得再花一次。
        """
        p = HvtaldProvider(api_key=FULL)
        p._find_outs = lambda log=None: "http://dav/outs"
        p._list_url = lambda url, missing_ok=True, sub="": []
        with self.assertRaises(ApiError) as raised:
            p._wait("zzz", 1, 1, log=lambda *a: None)
        exc = raised.exception
        self.assertEqual(exc.kind, TASK_FATAL)
        # 有自己的码：原来落到 UNKNOWN，于是 should_failover 判 True ——
        # 「换不换家」是猜出来的，不是定下来的。
        self.assertEqual(exc.err_code, "VIDEO_POLL_TIMEOUT")
        # 任务没丢这件事必须写在报文里，否则人会以为白花了钱
        self.assertIn("actionId", str(exc))

    def test_the_wall_is_shorter_than_the_generic_video_default(self):
        """★ 这一家的判定就是「outs/ 里有没有出现成片」，别的什么都不看，
        所以墙可以短。能短的前提是**排队在墙外**（账号池的槽位在调
        generate_video 之前拿）。别家排队几十分钟是常态，墙短了会把快出来的
        片子判成失败 —— 钱照花、东西没拿到。"""
        d = HvtaldProvider.poll_defaults
        self.assertLess(d["timeout"], 2400)


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
        """★ 以前要等满 poll_timeout（默认现在 1200 秒）才说话，而且说错。

        2026-08-28 起不再「把整个空间找一遍」—— 位置是固定的
        （`<填的地址>/outs`）。所以这条改成：**探的那一个地址要出现在报错里**，
        并且要说清该往哪层填。
        """
        p = self._prov(404)
        with self.assertRaises(ApiError) as e:
            p._wait("abc123", 1, 5, log=lambda m: None)
        msg = str(e.exception)
        # 这个 stub 全返 404 —— 连**填的那一层本身**都不存在，走的是
        # 「路径整段写错了」那一支（和「路径在、只是没有 outs」两回事，
        # 改法完全不同：前者改整条地址，后者只是层级填浅/填深了）。
        self.assertIn("本身就不存在", msg)
        self.assertIn("5051", msg, "得把它填的那个地址原样列出来")
        # 不管哪一支，都要说清 outs 在哪儿 / 该怎么找
        self.assertTrue("在这一层下面" in msg or "网页版翻一下" in msg, msg)
        self.assertEqual(e.exception.err_code, "HVTALD_OUTS_MISSING",
                         "没有码就落到 UNKNOWN，页面显示「没见过的错误」")

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
    """`outs/` 就在填的那一层下面。**2026-08-28 改了判断。**

    原来是「哪一层有 outs 是程序的活」，往上剪 8 层、往下看一层地找。
    用户否掉了：「理论上他的生产位置是固定的一个 outs，而不是要去找多层…
    为什么有这种八、九层的查找」。翻找的两种结果都不好 —— 白探十几次，
    或者探到另一条线路的同名 outs 然后一直等一个不会出现的成片。
    现在只探一次，不在那儿就把「该改哪儿」说清。

    下面几条原来断言「往上 3 层也能找到」，已按新判断改写。

    历史（保留着，因为报错文案还靠它）：客服给的地址可能指到线路目录
    （实遇 `/project/pro_test/conf/sd2_HVTALD_0818/5051`）—— 所以报错里要
    列出这一层有哪些子目录，人一眼看得出该往哪层填。

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

    def test_outs_up_the_tree_is_not_used(self):
        """★ 上层有 outs 也**不认** —— 那多半是别条线路的。

        原来这一条断言「往上 3 层也能找到」。去掉翻找之后，
        上层那个 outs 正是最危险的一种命中：成片永远不会出现在那儿，
        而程序会一直等到超时，报「等了 N 秒没看到」——
        比直接说「你填的这一层没有 outs」难查得多。
        """
        p = self._prov({"http://dav.x/project/pro_test/outs"})
        self.assertEqual(p._find_outs(lambda m: None), "")

    def test_the_space_root_is_not_used_either(self):
        p = self._prov({"http://dav.x/outs"})
        self.assertEqual(p._find_outs(lambda m: None), "")

    def test_it_probes_only_the_configured_level(self):
        """★ 只探一次。每一次 PROPFIND 都要等，而这发生在每条视频出片之前。"""
        p = self._prov(set())
        p._find_outs(lambda m: None)
        self.assertEqual(p._tried, [self.BASE + "/outs"])

    def test_it_never_uses_dotdot_in_the_url(self):
        """★ URL 里的 `..` 要服务端规范化，不少自建 WebDAV 不做。"""
        p = self._prov(set())
        p._find_outs(lambda m: None)
        self.assertTrue(p._tried)
        for u in p._tried:
            self.assertNotIn("..", u)

    def test_no_duplicate_probes(self):
        p = self._prov(set())
        p._find_outs(lambda m: None)
        self.assertEqual(len(p._tried), len(set(p._tried)), "有重复的探测")

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

    def test_outs_only_counts_at_the_configured_level(self):
        """★ 上层有 outs **不算通过**（原来算，还提示「程序自己找到的」）。

        改的原因：那个 outs 多半属于别条线路，成片永远不会出现在那儿 ——
        自检绿着、出片却一直等到超时，比自检直接说「这一层没有」难查得多。
        """
        r = self._run(self.KEY.format(self.BASE),
                      {self.BASE: 207,
                       "http://dav.x/project/pro_test/outs": 207})
        self.assertFalse(r["ok"])
        self.assertIn("没有 `outs/`", r["msg"])
        self.assertIn("上一层", r["msg"], "得说清该往哪层填")

    def test_connected_but_no_outs_here(self):
        """连得上、但这一层没有 outs。（文案从「整个空间里找不到」改成
        「这个地址下面没有」—— 因为不再翻整个空间了。）"""
        r = self._run(self.KEY.format(self.BASE), {self.BASE: 207})
        self.assertFalse(r["ok"])
        self.assertIn("没有 `outs/`", r["msg"])
        self.assertIn("上一层", r["msg"])

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


class OutsIsOneFixedLevelTests(unittest.TestCase):
    """★ `outs` 的位置是固定的：`<填的 webdav 地址>/outs`。不再翻目录。

    用户原话（2026-08-28）：「理论上他的生产位置是固定的一个 outs，
    而不是要去找多层…为什么有这种八、九层的查找」。

    原来往上剪 8 层、往下看一层地找。两种结果都不好：
      · 白探十几次 —— 每一次都是一个 WebDAV 请求，而这发生在每条视频的
        第一次轮询之前
      · **探到一个同名但不属于这条线路的 `outs`** —— 然后一直等一个永远
        不会出现在那儿的成片，等到超时。这比直接报「路径不对」难查得多。
    """

    def test_it_probes_exactly_one_url(self):
        p = HvtaldProvider(api_key=FULL)
        seen = []
        p._probe_url = lambda u: (seen.append(u), False)[1]
        p._child_dirs = lambda: ["a", "b", "c"]
        self.assertEqual(p._find_outs(), "")
        self.assertEqual(len(seen), 1, f"探了 {len(seen)} 次：{seen}")
        self.assertTrue(seen[0].endswith("/outs"), seen[0])

    def test_it_no_longer_climbs_or_descends(self):
        import inspect
        src = inspect.getsource(HvtaldProvider._find_outs)
        self.assertNotIn("_UP_LEVELS", src)
        self.assertNotIn("_child_dirs", src)

    def test_the_error_names_the_exact_url_and_what_to_change(self):
        """★ 不翻了，就必须把「该改哪儿」说清 —— 否则等于把翻找的活推给人，
        而他不知道该往哪层填。"""
        p = HvtaldProvider(api_key=FULL)
        p._probe_url = lambda u: False
        p._child_dirs = lambda: ["sd2_HVTALD_0818", "conf"]
        with self.assertRaises(ApiError) as e:
            p._wait("aid", 1, 1, log=lambda *a: None)
        msg = str(e.exception)
        # 填的那个地址要原样列出来 —— 人得看见程序探的到底是哪个 URL。
        # （断言从写死的 `sd2_HVTALD_0818` 改成这条测试自己配的地址：
        #   那个字符串是从另一条测试抄来的，这条用的是别的地址。）
        self.assertIn(p._up(0).rstrip("/"), msg)
        # 「去哪改」说得具体：直接给出该填的那个地址，或者明说
        # 「往上几层也都没有」—— 比让人自己去数层级有用
        self.assertTrue("把 WebDAV 地址改成它" in msg
                        or "网页版翻一下" in msg, msg)
        self.assertEqual(e.exception.kind, TASK_FATAL)   # 路径不对，重试无意义
        self.assertEqual(e.exception.err_code, "HVTALD_OUTS_MISSING")

    def test_it_caches_the_hit(self):
        """找到了就记住 —— 每轮轮询都重探一次是白花请求。"""
        p = HvtaldProvider(api_key=FULL)
        n = {"c": 0}

        def probe(u):
            n["c"] += 1
            return True

        p._probe_url = probe
        self.assertEqual(p._find_outs(), p._find_outs())
        self.assertEqual(n["c"], 1)


class SubmitNonJsonTests(unittest.TestCase):
    """★ 投递回了非 JSON 时，报错要带状态码和正文。

    用户实遇（2026-08-28）：日志里只有
        HVTALD 投递失败（http://ha.z988.top/dy/brush/fromApi）：
        Expecting value: line 1 column 1 (char 0)
    那句话是 `r.json()` 抛的 —— 不含状态码、不含它到底回了什么，
    看不出是网关 502 还是 base_url 填错回了个登录页。
    """

    class _R:
        def __init__(self, code, text):
            self.status_code, self.text = code, text

        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    def _submit(self, code, text):
        import core.providers.hvtald as M
        from core.providers.base import VideoTask
        p = HvtaldProvider(api_key=FULL)
        real = M.requests
        M.requests = type("X", (), {
            "post": staticmethod(lambda *a, **k: self._R(code, text))})()
        try:
            with self.assertRaises(ApiError) as e:
                p.generate_video(
                    VideoTask(prompt="p", refs=["https://x/a.png"], duration=15,
                              ratio="9:16", model="即梦国际版"),
                    "out.mp4", log=lambda *a: None)
            return e.exception
        finally:
            M.requests = real

    def test_it_reports_the_status_code(self):
        exc = self._submit(502, "<html>502 Bad Gateway</html>")
        self.assertIn("502", str(exc))
        self.assertIn("Bad Gateway", str(exc))

    def test_an_empty_200_says_the_gateway_is_probably_down(self):
        """★ 200 + 空正文和 502 是两种毛病，改法不同 —— 话要分开。"""
        exc = self._submit(200, "")
        self.assertIn("空", str(exc))
        self.assertIn("网关", str(exc))

    def test_html_hints_at_a_wrong_base_url(self):
        exc = self._submit(200, "<!doctype html><title>登录</title>")
        self.assertIn("base_url", str(exc))

    def test_it_is_retryable_not_fatal(self):
        """★ 网关抖动换个时间就好 —— 判死这一条等于把能救的活丢掉。"""
        self.assertEqual(self._submit(502, "x").kind, "retryable")

    def test_the_bare_parse_error_is_gone(self):
        """★ 这一句本身就是那个「什么都没说」的报错。"""
        exc = self._submit(200, "")
        self.assertNotIn("Expecting value", str(exc))
