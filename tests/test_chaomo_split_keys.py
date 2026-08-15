# -*- coding: utf-8 -*-
"""超模的 LLM、图片、视频凭据彼此独立，不能当成一个通用 key。"""
import unittest

from server.app import build_llm, resolve_chain, resolve_provider_cfg


def _cfg(**chaomo):
    return {
        "providers": {
            "chaomo": {
                "base_url": "https://www.chaomoapi.com",
                **chaomo,
            },
        },
        "chains": {
            "asset": [{"provider": "chaomo", "model": "gpt-image2-1K"}],
            "storyboard": [{"provider": "chaomo", "model": "gpt-image2-1K"}],
            "video": [{"provider": "chaomo", "model": "seedance2"}],
        },
        "llm": {"provider": "chaomo", "model": "gpt-5.6-sol"},
    }


class ChaomoSplitKeyTests(unittest.TestCase):
    def test_each_work_type_receives_only_its_own_key(self):
        cfg = _cfg(llm_api_key="llm-k", image_1k_api_key="image-1k-k",
                   image_4k_api_key="image-4k-k", video_api_key="video-k")
        cfg["chains"]["storyboard"][0]["model"] = "gpt-image2-4K"
        self.assertEqual(resolve_chain(cfg, "asset")[0]["api_key"], "image-1k-k")
        self.assertEqual(resolve_chain(cfg, "storyboard")[0]["api_key"], "image-4k-k")
        self.assertEqual(resolve_chain(cfg, "video")[0]["api_key"], "video-k")
        self.assertEqual(build_llm(cfg).api_key, "llm-k")

    def test_missing_one_image_resolution_key_does_not_disable_the_other(self):
        image_cfg = _cfg(image_1k_api_key="image-1k-k")
        self.assertEqual(resolve_chain(image_cfg, "asset")[0]["api_key"], "image-1k-k")
        with self.assertRaisesRegex(ValueError, "图片 4K Key"):
            resolve_chain(image_cfg, "asset",
                          [{"provider": "chaomo", "model": "gpt-image2-4K"}])

        image_cfg = _cfg(image_4k_api_key="image-4k-k")
        self.assertEqual(
            resolve_chain(image_cfg, "asset",
                          [{"provider": "chaomo", "model": "gpt-image2-4K"}])[0]["api_key"],
            "image-4k-k")
        with self.assertRaisesRegex(ValueError, "图片 1K Key"):
            resolve_chain(image_cfg, "asset")

    def test_missing_media_key_does_not_disable_the_other_media(self):
        image_cfg = _cfg(image_1k_api_key="image-1k-k")
        self.assertEqual(resolve_chain(image_cfg, "asset")[0]["api_key"], "image-1k-k")
        with self.assertRaisesRegex(ValueError, "视频 Key"):
            resolve_chain(image_cfg, "video")

        video_cfg = _cfg(video_api_key="video-k")
        self.assertEqual(resolve_chain(video_cfg, "video")[0]["api_key"], "video-k")
        with self.assertRaisesRegex(ValueError, "图片 1K Key"):
            resolve_chain(video_cfg, "asset")

    def test_manual_video_resolution_does_not_require_image_key(self):
        cfg = _cfg(video_api_key="video-k")
        selected = resolve_provider_cfg(
            cfg, {"provider": "chaomo", "model": "seedance2"}, "video")
        self.assertEqual(selected["api_key"], "video-k")

    def test_manual_4k_image_resolution_uses_4k_key(self):
        cfg = _cfg(image_1k_api_key="image-1k-k", image_4k_api_key="image-4k-k")
        selected = resolve_provider_cfg(
            cfg, {"provider": "chaomo", "model": "gpt-image2-4K"}, "asset")
        self.assertEqual(selected["api_key"], "image-4k-k")

    def test_chaomo_legacy_generic_key_is_not_reused_across_capabilities(self):
        cfg = _cfg(api_key="legacy-generic-k", image_api_key="legacy-image-k")
        for kind in ("asset", "storyboard", "video"):
            with self.assertRaises(ValueError, msg=kind):
                resolve_chain(cfg, kind)
        with self.assertRaises(ValueError):
            build_llm(cfg)

    def test_other_providers_keep_the_single_key_behavior(self):
        cfg = {
            "providers": {"paisio": {"api_key": "shared-k"}},
            "chains": {
                "asset": [{"provider": "paisio", "model": "image"}],
                "video": [{"provider": "paisio", "model": "video"}],
            },
            "llm": {"provider": "paisio", "model": "gpt-5.6-sol"},
        }
        self.assertEqual(resolve_chain(cfg, "asset")[0]["api_key"], "shared-k")
        self.assertEqual(resolve_chain(cfg, "video")[0]["api_key"], "shared-k")
        self.assertEqual(build_llm(cfg).api_key, "shared-k")

    def test_explicit_analysis_engine_key_still_overrides_provider_key(self):
        cfg = _cfg(llm_api_key="provider-llm-k")
        cfg["llm"]["api_key"] = "direct-llm-k"
        self.assertEqual(build_llm(cfg).api_key, "direct-llm-k")


if __name__ == "__main__":
    unittest.main()
