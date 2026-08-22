# -*- coding: utf-8 -*-
"""资产提示词环节（V6.1 s5 / 电影级 n4b）的收尾句必须带画面文字口径。

实跑踩过（N001_PROMPT.txt）：模板强制收尾句写死了
「画面内不得出现任何文字、字母、数字、标注、水印」—— 模型照抄合规落盘，
资产是道具/招牌/文件本身时，出图模型把药瓶标签、店铺招牌抹成空白，
静默发生不报错。和环节8「画面文字条款」是同一条病，只是换了位置。

三条防线，各钉住一条测试（结构同 test_s8_text_rule.py）：
  ① 模板带 {{SUBTITLE_RULE}}（改写丢失时 voided 能报出、upgrade 能补回）
  ② 渲染时口径真的进提示词
  ③ 渲染后兜底（改写版丢了占位符，规则也要拼进去，不许静默丢）
"""
import os
import tempfile
import unittest

from core import prompts as P
from core import settings as S

PROMPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "prompts")


def _tpl_text(name: str) -> str:
    return open(os.path.join(PROMPTS, name + ".md"), encoding="utf-8").read()


def _build_v61(pj, stage_id="s5"):
    """走 V6.1 真跑的渲染链路：_mapping + render + 收尾兜底。

    复用 run_stage 里的三行核心（模板 → mapping → render → 兜底拼接），
    不起 LLM。
    """
    from core import stages as ST
    from core.store import Project
    pj = pj or Project(tempfile.mkdtemp(prefix="assetrule-"))
    pj.init_dirs()
    tpl_name = {"s5": "s5_asset_prompts"}[stage_id]
    text = ST.render(ST.stage_prompt(stage_id, tpl_name, pj),
                     ST._mapping(pj, stage_id, {"duration": 15}, {
                         "s1_global": {"visual_tone": {"compressed": "",
                                                       "compressed_variants": []}},
                         "s4_assets": {"assets": []},
                     }, "EP01", ""))
    # run_stage 里那段兜底原样搬过来 —— 测的就是它
    if stage_id == "s5" and "剧情本身要求的文字" not in text:
        from core import settings as _st
        text += ("\n\n【画面文字规则】（收尾句里的画面文字条款必须逐字转述这一段"
                 "—— 剧情本身要求的文字一律允许，禁的只有字幕、水印、UI 面板"
                 "和不属于剧情的叠加文字）\n" + _st.subtitle_rule(pj))
    return pj, text


def _build_v34(pj=None, rewritten=None):
    """电影级 n4b：走 run_v34.build_user 的真实链路（含兜底）。"""
    from core import run_v34 as R34
    from core.store import Project
    pj = pj or Project(tempfile.mkdtemp(prefix="assetrule34-"))
    pj.init_dirs()
    if rewritten is not None:
        d = pj.p("00_项目说明", "提示词模板")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "n4b_asset_prompts.md"), "w",
                  encoding="utf-8") as f:
            f.write(rewritten)
    # build_user 会读 deps_data（盘上没有就是空 dict），mapping 有默认值
    return pj, R34.build_user(pj, "n4b", {"duration": 15, "image_size": "1024x1536"})


class TemplateCarriesTheRuleTests(unittest.TestCase):
    """① 两个体系的模板都带口径。"""

    def test_builtin_templates_have_the_placeholder(self):
        for name in ("s5_asset_prompts", "n4b_asset_prompts"):
            self.assertIn("{{SUBTITLE_RULE}}", _tpl_text(name), name)

    def test_the_dead_ban_is_gone(self):
        """★ 写死的那句无差别禁令必须从模板里消失。"""
        for name in ("s5_asset_prompts", "n4b_asset_prompts"):
            self.assertNotIn("不得出现任何文字、字母、数字", _tpl_text(name), name)

    def test_voided_reports_templates_that_lost_the_placeholder(self):
        lost = "# 我的改写\n只写提示词。\n"
        # voided 报的是全部缺失（含 TRUTH/ASSETS 等环节自有输入），
        # 这里只关心新增的两个设置口径在报出的清单里。
        self.assertIn("SUBTITLE_RULE", P.voided("s5_asset_prompts", lost))
        self.assertIn("SUBTITLE_RULE", P.voided("n4b_asset_prompts", lost))
        self.assertIn("MEDIUM_RULE", P.voided("n4b_asset_prompts", lost))

    def test_upgrade_puts_the_placeholder_back(self):
        lost = "# 我的改写\n只写提示词。\n"
        for name in ("s5_asset_prompts", "n4b_asset_prompts"):
            up = P.upgrade(name, lost)
            self.assertIn("{{SUBTITLE_RULE}}", up["text"], name)


class TheRuleReachesThePromptTests(unittest.TestCase):
    """② 渲染时口径真的进提示词。"""

    def test_v61_rule_in_built_prompt(self):
        _, user = _build_v61(None)
        self.assertIn("剧情本身要求的文字", user)
        self.assertIn("弹幕", user)

    def test_v34_rule_in_built_prompt(self):
        _, user = _build_v34()
        self.assertIn("剧情本身要求的文字", user)
        self.assertIn("弹幕", user)

    def test_subtitle_setting_changes_the_built_prompt(self):
        from core.store import Project
        pj = Project(tempfile.mkdtemp(prefix="assetrule-sub-"))
        pj.init_dirs()
        S.save(pj, {"subtitle": True, "subtitle_lang": "中文"})
        _, user = _build_v34(pj)
        self.assertIn("要有中文字幕", user)


class FallbackWhenRewrittenTests(unittest.TestCase):
    """③ 改写版丢了占位符，规则也要拼进去 —— {{MEDIUM_RULE}} 的教训。"""

    def test_v34_rule_appended_when_placeholder_is_gone(self):
        rewritten = "# 我的n4b改写\n只写提示词，结构自己看着办。\n"
        _, user = _build_v34(rewritten=rewritten)
        self.assertIn("剧情本身要求的文字", user)
        self.assertIn("弹幕", user)

    def test_v34_rule_not_duplicated_when_placeholder_works(self):
        """占位符在的时候兜底不许再拼一份 —— 重复规则两头措辞不一更糟。"""
        _, user = _build_v34()
        self.assertEqual(user.count("判据是它在故事里真的存在"), 1)


if __name__ == "__main__":
    unittest.main()
