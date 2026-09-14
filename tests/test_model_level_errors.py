# -*- coding: utf-8 -*-
"""「这个**模型**不行」和「这个**账户**不行」是两回事，而它们共用状态码。

实遇（2026-09-14，超模）：一个账号按能力分四把 Key，**每把能看见的模型完全
不重叠** —— 一把只有 `gpt-image-1k-th`，另一把只有 2.5 那六个＋Native 三个，
交集为空。拿错 Key 槽去发某个模型，回的是

    403 {"message": "model gpt-image-1k-th is not available for this API key"}

而 403 原来一律判 `batch_fatal` —— **立即熔断整批**：几百条任务当场停，
卡片写着「账户问题」，人会跑去充值。而真相只是模型选错了 / Key 槽用错了。

反向也有一条：`404 model not found` 原来落到 `retryable`，于是模型名打错会
同参重发，每次撞同一堵墙 —— 而巨轮那家的名字带空格和全角括号，打错是常事。
"""
import unittest

from core.apiutil import BATCH_FATAL, RETRYABLE, TASK_FATAL, classify


class ModelLevelErrorTests(unittest.TestCase):
    def test_model_not_available_for_this_key_is_not_account_death(self):
        """★ 模型没权限 ≠ 账户没钱。熔断整批的代价是几百条任务。"""
        for msg in (
            "model gpt-image-1k-th is not available for this API key",
            "model gpt-image2-4K-low is not available for this API key",
            "无权使用该模型",
            "当前 API Key 无权使用该模型",
        ):
            self.assertEqual(classify(403, msg), TASK_FATAL, msg)

    def test_model_not_found_is_not_retryable(self):
        """★ 名字打错了，重发一百次还是错。

        巨轮的模型名带空格、中文、全角括号（`grok-imagine-video-1.5（按次）`、
        `SD 2.5-301010`），打错是常事，而它回的是 404。
        """
        for msg in (
            "model not found. Call GET /v1/models with the current API Key",
            "模型不存在，请检查空格、大小写和完整 ID",
            "model xyz is not supported",
        ):
            self.assertEqual(classify(404, msg), TASK_FATAL, msg)

    def test_real_account_problems_still_break_the_batch(self):
        """★ 反过来不能放过：真的账户问题必须还是熔断。

        这条是上面那两条的安全网 —— 放宽 403 很容易把余额不足也放过去，
        而那种情况继续跑就是几百条连着失败。
        """
        for st, msg in ((403, "Account has been banned"),
                        (403, "Forbidden"),
                        (402, "insufficient balance"),
                        (401, "invalid api key"),
                        (401, "unauthorized")):
            self.assertEqual(classify(st, msg), BATCH_FATAL, f"{st} {msg}")

    def test_the_mixed_message_leans_to_the_cheaper_verdict(self):
        """巨轮的 403 文案把两种原因写在一句里：
        「账户余额不足，或者当前 API Key 无权使用该模型」。

        判 task_fatal —— 跳过这一条继续跑。真是余额不足的话，下一条也会失败，
        那时别的信号（余额关键词单独出现）会接手熔断；而反过来判熔断的话，
        一个选错的模型就停掉整批，那个代价是不可逆的。
        """
        self.assertEqual(
            classify(403, "账户余额不足，或者当前 API Key 无权使用该模型"),
            TASK_FATAL)

    def test_other_levels_are_untouched(self):
        self.assertEqual(classify(429, "rate limit"), RETRYABLE)
        self.assertEqual(classify(500, "server error"), RETRYABLE)
        self.assertEqual(classify(400, "该提示可能违反了关于暴力内容的防护限制"),
                         TASK_FATAL)

    def test_the_pattern_has_no_control_characters(self):
        """正则里混进真换行会让整条失效 —— 这个仓库踩过好几次。"""
        from core.apiutil import _MODEL_LEVEL
        self.assertEqual([c for c in _MODEL_LEVEL.pattern if ord(c) < 32], [])


if __name__ == "__main__":
    unittest.main()
