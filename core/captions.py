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


_EXT = (".txt", ".json")


def _styles_in(d: str) -> list:
    if not d or not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d))
            if f.lower().endswith(_EXT)]


def bundled() -> list:
    """自带的那几份样式文件（打包后从解压目录读）。"""
    return _styles_in(paths.res(DIR_NAME))


USER_DIR_NAME = DIR_NAME + "-自定义"


def user_dir() -> str:
    """用户自己传的样式放这儿：`<数据目录>/字幕样式-自定义/`。

    **不能放自带那个目录**，两个原因：
      · 打成 exe 之后自带样式在 onefile 的临时解压目录（`_MEI*`）里 ——
        每次启动重建、退出即删，往里写等于下次就没了，而且不报错
      · 源码方式跑的时候 `paths.data_dir()` 就是仓库根，和自带样式同一个
        目录 —— 传一份同名的会直接覆盖仓库里的源文件

    所以另起一个目录名，任何运行方式下都不会和自带的撞。
    """
    return os.path.join(paths.data_dir(), USER_DIR_NAME)


def sources() -> list:
    """所有要装进 videocaptioner 的样式文件：自带的 + 用户传的。

    同名以**用户传的**为准 —— 他后传的那份就是他要的。
    """
    out = {}
    for p in bundled() + _styles_in(user_dir()):
        out[os.path.splitext(os.path.basename(p))[0]] = p
    return [out[k] for k in sorted(out)]


def user_styles() -> list:
    """用户自己传的那几份（页面上列出来、可以删）。"""
    return [{"name": os.path.splitext(os.path.basename(p))[0],
             "file": os.path.basename(p),
             "size": os.path.getsize(p)}
            for p in _styles_in(user_dir())]


def install(log: Optional[Callable] = None, force=None) -> dict:
    """把样式装进 videocaptioner 的样式目录。返回 {装了, 已有, 失败}。

    **同名的不覆盖** —— 用户在那边改过的样式是他的，我们每次启动都盖回去
    等于「我改的怎么又没了」。要更新就先删掉那一份。

    `force` 是刚上传的那几个名字：人明确要求换这一份，这时候必须覆盖，
    否则「传了新的、字幕还是老样子」，而且一处都不报错。
    """
    say = log or (lambda *a: None)
    out = {"installed": [], "kept": [], "failed": []}
    src = sources()
    force = set(force or ())
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
        if os.path.isfile(dst) and stem not in force:
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


# 文件名就是**样式名**（videocaptioner 的 legacy txt 分支拿 `path.stem` 当名字）。
# 所以名字要挡住路径分隔符和跨平台的非法字符 —— 不挡的话
# `../../x` 能写到样式目录外面去。
_BAD_NAME = set(chr(92) + '/:*?"<>|') | {chr(i) for i in range(32)}


def add_style(filename: str, text: str) -> dict:
    """收一份用户传的样式，落到用户目录并立刻装进 videocaptioner。

    **先解析再落盘。** 解析不过就当场拒 —— 存下来的话页面上会列出一份
    永远装不进去的样式，而「列出来了、选中了、字幕还是默认样子」正是
    这一路上一直在消灭的那类失效。
    """
    raw = str(filename or "").strip()
    # 带路径的**直接拒**，不要 basename 一下悄悄接受。
    # basename 确实挡住了穿越（`../../x.txt` 只会落在用户目录里），但它把
    # 一个和用户写的不一样的名字静默收下了 —— 而文件名就是样式名，
    # 人会在下拉里找一个自己没起过的名字。
    if set(raw) & {"/", chr(92)}:
        raise ValueError(f"文件名里不能带路径：{raw!r}")
    name = os.path.splitext(os.path.basename(raw))[0].strip()
    ext = os.path.splitext(raw)[1].lower()
    if not name:
        raise ValueError("没给文件名 —— 文件名就是样式名，页面下拉里显示的就是它")
    if set(name) & _BAD_NAME:
        raise ValueError(f"样式名 {name!r} 里有不能做文件名的字符"
                         f"（{chr(92)}/:*?{chr(34)}<>| 和控制字符）")
    if ext not in _EXT:
        raise ValueError(f"只认 .txt（ASS [V4+ Styles] 块）和 .json"
                         f"（VideoCaptioner 自己导出的），收到 {ext or '（没有扩展名）'}")
    if not str(text or "").strip():
        raise ValueError("文件是空的")

    import tempfile
    from pathlib import Path
    try:
        from videocaptioner.core.subtitle.style_manager import SubtitleStyle
    except Exception as exc:                                # noqa: BLE001
        raise ValueError(f"这一版里没有 videocaptioner，装不了样式：{exc}") from exc
    # 用真名建临时文件 —— txt 分支拿 stem 当样式名，用随机名解析出来的
    # 名字是错的，而它只在最后写 json 时才看得出来。
    tmp = os.path.join(tempfile.mkdtemp(prefix="stystyle-"), name + ext)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        st = SubtitleStyle.from_file(Path(tmp))
    except Exception as exc:                                # noqa: BLE001
        raise ValueError(
            f"这份样式解析不了：{exc}。"
            f".txt 要是 ASS 的 `[V4+ Styles]` 块（至少有一行 `Style: ...`）；"
            f".json 要是 VideoCaptioner 自己导出的那种。") from exc

    d = user_dir()
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, name + ext)
    replaced = os.path.isfile(dst)
    # 同名但换了扩展名的旧的那份要清掉，否则 sources() 里两份同名打架
    other = os.path.join(d, name + (".json" if ext == ".txt" else ".txt"))
    if os.path.isfile(other):
        os.remove(other)
        replaced = True
    with open(dst, "w", encoding="utf-8") as f:
        f.write(text)
    r = install(force=[name])          # 明确要换这一份，必须覆盖
    ok = name in r["installed"]
    return {"name": name, "replaced": replaced, "installed": ok,
            "font": getattr(st, "font_name", "") or "",
            "font_size": getattr(st, "font_size", None),
            "failed": r["failed"],
            "msg": (f"「{name}」已{'替换' if replaced else '加入'}，"
                    f"字幕样式下拉里现在能选它了。"
                    if ok else
                    f"「{name}」存下来了，但没能装进 VideoCaptioner："
                    + "；".join(r["failed"] or ["原因不明"]))}


def remove_style(name: str) -> dict:
    """删掉一份**用户自己传的**样式（自带的和 VideoCaptioner 自带的不动）。"""
    name = os.path.splitext(os.path.basename(str(name or "").strip()))[0].strip()
    if not name or set(name) & _BAD_NAME:
        raise ValueError(f"样式名不合法：{name!r}")
    hits = [p for p in _styles_in(user_dir())
            if os.path.splitext(os.path.basename(p))[0] == name]
    if not hits:
        raise ValueError(f"「{name}」不是你传上来的样式 —— "
                         f"自带的四份和 VideoCaptioner 自己的不从这里删。")
    for p in hits:
        os.remove(p)
    # videocaptioner 那边那份也要清，不然下拉里还在、选中了还能用，
    # 而它在用户目录里已经没有了 —— 下次换机器就少了一份，说不清为什么。
    gone = False
    try:
        from videocaptioner.config import SUBTITLE_STYLE_PATH as dst_dir
        p = os.path.join(str(dst_dir), name + ".json")
        if os.path.isfile(p):
            os.remove(p)
            gone = True
    except Exception:                                       # noqa: BLE001
        pass
    return {"name": name, "removed": len(hits), "unregistered": gone,
            "msg": f"「{name}」已删。"}


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
