"""Q10 专用文档契约；纯离线测试，不创建付费任务。"""
import json

import pytest
import requests

from core.apiutil import ApiError
from core.model_catalog import apply_catalog
from core.providers.base import VideoTask
from core.providers.julun import JulunProvider, Q10_MODEL, UNSPECED


def task(**kw):
    return VideoTask(**dict({"model": Q10_MODEL, "prompt": "参考人物缓慢走入房间", "duration": 20}, **kw))


def test_q10_capabilities_and_default_resolution_match_document():
    p = JulunProvider()
    cap = apply_catalog(p.capabilities(), {"rows": [{"id": Q10_MODEL}]}, "fixture")
    opts = cap["video"]["model_options"][Q10_MODEL]
    assert opts["durations"] == list(range(4, 31))
    assert opts["resolutions"] == ["480p"]
    assert opts["ratios"] == ["16:9", "9:16"]
    assert opts["max_refs"] == 30
    assert Q10_MODEL not in UNSPECED
    path, body = p.build_video_body(task(ratio="9:16"))
    assert path == "/v1/videos"
    assert body == {"model": Q10_MODEL, "prompt": "参考人物缓慢走入房间",
                    "seconds": 20, "resolution": "480p", "aspect_ratio": "9:16"}


def test_full_reference_arrays_are_preserved_and_video_audio_only_allowed():
    refs = [f"https://example.com/{i}.png" for i in range(30)]
    vids = [f"https://example.com/{i}.mov" for i in range(10)]
    auds = [f"https://example.com/{i}.aac" for i in range(10)]
    p = JulunProvider()
    for images in (refs, []):
        _, body = p.build_video_body(task(refs=images, extra={"video_refs": vids, "audio_refs": auds}))
        assert body.get("image_urls", []) == images
        assert body["video_urls"] == vids
        assert body["audio_urls"] == auds


@pytest.mark.parametrize("duration", [4, 15, 20, 30])
def test_valid_seconds_not_changed(duration):
    assert JulunProvider().build_video_body(task(duration=duration))[1]["seconds"] == duration


@pytest.mark.parametrize("duration", [0, 3, 31, 4.5, True, None, "invalid"])
def test_invalid_seconds_rejected_without_clamping(duration):
    with pytest.raises(ApiError, match="整数秒"):
        JulunProvider().build_video_body(task(duration=duration))


@pytest.mark.parametrize("kw,match", [
    ({"resolution": "720p"}, "清晰度"), ({"ratio": "1:1"}, "比例"),
    ({"refs": ["https://example.com/a.png"] * 31}, "参考图最多 30"),
    ({"extra": {"video_refs": ["https://example.com/a.mp4"] * 11}}, "参考视频最多 10"),
    ({"extra": {"audio_refs": ["https://example.com/a.wav"] * 11}}, "参考音频最多 10"),
    ({"prompt": "字" * 4501}, "4500 字符"),
])
def test_invalid_fields_have_specific_errors(kw, match):
    with pytest.raises(ApiError, match=match):
        JulunProvider().build_video_body(task(**kw))


def test_chinese_model_prompt_and_ref_url_serialize_without_ascii_error(monkeypatch):
    p = JulunProvider(api_key="fixture-not-a-real-key")
    captured = []

    def request(method, url, **kw):
        if method == "POST":
            prepared = requests.Request(method, url, json=kw["json"], headers=kw["headers"]).prepare()
            assert prepared.body.isascii()
            decoded = json.loads(prepared.body)
            assert decoded["model"] == Q10_MODEL
            assert decoded["prompt"] == "字" * 4500
            assert decoded["image_urls"] == ["https://example.com/参考图.png"]
            assert not any(Q10_MODEL in v for v in prepared.headers.values())
            captured.append(decoded)
            result = {"task_id": "q10-fixture"}
        else:
            result = {"status": "completed", "result_url": "https://example.com/final.mp4"}
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(result).encode()
        return response

    monkeypatch.setattr(requests, "request", request)
    monkeypatch.setattr(p.session, "save_item", lambda *a, **kw: None)
    result = p.generate_video(task(prompt="字" * 4500, refs=["https://example.com/参考图.png"]),
                              "out.mp4", poll_interval=0, log=lambda _: None)
    assert len(captured) == 1
    assert result["task_id"] == "q10-fixture"
