# -*- coding: utf-8 -*-
"""读文件的真实宽高，用来核对「出来的画面比例，是不是你要的那个」。

为什么要这道检查：服务商对画面比例的处理各家不同——有的按你给的像素尺寸出，
有的要你显式发 ratio 字段，不发就按它自己的默认值来（常见是回落成横屏 16:9）。
这类问题**不会报错**：接口 200、文件正常下载、只是画面躺倒了。
不主动量一下，就要等到人工验收才发现，那时钱已经花完了。

图片直接读文件头（不依赖 Pillow）；视频用 ffprobe（拼接本来就要 ffmpeg）。
量不到就当没这回事——这只是加保险，不能反过来卡住主流程。
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
from typing import Optional

RATIO_VALUES = {"21:9": 21 / 9, "16:9": 16 / 9, "4:3": 4 / 3, "3:2": 1.5,
                "1:1": 1.0, "2:3": 2 / 3, "3:4": 3 / 4, "9:16": 9 / 16}


# ---------------------------------------------------------------- 图片文件头
def _png_size(f) -> Optional[tuple]:
    f.seek(16)
    d = f.read(8)
    return struct.unpack(">II", d) if len(d) == 8 else None


def _gif_size(f) -> Optional[tuple]:
    f.seek(6)
    d = f.read(4)
    return struct.unpack("<HH", d) if len(d) == 4 else None


def _jpeg_size(f) -> Optional[tuple]:
    """顺着 marker 链走到 SOFn，那里才有真实宽高。"""
    f.seek(2)
    while True:
        b = f.read(1)
        if not b:
            return None
        if b != b"\xff":
            continue
        while b == b"\xff":                       # 连续填充字节
            b = f.read(1)
        if not b:
            return None
        marker = b[0]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        seg = f.read(2)
        if len(seg) < 2:
            return None
        length = struct.unpack(">H", seg)[0]
        # SOF0..SOF15，除去 DHT(C4)/JPGA(C8)/DAC(CC) 这三个不是帧头
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            d = f.read(5)
            if len(d) < 5:
                return None
            h, w = struct.unpack(">HH", d[1:5])
            return w, h
        f.seek(length - 2, os.SEEK_CUR)


def _webp_size(f) -> Optional[tuple]:
    f.seek(12)
    tag = f.read(4)
    if tag == b"VP8 ":
        f.seek(26)
        d = f.read(4)
        if len(d) < 4:
            return None
        w, h = struct.unpack("<HH", d)
        return w & 0x3FFF, h & 0x3FFF
    if tag == b"VP8L":
        f.seek(21)
        d = f.read(4)
        if len(d) < 4:
            return None
        bits = struct.unpack("<I", d)[0]
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if tag == b"VP8X":
        f.seek(24)
        d = f.read(6)
        if len(d) < 6:
            return None
        w = d[0] | d[1] << 8 | d[2] << 16
        h = d[3] | d[4] << 8 | d[5] << 16
        return w + 1, h + 1
    return None


def image_size(path: str) -> Optional[tuple]:
    """返回 (宽, 高)；认不出格式或文件坏了就返回 None。"""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if head.startswith(b"\x89PNG\r\n\x1a\n"):
                return _png_size(f)
            if head.startswith(b"\xff\xd8"):
                return _jpeg_size(f)
            if head.startswith(b"GIF8"):
                return _gif_size(f)
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                return _webp_size(f)
    except (OSError, struct.error):
        pass
    return None


# ---------------------------------------------------------------- 视频
def find_ffmpeg() -> str:
    """系统装的优先；没有就用 imageio-ffmpeg 附带的那份。"""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:                             # noqa: BLE001
        return ""


def find_ffprobe() -> str:
    """ffprobe 常跟 ffmpeg 放在一起，但 imageio-ffmpeg 只带 ffmpeg 不带 ffprobe。

    只替换文件名，不能对整条路径做替换——路径里的目录名往往也含 "ffmpeg"，
    整条替换会把目录名一起改掉，指到一个不存在的地方。
    """
    exe = shutil.which("ffprobe")
    if exe:
        return exe
    ff = find_ffmpeg()
    if ff:
        d, base = os.path.split(ff)
        cand = os.path.join(d, base.replace("ffmpeg", "ffprobe", 1))
        if cand != ff and os.path.isfile(cand):
            return cand
    return ""


_SIZE_RE = re.compile(r"\b(\d{2,5})x(\d{2,5})\b")


def run_text(cmd: list, timeout: int = 30):
    """跑外部程序并按文本拿输出。**永远显式给编码。**

    踩过的坑：subprocess 的 text=True 用的是系统默认编码，中文 Windows 上是
    GBK。ffmpeg 打出来的流信息里常有 ® © 之类的字节，GBK 解不了，就在
    subprocess 内部的读取线程里抛 UnicodeDecodeError —— 那是另一个线程，
    调用方 try/except 根本接不住，整条拼接就这么断了。
    统一走这里：utf-8 + errors="replace"，解不了的字符变成 � 而不是炸掉。
    """
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def video_size(path: str) -> Optional[tuple]:
    """读第一条视频轨的宽高。ffprobe 和 ffmpeg 有哪个用哪个；都没有就返回 None。"""
    if not os.path.isfile(path):
        return None
    probe_exe = find_ffprobe()
    if probe_exe:
        try:
            r = run_text(
                [probe_exe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path])
            m = _SIZE_RE.search(r.stdout or "")
            if m:
                return int(m.group(1)), int(m.group(2))
        except (OSError, subprocess.SubprocessError):
            pass

    # 没有 ffprobe 就退而求其次：ffmpeg -i 会把流信息打到 stderr。
    # 不给输出文件，它会以非零码退出（"At least one output file..."），这是正常的，
    # 我们只要它退出前打出来的那段流信息。
    ff = find_ffmpeg()
    if not ff:
        return None
    try:
        r = run_text([ff, "-hide_banner", "-i", path])
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (r.stderr or "").splitlines():
        if "Video:" not in line:
            continue
        # 去掉 [SAR 1:1 DAR 16:9] 这种括号内容，免得把里头的数字当成分辨率
        m = _SIZE_RE.search(re.sub(r"\[[^\]]*\]", "", line))
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


# ---------------------------------------------------------------- 比对
def as_ratio(want: str) -> Optional[float]:
    """把 '9:16' 或 '1024x1536' 都换算成一个宽高比数值。"""
    s = (want or "").strip().lower().replace("×", "x")
    if s in RATIO_VALUES:
        return RATIO_VALUES[s]
    m = re.match(r"^(\d+)\s*[:x]\s*(\d+)$", s)
    if m and int(m.group(2)):
        return int(m.group(1)) / int(m.group(2))
    return None


def name_ratio(w: int, h: int) -> str:
    """量出来的宽高 → 最接近的常见比例名，纯粹为了说人话。"""
    if not h:
        return f"{w}x{h}"
    val = w / h
    name, _ = min(RATIO_VALUES.items(), key=lambda kv: abs(kv[1] - val))
    return name


def orientation(want: str) -> str:
    """'竖' / '横' / '方'。量不出来返回空串。

    只分三档是故意的：1024x1536（0.667）和 9:16（0.5625）差着 18%，
    但都是竖的，那点差别是各家出图尺寸档位不同，不是错。
    真正要抓的是躺倒 —— 竖屏片子配了横屏故事板。
    """
    v = as_ratio(want)
    if not v:
        return ""
    return "方" if abs(v - 1) < 0.05 else ("竖" if v < 1 else "横")


def orientation_conflict(image_size: str, ratio: str) -> Optional[str]:
    """出图尺寸和画面比例躺倒方向相反 —— 返回一句人话；没冲突返回 None。

    这两个是页面上两个各自独立的下拉框，谁也不管谁。选成
    「图片尺寸 1536x1024（横）+ 画幅 9:16（竖）」是合法输入，
    程序照跑，服务商也不报错 —— 出来的是横构图故事板配竖屏视频。

    后果不是「不好看」：故事板就是视频那一步的参考图。方向反了，
    出片时模型只能裁掉两边或者上下加黑边，人脸常常正好被裁掉。
    而这件事**要到成片才看得见**，那时候钱已经花完了。
    """
    a, b = orientation(image_size), orientation(ratio)
    if not a or not b or a == b or "方" in (a, b):
        return None
    return (f"「图片尺寸 {image_size}」是{a}的，「画幅 {ratio}」是{b}的，方向相反。\n"
            f"故事板是出片时的参考图 —— 方向反了，出片会裁掉两边或者上下加黑边，"
            f"人脸常常正好被裁掉，而这要到成片才看得见。\n"
            f"改成方向一致的再跑（竖屏片配 1024x1536，横屏片配 1536x1024）。")


def check(path: str, want: str, *, kind: str = "image", tol: float = 0.06) -> Optional[dict]:
    """量一下实际文件，和期望比例对不上就返回一份说明；对得上或量不到返回 None。

    tol 是允许的相对偏差 6%：服务商常把 1024x1536 出成 1024x1520 之类，
    这种几像素的出入不算问题，只有真的躺倒了（比如要竖屏给了横屏）才报。
    """
    want_val = as_ratio(want)
    if not want_val:
        return None
    wh = video_size(path) if kind == "video" else image_size(path)
    if not wh or not wh[0] or not wh[1]:
        return None
    got_val = wh[0] / wh[1]
    if abs(got_val - want_val) / want_val <= tol:
        return None
    return {"want": want, "got": f"{wh[0]}x{wh[1]}",
            "got_ratio": name_ratio(*wh), "kind": kind,
            "portrait_wanted": want_val < 1, "portrait_got": got_val < 1}
