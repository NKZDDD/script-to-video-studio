# -*- coding: utf-8 -*-
"""巨轮 HM 渠道（`SD 2.x`）—— 第六种请求格式。

⚠ **模型名用平台上真实的那套**，不是文档里的内部名。2026-09-14 实拉
`/v1/models` 确认：文档写 `seedance_v2.5-301010`，平台上是带空格的
`SD 2.5-301010` —— 照文档抄的那五个选中就是 404。
映射是坐实的：`-101010` / `-301010` 那串数字就是文档能力表里的
图/视频/音频上限（10/10/10、30/10/10），一一对得上。

依据《HM Studio（seedance_v2.x）对接文档》。接口和全站一样，但有三件
别处没有的事，每一件的失败都是静默的：

  1. **参考图必须在正文里 `@Image1` 点名**，不点名的话上游把图当「首帧/尾帧」，
     **第 3 张之后直接忽略** —— 片子照出、少用几张图、一处不报错
  2. 字段名是 `images/videos/audios`，和这家 `url_media` 格式的
     `image_urls/video_urls/audio_urls` **不一样** —— 给错名字不报错，
     上游只是收不到素材
  3. `face` 传 `true` 会走上游的「旧抠脸」（T 形纯色打码），很难看，
     而它是合法 JSON、上游照收
"""
import json
import unittest

from core.apiutil import ApiError
from core.providers.base import VideoTask
from core.providers.julun import (FACE_MODES, PORTRAIT_ALTERNATIVES,
                                  PORTRAIT_GUARDED, SPEC, JulunProvider,
                                  face_field, unnamed_images)

# 文档第四节「各模型能力对照」整张表。抄在这里是为了「哪天有人手改了 SPEC，
# 这里会亮」—— 亮了就该回去对文档，而不是把这份跟着改。
DOC = {
    "SD 2.0":        {"dur": (4, 15), "img": 9,  "vid": 0,  "aud": 0,  "guard": False},
    "SD 2.0-933":    {"dur": (4, 15), "img": 9,  "vid": 3,  "aud": 3,  "guard": False},
    "SD 2.5":        {"dur": (4, 30), "img": 9,  "vid": 0,  "aud": 0,  "guard": True},
    "SD 2.5-101010": {"dur": (4, 30), "img": 10, "vid": 10, "aud": 10, "guard": True},
    "SD 2.5-301010": {"dur": (4, 30), "img": 30, "vid": 10, "aud": 10, "guard": True},
}


def _body(model, prompt="@Image1 @Image2 两人对话", n_img=2, n_vid=0, n_aud=0,
          sec=10, ratio="16:9", extra=None):
    p = JulunProvider(api_key="k")
    t = VideoTask(prompt=prompt, refs=[f"https://x/{i}.png" for i in range(n_img)],
                  duration=sec, ratio=ratio, model=model,
                  extra=dict(extra or {},
                             video_refs=[f"https://x/{i}.mp4" for i in range(n_vid)],
                             audio_refs=[f"https://x/{i}.wav" for i in range(n_aud)]))
    return p.build_video_body(t, log=lambda *a: None)


class JulunHmTests(unittest.TestCase):
    def test_the_spec_matches_the_doc(self):
        """★ 规格表和文档第四节逐条对上。"""
        for m, want in DOC.items():
            self.assertIn(m, SPEC, f"{m} 没进规格表 —— 页面上选不到")
            fmt, rule, res, _ratios, ci, cv, ca, _note = SPEC[m]
            self.assertEqual(fmt, "hm", m)
            self.assertEqual((rule[0], rule[1]), want["dur"], m)
            self.assertEqual((ci, cv, ca), (want["img"], want["vid"], want["aud"]), m)
            # **分辨率只有 720p**，传别的会被拒或忽略
            self.assertEqual(res, ["720p"], m)
        self.assertEqual(set(PORTRAIT_GUARDED),
                         {m for m, w in DOC.items() if w["guard"]})

    def test_the_body_matches_the_doc_example(self):
        """★ 字段名要和 `url_media` 分清楚。

        那边是 `image_urls/video_urls/audio_urls`，这边是 `images/videos/audios`。
        给错名字**不报错** —— 上游只是收不到素材，片子照出、没用参考图。
        """
        path, b = _body("SD 2.0", "@Image1 中的人物向镜头走来", n_img=1)
        self.assertEqual(path, "/v1/videos")
        self.assertEqual(b, {"model": "SD 2.0",
                             "prompt": "@Image1 中的人物向镜头走来",
                             "seconds": 10, "ratio": "16:9", "resolution": "720p",
                             "images": ["https://x/0.png"]})
        for wrong in ("image_urls", "video_urls", "audio_urls", "image_refs"):
            self.assertNotIn(wrong, b)

    def test_media_field_names_for_all_three_kinds(self):
        _, b = _body("SD 2.5-101010",
                     "@Image1 按 @Audio1 的语气说话", n_img=1, n_vid=1, n_aud=1)
        self.assertEqual(sorted(k for k in b if k in
                                ("images", "videos", "audios")),
                         ["audios", "images", "videos"])

    def test_every_image_must_be_named_in_the_prompt(self):
        """★ 不点名就丢图，而且**不报错** —— 所以发之前就拦。"""
        # 文档的正确写法
        _body("SD 2.0", "@Image1 是女主角，@Image2 是男主角", n_img=2)
        # 文档的两个错误写法
        for prompt, n, why in (
            ("女主角和男主角在咖啡馆交谈", 2, "一张都没点名"),
            ("Image 1 是女主角", 1, "带空格 —— 文档明说匹配不上"),
            ("@Image1 是女主", 3, "只点了第 1 张"),
        ):
            with self.assertRaises(ApiError, msg=why) as c:
                _body("SD 2.0", prompt, n_img=n)
            self.assertIn("没点名", str(c.exception), why)

    def test_the_check_does_not_silently_append(self):
        """★ 只查缺，**不自动补**。

        自动补是鹤那边 `@图N` 的坑：材料的正文本来就用 `@Image1..N` 写好了
        身份映射，再追加一遍就是同一个请求里两套编号，而画面会照着错的那套走。
        """
        prompt = "@Image1 是女主角，@Image2 是男主角"
        _, b = _body("SD 2.0", prompt, n_img=2)
        self.assertEqual(b["prompt"], prompt, "正文被改写了")

    def test_at_image_matching_is_exact(self):
        """`@Image1` 不许命中 `@Image12`，空格写法不认。"""
        self.assertEqual(unnamed_images("@Image12 只有这个", 1), [1])
        self.assertEqual(unnamed_images("@Image1 @Image2", 2), [])
        self.assertEqual(unnamed_images("Image 1", 1), [1])
        self.assertEqual(unnamed_images("随便什么", 0), [])

    def test_face_true_is_refused(self):
        """★ `"face": true` 当场拒。

        文档专门标了千万别传 —— 那会走上游的「旧抠脸」（T 形纯色打码）。
        而 `true` 是合法 JSON、上游照收，所以只能我们挡。
        """
        with self.assertRaises(ApiError) as c:
            _body("SD 2.0", "@Image1", n_img=1, extra={"face": True})
        self.assertIn("旧抠脸", str(c.exception))

    def test_face_allows_exactly_what_the_doc_lists(self):
        self.assertEqual(face_field(None), {})                    # 不传 = 浅洗
        self.assertEqual(face_field({"enabled": False}), {"enabled": False})
        for mode in FACE_MODES:
            self.assertEqual(face_field({"enabled": True, "mode": mode}),
                             {"enabled": True, "mode": mode})
        for bad in ({"enabled": True}, {"enabled": True, "mode": "光滑"}, {"mode": "heavy"}):
            with self.assertRaises(ApiError, msg=str(bad)):
                face_field(bad)

    def test_caps_are_enforced_before_the_request(self):
        """超了就停 —— 发出去是服务商截掉多的，截掉的正是排在后面那几张。"""
        named = " ".join(f"@Image{i}" for i in range(1, 31))
        _body("SD 2.5-301010", named, n_img=30)            # 正好 30，放行
        with self.assertRaises(ApiError) as c:
            _body("SD 2.5", named[:200], n_img=10)
        self.assertIn("参考图最多 9 张", str(c.exception))
        with self.assertRaises(ApiError) as c:
            _body("SD 2.0", "@Image1", n_img=1, n_vid=1)
        self.assertIn("不支持参考视频", str(c.exception))

    def test_duration_is_corrected_out_loud(self):
        """v2.0 上限 15、v2.5 上限 30 —— 改了要说，悄悄改会让人对不上账。"""
        said = []
        p = JulunProvider(api_key="k")
        t = VideoTask(prompt="@Image1", refs=["https://x/0.png"], duration=20,
                      ratio="16:9", model="SD 2.0")
        _, b = p.build_video_body(t, log=said.append)
        self.assertEqual(b["seconds"], 15)
        self.assertTrue([s for s in said if "15" in s], "纠正了但没说")
        _, b = _body("SD 2.5", "@Image1", n_img=1, sec=30)
        self.assertEqual(b["seconds"], 30)

    def test_portrait_protection_points_at_the_way_out(self):
        """肖像保护本地判不了 —— 撞上了要把文档点名的三条出路接上。"""
        self.assertEqual(PORTRAIT_ALTERNATIVES,
                         ("sd2.5-9img", "sd2-mini", "SD 2.0"))
        import io
        import os
        src = io.open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "core", "providers", "julun.py"),
            encoding="utf-8").read()
        self.assertIn("肖像保护", src)
        self.assertIn("PORTRAIT_ALTERNATIVES", src)


if __name__ == "__main__":
    unittest.main()


class FallbackTests(unittest.TestCase):
    """表外的模型：**不知道 ≠ 不允许**，更不该拿编出来的数去改参数。

    实遇（2026-09-14）：`sd2.5-9img` 不在规格表里，走兜底
    `("url_media", (4, 15), ["720p"], R5, 9, 3, 3)` —— 那几个数是**代码里编的**，
    不属于任何一个模型。用户要 30 秒，被这个 4–15 悄悄改成 15 秒发出去。
    而那个模型平台卡上明写支持 **5/10/15/30 且四档同价** ——
    也就是白白短了一半、钱一分没省。日志里那句「已把 30 纠正为 15」
    读起来像是模型的限制，其实是我们自己的默认值。
    """

    def test_an_unlisted_model_keeps_its_parameters(self):
        """★ 表外的一秒都不改、一条都不拦。"""
        from core.providers.julun import is_known
        self.assertFalse(is_known("某个全新的模型"))
        logs = []
        p = JulunProvider(api_key="k")
        t = VideoTask(prompt="@Image1 正文",
                      refs=[f"https://x/{i}.png" for i in range(20)],
                      duration=45, ratio="21:9", model="某个全新的模型")
        _, b = p.build_video_body(t, log=logs.append)
        self.assertEqual(b["seconds"], 45, "时长被编出来的范围夹了")
        self.assertEqual(b["ratio"], "21:9", "比例被编出来的清单拦了")
        self.assertEqual(len(b["image_urls"]), 20, "张数被编出来的上限削了")
        self.assertTrue([l for l in logs if "不校验" in l],
                        "放行了但没说这一趟没校验")

    def test_the_fallback_declares_no_limits(self):
        """★ 兜底那条里不许再有具体数字。"""
        from core.providers.julun import _UNKNOWN, spec_of
        fmt, rule, res, ratios, ci, cv, ca, _note = spec_of("不存在的模型")
        self.assertIsNone(rule, "兜底又写了一个时长范围")
        self.assertEqual((res, ratios), ([], []))
        self.assertEqual((ci, cv, ca), (0, 0, 0))
        self.assertTrue(fmt, "总得给一个请求形状，否则发不出去")
        del _UNKNOWN

    def test_sd25_9img_matches_the_model_card(self):
        """★ `sd2.5-9img` 按平台模型卡进表。

        卡片原话：「SD2.5 原生过人脸（无需认证人脸）· 最多 9 张参考图 ·
        5/10/15/30 秒 · 默认 720p · 支持 16:9 / 9:16 / 4:3 / 3:4 / 1:1 ·
        固定按次 4.5 元/次（5/10/15/30 秒同价，不按帧叠加）」
        """
        fmt, rule, res, ratios, ci, cv, ca, note = SPEC["sd2.5-9img"]
        # 时长是**离散四档**，不是区间 —— 写成 (5, 30) 的话 7 秒会被放过去
        self.assertEqual(rule[2], [5, 10, 15, 30])
        self.assertEqual(res, ["720p"])
        self.assertEqual(sorted(ratios),
                         sorted(["16:9", "9:16", "4:3", "3:4", "1:1"]))
        self.assertEqual((ci, cv, ca), (9, 0, 0))
        self.assertIn("没有实拉背书", note, "格式是推的，得标出来")
        # 四档都要能原样发出去
        for want in (5, 10, 15, 30):
            p = JulunProvider(api_key="k")
            t = VideoTask(prompt="@Image1", refs=["https://x/0.png"],
                          duration=want, ratio="16:9", model="sd2.5-9img")
            _, b = p.build_video_body(t, log=lambda *a: None)
            self.assertEqual(b["seconds"], want, f"{want} 秒被改了")

    def test_the_portrait_way_out_is_actually_listed_now(self):
        """文档把 `sd2.5-9img` 当肖像保护的出路 —— 它得真在清单里，
        否则人照着提示换过去，又落回表外没校验那条路。"""
        from core.providers.julun import PORTRAIT_ALTERNATIVES, VIDEO_MODELS
        for m in PORTRAIT_ALTERNATIVES:
            self.assertIn(m, VIDEO_MODELS, m)
