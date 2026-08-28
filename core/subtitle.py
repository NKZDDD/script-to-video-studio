# -*- coding: utf-8 -*-
"""字幕环节：把成片交给 VideoCaptioner，由它接管识别、断句、优化、压制。

**这一环节自己不做字幕。** 它只做三件事：把设置页填的东西翻译成
VideoCaptioner 认的 config.toml、按顺序调它的两个子命令、把它的退出码
翻译成人看得懂的话。字幕怎么断句、怎么纠错、怎么排版，全是它的事。

为什么是外部程序而不是把代码搬进来
--------------------------------
VideoCaptioner 是 **GPL-3.0**。当独立命令行工具调用不受影响，
把它的代码拷进本仓库会传染整个项目的许可证。所以这里只有 subprocess，
一行它的代码都没有。

为什么每次跑都自己写一份 config.toml
----------------------------------
它默认读 `~/.config/videocaptioner/config.toml`。如果依赖那份：

  1. 用户在 studio 设置页填的 key **不生效**（它读的是另一个文件），
     而页面上照旧显示着填好的值 —— 又一个「填了白填」。
  2. studio 反过来去写用户的全局配置，会覆盖掉他手动用
     `videocaptioner config set` 配的东西。

它有 `--config FILE` 这个全局参数，所以每次跑生成一份临时的传进去：
**studio 设置页是唯一权威，两边互不干扰。**

分两步而不是一把 `process`
------------------------
`process` 一条命令能从视频直接出压好字幕的成片，但中间的 srt 留不下来。
分成 `transcribe`（出 srt）+ `synthesize`（压制）有两个好处：

  - srt 是**可看可改**的产物。识别错了人名，改那一行再跑压制就行，
    不用重新识别一遍。
  - 和本程序的续跑逻辑一致：**以磁盘产物为准**。srt 在就跳过识别，
    带字幕的成片在就跳过压制。

失败了不阻断交付
--------------
成片在环节12 就已经出来了。字幕没做成是**少几行字**，不是成片坏了 ——
和「缺一段不许拼」（那个是成片短一截，必须停）不是一回事。
所以这一步失败只记提醒，不把整条流水线判失败。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Callable, Optional

from . import paths, probe
from .store import Project

# 它的退出码（cli/exit_codes.py）。翻译成人话时要按码分开说 ——
# 「依赖缺失」和「API 调用失败」的处理方式完全不同，都报「跑失败了」
# 等于让人从头猜。
EXIT_OK = 0
EXIT_GENERAL = 1
EXIT_USAGE = 2
EXIT_FILE_NOT_FOUND = 3
EXIT_DEPENDENCY_MISSING = 4
EXIT_RUNTIME = 5

_EXIT_ZH = {
    EXIT_GENERAL: "参数不对或者必需的配置没填",
    EXIT_USAGE: "命令行参数不合法",
    EXIT_FILE_NOT_FOUND: "它找不到输入文件",
    EXIT_DEPENDENCY_MISSING: "它缺依赖（ffmpeg / yt-dlp / 模型文件之类）",
    EXIT_RUNTIME: "识别或接口调用失败",
}

# 设置页那一组的默认值。键名和 VideoCaptioner 的 config.toml 一一对应，
# 不另起一套名字 —— 对不上的时候好查。
DEFAULTS = {
    "enabled": False,
    "asr": "bijian",              # 免费、不用 key、不用下模型
    "language": "auto",
    "optimize": True,             # 让 LLM 纠错、顺句子
    "translate": False,
    "target_language": "zh-Hans",
    "translate_service": "bing",  # 免费；选 llm 就走下面那套 key
    # hard=烧录进画面。**默认是 hard，因为样式只在 hard 下生效** ——
    # VideoCaptioner 自己在样式清单末尾写着「Styles only apply to hard
    # subtitles；Soft subtitles are rendered by the video player」。
    # 用户备了四份样式（中/英 × 横/竖屏），选 soft 的话那四份一个字都不起
    # 作用，而且**不报错**：片子出来就是播放器的默认字幕样子。
    # 代价是整片重编码（慢、掉一次画质、字幕不可关）—— 这是用户 2026-08-27
    # 明确定的：「改成硬字幕，让样式生效」。
    "subtitle_mode": "hard",
    # 空 = 用 VideoCaptioner 自己的默认。**不能填 "default"** ——
    # 它的样式清单里根本没有叫这个名字的样式，那是提示文字里的一个词。
    # 填了它等于指向一个不存在的样式。
    "style": "",
    # 「字幕微调」：把所选样式的某几个值盖掉。空 = 完全按样式走。
    # 存成嵌套 dict 而不是一堆 style_font_size 平铺键 —— 平铺的话
    # 每加一个旋钮都要动 DEFAULTS、动 merged 的过滤、动前端三张表，
    # 而这一组就是「样式的字段」，本来就是一个整体。
    "style_tweak": {},
    "quality": "medium",
    "max_word_count_cjk": 18,
    "max_word_count_english": 12,
    "llm_api_base": "",
    "llm_api_key": "",
    "llm_model": "",
    "timeout": 3600,
}


# 可以**按剧**覆盖的键。字幕的观感是剧的属性：这部剧是竖屏中文、
# 那部剧是横屏英文，样式、字体、断句长度都不一样。
#
# 识别引擎也在里面（2026-08-27 用户定的）：有的剧用付费的 whisper-api
# 求准、有的用免费的必剪省钱，那它就是剧的属性。
PROJECT_KEYS = {
    "enabled", "asr", "language", "subtitle_mode", "style", "style_tweak",
    "optimize", "translate", "translate_service", "target_language",
    "max_word_count_cjk", "max_word_count_english", "quality",
}

# 只在设置页、所有剧共用，**剧级盖不了**。
#
#   llm_*   是账号级的凭据。做成剧级的话，40 部剧要填 40 遍同一把 key，
#           而且 key 会散落在 40 个项目目录里跟着备份到处走。
#   timeout 是这台机器快慢，不是剧的属性。
GLOBAL_ONLY_KEYS = {"llm_api_base", "llm_api_key", "llm_model", "timeout"}

PROJECT_META_KEY = "subtitle"


# 剧级面板的字段表。**schema 由后端给，前端只管渲染** ——
# 和「项目基础信息」那张面板一个做法（见 /api/project/settings 的注释：
# 「页面不该自己维护一份字段表，字段一改就得两边同步，
# 漏一处就是『填了没生效』而且不报错」）。
#
# 每个字段在剧级面板上都是**三态**：跟随全局 / 自定义。所以没有 default ——
# 默认值是「跟随」，具体的数由全局那一层给。
PROJECT_FIELDS = [
    {"key": "enabled", "label": "这部剧要不要字幕", "type": "bool",
     "group": "开关"},
    {"key": "subtitle_mode", "label": "字幕怎么加进成片", "type": "enum",
     "options": ["hard", "soft"], "group": "开关",
     "zh": {"hard": "烧录进画面（样式生效；整片重编码）",
            "soft": "内封软字幕轨（快；样式不生效）"}},
    {"key": "style", "label": "字幕样式", "type": "style", "group": "观感",
     "hint": "只在「烧录进画面」时生效"},
    {"key": "style_tweak", "label": "字幕微调", "type": "tweak",
     "group": "观感"},
    {"key": "asr", "label": "识别引擎", "type": "enum", "group": "识别",
     "options": ["bijian", "jianying", "faster-whisper", "whisper-cpp",
                 "whisper-api"],
     "zh": {"bijian": "必剪（免费）", "jianying": "剪映（免费）",
            "faster-whisper": "faster-whisper（本机，要下模型）",
            "whisper-cpp": "whisper-cpp（本机，要下模型）",
            "whisper-api": "Whisper API（按量收费）"}},
    {"key": "language", "label": "识别语言", "type": "text", "group": "识别",
     "hint": "auto / zh / en / ja…"},
    # 「要不要花 LLM 的钱润这部剧的字幕」是剧的选择；**key 是全局的**。
    # 开关和凭据分在两层，正是这次拆分的意思。
    {"key": "optimize", "label": "让 LLM 纠错、顺句子", "type": "bool",
     "group": "翻译", "hint": "要设置页里配好字幕用的 LLM"},
    {"key": "translate", "label": "翻译成另一种语言", "type": "bool",
     "group": "翻译"},
    {"key": "translate_service", "label": "翻译用什么", "type": "enum",
     "options": ["bing", "google", "llm"], "group": "翻译",
     "when": "translate",
     "zh": {"bing": "Bing（免费）", "google": "Google（免费）",
            "llm": "用设置页配的 LLM（花钱）"}},
    {"key": "target_language", "label": "翻译成", "type": "text",
     "group": "翻译", "when": "translate", "hint": "zh-Hans / en / ja…"},
    {"key": "max_word_count_cjk", "label": "中文每行最多几个字",
     "type": "int", "group": "断句", "min": 6, "max": 40,
     "hint": "和字号、画幅一起决定会不会超出画面宽度"},
    {"key": "max_word_count_english", "label": "英文每行最多几个词",
     "type": "int", "group": "断句", "min": 4, "max": 30},
    {"key": "quality", "label": "压制画质", "type": "enum", "group": "断句",
     "options": ["low", "medium", "high"],
     "zh": {"low": "低（快）", "medium": "中", "high": "高（慢）"}},
]


def project_values(pj) -> dict:
    """这部剧**显式覆盖**了哪几项。没覆盖的键根本不在里面。

    「没覆盖」和「覆盖成和全局一样的值」必须分得开 —— 分不开的话，
    改了全局默认之后，那些「其实只是当时跟全局一样」的剧不会跟着变，
    而人以为它们是继承的。
    """
    raw = (pj.meta() or {}).get(PROJECT_META_KEY) or {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in PROJECT_KEYS}


def save_project(pj, values: dict) -> dict:
    """存这部剧的字幕覆盖。

    值为 None = **清除覆盖，回到继承全局**。用空串表示清除是不行的：
    有的字段（style、language）空串本身就是合法取值。
    """
    meta = dict(pj.meta() or {})
    cur = dict(meta.get(PROJECT_META_KEY) or {})
    for k, v in (values or {}).items():
        if k not in PROJECT_KEYS:
            continue                    # 全局键从剧级面板送上来一律忽略
        if v is None:
            cur.pop(k, None)
        else:
            cur[k] = v
    meta[PROJECT_META_KEY] = cur
    pj.save_meta(meta)
    return project_values(pj)


def migrate(sub: dict) -> bool:
    """把老 config.json 里已经失效的取值改掉。改了返回 True。

    **只改「一定是错的」，不改「你可能是故意选的」。**

    `style: "default"` 属于前者：早先的版本硬写了这个值，而 VideoCaptioner
    的样式清单里根本没有叫 default 的样式（那是它提示句里的一个词）。
    留着的表现是**字幕照出、样式没生效、不报错**。

    为什么必须在这里改而不是只改 DEFAULTS：`merged()` 是「存了的盖默认的」，
    存进去的 "default" 会一直盖住新默认值 —— 改了 DEFAULTS 对已经跑过一次
    的机器**一点用都没有**，而那正是所有老用户。
    """
    if str(sub.get("style") or "").strip().lower() == "default":
        sub["style"] = ""
        return True
    return False


def merged(cfg: dict, pj=None) -> dict:
    """这次真正生效的一整套字幕设置。

        内置默认 → 全局基础（设置页）→ 本剧

    下面的盖上面的 —— 和这个项目里「本剧的提示词」是同一条继承链
    （见页面上那句「内置 → 全局基础（设置页）→ 本剧」），
    不另起一套心智模型。

    **不传 pj 就只到全局那一层。** 调用方拿得到项目就该传 ——
    不传的表现是「剧级配置填了不生效」，而页面上照旧显示着填好的值。
    """
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (cfg.get("subtitle") or {}).items()
                if k in DEFAULTS})
    if pj is not None:
        # 剧级只能盖 PROJECT_KEYS。llm_* 和 timeout 就算被写进了项目
        # meta（手改过、或者从别处拷来的项目）也不认 —— 凭据的家只有一个。
        out.update(project_values(pj))
    migrate(out)
    return out


# --------------------------------------------------------------------------
# 找到它
# --------------------------------------------------------------------------

def find_cli() -> list:
    """返回调用 VideoCaptioner 的命令前缀，没装就返回空列表。

    两种装法都要认：

      `pip install videocaptioner` 会在 Scripts/ 放一个 videocaptioner.exe，
      正常情况下 which 找得到。但**打包成 exe 之后 PATH 常常不一样**，
      而且用户可能装在另一个解释器的环境里。所以 which 找不到时退回
      `<当前解释器> -m videocaptioner` —— 只要它和 studio 装在同一个
      Python 环境里就能跑。

    不做的事：**不自动 pip install**。装包是有副作用的动作，
    偷偷装了用户不知道装了什么、装到哪个环境、占了多少盘。
    没装就明说没装，把命令给他。
    """
    # **打包进来了就用自己。** 2026-08-27 起 videocaptioner 随包发，
    # exe 里通过 `<自己> caption ...` 直通（run.py 的 _caption）。
    # 这一支必须在最前面：目标机器上既没有 Scripts/videocaptioner.exe，
    # `sys.executable` 也不是 python —— 两条老路都走不通，于是字幕这一环
    # **在 exe 里永远报「没装 VideoCaptioner」**，而它明明就在包里。
    # 用户实遇（2026-08-27）：「字幕功能加在哪里了我为什么没看见」。
    if paths.FROZEN:
        try:
            import videocaptioner                        # noqa: F401, PLC0415
        except Exception:                                # noqa: BLE001
            pass
        else:
            return [sys.executable, "caption"]
    exe = shutil.which("videocaptioner")
    if exe:
        return [exe]
    py = sys.executable or ""
    # 打包成 exe 时 sys.executable 是 studio 自己，不是 python —— 那样
    # `-m videocaptioner` 一定跑不起来，别给出一个假的候选。
    if py and os.path.basename(py).lower().startswith("python"):
        try:
            r = probe.run_text([py, "-m", "videocaptioner", "--version"], timeout=60)
            if r.returncode == EXIT_OK:
                return [py, "-m", "videocaptioner"]
        except (OSError, subprocess.SubprocessError):
            pass
    return []


NOT_INSTALLED_FROZEN = (
    "这一版里应该带着 VideoCaptioner，但没调起来 —— 这是包的问题，不是你的。\n"
    "在命令行里跑一次看它说什么：\n"
    "    Respect短剧制作平台.exe caption --version\n"
    "把那几行发回来。")

NOT_INSTALLED = (
    "没找到 VideoCaptioner —— 字幕这一步是交给它做的，得先装：\n"
    "    pip install videocaptioner\n"
    "装完回来再点「开始」，前面已经做好的一步都不会重做。\n"
    "（它是独立的命令行程序，本程序只是调用它；识别用的默认引擎「必剪」"
    "免费、不用填 key、不用下模型。）")


def version(cli: Optional[list] = None) -> str:
    cli = cli if cli is not None else find_cli()
    if not cli:
        return ""
    try:
        r = probe.run_text(cli + ["--version"], timeout=60)
        return (r.stdout or r.stderr or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def selftest(cfg: dict) -> dict:
    """设置页的「运行自检」：装没装、它自己的依赖齐不齐。

    走它的 `doctor` 子命令 —— 那正是它给这件事准备的。
    零成本：不识别、不调接口。
    """
    cli = find_cli()
    if not cli:
        return {"ok": False, "installed": False,
                "msg": (NOT_INSTALLED_FROZEN if paths.FROZEN
                        else NOT_INSTALLED)}
    # **调它之前先把 ffmpeg 摆好。** 不摆的话它的 doctor 报「ffmpeg not found」，
    # 而机器上有一个 —— 只是名字不叫 ffmpeg.exe（imageio-ffmpeg 带的）。
    try:
        from . import captions                            # noqa: PLC0415
        captions.ensure_ffmpeg()
    except Exception:                                     # noqa: BLE001
        pass
    ver = version(cli)
    out = {"ok": True, "installed": True, "version": ver,
           "cmd": " ".join(cli), "msg": f"已安装 {ver}"}
    try:
        r = probe.run_text(cli + ["doctor"], timeout=300)
        out["doctor"] = ((r.stdout or "") + (r.stderr or "")).strip()[:4000]
        blocking, aside = _split_doctor(out["doctor"])
        out["aside"] = aside
        if blocking:
            out["ok"] = False
            out["msg"] = (f"装是装了（{ver}），但这几项会挡住字幕："
                          + "；".join(blocking[:3]))
        elif aside:
            out["msg"] = (f"已安装 {ver}　可用。"
                          f"它的自检还报了 {len(aside)} 项，"
                          f"但都不影响我们用的那两步（转写 + 压制）")
    except (OSError, subprocess.SubprocessError) as exc:
        out["doctor"] = f"doctor 跑不起来：{exc}"
    return out


# 它的 doctor 会报一堆 ERROR，但**我们只用两个子命令**：
# `transcribe`（出 srt）+ `synthesize`（压制）。照抄它的总判决就会把
# 「能用」报成「不能用」—— 而那种误报比漏报更贵：人会去装一堆用不上的
# 东西，或者干脆以为这功能坏了（实遇 2026-08-27）。
#
# 逐项查过它的源码，这几项和我们无关：
#   ffprobe  只在 doctor 自己和 `core/dubbing/audio.py`（配音）里用到 ——
#            转写和压制一次都不碰
#   yt-dlp   只给 `download`（从网上下视频）用，我们的片子是本地产的
#   python   它声明 <3.13 的真原因是 pydub 要 audioop（3.13 删了标准库那个），
#            装了 audioop-lts 就跑得通 —— 实测全部子命令正常
_DOCTOR_ASIDE = ("ffprobe", "yt-dlp", "yt_dlp", "python")


def _split_doctor(text: str) -> tuple:
    """doctor 的输出 → (会挡住字幕的, 不影响我们的)。

    只看 ERROR 行。WARN 是「没配 key」这类，配了才用得上某些功能，
    不该让整个自检变红。
    """
    stop, aside = [], []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s.upper().startswith("ERROR"):
            continue
        body = s[5:].strip(" :")
        if not body:
            continue
        # 「Doctor found 4 error(s) and 4 warning(s)」是**汇总行**，
        # 不是一项检查。算进去的话永远至少有一项「挡住字幕」，
        # 于是自检永远红着 —— 而那一行本身什么信息都没多给。
        if body.lower().startswith("doctor found"):
            continue
        who = body.split(":")[0].strip().lower()
        (aside if any(k in who for k in _DOCTOR_ASIDE) else stop).append(body[:120])
    return stop, aside


def _why(code: int) -> str:
    return _EXIT_ZH.get(code, f"退出码 {code}")


# --------------------------------------------------------------------------
# 把设置翻译成它的 config.toml
# --------------------------------------------------------------------------

def _toml_str(v: str) -> str:
    """TOML 基本字符串。Windows 路径里的反斜杠必须转义，不然它读出来是转义符。"""
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_config(st: dict) -> str:
    """按设置生成一份完整的 config.toml 文本。

    只写我们管的键，其余由它自己的默认值补 —— 把它整张默认表抄一遍的话，
    它升级改了默认值，我们这份会**悄悄把旧默认值钉死**。
    """
    lines = ["# 本文件由 Respect短剧制作平台自动生成，每次跑都会重写。",
             "# 想改请去「设置 → 字幕」，手改这里下次会被覆盖。", ""]

    def sec(name: str, kv: list) -> None:
        kv = [(k, v) for k, v in kv if v != ""]
        if not kv:
            return
        lines.append(f"[{name}]")
        for k, v in kv:
            if isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                lines.append(f"{k} = {v}")
            else:
                lines.append(f"{k} = {_toml_str(v)}")
        lines.append("")

    sec("llm", [("api_key", st.get("llm_api_key", "")),
                ("api_base", st.get("llm_api_base", "")),
                ("model", st.get("llm_model", ""))])
    sec("transcribe", [("asr", st.get("asr", "bijian")),
                       ("language", st.get("language", "auto"))])
    sec("subtitle", [("optimize", bool(st.get("optimize", True))),
                     ("translate", bool(st.get("translate", False))),
                     ("max_word_count_cjk", int(st.get("max_word_count_cjk", 18))),
                     ("max_word_count_english",
                      int(st.get("max_word_count_english", 12)))])
    sec("translate", [("service", st.get("translate_service", "bing")),
                      ("target_language", st.get("target_language", "zh-Hans"))])
    # style 空就整个键不写（sec() 会把空值过滤掉），让它用自己的默认。
    # 写成空串或者一个不存在的名字，表现都是「样式没生效」而不报错。
    sec("synthesize", [("subtitle_mode", st.get("subtitle_mode", "hard")),
                       ("quality", st.get("quality", "medium")),
                       ("style", str(st.get("style") or "").strip())])
    return "\n".join(lines) + "\n"


def list_styles() -> list:
    """VideoCaptioner 现在认得哪些样式。给设置页那个下拉用。

    返回 [{"name":…, "note":…}]。note 里放字号和底边距 ——
    「中文横屏标准版」和「中文竖屏标准版」光看名字分不出哪个配 9:16，
    看字号 50/40、底边距 58/138 就一目了然。

    **从它那里读，不读我们自己的 `字幕样式/` 目录。** 两者不是一回事：
    我们只是把自带的四份装进去（见 core/captions），用户自己在那边加的、
    改的、删的都算数，而那些我们目录里没有。列我们的目录 =
    页面上列四份、实际可用的是另一批，选中一个不存在的还不报错。

    列之前先 install() 一次：新机器上那个目录第一次跑才建，
    不装的话下拉是空的，而用户明明备好了四份样式。
    """
    try:
        from . import captions                               # noqa: PLC0415
        captions.install()
    except Exception:                                        # noqa: BLE001
        pass
    try:
        from videocaptioner.core.subtitle.style_manager import (  # noqa: PLC0415
            list_styles as _ls)
    except Exception:                                        # noqa: BLE001
        return []
    out = []
    try:
        for s in _ls():
            name = str(getattr(s, "name", "") or "").strip()
            if not name:
                continue
            bits = []
            if getattr(s, "font_size", None):
                bits.append(f"{s.font_size}号")
            if getattr(s, "font_name", None):
                bits.append(str(s.font_name))
            if getattr(s, "margin_bottom", None) is not None:
                bits.append(f"底边距{s.margin_bottom}")
            out.append({"name": name, "note": " · ".join(bits)})
    except Exception:                                        # noqa: BLE001
        return []
    return sorted(out, key=lambda x: x["name"])


def style_names() -> list:
    return [s["name"] for s in list_styles()]


# 设置页「字幕微调」那一组：**所选样式的哪些值可以在页面上盖掉**。
#
# 这张表就是 ASS 模式下真正起作用的全部旋钮，一个不多一个不少 ——
# 对着 videocaptioner 的 `SubtitleStyle.to_ass_string()` 逐个核过的。
#
#   scaled: 会被 `_scale_ass_style` 乘缩放系数的字段。竖屏要补偿的就是这几个
#           （见 scale_compensation），颜色和字体名不缩放。
#
# **行间距（line_spacing）不在这里，因为 ASS 模式根本没有这一项。**
# 它只存在于 `to_rounded_dict()`（圆角背景块那种渲染模式）。放进来的话，
# 人填了没反应，而且不报错 —— 正是这一批改动一直在消灭的那类失效。
STYLE_FIELDS = [
    {"key": "font_name",      "label": "字体",       "type": "font"},
    {"key": "font_size",      "label": "字号",       "type": "int",
     "scaled": True, "min": 8, "max": 200},
    {"key": "margin_bottom",  "label": "垂直间距",   "type": "int",
     "scaled": True, "min": 0, "max": 2000,
     "hint": "字幕离画面底边多少像素 —— 数越大越往上"},
    {"key": "primary_color",  "label": "字幕颜色",   "type": "color"},
    {"key": "outline_color",  "label": "描边颜色",   "type": "color"},
    {"key": "outline_width",  "label": "描边粗细",   "type": "float",
     "scaled": True, "min": 0, "max": 20},
    {"key": "spacing",        "label": "字符间距",   "type": "float",
     "scaled": True, "min": -10, "max": 50,
     "hint": "字与字之间加多少像素；0 = 按字体本身的间距"},
    {"key": "bold",           "label": "加粗",       "type": "bool"},
]

STYLE_KEYS = [f["key"] for f in STYLE_FIELDS]

# ASS 模式下没有的东西。人问起来时要能说清「不是没做，是它没有」。
STYLE_NOT_AVAILABLE = {
    "line_spacing": "行间距：ASS 模式（也就是这四份样式用的模式）没有这一项，"
                    "行距由字体本身的行高决定。只有「圆角背景块」那种渲染模式"
                    "才有行间距，但那是完全不同的观感。",
}


def style_tweak(cfg: dict) -> dict:
    """设置页填的微调值。只留认识的键、只留填了的。"""
    raw = (cfg.get("subtitle") or {}).get("style_tweak") or {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for f in STYLE_FIELDS:
        k = f["key"]
        if k not in raw:
            continue
        v = raw[k]
        # 空串 = 没填 = 用样式自己的值。**不能当成 0 或空字体名** ——
        # 那会把「没动过」变成「把字号设成 0」。
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        try:
            if f["type"] == "int":
                v = int(float(v))
            elif f["type"] == "float":
                v = float(v)
            elif f["type"] == "bool":
                v = bool(v)
            else:
                v = str(v).strip()
        except (TypeError, ValueError):
            continue                                    # 填了个非数字，忽略
        if f["type"] in ("int", "float"):
            lo, hi = f.get("min"), f.get("max")
            if lo is not None and v < lo:
                v = lo
            if hi is not None and v > hi:
                v = hi
        out[k] = v
    return out


# VideoCaptioner 的「参考高度」。它按 `height / 720` 缩放整份样式
# （`core/subtitle/ass_renderer.py` 的 `_scale_ass_style`），
# 意思是「样式是按 720P 写的，按视频高度等比放大」。
VC_REFERENCE_HEIGHT = 720

# 被它缩放的字段 → 样式 json 里对应的键。四个都要补，只补字号不行：
# 描边和底边距跟着放大 1.78 倍时，字幕会又粗又高，跟写的完全不是一回事。
_SCALED_FIELDS = ("font_size", "outline_width", "spacing", "margin_bottom")


def scale_compensation(width: int, height: int) -> float:
    """VideoCaptioner 会把样式乘多少。返回 1.0 表示不用补。

    **它这个缩放对竖屏是错的。** `height / 720` 的前提是「720P = 1280×720
    的横屏片」，取的是短边。竖屏片 720×1280 的 height 是 **1280**（长边），
    于是 scale = 1.78 —— 字号 40 渲染成 71、底边距 138 变成 245，
    18 个字一行按 71px 要 1278px，而画面只有 720px 宽，两头切掉。

    横屏 1280×720 反而正好 ×1.0，一点事没有 —— 所以这个坑只在竖屏踩到，
    而短剧全是竖屏。

    只在**竖屏**（高 > 宽）时补：横屏本来就是对的，去动它等于制造新问题。
    """
    if not width or not height or height <= width:
        return 1.0
    return height / VC_REFERENCE_HEIGHT


def preset_values(name: str) -> dict:
    """所选样式各字段的当前值。给微调框当 placeholder 用。

    看得见「不填是多少」，人才知道自己在改什么 —— 对着一排空框猜
    「字号默认多少」的话，多半会填一个数进去，于是本来不该动的字段
    也被覆盖了。
    """
    st = _preset(name) if name else None
    if st is None:
        return {}
    out = {}
    for f in STYLE_FIELDS:
        v = getattr(st, f["key"], None)
        if v is not None:
            out[f["key"]] = v
    return out


def _preset(name: str):
    try:
        from videocaptioner.core.subtitle.style_manager import (  # noqa: PLC0415
            load_style)
        return load_style(name, mode="ass")
    except Exception:                                        # noqa: BLE001
        return None


def style_override(name: str, width: int, height: int,
                   tweak: Optional[dict] = None) -> dict:
    """发给 `--style-override` 的那个 dict：微调值 + 竖屏缩放补偿。

    两件事必须一起做，不能分开：

      · 微调是「把所选样式的某个值盖掉」
      · 补偿是「把最终值预先除掉，让它乘回去之后等于这个数」

    分开做的结果是**微调过的字段不补偿** —— 你在页面上把字号改成 50，
    出来的是 89（50×1.78），而没改的字段都是对的。那种错最难查：
    一部分对一部分不对。

    走 `--style-override` 而不是改 config.toml：它在 `to_ass_string()`
    **之前**合并进样式，而缩放在之后 —— 所以覆盖值同样会被乘，必须先除。
    （顺序反了的话就不该除，那会把字缩到一半大。）
    """
    tweak = style_tweak({"subtitle": {"style_tweak": tweak or {}}}) \
        if tweak and not isinstance(tweak, dict) else dict(tweak or {})
    sf = scale_compensation(width, height)
    if sf == 1.0 and not tweak:
        return {}                                   # 横屏 + 没微调 = 不用发

    st = _preset(name) if name else None
    out = {}

    # 1) 不缩放的字段：微调了就原样发过去
    for f in STYLE_FIELDS:
        k = f["key"]
        if not f.get("scaled") and k in tweak:
            out[k] = tweak[k]

    # 2) 缩放的字段：**最终值**（微调优先，没微调就用样式自己的）÷ 系数
    for k in _SCALED_FIELDS:
        v = tweak.get(k, getattr(st, k, None) if st else None)
        if not isinstance(v, (int, float)):
            continue
        if v == 0 and k not in tweak:
            continue                                # 样式本身就是 0，不用发
        if sf == 1.0:
            out[k] = v                              # 横屏：只是微调，不用除
        else:
            # 字号和底边距它取 int()，除出来的小数会被截断 —— 先四舍五入，
            # 少一个像素不要紧，但 truncate 累积起来会矮一截。
            out[k] = round(v / sf) if k in ("font_size", "margin_bottom") \
                else round(v / sf, 3)

    # 3) 副样式（双语字幕的第二行）走同一条缩放，也得补，
    #    否则开了翻译之后译文那行还是偏大。
    #    微调了主字号时按样式里主副的比例跟着走 —— 不然改完主字号，
    #    译文那行还是老样式的大小，两行比例失调。
    sec = getattr(st, "secondary", None) if st else None
    sec_size = getattr(sec, "font_size", None) if sec else None
    if sec_size:
        base = getattr(st, "font_size", None)
        if "font_size" in tweak and base:
            sec_size = sec_size * tweak["font_size"] / base
        out.setdefault("secondary", {})["font_size"] = round(sec_size / sf)
    return out


def config_problems(st: dict) -> list:
    """跑之前能查出来的配置问题。**在花时间识别之前说。**

    识别一集要几分钟，跑完了才报「LLM 没填 key」，那几分钟就白等了。
    """
    bad = []
    if st.get("optimize") and not str(st.get("llm_api_key") or "").strip():
        bad.append("勾了「让 LLM 优化字幕」但没填 API Key —— "
                   "要么去「设置 → 字幕」填上，要么把优化关掉"
                   "（关掉也能出字幕，只是不纠错、不顺句子）")
    if (st.get("translate")
            and st.get("translate_service") == "llm"
            and not str(st.get("llm_api_key") or "").strip()):
        bad.append("翻译选了「用 LLM」但没填 API Key —— "
                   "换成 bing / google（免费）或者把 key 填上")
    # ---- 样式：两种静默失效，都要在开跑前拦 ----
    #
    # 这一组是补一个**踩到过的**洞：程序原来硬写 style="default"，而它的
    # 样式清单里根本没有叫这个名字的样式；同时默认走 soft，而样式只在
    # hard 下生效。两处叠起来的表现是「样式一个字都不起作用，且不报错」——
    # 用户备好的四份样式白备（2026-08-27：「我的那4份字幕样式呢」）。
    style = str(st.get("style") or "").strip()
    if style and st.get("subtitle_mode") != "hard":
        bad.append(
            f"选了字幕样式「{style}」，但「字幕怎么加进成片」是软字幕 —— "
            f"**样式只在烧录进画面（硬字幕）时才生效**，"
            f"软字幕长什么样是播放器定的。"
            f"要用这份样式就改成「烧录进画面」；"
            f"想保持软字幕就把样式清空，免得以为它在起作用。")
    # 字体名写错的表现是**静默回落成默认字体**：片子照出、字幕照有、
    # 字体不是你要的那个，没有任何一处报错。所以在烧录之前核一次。
    font = str((st.get("style_tweak") or {}).get("font_name") or "").strip()
    if font and st.get("subtitle_mode") == "hard":
        try:
            from . import captions                          # noqa: PLC0415
            fonts = captions.system_fonts()
        except Exception:                                    # noqa: BLE001
            fonts = []
        # 列不出来（非 Windows、注册表读不到）就不拦 —— 把「我们读不到」
        # 报成「你的字体不存在」是另一种错。
        if fonts and font not in fonts:
            near = [f for f in fonts if font[:2] in f][:5]
            bad.append(
                f"字幕微调里的字体「{font}」在这台机器上找不到，"
                f"烧出来会**静默变成另一个字体**（不报错）。"
                + (f"是不是想填：{'、'.join(near)}？" if near
                   else "去「设置 → 字幕 → 字幕微调」的字体框里挑一个。"))
    if style:
        known = style_names()
        # 列不出来（VideoCaptioner 没装好）时不拦 —— 那是另一个问题，
        # 由「没装」那条去报，在这儿再报一遍只会把人往样式上引。
        if known and style not in known:
            bad.append(
                f"字幕样式「{style}」在 VideoCaptioner 里找不到。"
                f"现在认得的是：{'、'.join(known)}。"
                f"（样式名对不上的表现是**字幕照出、但还是默认样子**，不报错，"
                f"所以在这儿先拦下。）")
    if st.get("translate") and not str(st.get("target_language") or "").strip():
        bad.append("勾了翻译但没填目标语言")
    return bad


# --------------------------------------------------------------------------
# 跑
# --------------------------------------------------------------------------

def _run(cli: list, args: list, cfg_path: str, timeout: int,
         log: Callable) -> subprocess.CompletedProcess:
    cmd = cli + args + ["--config", cfg_path, "--quiet"]
    # 打日志时**把 key 摘掉**。这里传的是文件路径不是 key，
    # 所以命令行本身不含密钥 —— 这条注释是留给以后想改成 --api-key 的人：
    # 别改，改了 key 就会出现在日志、诊断包和支持包里。
    log("  " + " ".join(os.path.basename(c) if i == 0 else c
                        for i, c in enumerate(cmd)))
    return probe.run_text(cmd, timeout=timeout)


def _has_subtitle_track(path: str) -> Optional[bool]:
    """成片里已经有字幕轨了吗。读不出来返回 None（不是 False）。

    None 和 False 必须分开：读不出来时按「没有」处理会**每次跑都重压一遍**，
    而软字幕重压是流拷贝，看不出异常，只是每次都多花一分钟。
    """
    ff = probe.find_ffprobe()
    if ff:
        try:
            r = probe.run_text(
                [ff, "-v", "error", "-select_streams", "s",
                 "-show_entries", "stream=index", "-of", "csv=p=0", path],
                timeout=120)
            if r.returncode == 0:
                return bool((r.stdout or "").strip())
        except (OSError, subprocess.SubprocessError):
            pass
    ff = probe.find_ffmpeg()
    if not ff:
        return None
    try:
        r = probe.run_text([ff, "-i", path], timeout=120)
        out = (r.stderr or "")
        if "Stream #" not in out:
            return None
        return any("Subtitle:" in ln for ln in out.splitlines())
    except (OSError, subprocess.SubprocessError):
        return None


def _masters(pj: Project, episode: str = "") -> list:
    """环节12 拼出来的成片。按文件名排序，一集一个。"""
    d = pj.p("06_成片")
    if not os.path.isdir(d):
        return []
    out = []
    for f in sorted(os.listdir(d)):
        if not f.lower().endswith(".mp4"):
            continue
        if "_SUB" in f:                       # 我们自己压出来的，不是源
            continue
        if episode and f"_{episode}_" not in f and not f.startswith(f"{episode}_"):
            continue
        out.append(os.path.join(d, f))
    return out


def run(pj: Project, cfg: dict, log: Callable = print,
        episode: str = "") -> dict:
    """给这一集（或全部集）的成片配字幕。

    返回 {"done": [...], "skipped": [...], "problems": [...]}。
    **有问题也返回，不抛**（除非连成片都没有）—— 见模块开头：
    这一步失败不阻断交付。
    """
    # **带上 pj**：字幕配置是剧维度的，不传的话这部剧填的东西一律不生效，
    # 而页面上照旧显示着填好的值 —— 又一个「填了白填」。
    st = merged(cfg, pj)
    if not st.get("enabled"):
        return {"done": [], "skipped": [], "problems": [],
                "msg": "这部剧没开字幕（流程页 →「本剧的字幕」）"}

    masters = _masters(pj, episode)
    if not masters:
        raise RuntimeError(
            "没有成片可以配字幕 —— 字幕是加在环节12 拼好的成片上的，"
            "先把拼接跑完。")

    cli = find_cli()
    if not cli:
        return {"done": [], "skipped": [],
                "problems": [NOT_INSTALLED_FROZEN if paths.FROZEN
                             else NOT_INSTALLED],
                "msg": "VideoCaptioner 没装"}

    # **样式和 ffmpeg 都要在检查之前摆好。**
    # 顺序很重要：config_problems 会去核对样式名存不存在，而样式是
    # install() 装进去的 —— 反过来的话，新机器上第一次跑必然报
    # 「样式找不到」，而它下一秒就会被装上。
    try:
        from . import captions                            # noqa: PLC0415
        captions.install(log)
        captions.ensure_ffmpeg(log)
    except Exception as exc:                              # noqa: BLE001
        log(f"  ⚠ 字幕样式 / ffmpeg 没摆好：{exc}")

    bad = config_problems(st)
    if bad:
        return {"done": [], "skipped": [], "problems": bad,
                "msg": "字幕设置还差点东西，见下面"}

    # 临时 config.toml 放项目里，不放系统临时目录：出问题时它和产物在一起，
    # 支持包一打就带上了，不用再问用户「你那个 toml 里写的什么」。
    cfg_dir = pj.p("07_检查与记录")
    os.makedirs(cfg_dir, exist_ok=True)
    cfg_path = os.path.join(cfg_dir, "videocaptioner.toml")
    with open(cfg_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(build_config(st))

    timeout = max(300, int(st.get("timeout") or 3600))
    done, skipped, problems = [], [], []

    for m in masters:
        name = os.path.basename(m)
        stem = os.path.splitext(m)[0]
        srt = stem + ".srt"

        # ---- 第一步：识别出 srt --------------------------------------
        if os.path.isfile(srt) and os.path.getsize(srt) > 0:
            log(f"{name} 的字幕文件已经有了，跳过识别")
        else:
            log(f"{name} 识别中（引擎 {st.get('asr')}）…")
            try:
                r = _run(cli, ["transcribe", m, "-o", srt], cfg_path, timeout, log)
            except subprocess.TimeoutExpired:
                problems.append(f"{name}：识别超过 {timeout} 秒还没结束，已放弃。"
                                f"可以在「设置 → 字幕」把超时调大。")
                continue
            except OSError as exc:
                problems.append(f"{name}：调不起 VideoCaptioner —— {exc}")
                continue
            if r.returncode != EXIT_OK or not os.path.isfile(srt):
                problems.append(_fail_msg(name, "识别", r, srt))
                continue
            log(f"{name} 字幕识别完成 → {pj.rel(srt)}")

        # ---- 第二步：压进成片 ----------------------------------------
        out = f"{stem}_SUB.mp4"
        hard = st.get("subtitle_mode") == "hard"

        # **「做过没有」在两种模式下判据不一样。**
        #
        # 软字幕会在文件里留下一条字幕轨，查得到；**硬字幕是烧进画面的，
        # 查不到任何字幕轨** —— 拿「有没有字幕轨」当判据的话，硬字幕永远
        # 判成「没做过」，每跑一次就重编码一次（一次几分钟，且不报错）。
        #
        # 反过来更坑：从软字幕切到硬字幕时，源成片上还留着上一轮压进去的
        # 字幕轨，于是这里判「已经有了，跳过」——**用户改了设置却什么都没变**，
        # 而全程没有一处说过它跳过了。
        #
        # 所以硬字幕只认产物文件在不在，不看字幕轨。
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            log(f"{name} 的带字幕成片已经有了，跳过压制")
            done.append({"master": pj.rel(m), "srt": pj.rel(srt),
                         "muxed": pj.rel(out), "reused": True})
            continue
        if not hard:
            has = _has_subtitle_track(m)
            if has is True:
                log(f"{name} 里已经有字幕轨了，跳过压制")
                done.append({"master": pj.rel(m), "srt": pj.rel(srt),
                             "muxed": pj.rel(m), "reused": True})
                continue
            if has is None:
                # 读不出来就别赌。压到新文件上，源片不动 —— 最坏情况是多一个
                # 文件，而不是把成片重压了一遍或者压了两层。
                log(f"{name} 读不出有没有字幕轨（缺 ffprobe？），压到新文件上")
        # **样式补偿按每一集的真实分辨率算，不按设置里的比例。**
        # 设置里的「画面比例」是给出图出片用的意图值，成片实际是多少像素
        # 只有文件本身知道 —— 换过服务商、中途改过尺寸，两者就对不上，
        # 而补偿系数算错的表现是「字幕大小莫名其妙」，不报错。
        extra = []
        style = str(st.get("style") or "").strip()
        if hard and style:
            wh = probe.video_size(m) or (0, 0)
            ov = style_override(style, wh[0], wh[1], st.get("style_tweak") or {})
            if ov:
                sf = scale_compensation(wh[0], wh[1])
                log(f"{name} {wh[0]}×{wh[1]} 竖屏 —— VideoCaptioner 会把样式"
                    f"放大 {sf:.2f} 倍，已预先补偿（字号 "
                    f"{ov.get('font_size')}→渲染成你写的那个数）")
                extra = ["--style-override", json.dumps(ov, ensure_ascii=False)]
            elif wh == (0, 0):
                log(f"{name} 读不出分辨率，样式不做补偿 —— "
                    f"竖屏的话字幕会偏大，检查一下 ffprobe")
            log(f"{name} 用样式「{style}」烧录 —— 整片重编码，会比软字幕慢不少")
        log(f"{name} 压制字幕（{'内封软字幕' if st.get('subtitle_mode') == 'soft' else '烧录进画面'}）…")
        try:
            r = _run(cli, ["synthesize", m, "-s", srt, "-o", out] + extra,
                     cfg_path, timeout, log)
        except subprocess.TimeoutExpired:
            problems.append(f"{name}：压制超过 {timeout} 秒还没结束。"
                            f"字幕文件已经出来了（{pj.rel(srt)}），"
                            f"可以自己用播放器挂上。")
            continue
        except OSError as exc:
            problems.append(f"{name}：压制调不起来 —— {exc}")
            continue
        if r.returncode != EXIT_OK or not os.path.isfile(out):
            problems.append(_fail_msg(name, "压制", r, out)
                            + f"\n字幕文件本身是好的（{pj.rel(srt)}），"
                              f"实在不行可以用播放器挂着看。")
            continue
        done.append({"master": pj.rel(m), "srt": pj.rel(srt),
                     "muxed": pj.rel(out),
                     "size": os.path.getsize(out)})
        log(f"{name} 好了 → {pj.rel(out)}")

    pj.log_event({"stage": "subtitle", "episode": episode or "",
                  "result": "ok" if done and not problems else "partial",
                  "count": len(done), "problems": len(problems)})
    msg = f"{len(done)} 集配好字幕"
    if problems:
        msg += f"，{len(problems)} 集没配上"
    return {"done": done, "skipped": skipped, "problems": problems, "msg": msg}


def _fail_msg(name: str, what: str, r, expect: str) -> str:
    """把它的退出码和输出翻译成一句人话 + 原文。

    原文要留：它自己的报错常常比我们的转述准（比如「必剪接口今天限流」）。
    但只留尾部 —— 前面多半是进度条。
    """
    tail = ((r.stderr or "") + "\n" + (r.stdout or "")).strip()
    tail = "\n".join([ln for ln in tail.splitlines() if ln.strip()][-6:])
    head = f"{name}：{what}没成功 —— {_why(r.returncode)}"
    if r.returncode == EXIT_DEPENDENCY_MISSING:
        head += "。它要 ffmpeg；选了 faster-whisper 的话还要先下模型，" \
                "第一次跑会很慢。也可以换回默认的「必剪」引擎（免费、不用下东西）"
    elif r.returncode == EXIT_RUNTIME:
        head += "。免费引擎（必剪 / 剪映）是公共接口，忙起来会失败，" \
                "过一会儿再点「开始」就是续跑，已经做好的不重做"
    elif r.returncode == EXIT_OK:
        head = f"{name}：{what}说是成功了，但要的文件没出现（{os.path.basename(expect)}）"
    return head + (f"\n它自己的输出：\n{tail}" if tail else "")


def status(pj: Project, cfg: dict, episode: str = "") -> dict:
    """这一步做过没有 —— 「先看会做什么（不花钱）」和续跑判定都用它。"""
    st = merged(cfg, pj)
    if not st.get("enabled"):
        return {"todo": 0, "skip": 0, "reason": "这部剧没开字幕"}
    todo = skip = 0
    # 判据要和 run() 里那一段**完全一致**，否则预览说「0 集要配」而实跑
    # 又做了一遍（或者反过来）。硬字幕不看字幕轨 —— 理由见 run()。
    hard = st.get("subtitle_mode") == "hard"
    for m in _masters(pj, episode):
        stem = os.path.splitext(m)[0]
        out = stem + "_SUB.mp4"
        done_ = (os.path.isfile(out) and os.path.getsize(out) > 0) or (
            not hard and _has_subtitle_track(m) is True)
        if done_:
            skip += 1
        else:
            todo += 1
    return {"todo": todo, "skip": skip,
            "reason": "" if todo else "都配好了"}
