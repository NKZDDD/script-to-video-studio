# -*- coding: utf-8 -*-
"""JSON 后面多出一截时，取前面那份完整的。

实跑撞到（n11 的 EP01-SEG05，连废三次）：

    结束原因=stop，服务商记账输出 5807 token
    原因：JSON 校验不过（第 3 次）：Extra data: line 110 column 3 (char 7893)

那份 JSON **是好的**：四个顶层键齐全、模型自己也认为写完了。它只是把
结尾的 `]` `}` 又多写了一遍：

    第一个完整 JSON 结束于 7890，后面还剩：'\\n  ]\\n}\\n'

而我们在围栏分支里直接 `json.loads` 整段、不做回退，于是判成校验失败，
重试三次、这一段废掉 —— 一个字都不缺的产出被扔了。

**但不能连截断一起放过。** 「写到一半」和「多写了一点」的修法完全相反：
前者要减小输出/拆小任务，后者什么都不用做。所以只有前面那份**完整**时
才走这条路，不完整照旧报截断。
"""
import json
import unittest

from core.llm import LLMError, extract_json

GOOD = {"episode": "EP01", "segment": "EP01-SEG05",
        "scstates": [{"scstate_id": "S1"}], "coverage_note": "x"}
BODY = json.dumps(GOOD, ensure_ascii=False, indent=2)


def fenced(inner):
    return "```json\n" + inner + "\n```"


class ExtraDataTests(unittest.TestCase):

    def test_a_doubled_tail_is_tolerated(self):
        """★ 这就是那三次白跑。"""
        self.assertEqual(extract_json(fenced(BODY + "\n  ]\n}\n")), GOOD)

    def test_a_clean_reply_still_works(self):
        self.assertEqual(extract_json(fenced(BODY)), GOOD)

    def test_prose_after_the_json_is_dropped(self):
        self.assertEqual(extract_json(fenced(BODY + "\n以上就是本段的状态图。")),
                         GOOD)

    def test_a_second_object_is_dropped(self):
        """两份 JSON 时取第一份 —— 第二份多半是它自己的复述。"""
        self.assertEqual(extract_json(fenced(BODY + "\n" + BODY)), GOOD)

    def test_it_says_what_it_threw_away(self):
        """★ 不说的话，模型每次都多吐一截也没人知道。"""
        said = []
        extract_json(fenced(BODY + "\n  ]\n}"), said.append)
        self.assertTrue(said)
        self.assertIn("丢弃", said[0])

    def test_nothing_is_said_when_there_is_nothing_extra(self):
        said = []
        extract_json(fenced(BODY), said.append)
        self.assertEqual(said, [])

    def test_a_truncated_reply_is_still_a_truncation(self):
        """★ 最要紧的一条：别把「写到一半」当成「多写了一点」。

        两者的修法完全相反 —— 认错了会让人去查一个不存在的问题。
        """
        with self.assertRaises(LLMError) as cm:
            extract_json(fenced(BODY[:len(BODY) // 2]))
        self.assertIn("括号始终没有闭合", str(cm.exception))

    def test_junk_before_the_json_is_not_silently_accepted(self):
        """开头就不是 JSON 的话，前面那份根本不存在 —— 该报截断/格式错。"""
        with self.assertRaises(LLMError):
            extract_json(fenced("这是一段说明文字，没有 JSON。"))

    def test_an_unfenced_reply_is_unaffected(self):
        self.assertEqual(extract_json(BODY), GOOD)

    def test_json_call_passes_its_log_in(self):
        """写了不接等于没写。"""
        import inspect

        from core.llm import LLM
        self.assertIn("extract_json(text, log, stop)", inspect.getsource(LLM.json_call))


if __name__ == "__main__":
    unittest.main()
