# -*- coding: utf-8 -*-
r"""自带的字幕样式，装进 videocaptioner 的样式目录。

用户给了四份（2026-08-27）：中文/英文 × 竖屏/横屏，都是 ASS `[V4+ Styles]`
块。videocaptioner 认这个格式（`SubtitleStyle.from_file` 有 legacy .txt 分支），
**但 `list_styles` / `load_style` 只 glob `*.json`** —— 直接把 txt 丢进它的
目录，`caption style` 一个都列不出来，`--style 中文竖屏标准版` 也找不到。
所以这里做一次转换：txt → 它的 json 形状，写进它的样式目录。

为什么要程序来装、不让人手动放：
  · 那个目录在 `%LOCALAPPDATA%\VideoCaptioner\...\resource\subtitle_style`，
    路径既深又跟我们的数据目录无关，没人会记得
  · 打进 exe 之后目标机器上压根没有这个目录（第一次跑才建）
  · 转换要按它的字段名来，手写 json 迟早写错一个键，而**写错的表现是
    「样式列出来了、字幕出来还是默认样子」** —— 不报错
"""

from __future__ import annotations

import json
import os
from typing import Callable, Optional

from . import paths

DIR_NAME = "字幕样式"


def bundled() -> list:
    """自带的那几份样式文件（打包后从解压目录读）。"""
    d = paths.res(DIR_NAME)
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d))
            if f.lower().endswith((".txt", ".json"))]


def install(log: Optional[Callable] = None) -> dict:
    """把自带样式装进 videocaptioner 的样式目录。返回 {装了, 已有, 失败}。

    **同名的不覆盖** —— 用户在那边改过的样式是他的，我们每次启动都盖回去
    等于「我改的怎么又没了」。要更新就先删掉那一份。
    """
    say = log or (lambda *a: None)
    out = {"installed": [], "kept": [], "failed": []}
    src = bundled()
    if not src:
        return out
    try:
        from videocaptioner.core.subtitle.style_manager import SubtitleStyle
        from videocaptioner.config import SUBTITLE_STYLE_PATH as dst_dir
    except Exception as exc:                                # noqa: BLE001
        out["failed"].append(f"videocaptioner 不在这一版里：{exc}")
        return out
    os.makedirs(str(dst_dir), exist_ok=True)
    from pathlib import Path
    for p in src:
        stem = os.path.splitext(os.path.basename(p))[0]
        dst = os.path.join(str(dst_dir), stem + ".json")
        if os.path.isfile(dst):
            out["kept"].append(stem)
            continue
        try:
            st = SubtitleStyle.from_file(Path(p))
            with open(dst, "w", encoding="utf-8") as f:
                json.dump(st.to_json_dict(), f, ensure_ascii=False, indent=2)
            out["installed"].append(stem)
        except Exception as exc:                            # noqa: BLE001
            out["failed"].append(f"{stem}：{exc}")
    if out["installed"]:
        say(f"  已装入 {len(out['installed'])} 份自带字幕样式："
            + "、".join(out["installed"]))
    for bad in out["failed"]:
        say(f"  ⚠ 字幕样式装不进去 —— {bad}")
    return out


def ensure_ffmpeg(log: Optional[Callable] = None) -> tuple:
    """把 ffmpeg / ffprobe 按**标准名**摆到 PATH 上。返回 (ffmpeg, ffprobe)。

    videocaptioner 用 `which("ffmpeg")` 找它，而 imageio-ffmpeg 带的那个二进制
    叫 `ffmpeg-win-x86_64-v7.1.exe` —— **名字对不上，把目录塞进 PATH 也没用**。
    所以按标准名做一份影子副本（只拷一次），放数据目录的 bin/ 下。

    这一段原来只长在 `run.py` 的 caption 直通里，于是源码方式跑、
    或者字幕环节自己调 doctor 时，照旧报「ffmpeg not found」——
    而机器上明明有一个。一处实现、两处调用。
    """
    say = log or (lambda *a: None)
    from . import probe
    got = []
    shim = os.path.join(paths.data_dir(), "bin")
    for exe, want in ((probe.find_ffmpeg(), "ffmpeg.exe"),
                      (probe.find_ffprobe(), "ffprobe.exe")):
        if not exe:
            got.append("")
            continue
        dst = exe
        if os.path.basename(exe).lower() != want:
            dst = os.path.join(shim, want)
            if not os.path.isfile(dst):
                os.makedirs(shim, exist_ok=True)
                import shutil
                shutil.copy2(exe, dst)
                say(f"  已按标准名做了一份：{dst}")
        d = os.path.dirname(dst)
        if d and d not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        got.append(dst)
    return tuple(got)


# --------------------------------------------------------------------------
# 系统字体
# --------------------------------------------------------------------------
#
# 字幕样式里的「字体」填的是**字体族名**，烧录时由 libass 去系统字体集里
# 找。找不到就**静默回落**成一个默认字体 —— 片子照出、字幕照有、字体不是
# 你要的那个，而且没有任何一处报错。所以设置页那个框要给真实候选，
# 不能让人凭记忆敲。
#
# 为什么读注册表而不是扫 C:\Windows\Fonts：
#   · 注册表里的显示名就是**你在 Windows 字体列表里看到的那个名字**
#     （「华文楷体」而不是 STKAITI.TTF），和人要填的东西一致
#   · 用户自己装在别处的字体也在注册表里（HKCU 那一支）
#   · 扫目录要逐个文件解析 name 表，371 个文件要几秒，这里几毫秒

_FONT_SUFFIX = (" (TrueType)", " (OpenType)", " (All res)", " (VGA res)",
                " (TrueType-Auslassung)", " (120dpi)", " (96dpi)")

_font_cache: list = []


def system_fonts(refresh: bool = False) -> list:
    """这台机器上能用的字体族名。取不到就返回空表。

    空表和「一个字体都没有」不是一回事 —— 调用方**不要**拿空表去校验
    用户填的字体名，那会把「我们读不到注册表」报成「你的字体不存在」。
    """
    if _font_cache and not refresh:
        return list(_font_cache)
    names = set()
    try:
        import winreg                                     # noqa: PLC0415
    except ImportError:
        return []                                          # 非 Windows
    sub = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
    for root in (getattr(__import__("winreg"), "HKEY_LOCAL_MACHINE"),
                 getattr(__import__("winreg"), "HKEY_CURRENT_USER")):
        try:
            k = winreg.OpenKey(root, sub)
        except OSError:
            continue                                       # HKCU 那一支常常没有
        try:
            for i in range(winreg.QueryInfoKey(k)[1]):
                try:
                    raw = winreg.EnumValue(k, i)[0]
                except OSError:
                    continue
                for suf in _FONT_SUFFIX:
                    if raw.endswith(suf):
                        raw = raw[:-len(suf)]
                        break
                raw = raw.strip()
                # 「Microsoft YaHei & Microsoft YaHei UI」这种一条含多个族名
                for part in raw.split("&"):
                    part = part.strip()
                    # 「Arial Bold」「Arial Italic」是同一族的不同字重，
                    # 不该各占一行 —— 但**不能**按空格切掉最后一个词：
                    # 「Segoe UI」「华文中宋」会被切坏。只去掉明确的字重后缀。
                    for w in (" Bold Italic", " Bold", " Italic", " Oblique",
                              " Light", " Semibold", " Black"):
                        if part.endswith(w) and len(part) > len(w) + 1:
                            part = part[:-len(w)]
                            break
                    if part:
                        names.add(part)
        finally:
            k.Close()
    _font_cache[:] = sorted(names, key=lambda s: (s[0].isascii(), s.lower()))
    return list(_font_cache)
