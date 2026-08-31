# -*- coding: utf-8 -*-
"""材料导入模式下「按集出图 / 出片」。

用户的主路已经变成：codex 产材料 → 导入 → 跳过分析、按集出图出片。
这条路上按集过滤有三处**各自静默失效**，这个文件把三处都钉住。
"""
import json
import tempfile
import unittest

from core import episodes as E
from core import matimport as M
from core import pipeline_v34 as PV
from core.store import Project


def _material():
    """一份合契约的两集材料：1 个全剧资产 + 每集 2 张故事板 + 1 段视频。"""
    rows = [{"kind": "image", "key": "PRJ_X__CHAR_001_R02", "family": "CHAR",
             "name": "林溪身份根", "filename": "PRJ_X__CHAR_001_R02.png",
             "size": "9:16", "reference_images": [], "prompt": "资产正文"}]
    for ep in ("EP01", "EP02"):
        for tag in ("A", "B"):
            rows.append({
                "kind": "image", "key": f"PRJ_X__SBSHEET_{ep}_SEG01_{tag}_R01",
                "family": "SBSHEET", "name": f"{ep} 故事板 {tag}",
                "filename": f"PRJ_X__SBSHEET_{ep}_SEG01_{tag}_R01.png",
                "size": "9:16", "reference_images": [], "prompt": "故事板正文"})
        rows.append({
            "kind": "video", "key": f"{ep}-SEG01", "episode": ep, "seg": "SEG01",
            "filename": f"PRJ_X__VIDEO_{ep}_SEG01_R01.mp4",
            "duration": 15, "ratio": "9:16",
            "storyboard_refs": [
                {"image_n": 1, "key": f"PRJ_X__SBSHEET_{ep}_SEG01_A_R01", "role": "ENTRY"},
                {"image_n": 2, "key": f"PRJ_X__SBSHEET_{ep}_SEG01_B_R01", "role": "KEY"}],
            "reference_images": [{"image_n": 3, "key": "PRJ_X__CHAR_001_R02", "role": "CHAR"}],
            "prompt": "视频正文 @Image1 @Image2 @Image3"})
    return M.parse("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))


def _project(units):
    pj = Project(tempfile.mkdtemp(prefix="matep-"))
    built = M.build(units, system="v34")
    pj.save_tasks(built["tasks"])
    pj.save_stage(E.FILE[:-5], M.episodes_stub(units))
    return pj, built


class MaterialByEpisodeTests(unittest.TestCase):
    def test_import_writes_the_episode_list(self):
        """★ 集清单要落盘。

        材料每条都带集号、任务也带，**按集过滤的机器一直是通的**；缺的只是
        「这个项目有哪几集」——而那份清单只有环节1（切集）写，材料导入把
        环节1 整个顶掉了。没有它：页头「集」下拉是空的 → 生产页发出去的
        episode 是空串 → 不过滤 → 想只出 EP01，全部集都出了，钱照花。
        """
        pj, _ = _project(_material())
        self.assertEqual(E.ids(pj), ["EP01", "EP02"])
        self.assertTrue(E.load(pj)["from_material"],
                        "这份不是切出来的，得标出来 —— 它没有正文")

    def test_images_get_their_episode_from_the_key(self):
        """★ 集号对图也要解出来，不只是视频。

        契约里只有 video 行要求写 episode；故事板那种 image 行的集号藏在 key
        里（`..._SBSHEET_EP01_SEG01_A_R01`）。原来只在 kind=="video" 时才解，
        于是所有图的 episode 都是空串，而两个下游对空串的处理**是相反的**：
        生产页 `==` 比较 → 一条不剩、点了没反应；只跑生产 `not ep or ...`
        → 一律留下、全部集都出。一个漏做一个多做，都不报错。

        资产不属于任何一集，key 里没有 EPnn，解不出来就是空 —— 那是对的。
        """
        _, built = _project(_material())
        got = {t["key"]: t.get("episode") for t in built["tasks"]["storyboard_tasks"]}
        self.assertEqual(sorted(set(got.values())), ["EP01", "EP02"])
        self.assertEqual([t.get("episode") for t in built["tasks"]["asset_tasks"]],
                         [""], "资产是全剧共享的，不该被派进某一集")

    def test_produce_page_filter_splits_by_episode(self):
        """生产页那条按集过滤（/api/generate 用的就是这个判断）。"""
        _, built = _project(_material())
        for key, whole, one in (("storyboard_tasks", 4, 2), ("video_tasks", 2, 1)):
            items = built["tasks"][key]
            self.assertEqual(len(items), whole, key)
            self.assertEqual(
                len([t for t in items if t.get("episode", "") == "EP01"]), one,
                f"{key}: 选了 EP01 应该只剩这一集的")

    def test_produce_only_run_honours_both_episode_boxes(self):
        """★「只跑生产」两个范围框都要真的管用。

        `produce_episodes`（页面上「只出这几集的图/片」）以前只有通用级收，
        电影级这边压根没有这个参数 —— 页面一直在发，填了等于没填，
        全部集照出、钱按全剧花。又一个「旋钮看着在、其实没接线」。
        """
        pj, _ = _project(_material())

        def todo(**kw):
            steps = [s for s in PV.plan(pj, include_llm=False, include_deliver=False, **kw)
                     if s["kind"] == "produce"]
            return {s["stage"]: len(PV._produce_todo(pj, s["task_key"], s["only"]))
                    for s in steps}

        self.assertEqual(todo(), {"p1": 1, "p2": 0, "p3": 4, "p4": 2})
        for kw in ({"only_episodes": ["EP01"]}, {"produce_episodes": ["EP02"]}):
            self.assertEqual(todo(**kw), {"p1": 1, "p2": 0, "p3": 2, "p4": 1},
                             f"{kw} 没把范围收窄到一集")

    def test_a_produce_range_outside_the_run_is_refused(self):
        """范围填了个不存在的集，要当场说，别静默出全剧。"""
        pj, _ = _project(_material())
        with self.assertRaises(ValueError) as cm:
            PV.plan(pj, include_llm=False, produce_episodes=["EP09"])
        self.assertIn("EP09", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
