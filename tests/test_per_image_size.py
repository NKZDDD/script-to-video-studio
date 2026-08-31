# -*- coding: utf-8 -*-
"""画幅按**每一张**来，不是全剧一个数。

LLM 交给 codex 之后，画幅本来就该照这一张画什么来定（人物立绘竖、场景
全景横、道具方）。材料里每条 image 都能自己写 `size`，程序按目标服务商的
规范换成它认的写法。

这一路上的失败**全是静默的**：写了这一家不收的值，多数家自己挑一个默认值
出图 —— 图出得来、形状不是你要的、任务标 ok。
"""
import json
import unittest

from core import matimport as M
from core import produce
from core import providers as P
from core import sizes as sz


class PerImageSizeTests(unittest.TestCase):
    def test_each_image_keeps_its_own_size(self):
        """材料里各写各的 → tasks.json 里各是各的，申报头只当兜底。"""
        rows = [{"kind": "manifest", "total": 3,
                 "params": {"image_size": "9:16", "ratio": "9:16"}},
                {"kind": "image", "key": "PRJ_X__CHAR_001_R02", "family": "CHAR",
                 "name": "人", "filename": "PRJ_X__CHAR_001_R02.png",
                 "size": "3:4", "reference_images": [], "prompt": "正文"},
                {"kind": "image", "key": "PRJ_X__LOC_001_R01", "family": "LOC",
                 "name": "景", "filename": "PRJ_X__LOC_001_R01.png",
                 "size": "16:9", "reference_images": [], "prompt": "正文"},
                {"kind": "image", "key": "PRJ_X__PROP_001_R01", "family": "PROP",
                 "name": "物", "filename": "PRJ_X__PROP_001_R01.png",
                 "reference_images": [], "prompt": "正文"}]      # 不写 = 用兜底
        units = M.parse("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
        got = {t["key"]: t["params"]["size"]
               for t in M.build(units, system="v34")["tasks"]["asset_tasks"]}
        self.assertEqual(got, {"PRJ_X__CHAR_001_R02": "3:4",
                               "PRJ_X__LOC_001_R01": "16:9",
                               "PRJ_X__PROP_001_R01": "9:16"})

    def test_a_tier_is_never_picked_as_a_shape(self):
        """★ 档位（1K/2K/4K）说的是分辨率，**说不了形状**。

        坤鸡同时收档位和像素，而 `4K` 的代表像素是 2048x2048，约简后正好
        是 1:1 —— 于是要 `1:1` 会命中「同形状的 4K」，发出去一个**没说形状**
        的值。图出得来、形状由服务商定、任务标 ok，一处不报错。

        反方向（要档位、这家只收比例）本来就拒了；这是对称的另一半。
        """
        sup = P.REGISTRY["kunji"]().capabilities()["image"]["sizes"]
        self.assertIn("4K", sup, "坤鸡不再声明档位的话这条测试要重写")
        for want in ("1:1", "9:16", "16:9", "4:3"):
            val, _ = sz.resolve(want, sup)
            self.assertNotEqual((sz.parse(val) or ("", 0, 0))[0], "tier",
                                f"要 {want} 却挑了档位 {val}")
        # 只收档位的家：要形状换不过来，**停下来**，不许蒙一个
        val, note = sz.resolve("9:16", ["1K", "2K", "4K"])
        self.assertIsNone(val)
        self.assertIn("说不了形状", note)
        # 要档位、这家收档位 —— 照发
        self.assertEqual(sz.resolve("4K", ["1K", "2K", "4K"])[0], "4K")

    def test_every_provider_gets_a_form_it_declared(self):
        """★ 发出去的值必须在这一家自己声明的清单里。

        各家写法根本不是一套（像素 / 比例 / 档位），同一个值换一家就可能
        不合法 —— 而不合法的后果不是报错，是它自己挑一个默认值出图。
        """
        for pid in sorted(P.REGISTRY):
            try:
                sup = ((P.REGISTRY[pid]().capabilities() or {})
                       .get("image") or {}).get("sizes") or []
            except Exception:                                # noqa: BLE001
                continue
            # 和 sizes.resolve 一样滤掉空值再判「有没有声明」。
            # 巨轮声明的是 `[""]` —— 它自己的说明写着「size 按 OpenAI 语义
            # 透传，不支持的值会 400」，也就是**故意不声明清单**。
            # 那种情况原样发是对的，而且 400 是响的，不是静默降级。
            sup = [str(x).strip() for x in sup if str(x).strip()]
            if not sup:
                continue
            for want in ("9:16", "16:9", "1:1", "1024x1536"):
                try:
                    got = produce._fit_size({"provider": pid}, "", "image",
                                            want, log=lambda m: None)
                except RuntimeError:
                    continue            # 换不过来时停下，是对的
                self.assertIn(got, sup,
                              f"{pid}: 要 {want}，发出去的 {got} 不在它声明的清单里")

    def test_a_storyboard_shaped_unlike_its_segment_is_flagged(self):
        """故事板的形状和用它出片的那一段对不上 → 提醒。

        视频那一步要么裁要么加黑边，**而那一步不会说话**：片子出得来、
        构图被切掉一块，几百段里只能靠肉眼发现。画幅改成每张各写各的之后，
        这种错配第一次成为可能，所以这道检查是跟着那个改动一起加的。
        """
        def mat(sb_size):
            rows = [{"kind": "manifest", "total": 3,
                     "params": {"image_size": "9:16", "ratio": "9:16"}}]
            for tag in ("A", "B"):
                rows.append({
                    "kind": "image", "family": "SBSHEET", "name": tag,
                    "key": f"PRJ_X__SBSHEET_EP01_SEG01_{tag}_R01",
                    "filename": f"PRJ_X__SBSHEET_EP01_SEG01_{tag}_R01.png",
                    "size": sb_size, "reference_images": [], "prompt": "正文"})
            rows.append({
                "kind": "video", "key": "EP01-SEG01", "episode": "EP01",
                "seg": "SEG01", "ratio": "9:16", "duration": 10,
                "filename": "PRJ_X__VIDEO_EP01_SEG01_R01.mp4",
                "storyboard_refs": [
                    {"image_n": 1, "key": "PRJ_X__SBSHEET_EP01_SEG01_A_R01"},
                    {"image_n": 2, "key": "PRJ_X__SBSHEET_EP01_SEG01_B_R01"}],
                "reference_images": [], "prompt": "正文 @Image1 @Image2"})
            return M.parse("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))

        codes = lambda u: {i["code"] for i in M.audit(u)}       # noqa: E731
        self.assertNotIn("SHAPE_MISMATCH", codes(mat("9:16")), "形状一样却报了")
        self.assertIn("SHAPE_MISMATCH", codes(mat("16:9")))
        # ★ 档位不参与形状比较：`4K` 的代表像素是 2048x2048 = 1:1，
        #   拿它当形状比会报一条**假的**错配 —— 而假警报比漏报更贵，
        #   人会学会忽略这一条，然后真错配那次也被忽略。
        got = codes(mat("4K"))
        self.assertIn("SIZE_IS_A_TIER", got)
        self.assertNotIn("SHAPE_MISMATCH", got, "档位被当成 1:1 比出了假错配")

    def test_the_contract_tells_codex_to_vary_the_size(self):
        """契约得说清楚，而且示例本身要照做。

        示例全写同一个值的话，codex 照示例产就是全剧一个数 ——
        示例本身在教它「画幅是全局设置」。
        """
        from core import matspec
        text = matspec.render()
        self.assertIn("每张各写各的", text)
        self.assertIn("1K", text)                 # 档位那个坑要写进去
        sizes = set()
        for ln in text.split("\n"):
            s = ln.strip()
            if s.startswith("{") and '"size"' in s:
                sizes.add(json.loads(s).get("size"))
        self.assertGreater(len(sizes), 1,
                           f"示例里的画幅全一样（{sizes}）—— 它在教 codex 用全局值")


if __name__ == "__main__":
    unittest.main()
