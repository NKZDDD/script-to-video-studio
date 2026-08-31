# -*- coding: utf-8 -*-
"""生产任务那张表选了一集，进度列要跟着变。

实遇（2026-08-31）：标签写着 EP02、数字是全剧的 0/76。两个数并排摆着自相
矛盾，人会以为选集没生效 —— 而选集**是生效的**（`/api/generate` 真按集过滤），
只是这一列一直数的是全部任务。而那个数是「这一行点『开始』会跑多少条」的
唯一提示，错了就等于没有提示。
"""
import ast
import io
import json
import os
import re
import tempfile
import unittest

from core import episodes as E
from core import matimport as M
from core.store import Project

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "server", "app.py")
PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "web", "index.html")


def _project():
    """两集，段数不同（4+2 / 6+3）—— 相同的话数错了也看不出来。"""
    rows = [{"kind": "manifest", "total": 9,
             "params": {"image_size": "9:16", "ratio": "9:16"}},
            {"kind": "image", "key": "PRJ_X__CHAR_001_R02", "family": "CHAR",
             "name": "人", "filename": "PRJ_X__CHAR_001_R02.png", "size": "3:4",
             "reference_images": [], "prompt": "正文"}]
    for ep, nseg in (("EP01", 2), ("EP02", 3)):
        for s in range(1, nseg + 1):
            for tag in ("A", "B"):
                rows.append({
                    "kind": "image", "family": "SBSHEET", "name": tag,
                    "key": f"PRJ_X__SBSHEET_{ep}_SEG{s:02d}_{tag}_R01",
                    "filename": f"PRJ_X__SBSHEET_{ep}_SEG{s:02d}_{tag}_R01.png",
                    "size": "9:16", "reference_images": [], "prompt": "正文"})
            rows.append({
                "kind": "video", "key": f"{ep}-SEG{s:02d}", "episode": ep,
                "seg": f"SEG{s:02d}", "ratio": "9:16", "duration": 10,
                "filename": f"PRJ_X__VIDEO_{ep}_SEG{s:02d}_R01.mp4",
                "storyboard_refs": [
                    {"image_n": 1, "key": f"PRJ_X__SBSHEET_{ep}_SEG{s:02d}_A_R01"},
                    {"image_n": 2, "key": f"PRJ_X__SBSHEET_{ep}_SEG{s:02d}_B_R01"}],
                "reference_images": [], "prompt": "正文 @Image1 @Image2"})
    units = M.parse("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    pj = Project(tempfile.mkdtemp(prefix="epsum-"))
    pj.save_tasks(M.build(units, system="v34")["tasks"])
    pj.save_stage(E.FILE[:-5], M.episodes_stub(units))
    return pj


class GenEpisodeCountTests(unittest.TestCase):
    def test_the_api_reports_counts_per_episode(self):
        """★ 按集的数要一起给 —— 一次请求，切集不用再往返。"""
        pj = _project()
        # /api/project 的那段算法：照它的口径重算一遍，验的是**分组对不对**
        tasks = pj.tasks()
        from core import probe
        by_ep = {}
        for key, tk in (("storyboard", "storyboard_tasks"), ("video", "video_tasks")):
            for it in tasks.get(tk, []):
                e = str(it.get("episode") or "")
                if not e:
                    continue
                slot = by_ep.setdefault(e, {}).setdefault(key, {"total": 0, "done": 0})
                slot["total"] += 1
        self.assertEqual(by_ep["EP01"]["storyboard"]["total"], 4)
        self.assertEqual(by_ep["EP01"]["video"]["total"], 2)
        self.assertEqual(by_ep["EP02"]["storyboard"]["total"], 6)
        self.assertEqual(by_ep["EP02"]["video"]["total"], 3)
        # 全剧 = 各集之和
        self.assertEqual(len(tasks["storyboard_tasks"]), 10)
        self.assertEqual(len(tasks["video_tasks"]), 5)
        del probe

    def test_assets_stay_out_of_the_per_episode_map(self):
        """★ 资产图不进按集那张表。

        它不带集号（全剧共享，同一个角色跨集只出一张图，跨集人脸才一致）。
        硬按集分只会分出个空的，然后页面显示 0/0 —— 而资产是真有活要干的，
        「开始」还会因为 total=0 被禁用。
        """
        src = io.open(APP, encoding="utf-8").read()
        i = src.index('if path == "/api/project":')
        seg = src[i:src.index('if path == "/api/episodes":', i)]
        self.assertIn('if key == "asset":', seg)
        self.assertIn("continue", seg[seg.index('if key == "asset":'):])

    def test_the_api_returns_the_field_the_page_reads(self):
        """前后端字段名对不上 = 页面拿到 undefined，然后静默显示 0/0。"""
        src = io.open(APP, encoding="utf-8").read()
        self.assertIn('"tasks_by_episode": by_ep', src)
        page = io.open(PAGE, encoding="utf-8").read()
        self.assertIn("d.tasks_by_episode", page)

    def test_changing_the_episode_repaints_the_whole_row(self):
        """★ 切集要整行重画，不能只改标签。

        只改标签的话「EP02」旁边挂着全剧的 0/76 —— 而那个数是这一行
        「开始」会跑多少条的唯一提示。
        """
        page = io.open(PAGE, encoding="utf-8").read()
        h = re.search(r"\$\('#genEp'\)\.onchange[\s\S]{0,700}?\n\};", page)
        self.assertIsNotNone(h)
        body = h.group(0)
        self.assertIn("renderGen()", body)
        self.assertNotIn("tag.textContent", body, "又回到只改标签了")

    def test_the_empty_reason_is_not_blamed_on_the_wrong_stage(self):
        """这一集没有这类活 ≠ 前置环节没跑。

        说错了会把人支到「流程」页去重跑一个已经跑完的环节。
        """
        page = io.open(PAGE, encoding="utf-8").read()
        self.assertIn("这一集没有「${label}」的任务", page)

    def test_the_page_still_parses(self):
        """改的是内联脚本 —— 语法坏了整页白屏，而白屏之前什么都不报。"""
        import subprocess
        r = subprocess.run(
            ["node", "--check", "-"], input=_inline_js(),
            capture_output=True, text=True, encoding="utf-8")
        if r.returncode == 127 or "not found" in (r.stderr or "").lower():
            self.skipTest("这台机器没有 node")
        self.assertEqual(r.returncode, 0, r.stderr)


def _inline_js() -> str:
    page = io.open(PAGE, encoding="utf-8").read()
    return "\n".join(re.findall(r"<script>(.*?)</script>", page, re.S))


if __name__ == "__main__":
    unittest.main()
