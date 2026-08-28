# -*- coding: utf-8 -*-
"""**接口地址不是结果。**

起因：ComfyUI 那边超模图生图报了一句「返回 1 项结果，但一项都解析不成图片，
首项开头 /v1/images/edits」。查下来不是超模的问题，是解析器的问题 ——
网关出错时把请求路径原样写进正文（OpenAI 风格的
`Invalid URL (POST /v1/images/edits)` 最常见），兜底解析器把它当成一张图捞了回来。

后果有两层，第二层才是真麻烦：

  1. `items` 变成非空 → `_pick_images` 里 `if items: return` 直接命中，
     **异步任务再也不去轮询了**；
  2. 报错变成「解析不出图」，**网关真正说的那句话被顶掉**，
     看日志的人会去查线路、查余额，查不到任何东西。

第二层是这个项目最贵的那类 bug：不报错的错，或者报一个指向完全错方向的错。

反过来也要守住：`/v1/videos/{id}/content` 是小裴、阿珂、好漫剧、灵感鸭、巨轮
**真正的下载地址**。第一版修复里我把 `content` 也写进了动作词黑名单，
那会把成片链接本身当噪音扔掉 —— 表现是「任务成功但没拿到文件」。
所以下面正反两个方向都锁。
"""
import unittest

from core.apiutil import _is_api_endpoint, extract_image_items

# 网关回一句话、HTTP 200，正文里带着刚才请求的那个端点
MASKED_ERRORS = [
    ("裸相对路径",
     {"error": {"message": "Invalid URL (POST /v1/images/edits)"}}),
    ("报错里带完整 URL",
     {"error": {"message": "Invalid URL (POST https://www.chaomoapi.com/v1/images/edits)"}}),
    ("request id 里夹着接口地址",
     {"error": {"message": "当前分组下无可用渠道 "
                           "(request id: 20260828 https://api.x.com/v1/images/generations)"}}),
    ("端点写在 action 字段里",
     {"task_id": "t-1", "status": "pending",
      "action": "https://www.chaomoapi.com/v1/images/edits"}),
    ("data[] 里放的是端点",
     {"data": [{"url": "https://api.x.com/v1/images/edits"}]}),
    ("端点在 docs_url 里",
     {"error": {"message": "参数不对"}, "docs_url": "https://docs.x.com/v1/images/generations"}),
]

# 这些是真结果，一个都不许误杀
REAL_RESULTS = [
    ("带扩展名", {"data": [{"url": "https://cdn.chaomoapi.com/out/9f8a.png"}]},
     "https://cdn.chaomoapi.com/out/9f8a.png"),
    ("无扩展名但路径里有 images", {"data": [{"url": "https://cdn.x.com/images/9f8a7b"}]},
     "https://cdn.x.com/images/9f8a7b"),
    ("带 query 的大写扩展名", {"data": [{"url": "https://cdn.x.com/a/b.JPEG?sig=1"}]},
     "https://cdn.x.com/a/b.JPEG?sig=1"),
    ("内嵌 base64", {"data": [{"b64_json": "iVBORw0KGgo="}]},
     "data:image/png;base64,iVBORw0KGgo="),
    ("/content 下载口（五家在用）",
     {"data": [{"url": "https://api.aicopy.top/v1/videos/t-9/content"}]},
     "https://api.aicopy.top/v1/videos/t-9/content"),
    ("markdown 图片嵌在正文里",
     {"choices": [{"message": {"content": "好了 ![img](https://cdn.x/out.png)"}}]},
     "https://cdn.x/out.png"),
]


class EndpointIsNotAResult(unittest.TestCase):
    def test_masked_errors_yield_nothing(self):
        """★ 捞出一项假的比捞不到更糟：轮询被跳过，真正的报错被顶掉。"""
        for name, body in MASKED_ERRORS:
            with self.subTest(name):
                self.assertEqual(extract_image_items(body), [], name)

    def test_real_results_survive(self):
        for name, body, want in REAL_RESULTS:
            with self.subTest(name):
                self.assertIn(want, extract_image_items(body), name)

    def test_content_must_not_be_treated_as_an_endpoint(self):
        """`/content` 是五家真正的下载地址，挡掉它等于把成片扔了。"""
        self.assertFalse(_is_api_endpoint("https://api.aicopy.top/v1/videos/t-9/content"))
        self.assertFalse(_is_api_endpoint("https://api.paisio.online/v1/images/x/content"))

    def test_bare_endpoints_are_recognised(self):
        for u in ("/v1/images/edits", "/v1/images/generations", "/v1/videos",
                  "/v1/video/generations", "/v1/chat/completions", "/v1/models",
                  "https://host/v1/images/edits", "https://host/v1/images/edits/"):
            with self.subTest(u):
                self.assertTrue(_is_api_endpoint(u), u)

    def test_ids_after_the_action_word_are_not_endpoints(self):
        """动作词后面还跟着东西的，是资源、不是端点。"""
        for u in ("https://host/v1/files/abc123",
                  "https://host/v1/videos/t-1/content",
                  "https://host/v1/images/task_abc/result.png"):
            with self.subTest(u):
                self.assertFalse(_is_api_endpoint(u), u)


if __name__ == "__main__":
    unittest.main()
