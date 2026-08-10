# -*- coding: utf-8 -*-
"""提示词里的 Image 编号必须和实际上传顺序对得上。

出图模型收到的是 N 张**没有标签**的图，它只知道第 1 张、第 2 张，
不认识 `C001` 这个编号。所以提示词只写「参考资产C001，严格沿用同一Aisyah」
等于让它猜 —— 一张时勉强蒙对，两张必然错位：拿病房那张图去沿用人脸。

而且编号写错**不会报错**，图照出、任务标 ok，只能靠肉眼在几百张里发现。
"""
import unittest

from core.produce import check_image_map


def refs(*ids):
    return [{"image_n": i + 1, "asset_id": a, "file_ref": f"x/{a}.png"}
            for i, a in enumerate(ids)]


class ImageMapTests(unittest.TestCase):

    def test_no_refs_needs_no_mapping(self):
        """父资产自己没有参考图，整段该省略，不该因此报错。"""
        bad, warn = check_image_map("资产名称：Aisyah。输出结构：four_view。", [])
        self.assertEqual((bad, warn), ("", ""))

    def test_correct_mapping_passes(self):
        p = ("参考图角色映射：\nImage 1 = C002 Rizky Adhitama（父资产）\n"
             "Image 2 = ST008 医院重症监护室夜间状态（依赖资产）\n")
        bad, warn = check_image_map(p, refs("C002", "ST008"))
        self.assertEqual((bad, warn), ("", ""))

    def test_full_width_punctuation_is_accepted(self):
        """模型经常写全角冒号/等号，不该因为标点判成缺失。"""
        for sep in ("=", "＝", ":", "："):
            bad, warn = check_image_map(f"Image 1 {sep} C001 Aisyah", refs("C001"))
            self.assertEqual((bad, warn), ("", ""), f"分隔符 {sep} 没认出来")

    def test_swapped_order_is_a_hard_stop(self):
        """★ 模型把两张的编号写反 —— 每张参考图都错误归属，必须停。"""
        p = "Image 1 = ST008 病房状态\nImage 2 = C002 Rizky"
        bad, warn = check_image_map(p, refs("C002", "ST008"))
        self.assertTrue(bad, "编号写反了却放行")
        self.assertIn("Image 1 实际传的是 C002", bad)
        self.assertIn("却写成 ST008", bad)
        self.assertIn("Image 1=C002", bad)          # 告诉他正确顺序是什么
        self.assertIn("任务明细", bad)               # 告诉他去哪改

    def test_missing_one_number_is_a_hard_stop(self):
        p = "Image 1 = C002 Rizky"                  # 漏了 Image 2
        bad, _ = check_image_map(p, refs("C002", "ST008"))
        self.assertIn("Image 2 应该是 ST008", bad)

    def test_extra_number_is_a_hard_stop(self):
        p = "Image 1 = C002\nImage 2 = ST008\nImage 3 = S001"
        bad, _ = check_image_map(p, refs("C002", "ST008"))
        self.assertIn("多写了 Image 3", bad)

    def test_two_or_more_refs_without_mapping_is_a_hard_stop(self):
        """★ 这是实际踩到的：环节5 写的是「参考资产C002及ST008」，没有编号。"""
        p = ("资产名称：Rizky拔针后手背状态。"
             "参考图角色映射：沿用父资产C002及状态资产ST008的手部肤色和病床材质。")
        bad, _ = check_image_map(p, refs("C002", "ST008"))
        self.assertTrue(bad, "两张参考图却没写编号，居然放行")
        self.assertIn("没有 `Image N = 资产ID`", bad)
        self.assertIn("C002", bad)
        self.assertIn("ST008", bad)

    def test_single_ref_without_mapping_only_warns(self):
        """只有一张时顺序上没有歧义，不该卡住整批出图，但要提醒补。"""
        p = "参考图角色映射：参考资产C001，严格沿用同一Aisyah。"
        bad, warn = check_image_map(p, refs("C001"))
        self.assertEqual(bad, "", "单张不该硬停")
        self.assertIn("没有 `Image N = 资产ID`", warn)
        self.assertIn("建议补上", warn)

    def test_prose_mentions_do_not_count_as_mapping(self):
        """正文里提一句「Image 1 优先级更高」不算映射，别误判成写过了。"""
        p = "输出限制：Image 1 优先级高于 Image 2。"
        bad, _ = check_image_map(p, refs("C002", "ST008"))
        self.assertTrue(bad)

    def test_duplicate_number_takes_first_and_still_catches_mismatch(self):
        p = "Image 1 = ST008\nImage 1 = C002\nImage 2 = ST008"
        bad, _ = check_image_map(p, refs("C002", "ST008"))
        self.assertIn("Image 1", bad)

    def test_id_only_without_name_still_passes_program_check(self):
        """程序只核对 ID；名称是给模型看的，缺名称由模板要求约束，不在这里硬卡。"""
        bad, warn = check_image_map("Image 1 = C001", refs("C001"))
        self.assertEqual((bad, warn), ("", ""))


class RealProjectPromptTests(unittest.TestCase):
    """拿真实生成过的两种写法各跑一遍，确认这个检查抓的是真问题。"""

    def test_real_s5_style_single_ref_warns(self):
        p = ("资产名称：Aisyah沾血旧罩衫与头巾湿透沾泥状态。输出结构：closeup。"
             "参考图角色映射：参考资产C001，严格沿用同一Aisyah服装锚点。"
             "父资产identity_anchors原文：28岁印度尼西亚女性，椭圆偏窄脸型。")
        bad, warn = check_image_map(p, refs("C001"))
        self.assertEqual(bad, "")
        self.assertTrue(warn)

    def test_real_s8_style_five_refs_passes(self):
        p = ("二、参考图角色映射：Image 1 = C001 Aisyah，控制身份；"
             "Image 2 = C005 Nyonya Dewi，控制身份；"
             "Image 3 = ST001 虚弱状态，控制连续状态；"
             "Image 4 = ST008 病房夜间状态，控制空间；"
             "Image 5 = ST005 授权书签字后状态，控制文件状态。")
        bad, warn = check_image_map(p, refs("C001", "C005", "ST001", "ST008", "ST005"))
        self.assertEqual((bad, warn), ("", ""))


if __name__ == "__main__":
    unittest.main()
