# -*- coding: utf-8 -*-
"""环节8 的正文完整性闸 —— 「字段在、内容空壳」那次实跑。

storyboard_prompt 只有 287 个字、十节只剩一节、Image 映射一行没有：
JSON 合法、必需字段全在 → schema 校验放行 → 静默落盘 → 出图按残段跑，
要到人工验收才发现。这道闸把它变成「带具体缺口的重试」，三次不过
该段显性失败 —— 不再静默。

三层各钉一条测试：
  ① s8_prompt_gaps：缺口判定（字数下限 / Image 映射行数）
  ② json_call 的 content_check：走反馈重试，反馈里带着缺口原话
  ③ run_segmented 接线：闸没过 → 该段失败、不落盘；闸过了 → 正常存
"""
import json
import shutil
import unittest

from core import stages as S
from core.llm import LLM, LLMError, _truncated
from test_v34_run import new_project


def _sb_text(n_refs, extra=""):
    """一份合格的故事板正文：够长 + 每张参考图一行映射。"""
    mapping = "".join(f"Image {i + 1} = C00{i + 1} 角色{i + 1}\n"
                      for i in range(n_refs))
    return ("一、输出结构：分格故事板……\n二、参考图角色映射：\n" + mapping
            + "五、空间与轴线……\n十、输出限制……" + "正文内容" * 200 + extra)


def _item(sb=None, vd=None, refs=("C001", "C002")):
    return {
        "storyboard_prompt": sb if sb is not None else _sb_text(len(refs)),
        "video_prompt": vd if vd is not None else "基础输出" * 200,
        "reference_order": [{"image_n": i + 1, "asset_id": a}
                            for i, a in enumerate(refs)],
    }


class GapDetectionTests(unittest.TestCase):
    """① 缺口判定 —— 这道闸拦的是数量级，不是措辞。"""

    def test_the_real_failure_is_caught(self):
        """★ 实跑那份：短正文 + 映射行缺失，两个缺口都要报出来。"""
        item = _item(sb="一、输出结构：生成一张分格故事板，按时间顺序排布。" * 4,
                     refs=("C001", "C002", "C003", "C004"))
        gaps = S.s8_prompt_gaps(item)
        self.assertTrue(any("storyboard_prompt" in g and "字" in g for g in gaps))
        self.assertTrue(any("4 行" in g and "0 行" in g for g in gaps))

    def test_good_output_passes(self):
        self.assertEqual(S.s8_prompt_gaps(_item()), [])

    def test_short_video_prompt_is_its_own_gap(self):
        gaps = S.s8_prompt_gaps(_item(vd="太短"))
        self.assertTrue(any("video_prompt" in g for g in gaps))

    def test_partial_mapping_lines_are_reported(self):
        """声明 3 张、正文只写了 1 行映射 —— 张冠李戴的温床。"""
        sb = _sb_text(1)                      # 只有 Image 1
        item = _item(sb=sb, refs=("C001", "C002", "C003"))
        gaps = S.s8_prompt_gaps(item)
        self.assertTrue(any("3 行" in g and "1 行" in g for g in gaps))

    def test_no_declared_refs_means_no_mapping_requirement(self):
        """没声明参考图就不要求映射行 —— 绑定表里可能有幽灵引用，
        模型少写反而是对的，这道闸不拿绑定当期望（误伤三次重试全废）。"""
        self.assertEqual(S.s8_prompt_gaps(_item(refs=())), [])

    def test_empty_item_reports_length_not_crash(self):
        gaps = S.s8_prompt_gaps({})
        self.assertEqual(len(gaps), 2)


class ContentCheckRetryTests(unittest.TestCase):
    """② json_call：闸没过走反馈重试，反馈带着缺口原话。"""

    def _client(self, replies):
        c = LLM("k", "https://example.invalid", "fake-model")
        users = []

        def chat(system, user, **kw):
            users.append(user)
            return replies.pop(0)

        c.chat = chat
        return c, users

    @staticmethod
    def _reply(item):
        return "```json\n" + json.dumps({"compiled": [item]},
                                        ensure_ascii=False) + "\n```"

    def test_gap_triggers_retry_with_specific_feedback(self):
        """★ 第一次交残段 → 重试，且反馈里有「应写 N 行只出现 M 行」。"""
        c, users = self._client([
            self._reply(_item(sb="残段" * 20)),
            self._reply(_item()),
        ])
        out = c.json_call("", "任务", json_retries=2, log=lambda *_: None,
                          content_check=S._s8_content_check)
        self.assertEqual(len(out["compiled"]), 1)
        self.assertEqual(len(users), 2)
        self.assertIn("输出内容不完整", users[1])
        self.assertIn("只出现 0 行", users[1])

    def test_exhausted_retries_fail_loudly(self):
        c, users = self._client([self._reply(_item(sb="残段" * 20))] * 3)
        with self.assertRaises(LLMError) as cm:
            c.json_call("", "任务", json_retries=2, log=lambda *_: None,
                        content_check=S._s8_content_check)
        self.assertIn("输出内容不完整", str(cm.exception))
        self.assertEqual(len(users), 3)      # 3 次尝试，不是 1 次

    def test_check_runs_after_required_fields(self):
        """缺必需字段的老路不变 —— content_check 不掺和结构校验。"""
        c, users = self._client([self._reply({})])    # 两份正文都没有
        with self.assertRaises(LLMError) as cm:
            c.json_call("", "任务", json_retries=0, log=lambda *_: None,
                        required=["compiled[].storyboard_prompt"],
                        content_check=S._s8_content_check)
        self.assertIn("缺少必需字段", str(cm.exception))


class WiringTests(unittest.TestCase):
    """③ run_segmented：闸没过的段失败且不落盘，闸过的照常存。"""

    class GapAwareLLM:
        model = "fake"

        def __init__(self, items):
            self.items = list(items)

        def json_call(self, system, user, required=None, json_retries=2,
                      log=print, cancel=None, on_usage=None, on_partial=None,
                      validator=None, on_soft=None, transport_retries=2,
                      content_check=None):
            item = self.items.pop(0)
            data = {"compiled": [dict(item, id="EP01-SEG01")]}
            if content_check:
                gaps = content_check(data)
                if gaps:
                    raise LLMError("输出内容不完整：" + "；".join(gaps))
            return data

    def setUp(self):
        self.pj = new_project()
        self.segs = [{"id": "EP01-SEG01"}]

    def tearDown(self):
        shutil.rmtree(self.pj.root, ignore_errors=True)

    def _run(self, llm):
        return S.run_segmented(
            self.pj, stage_id="s8", out_name="s8_compile", key="compiled",
            segs=self.segs, done_ids=set(), llm=llm,
            build_user=lambda seg: "u", params={"duration": 15},
            required=["compiled[]"], content_check=S._s8_content_check,
            log=lambda *_: None, episode="EP01", cancel=None, seg_concurrency=1)

    def test_hollow_segment_fails_and_is_not_saved(self):
        """★ 残段不落盘：以前这份 287 字的产物会被当成做过了。"""
        result, failed, cancelled = self._run(
            self.GapAwareLLM([_item(sb="残段" * 20)]))
        self.assertEqual(failed, ["EP01-SEG01"])
        self.assertEqual(result["compiled"], [])
        self.assertEqual((self.pj.stage_data("s8_compile", "EP01")
                          or {}).get("compiled"), [])

    def test_complete_segment_saves_as_before(self):
        result, failed, _ = self._run(self.GapAwareLLM([_item()]))
        self.assertEqual(failed, [])
        self.assertEqual(len(result["compiled"]), 1)


class TruncatedWordingTests(unittest.TestCase):
    """④ 截断反馈的「不要删减」明确覆盖字段值里的大段正文。"""

    def test_stop_branch_covers_field_value_bodies(self):
        err = _truncated('{"a": 1', 7, "stop")
        self.assertIn("字段值里的大段正文", str(err))
        self.assertIn("不许缩写删节", str(err))

    def test_transport_branch_covers_field_value_bodies(self):
        err = _truncated('{"a": 1', 7, "")
        self.assertIn("字段值里的大段正文", str(err))


if __name__ == "__main__":
    unittest.main()
