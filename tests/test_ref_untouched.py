# -*- coding: utf-8 -*-
"""参考图**默认不动原图**。

用户原话（2026-08-31）：「我就不需要你做这个压缩，PNG 改 JPG 除非是服务商
要求，否则都不要对原图进行修改才对」。

原来是无条件缩到最长边 1024 再转 JPEG q80，而那个 1024 全项目没有一处
设置过 —— 它是 produce 里的硬编码兜底，也就是唯一生效的值。代价实测：
1024x1536 的故事板发出去是 682x1024（剩 44% 像素），2048x2048 的资产
只剩 25%。参考图是喂给模型的身份和构图来源，而谁都没选过这件事。
"""
import os
import tempfile
import unittest

from core import diagnose
from core import produce
from core import providers as P
from core.apiutil import REF_KEEP, encode_ref


def _png(w=1024, h=1536, mode="RGB"):
    from PIL import Image, ImageDraw
    img = Image.new(mode, (w, h), (250, 248, 245) if mode == "RGB" else (0, 0, 0, 0))
    if mode == "RGB":
        d = ImageDraw.Draw(img)
        for i in range(0, w, 9):
            d.line([(i, 0), (i, h)], fill=(60, 60, 70))
    p = os.path.join(tempfile.mkdtemp(), "sb.png")
    img.save(p)
    return p


class RefUntouchedTests(unittest.TestCase):
    def test_default_is_byte_for_byte_identical(self):
        """★ 默认这条路一个字节都不许改。

        「大小差不多」不算 —— 逐字节比。重编码一次哪怕看不出来，
        也已经不是原图了，而这条路上没有任何一处会说话。
        """
        p = _png()
        raw, mime, ext, note = encode_ref(p)
        with open(p, "rb") as f:
            self.assertEqual(raw, f.read(), "默认居然动了原图")
        self.assertEqual(mime, "image/png")
        self.assertEqual(ext, ".png")
        self.assertIn("原样", note)

    def test_no_provider_asks_for_compression_today(self):
        """★ 现在一家都没声明要求，所以全都是原样发。

        这条测试是「默认值不会悄悄回来」的闸门：哪天有人给某家加了
        `ref_max_side`，这里会亮，得说清楚是那家真要求、还是又在填兜底。
        """
        for pid in sorted(P.REGISTRY):
            for media in ("image", "video"):
                side, fmt = produce._ref_rules({"provider": pid}, media)
                self.assertEqual((side, fmt), (0, ""),
                                 f"{pid}/{media} 声明了要改参考图：{side} {fmt}")

    def test_alpha_survives_and_is_called_out(self):
        """透明通道保留，但要出声。

        以前一律 `convert("RGB")`，把透明压平成黑底。现在不动它 ——
        而各家对 alpha 的处理不一样，出图不对时要能想到这一条。
        """
        p = _png(600, 800, mode="RGBA")
        raw, _mime, ext, note = encode_ref(p)
        with open(p, "rb") as f:
            self.assertEqual(raw, f.read())
        self.assertEqual(ext, ".png")
        self.assertIn("透明", note)

    def test_a_declared_rule_is_obeyed_and_reported(self):
        """声明了就照做，而且**做了什么必须写在说明里**。"""
        p = _png()
        raw, _m, ext, note = encode_ref(p, max_side=1024)
        self.assertEqual(ext, ".png", "只说了上限，没说要转格式，就别转")
        self.assertIn("682x1024", note)
        self.assertIn("44%", note)

        raw2, mime2, ext2, note2 = encode_ref(p, fmt="jpeg")
        self.assertEqual((mime2, ext2), ("image/jpeg", ".jpg"))
        self.assertIn("JPEG", note2)
        self.assertNotEqual(raw, raw2)

    def test_the_upload_cache_key_covers_the_format(self):
        """★ 格式要进缓存 key。

        不进的话，改成「默认原样发」之后同一张图会命中上一次那条 JPEG 的
        缓存 URL —— 设置改了、日志写着「原样」、服务商拿到的还是压过的旧图。
        一处都不报错。
        """
        from core import uploader
        p = _png(64, 64)
        keys = {uploader._sha(p, 0, ""), uploader._sha(p, 0, "jpeg"),
                uploader._sha(p, 1024, ""), uploader._sha(p, 1024, "jpeg")}
        self.assertEqual(len(keys), 4, "不同处理参数算出了同一个缓存 key")

    def test_body_too_large_points_at_the_knob(self):
        """★ 请求体撑不住时要指到能改的那一格。

        默认原样发的代价是内联 base64 那条路的请求体变大。这条错长得像
        网络问题，不指路的话人会去调并发 —— 而且好几家把它回成 400 带一句
        `invalid request`，那会被认成「提示词有问题」，人去改提示词。
        """
        for msg, status in (("HTTP 413 Payload Too Large", 413),
                            ("400 invalid request: body size exceeded", 400),
                            ("请求体过大，请压缩图片后重试", 400),
                            ("image exceeds maximum size of 4MB", 400)):
            self.assertEqual(diagnose.code_of(msg, status), "BODY_TOO_LARGE", msg)
        # 别把提示词过长抢走
        self.assertEqual(diagnose.code_of("prompt too long", 400), "PROMPT_INVALID")
        e = diagnose.build(RuntimeError("HTTP 413 Payload Too Large"))
        self.assertIn("ref_max_side", e["where"])

    def test_the_keep_constant_is_zero(self):
        """0 = 不缩。写成别的值就等于又给了一个没人选过的默认。"""
        self.assertEqual(REF_KEEP, 0)


if __name__ == "__main__":
    unittest.main()
