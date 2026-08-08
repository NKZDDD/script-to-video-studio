# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

from core import explorer
from core.store import Project, write_text


class ExplorerVideoReferenceTests(unittest.TestCase):
    def test_video_task_displays_storyboard_as_first_reference(self):
        with tempfile.TemporaryDirectory() as root:
            pj = Project(root)
            pj.init_dirs()
            storyboard = "04_故事板/T_EP01_SEG02_STORYBOARD_V01_FIXED.png"
            storyboard_path = pj.p(*storyboard.split("/"))
            os.makedirs(os.path.dirname(storyboard_path), exist_ok=True)
            with open(storyboard_path, "wb") as fh:
                fh.write(b"fake-png")
            prompt = "03_提示词/视频提示词/EP01-SEG02_VIDEO_PROMPT.txt"
            write_text(pj.p(*prompt.split("/")), "video prompt")
            pj.save_tasks({
                "asset_tasks": [], "storyboard_tasks": [],
                "video_tasks": [{
                    "key": "EP01-SEG02", "episode": "EP01",
                    "prompt_ref": prompt, "storyboard_ref": storyboard,
                    "aux_reference": None, "params": {"duration": 15, "ratio": "9:16"},
                    "output": "05_分段视频/T_EP01_SEG02_VIDEO_V01_FIXED.mp4",
                }],
            })
            view = explorer.tasks(pj, "EP01")
            group = next(g for g in view["groups"] if g["kind"] == "video")
            refs = group["rows"][0]["refs"]
            self.assertEqual(len(refs), 1)
            self.assertEqual(refs[0]["image_n"], 1)
            self.assertEqual(refs[0]["asset_id"], "本段固定故事板")
            self.assertTrue(refs[0]["file"]["exists"])
            self.assertEqual(refs[0]["file"]["rel"], storyboard)

    def test_video_task_displays_auxiliary_reference_second(self):
        with tempfile.TemporaryDirectory() as root:
            pj = Project(root)
            pj.init_dirs()
            pj.save_tasks({
                "asset_tasks": [], "storyboard_tasks": [],
                "video_tasks": [{
                    "key": "EP01-SEG01", "episode": "EP01", "prompt_ref": "",
                    "storyboard_ref": "04_故事板/storyboard.png",
                    "aux_reference": "02_固定资产/人物身份资产/C001.png",
                    "params": {}, "output": "05_分段视频/video.mp4",
                }],
            })
            view = explorer.tasks(pj, "EP01")
            group = next(g for g in view["groups"] if g["kind"] == "video")
            refs = group["rows"][0]["refs"]
            self.assertEqual([r["asset_id"] for r in refs],
                             ["本段固定故事板", "补充资产参考图"])
            self.assertEqual([r["image_n"] for r in refs], [1, 2])


if __name__ == "__main__":
    unittest.main()
