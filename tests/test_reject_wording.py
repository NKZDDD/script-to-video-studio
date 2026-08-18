# -*- coding: utf-8 -*-
"""各家说「内容不让过」的方式差很远 —— 认不出就等于这个功能不存在。

实跑（章鱼哥出图，EP01-SEG01/02/03 连挂）：

    这次没成功: 任务失败：{"status":"failed","error":{"code":"upstream_error",
      "message":"非常抱歉，该提示可能违反了关于暴力内容的防护限制。
       如果你认为此判断有误，请重试或修改提示语。"}}
    第 2 次重试（最多 2 次）
    状态栏：没见过的错误

两处都漏了，而且互相独立：

  1. **认不出是内容问题。** 关键词表里有「违规」没有「违反」，
     也没有「暴力」—— 判成 UNKNOWN，于是自动改写提示词那一层
     （soften.py）**看都没看一眼**。
  2. **就算认出来也会被重试。** 轮询式服务商是「HTTP 200 + 任务状态
     failed」，我们抛的 ApiError status 是 0，而 0 在 classify 里属于
     「可重试」并且排在内容关键词**前面** —— 于是原样重发两次，
     每次重新出一张图，拿回同一句拒绝。

这份用例把两处都钉住。
"""
import unittest

from core import apiutil as A, diagnose, soften
from core.apiutil import RETRYABLE, TASK_FATAL, ApiError, classify

# 实跑收到的原文，一字不改
REAL = ('任务失败: {"status":"failed","error":{"code":"upstream_error",'
        '"message":"非常抱歉，该提示可能违反了关于暴力内容的防护限制。'
        '如果你认为此判断有误，请重试或修改提示语。"},"created_at":1786936}')

WORDINGS = [
    REAL,
    "非常抱歉，生成的图片可能违反了关于暴力内容的防护限制",
    "该内容包含血腥描写，已被安全策略拦截",
    "content policy violation: graphic violence",
    "Your request was blocked by our safety system",
    "输入内容涉嫌违规，请修改提示词后重试",
    "图片审核未通过",
    "This prompt was flagged as sensitive",
    "包含色情或裸露内容，不予生成",
    "内容政策不允许该描述",
]


class RecogniseTests(unittest.TestCase):

    def test_the_real_one_is_recognised(self):
        """★ 这就是漏掉的那一句。"""
        self.assertEqual(diagnose.code_of(REAL), "CONTENT_REJECTED")

    def test_all_the_common_wordings_are(self):
        for msg in WORDINGS:
            self.assertEqual(diagnose.code_of(msg), "CONTENT_REJECTED", msg)

    def test_the_softening_layer_sees_them(self):
        """★ 认出来才会自动改写重发 —— 这是这个功能的入口。"""
        for msg in WORDINGS:
            self.assertTrue(soften.is_content_rejection(RuntimeError(msg)), msg)

    def test_unrelated_failures_are_not_swept_in(self):
        """★ 别认过头：拿别的错去改提示词是白改，还多花一次调用。"""
        for msg in ("HTTP 524（等了 127 秒）: A timeout occurred",
                    "No available image quota. Please try again later.",
                    "参考图文件不存在: ST001.png",
                    "余额不足，请充值",
                    "rate limit exceeded",
                    "任务失败: {\"status\":\"failed\",\"error\":"
                    "{\"message\":\"internal server error\"}}"):
            self.assertNotEqual(diagnose.code_of(msg), "CONTENT_REJECTED", msg)
            self.assertFalse(soften.is_content_rejection(RuntimeError(msg)), msg)


class ScriptVocabularyTests(unittest.TestCase):
    """剧本里的词不能当判词。

    短剧天天写打人、流血、「你违反了约定」。而不少服务商会把整段提示词
    **原样回显**在报错里 —— 一旦把「暴力」「违反」这类内容名词当成触发词，
    一个网络错误就会因为台词而被判成内容审核：不再重试（内容问题按不可
    重试处理），还白跑几轮改写。

    分界线：平台说话的方式和剧本写人的方式不一样。
    「防护限制」「审核未通过」「修改提示语」不会出现在台词里。
    """

    SCRIPTY = [
        "李想一刀捅进林南桥的腹部，血从指缝间涌出",
        "他违反了约定，深夜才回来",
        "她说：你这是家庭暴力",
        "画面：地面上的血迹与打斗留下的痕迹",
        "两人爆发激烈冲突，场面血腥",
        "台词：这份社区规定我看过",
    ]

    def test_script_lines_alone_are_not_a_rejection(self):
        """★ 光是台词不能触发。"""
        for line in self.SCRIPTY:
            self.assertNotEqual(diagnose.code_of(line), "CONTENT_REJECTED", line)

    def test_a_network_error_that_echoes_the_script_is_not_a_rejection(self):
        """★ 这就是那个洞：报错里回显了提示词。"""
        prompt = "\n".join(self.SCRIPTY)
        msg = f"网络错误: connection reset\n发送的内容：{prompt}"
        self.assertNotEqual(diagnose.code_of(msg), "CONTENT_REJECTED")
        self.assertFalse(soften.is_content_rejection(RuntimeError(msg), prompt))

    def test_the_echo_is_stripped_before_judging(self):
        """★ 第二道保险：万一台词真命中了判词，先把回显剔掉再判。"""
        prompt = "台词：你的行为违反了公司规定，我必须上报。"
        msg = f"HTTP 500 internal error. prompt={prompt}"
        self.assertEqual(diagnose.code_of(msg), "CONTENT_REJECTED",
                         "不剔的话确实会误判 —— 这正是要剔的理由")
        self.assertFalse(soften.is_content_rejection(RuntimeError(msg), prompt),
                         "剔掉回显之后就不该再命中")

    def test_a_real_rejection_that_also_echoes_the_prompt_still_counts(self):
        """★ 别剔过头：服务商自己那句话必须留下来。"""
        prompt = "李想一刀捅进林南桥的腹部"
        msg = (f"任务失败：该提示可能违反了关于暴力内容的防护限制。"
               f"请重试或修改提示语。原始提示：{prompt}")
        self.assertTrue(soften.is_content_rejection(RuntimeError(msg), prompt))

    def test_stripping_without_a_prompt_changes_nothing(self):
        self.assertEqual(soften._strip_echo("abc", ""), "abc")

    def test_short_lines_are_not_stripped(self):
        """太短的行到处都是，剔了会把服务商的话也剔花。"""
        self.assertIn("防护限制",
                      soften._strip_echo("违反了防护限制", "违反\n限制"))


class StructuredCodeTests(unittest.TestCase):
    """有码就认码，别去猜措辞。

    措辞会变、会翻译、会本地化；码是给程序看的。以前 `poll()` 把整个响应
    `json.dumps` 成一句话抛出去 —— `error.code` 就此变成字符串里的一段文本，
    判断只剩「搜关键词」一条腿，然后就漏了。
    """

    def test_a_known_code_decides_it_without_reading_the_text(self):
        """★ 文案完全看不懂也该判对。"""
        exc = A.task_failed({"status": "failed",
                             "error": {"code": "content_policy_violation",
                                       "message": "Ez a tartalom nem megengedett"}})
        self.assertEqual(exc.err_code, "content_policy_violation")
        self.assertEqual(exc.kind, TASK_FATAL)
        self.assertEqual(diagnose.build(exc)["code"], "CONTENT_REJECTED")
        self.assertTrue(soften.is_content_rejection(exc))

    def test_quota_and_auth_codes_too(self):
        for code, want in (("insufficient_quota", "QUOTA_EXHAUSTED"),
                           ("invalid_api_key", "AUTH_INVALID"),
                           ("rate_limit_exceeded", "RATE_LIMITED")):
            exc = A.task_failed({"status": "failed", "error": {"code": code}})
            self.assertEqual(diagnose.build(exc)["code"], want, code)

    def test_a_generic_code_falls_back_to_the_text(self):
        """★ 这次那一家给的就是 upstream_error —— 什么错都用它。

        认了反而会把内容问题判成上游故障，所以通用码当没有。
        """
        exc = A.task_failed({"status": "failed", "error": {
            "code": "upstream_error",
            "message": "非常抱歉，该提示可能违反了关于暴力内容的防护限制。"}})
        self.assertEqual(exc.err_code, "", "upstream_error 不该被当成有效码")
        self.assertEqual(diagnose.build(exc)["code"], "CONTENT_REJECTED")

    def test_the_providers_own_sentence_comes_first(self):
        """★ 以前日志里是一整坨 JSON，真正那句话被埋在中间。"""
        exc = A.task_failed({"status": "failed", "created_at": 1786936,
                             "error": {"code": "upstream_error",
                                       "message": "该提示可能违反了防护限制"}})
        self.assertTrue(str(exc).startswith("任务失败：该提示可能违反了防护限制"),
                        str(exc))

    def test_the_code_is_shown_when_there_is_one(self):
        exc = A.task_failed({"status": "failed",
                             "error": {"code": "content_filter", "message": "no"}})
        self.assertIn("content_filter", str(exc))

    def test_a_shapeless_response_still_produces_something_readable(self):
        exc = A.task_failed({"status": "failed", "detail": [1, 2, 3]})
        self.assertIn("任务失败", str(exc))

    def test_the_code_survives_into_the_diagnosis(self):
        """★ 中间任何一层把它丢了，这条路就断了。"""
        import inspect
        self.assertIn('getattr(exc, "err_code"', inspect.getsource(diagnose.build))
        self.assertIn("err_code", inspect.getsource(soften.is_content_rejection))


class NoPointlessRetryTests(unittest.TestCase):
    """同一段提示词重发只会拿回同一句拒绝，每次还要重新出一张图。"""

    def test_a_polled_rejection_is_not_retryable(self):
        """★ status=0（轮询式服务商）以前会走「可重试」。"""
        self.assertEqual(classify(0, REAL), TASK_FATAL)

    def test_the_error_object_agrees(self):
        self.assertEqual(ApiError(REAL).kind, TASK_FATAL)

    def test_content_beats_the_status_code(self):
        """从哪个状态码回来的都一样 —— 内容问题跟状态码没关系。"""
        for st in (0, 200, 400, 422, 500, 502, 504):
            self.assertEqual(classify(st, REAL), TASK_FATAL, st)

    def test_real_transient_failures_still_retry(self):
        """★ 别拦过头：这些重试一次经常就过了。"""
        for st, msg in ((0, "connection reset by peer"),
                        (524, "A timeout occurred"),
                        (503, "Service temporarily unavailable"),
                        (429, "too many requests")):
            self.assertEqual(classify(st, msg), RETRYABLE, f"{st} {msg}")

    def test_money_problems_still_stop_the_whole_batch(self):
        from core.apiutil import BATCH_FATAL
        self.assertEqual(classify(0, "Insufficient balance, please recharge"),
                         BATCH_FATAL)

    def test_a_busy_pool_is_still_only_temporary(self):
        self.assertEqual(
            classify(429, "No available image quota. Please try again later."),
            RETRYABLE)


class CardTests(unittest.TestCase):

    def test_it_no_longer_shows_as_an_unknown_error(self):
        """★ 状态栏上那句「没见过的错误」就是这个。"""
        d = diagnose.build(ApiError(REAL))
        self.assertEqual(d["code"], "CONTENT_REJECTED")
        self.assertNotIn("没见过", d["title"])

    def test_switching_providers_is_still_not_suggested(self):
        """换家碰运气是在赌，而且各家尺度不同会让同一部剧风格不一致。"""
        self.assertFalse(diagnose.should_failover({"code": "CONTENT_REJECTED"}))


if __name__ == "__main__":
    unittest.main()
