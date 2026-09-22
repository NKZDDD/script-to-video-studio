"""Exercise real pipe readers under the Chinese Windows default encoding."""
import subprocess
import sys

import pytest

from core import captions, probe


def emit(payload, exit_code=0):
    return [sys.executable, "-c",
            f"import os; os.write(1, {payload!r}); os.write(2, {payload!r}); "
            f"raise SystemExit({exit_code})"]


@pytest.mark.parametrize("mode", [{"text": True}, {"universal_newlines": True}])
def test_text_pipes_survive_gbk_locale_and_invalid_log_bytes(monkeypatch, mode):
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "gbk")
    payload = "真假拆迁前夫他悔疯了第1集.mp4".encode("utf-8") + b"\xaf"
    with captions.utf8_subprocess_logs():
        result = subprocess.run(emit(payload, 7), capture_output=True, **mode)
    assert result.stdout == result.stderr == payload.decode("utf-8", "replace")
    assert result.returncode == 7  # A real tool failure must still be reported.


def test_binary_pipes_and_explicit_encodings_are_preserved():
    with captions.utf8_subprocess_logs():
        binary = subprocess.run(emit(b"\xff\x00\xaf"), capture_output=True)
        explicit = subprocess.run(emit("字幕".encode("gbk")), capture_output=True,
                                  text=True, encoding="gbk", errors="strict")
    assert binary.stdout == binary.stderr == b"\xff\x00\xaf"
    assert explicit.stdout == explicit.stderr == "字幕"


def test_streaming_popen_and_restoration_on_exception(monkeypatch):
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "gbk")
    original = subprocess.Popen
    with pytest.raises(RuntimeError, match="fixture"):
        with captions.utf8_subprocess_logs():
            with subprocess.Popen(emit("字幕\n".encode("utf-8")),
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True) as proc:
                assert list(proc.stdout) == ["字幕\n", "字幕\n"]
            raise RuntimeError("fixture")
    assert subprocess.Popen is original


def test_real_caption_resolution_probe_with_chinese_path(monkeypatch, tmp_path):
    renderer = pytest.importorskip("videocaptioner.core.subtitle.ass_renderer")
    captions.ensure_ffmpeg()
    ffmpeg = probe.find_ffmpeg()
    assert ffmpeg
    video = tmp_path / "真假拆迁前夫他悔疯了第1集.mp4"
    subprocess.run([ffmpeg, "-f", "lavfi", "-i", "color=s=320x240:d=0.1",
                    "-metadata", "title=字幕🙂", "-c:v", "libx264", str(video)],
                   capture_output=True, check=True)
    raw = subprocess.run([ffmpeg, "-i", str(video)], capture_output=True).stderr
    with pytest.raises(UnicodeDecodeError):
        raw.decode("gbk")  # The fixture really exercises the reported failure.
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "gbk")
    with captions.utf8_subprocess_logs():
        assert renderer._get_video_resolution(str(video)) == (320, 240)
