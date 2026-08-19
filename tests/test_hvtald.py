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
        p._list = lambda sub="outs": [
            ("other.mp4", "u1"),
            (f"{aid}.txt", "u2"),               # 同名 txt 不能当成成片
            (f"{aid}_ab-4785.mp4", "u3"),
        ]
        self.assertEqual(p._wait(aid, 1, 5, log=lambda *a: None), "u3")

    def test_wait_timeout_is_retryable_not_fatal(self):
        """排队等超时不是「参数错了」，任务还在，标 retryable 让上层可以再来取。"""
        p = HvtaldProvider(api_key=FULL)
        p._list = lambda sub="outs": []
        with self.assertRaises(ApiError) as raised:
            p._wait("zzz", 1, 1, log=lambda *a: None)
        self.assertNotEqual(raised.exception.kind, TASK_FATAL)


if __name__ == "__main__":
    unittest.main()
