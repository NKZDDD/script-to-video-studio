# -*- coding: utf-8 -*-
"""字幕环节（外包给 VideoCaptioner）。

**没有一个用例真的去调 VideoCaptioner。** 它是外部程序，CI 上不装，
用户机器上装没装也不一定 —— 真调的话这一批用例就变成「装了才过」，
那还不如不写。这里查的是本程序这一侧：设置怎么翻译成它的配置、
它的退出码怎么翻译成人话、没装的时候会不会把整条流水线判死。
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import subtitle as S          # noqa: E402
from core.store import Project          # noqa: E402


# --------------------------------------------------------------------------
# 设置 → config.toml
# --------------------------------------------------------------------------

def test_默认不开():
    """没配过的项目不该突然多出一步字幕。"""
    assert S.merged({})["enabled"] is False


def test_只认识的键才进来():
    """config.json 里混进来的野键不该被塞进 toml。

    塞进去的话 VideoCaptioner 读到不认识的键，行为取决于它的实现 ——
    可能报错、可能忽略，两种都不是我们能控制的。
    """
    st = S.merged({"subtitle": {"enabled": True, "野键": "x"}})
    assert st["enabled"] is True
    assert "野键" not in st


def test_windows_路径要转义():
    """TOML 里反斜杠是转义符。不转义的话 C:\\new 里的 \\n 会变成换行。"""
    toml = S.build_config({**S.DEFAULTS, "llm_api_base": r"C:\new\path"})
    assert r"C:\\new\\path" in toml


def test_空值不写进toml():
    """没填的键别写成空串 —— 那会把 VideoCaptioner 自己的默认值覆盖掉。"""
    toml = S.build_config({**S.DEFAULTS, "llm_api_key": "", "llm_model": ""})
    assert "api_key" not in toml
    assert "model" not in toml


def test_布尔写成toml的true不是python的True():
    toml = S.build_config({**S.DEFAULTS, "optimize": True, "translate": False})
    assert "optimize = true" in toml
    assert "translate = false" in toml
    assert "True" not in toml


def test_数字不加引号():
    """加了引号 VideoCaptioner 读到的是字符串，断句长度就不起作用了。"""
    toml = S.build_config({**S.DEFAULTS, "max_word_count_cjk": 20})
    assert "max_word_count_cjk = 20" in toml
    assert 'max_word_count_cjk = "20"' not in toml


def test_我们不抄它的整张默认表():
    """只写我们管的键。抄全了的话，它升级改默认值，我们这份会把旧值钉死。"""
    toml = S.build_config(dict(S.DEFAULTS))
    for never in ("dubbing", "whisper_api", "faster_whisper"):
        assert f"[{never}]" not in toml


# --------------------------------------------------------------------------
# 开跑之前就该发现的问题
# --------------------------------------------------------------------------

def test_开了优化没填key_跑之前就说():
    """识别一集要几分钟。跑完才报「没填 key」，那几分钟就白等了。"""
    bad = S.config_problems({**S.DEFAULTS, "optimize": True, "llm_api_key": ""})
    assert bad and "Key" in bad[0]


def test_不开优化就不要key():
    assert S.config_problems({**S.DEFAULTS, "optimize": False}) == []


def test_翻译走免费服务不要key():
    """bing / google 免费。这时候拦着要 key 是凭空造出一个门槛。"""
    assert S.config_problems({**S.DEFAULTS, "optimize": False,
                              "translate": True, "translate_service": "bing"}) == []


def test_翻译走llm才要key():
    bad = S.config_problems({**S.DEFAULTS, "optimize": False, "translate": True,
                             "translate_service": "llm", "llm_api_key": ""})
    assert bad and "Key" in bad[0]


# --------------------------------------------------------------------------
# 它的退出码 → 人话
# --------------------------------------------------------------------------

class _R:
    def __init__(self, code, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def test_依赖缺失和接口失败要分开说():
    """都报「跑失败了」等于让人从头猜。这两种的处理方式完全不同。"""
    dep = S._fail_msg("EP01.mp4", "识别", _R(S.EXIT_DEPENDENCY_MISSING), "x.srt")
    api = S._fail_msg("EP01.mp4", "识别", _R(S.EXIT_RUNTIME), "x.srt")
    assert "ffmpeg" in dep
    assert "ffmpeg" not in api and "过一会儿再点" in api


def test_它说成功但文件没出现():
    """退出码 0 不等于产物在。**这一类错最坑**：一路绿灯，结果什么都没有。"""
    msg = S._fail_msg("EP01.mp4", "识别", _R(S.EXIT_OK), "/x/EP01.srt")
    assert "说是成功了" in msg and "EP01.srt" in msg


def test_报错要带上它自己的输出():
    """它自己的报错常常比我们的转述准（「必剪今天限流」这种）。"""
    msg = S._fail_msg("a.mp4", "识别", _R(5, err="bijian: rate limited"), "x")
    assert "rate limited" in msg


def test_只留输出的尾部():
    """前面多半是进度条，全贴出来会把真正的报错顶到看不见的地方。"""
    msg = S._fail_msg("a.mp4", "识别",
                      _R(5, out="\n".join(f"进度 {i}%" for i in range(50))), "x")
    assert msg.count("进度") <= 6


# --------------------------------------------------------------------------
# 没装的时候
# --------------------------------------------------------------------------

def test_没装不抛异常(tmp_path, monkeypatch):
    """**字幕失败不该把整条流水线判死。**

    成片在环节12 就已经交付了，没字幕是少几行字。抛出去的话
    上层会把这一整轮标成失败，而人看到的是「跑失败了」——
    然后去查成片，成片明明是好的。
    """
    monkeypatch.setattr(S, "find_cli", lambda: [])
    pj = Project(str(tmp_path))
    os.makedirs(pj.p("06_成片"), exist_ok=True)
    open(pj.p("06_成片", "X_EP01_MASTER_V01_FIXED.mp4"), "wb").write(b"x")
    r = S.run(pj, {"subtitle": {"enabled": True}})
    assert r["done"] == []
    assert r["problems"] and "pip install videocaptioner" in r["problems"][0]


def test_没装的提示要给出安装命令():
    assert "pip install videocaptioner" in S.NOT_INSTALLED


def test_没开的时候什么都不做(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(S, "find_cli", lambda: called.append(1) or [])
    r = S.run(Project(str(tmp_path)), {})
    assert r["done"] == [] and not called


def test_没成片要说清楚是先跑拼接(tmp_path):
    """「没有成片」得告诉人上一步是什么，不然他不知道去哪儿补。"""
    pj = Project(str(tmp_path))
    with pytest.raises(RuntimeError, match="拼接"):
        S.run(pj, {"subtitle": {"enabled": True}})


def test_不把自己压出来的片当成源(tmp_path):
    """_SUB.mp4 是这一步的产物。当成源的话，每跑一次就多压一层。"""
    pj = Project(str(tmp_path))
    d = pj.p("06_成片")
    os.makedirs(d, exist_ok=True)
    for n in ("X_EP01_MASTER_V01_FIXED.mp4", "X_EP01_MASTER_V01_FIXED_SUB.mp4"):
        open(os.path.join(d, n), "wb").write(b"x")
    got = [os.path.basename(x) for x in S._masters(pj)]
    assert got == ["X_EP01_MASTER_V01_FIXED.mp4"]


def test_读不出字幕轨要返回None不是False(monkeypatch):
    """None（读不出来）和 False（确实没有）必须分开。

    读不出来当成「没有」的话，每次跑都重压一遍 —— 软字幕重压是流拷贝，
    看不出异常，只是每次都多花一分钟，而人永远不会发现。
    """
    monkeypatch.setattr(S.probe, "find_ffprobe", lambda: "")
    monkeypatch.setattr(S.probe, "find_ffmpeg", lambda: "")
    assert S._has_subtitle_track("whatever.mp4") is None


def test_打包成exe时不给出假的python候选(monkeypatch):
    """打包后 sys.executable 是 studio 自己，`-m videocaptioner` 一定跑不起来。

    返回它的话，上层以为找到了，跑起来报一个莫名其妙的错。
    """
    monkeypatch.setattr(S.shutil, "which", lambda _n: None)
    monkeypatch.setattr(S.sys, "executable", r"C:\x\Respect短剧制作平台.exe")
    assert S.find_cli() == []


# --------------------------------------------------------------------------
# 接进流水线
# --------------------------------------------------------------------------

def test_没开就不进计划():
    """进了计划再跳过的话，「先看会做什么」会列出一步字幕，
    人以为要做，跑完发现没有，而全程没有一处说过它是关着的。"""
    from core import pipeline, pipeline_v34
    for mod in (pipeline, pipeline_v34):
        assert mod._subtitle_on() in (True, False)     # 不抛就行


def test_字幕不算进分析调用次数():
    """它内部可能调 LLM，但那是它的 key、它的账。
    算进「至少几次分析调用」会让那个数变成假的。"""
    from core import system_v34 as V
    d3 = next(s for s in V.STAGES if s["id"] == "d3")
    assert d3["kind"] == "local"


def test_d3排在d2后面():
    """字幕加在成片上，不是加在分段视频上 —— 顺序反了就无从下手。"""
    from core import system_v34 as V
    ids = [s["id"] for s in V.STAGES]
    assert ids.index("d3") > ids.index("d2")


def test_设置项在config里有默认值():
    """页面上有的每一项，config 里都要有默认值 —— 否则老 config.json
    打开设置页会是一片空白，保存一次就把空值存进去了。"""
    from server.app import load_config
    cfg = load_config()
    for k in S.DEFAULTS:
        assert k in cfg["subtitle"], f"config 里缺 subtitle.{k}"


def test_key不回前端():
    """上面 llm.api_key 就是漏了才补的，别在这儿再漏一次。"""
    from server import app
    src = open(app.__file__, encoding="utf-8").read()
    assert 'sb.pop("llm_api_key", None)' in src


# --------------------------------------------------------------------------
# 字幕样式（2026-08-27：「我的那4份字幕样式呢」）
# --------------------------------------------------------------------------
#
# 用户备了四份样式（中/英 × 横/竖屏），而它们**一份都没起作用**，
# 三层原因叠在一起、每一层都不报错：
#   1. 设置页没有选样式的控件
#   2. 程序硬写 style="default"，而它的样式清单里没有叫这个名字的
#   3. 默认走软字幕，而样式只在硬字幕下生效
# 下面这批用例钉住这三层。

def test_默认是硬字幕():
    """样式只在硬字幕下生效。默认软字幕的话，用户备的样式白备。"""
    assert S.DEFAULTS["subtitle_mode"] == "hard"


def test_默认样式是空不是default():
    """它的样式清单里**没有**叫 default 的样式 —— 那是提示文字里的一个词。
    写 "default" 等于指向一个不存在的样式，表现是「样式没生效」且不报错。"""
    assert S.DEFAULTS["style"] == ""


def test_样式为空时整个键不写进toml():
    """写成空串会覆盖掉 VideoCaptioner 自己的默认。"""
    toml = S.build_config({**S.DEFAULTS, "style": ""})
    assert "style" not in toml


def test_选了样式要写进toml():
    toml = S.build_config({**S.DEFAULTS, "style": "中文竖屏标准版"})
    assert 'style = "中文竖屏标准版"' in toml


def test_软字幕配样式要在跑之前拦下():
    """**这是最坑的一种**：字幕照出、样式静默失效，没有任何一处报错。"""
    bad = S.config_problems({**S.DEFAULTS, "optimize": False,
                             "subtitle_mode": "soft", "style": "中文竖屏标准版"})
    assert bad and "只在烧录进画面" in bad[0]


def test_硬字幕配样式不拦():
    bad = S.config_problems({**S.DEFAULTS, "optimize": False,
                             "subtitle_mode": "hard", "style": "中文竖屏标准版"})
    # 样式名存不存在取决于机器上装没装，这里只确认「软硬矛盾」那条没触发
    assert not any("只在烧录进画面" in b for b in bad)


def test_不选样式时软字幕也不拦():
    """没选样式就没有矛盾 —— 软字幕本身是合法选择。"""
    assert S.config_problems({**S.DEFAULTS, "optimize": False,
                              "subtitle_mode": "soft", "style": ""}) == []


def test_样式名对不上要拦(monkeypatch):
    """样式名写错的表现是**字幕照出、但还是默认样子**，不报错。"""
    monkeypatch.setattr(S, "style_names", lambda: ["中文竖屏标准版"])
    bad = S.config_problems({**S.DEFAULTS, "optimize": False,
                             "subtitle_mode": "hard", "style": "不存在的样式"})
    assert bad and "找不到" in bad[0]


def test_列不出样式时不要瞎拦(monkeypatch):
    """VideoCaptioner 没装好时列不出样式。那是「没装」该报的事，
    在这儿再报一遍「样式找不到」只会把人往样式上引。"""
    monkeypatch.setattr(S, "style_names", lambda: [])
    bad = S.config_problems({**S.DEFAULTS, "optimize": False,
                             "subtitle_mode": "hard", "style": "随便什么"})
    assert not any("找不到" in b for b in bad)


def test_硬字幕不拿字幕轨当做过没有的判据(tmp_path, monkeypatch):
    """**从软字幕切到硬字幕时最容易中招。**

    源成片上还留着上一轮压进去的字幕轨，硬字幕这一轮如果看字幕轨，
    就会判「已经有了，跳过」—— 用户改了设置却什么都没变，而且不报错。
    """
    monkeypatch.setattr(S, "_has_subtitle_track", lambda _p: True)
    pj = Project(str(tmp_path))
    os.makedirs(pj.p("06_成片"), exist_ok=True)
    open(pj.p("06_成片", "X_EP01_MASTER_V01_FIXED.mp4"), "wb").write(b"x")
    st = S.status(pj, {"subtitle": {"enabled": True, "subtitle_mode": "hard"}})
    assert st["todo"] == 1, "硬字幕被字幕轨误判成做过了"
    st2 = S.status(pj, {"subtitle": {"enabled": True, "subtitle_mode": "soft"}})
    assert st2["skip"] == 1, "软字幕该认字幕轨"


def test_产物在就跳过_两种模式都认(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_has_subtitle_track", lambda _p: False)
    pj = Project(str(tmp_path))
    d = pj.p("06_成片")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "X_EP01_MASTER_V01_FIXED.mp4"), "wb").write(b"x")
    open(os.path.join(d, "X_EP01_MASTER_V01_FIXED_SUB.mp4"), "wb").write(b"xx")
    for mode in ("hard", "soft"):
        st = S.status(pj, {"subtitle": {"enabled": True, "subtitle_mode": mode}})
        assert st["skip"] == 1 and st["todo"] == 0, mode


def test_零字节产物不算做过(tmp_path, monkeypatch):
    """压制中途被杀会留下一个 0 字节文件。当成做过的话，
    人永远拿不到带字幕的片，而页面上写着「都配好了」。"""
    monkeypatch.setattr(S, "_has_subtitle_track", lambda _p: False)
    pj = Project(str(tmp_path))
    d = pj.p("06_成片")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "X_EP01_MASTER_V01_FIXED.mp4"), "wb").write(b"x")
    open(os.path.join(d, "X_EP01_MASTER_V01_FIXED_SUB.mp4"), "wb").close()
    st = S.status(pj, {"subtitle": {"enabled": True, "subtitle_mode": "hard"}})
    assert st["todo"] == 1


def test_设置页有选样式的控件():
    """这一条是钉住起因本身：控件不存在 = 那四份样式永远选不到。"""
    import io
    src = io.open("web/index.html", encoding="utf-8").read()
    assert 'id="sbStyle"' in src, "设置页没有字幕样式选择控件"
    assert "sbStyle:'style'" in src, "样式没接进保存逻辑，选了也存不下"


def test_样式候选从videocaptioner实拉_不写死四份():
    """写死的话，用户在它那边自己加的样式选不到、删掉的还留在下拉里。"""
    import io
    src = io.open("web/index.html", encoding="utf-8").read()
    assert "/api/subtitle/styles" in src
    assert "中文竖屏标准版" not in src, "样式名被写死进页面了"


# --------------------------------------------------------------------------
# 竖屏样式缩放补偿
# --------------------------------------------------------------------------
#
# VideoCaptioner 按 `height / 720` 缩放整份样式，前提是「720P = 1280×720
# 横屏」。竖屏 720×1280 的 height 是长边 1280 → ×1.78：
# 字号 40 渲染成 71、底边距 138 变成 245，18 个字一行按 71px 要 1278px，
# 而画面只有 720px 宽，两头切掉（2026-08-27 实测截图确认）。
# 横屏 1280×720 正好 ×1.0，一点事没有 —— 所以只在竖屏补。

def test_竖屏要补偿():
    assert S.scale_compensation(720, 1280) == 1280 / 720


def test_横屏不补偿():
    """横屏本来就是对的，去动它等于制造一个新问题。"""
    assert S.scale_compensation(1280, 720) == 1.0


def test_正方形不补偿():
    """高不大于宽就不算竖屏。边界值别写成 >=，那会把 1:1 也算进去。"""
    assert S.scale_compensation(1024, 1024) == 1.0


def test_读不出分辨率时不补偿():
    """猜一个系数比不补更糟：字幕大小会莫名其妙，而且不报错。"""
    assert S.scale_compensation(0, 0) == 1.0


def test_补偿后乘回去等于你写的值():
    """**这是整件事的验收条件**：所见即所写。"""
    try:
        from videocaptioner.core.subtitle.style_manager import load_style
        st = load_style("中文竖屏标准版", mode="ass")
    except Exception:
        pytest.skip("这台机器上没装 videocaptioner / 没有这份样式")
    if st is None:
        pytest.skip("样式还没装进去")
    w, h = 720, 1280
    sf = S.scale_compensation(w, h)
    ov = S.style_override("中文竖屏标准版", w, h)
    # 底边距和描边要能精确还原；字号取整允许差 1px
    assert int(ov["margin_bottom"] * sf) == st.margin_bottom
    assert abs(ov["outline_width"] * sf - st.outline_width) < 0.01
    assert abs(int(ov["font_size"] * sf) - st.font_size) <= 1


def test_四个被缩放的字段都要补():
    """只补字号不行 —— 描边和底边距跟着放大 1.78 倍时，
    字幕会又粗又高，跟写的完全不是一回事。"""
    assert set(S._SCALED_FIELDS) == {
        "font_size", "outline_width", "spacing", "margin_bottom"}


def test_没选样式就不发override():
    assert S.style_override("", 720, 1280) == {}


def test_横屏不发override():
    assert S.style_override("中文横屏标准版", 1280, 720) == {}


def test_参考高度钉住():
    """这个数来自 VideoCaptioner 的 ass_renderer.reference_height。
    它哪天改了，我们的补偿就会算错 —— 钉住，让改动能被看见。"""
    assert S.VC_REFERENCE_HEIGHT == 720
    try:
        from videocaptioner.core.subtitle import ass_renderer as ar
        import inspect
        sig = inspect.signature(ar.render_ass_video)
        assert sig.parameters["reference_height"].default == S.VC_REFERENCE_HEIGHT, (
            "VideoCaptioner 改了参考高度，core/subtitle.py 的补偿要跟着改")
    except ImportError:
        pytest.skip("这台机器上没装 videocaptioner")


def test_老配置里的default样式要被纠正():
    """**只改 DEFAULTS 对已经跑过一次的机器一点用都没有。**

    merged() 是「存了的盖默认的」，config.json 里存着的 "default"
    会一直盖住新默认值 —— 而那正是所有老用户的处境。
    """
    st = S.merged({"subtitle": {"style": "default"}})
    assert st["style"] == "", "老配置里的无效样式名没被纠正"


def test_迁移不动用户真选的样式():
    """只改「一定是错的」，不改「你可能是故意选的」。"""
    sub = {"style": "中文竖屏标准版"}
    assert S.migrate(sub) is False
    assert sub["style"] == "中文竖屏标准版"


def test_load_config读进来就纠正():
    """页面上显示的、跑的时候用的，都得是纠正后的值。"""
    from server.app import load_config
    assert load_config()["subtitle"]["style"] != "default"


# --------------------------------------------------------------------------
# 字幕微调（2026-08-27：字体/字号/垂直间距/颜色/描边/字符间距）
# --------------------------------------------------------------------------

def test_可微调的字段就是ASS真正认的那些():
    """对着 videocaptioner 的 to_ass_string() 核过。多列一个 =
    人填了没反应且不报错，少列一个 = 白白少一个旋钮。"""
    assert set(S.STYLE_KEYS) == {
        "font_name", "font_size", "margin_bottom", "primary_color",
        "outline_color", "outline_width", "spacing", "bold"}


def test_行间距不在里面_并且说得清为什么():
    """ASS 模式没有行间距（只有圆角背景块那种模式才有）。
    放进来的话人填了没反应 —— 正是这一批改动在消灭的那类失效。"""
    assert "line_spacing" not in S.STYLE_KEYS
    assert "line_spacing" in S.STYLE_NOT_AVAILABLE


def test_空值不算微调():
    """空串 = 没填 = 用样式的值。当成 0 的话，
    「没动过」会变成「把字号设成 0」。"""
    tw = S.style_tweak({"subtitle": {"style_tweak": {
        "font_size": "", "font_name": "  ", "spacing": None}}})
    assert tw == {}


def test_微调值按类型转():
    """页面送来的都是字符串。不转的话 toml 里写成 "50"，
    VideoCaptioner 读到类型不对。"""
    tw = S.style_tweak({"subtitle": {"style_tweak": {
        "font_size": "50", "outline_width": "3.5", "bold": True}}})
    assert tw["font_size"] == 50 and isinstance(tw["font_size"], int)
    assert tw["outline_width"] == 3.5
    assert tw["bold"] is True


def test_微调值超范围要夹住():
    """字号填 99999 不会报错，只会烧出一个全屏都是字的片子。"""
    tw = S.style_tweak({"subtitle": {"style_tweak": {"font_size": 99999}}})
    assert tw["font_size"] == 200


def test_填了非数字就忽略不炸():
    tw = S.style_tweak({"subtitle": {"style_tweak": {"font_size": "大一点"}}})
    assert "font_size" not in tw


def test_不认识的键不进来():
    tw = S.style_tweak({"subtitle": {"style_tweak": {"line_spacing": 12}}})
    assert tw == {}


def test_微调过的字段也要补偿():
    """**这是最容易漏的一处。**

    微调和补偿分开做的话，改过的字段不补偿：你把字号改成 50，
    出来是 89（50×1.78），而没改的字段都是对的 ——
    一部分对一部分不对，最难查。
    """
    sf = S.scale_compensation(720, 1280)
    ov = S.style_override("中文竖屏标准版", 720, 1280, {"font_size": 50})
    if not ov:
        pytest.skip("这台机器上没装 videocaptioner")
    assert abs(int(ov["font_size"] * sf) - 50) <= 1, "微调的字号没被补偿"


def test_不缩放的字段原样发过去():
    """颜色和字体名不参与缩放。除了它们等于把颜色算成数字。"""
    ov = S.style_override("中文竖屏标准版", 720, 1280,
                          {"primary_color": "#ffdd00", "font_name": "华文中宋"})
    if not ov:
        pytest.skip("这台机器上没装 videocaptioner")
    assert ov["primary_color"] == "#ffdd00"
    assert ov["font_name"] == "华文中宋"


def test_横屏微调不除():
    """横屏缩放系数是 1，微调值原样发。除了的话字会缩到一半。"""
    ov = S.style_override("中文横屏标准版", 1280, 720, {"font_size": 60})
    if not ov:
        pytest.skip("这台机器上没装 videocaptioner")
    assert ov["font_size"] == 60


def test_横屏没微调就不发override():
    """没必要发一个全是原值的 override。"""
    assert S.style_override("中文横屏标准版", 1280, 720, {}) == {}


def test_字体名找不到要拦():
    """字体写错**静默回落成默认字体** —— 片子照出、字体不对、不报错。"""
    from core import captions
    if not captions.system_fonts():
        pytest.skip("这台机器列不出系统字体")
    bad = S.config_problems({**S.DEFAULTS, "optimize": False,
                             "subtitle_mode": "hard",
                             "style_tweak": {"font_name": "根本没有这个字体"}})
    assert bad and "找不到" in bad[0]


def test_真实存在的字体不拦():
    from core import captions
    fonts = captions.system_fonts()
    if not fonts:
        pytest.skip("这台机器列不出系统字体")
    bad = S.config_problems({**S.DEFAULTS, "optimize": False,
                             "subtitle_mode": "hard",
                             "style_tweak": {"font_name": fonts[0]}})
    assert not any("字体" in b for b in bad)


def test_列不出字体时不要瞎拦(monkeypatch):
    """把「我们读不到注册表」报成「你的字体不存在」是另一种错。"""
    from core import captions
    monkeypatch.setattr(captions, "system_fonts", lambda *a, **k: [])
    bad = S.config_problems({**S.DEFAULTS, "optimize": False,
                             "subtitle_mode": "hard",
                             "style_tweak": {"font_name": "随便什么"}})
    assert not any("字体" in b for b in bad)


def test_微调表单结构由后端给():
    """前后端各写一份的话，加一个旋钮要改两处，
    对不上的表现是「页面上有、存不下」。"""
    import io
    src = io.open("web/index.html", encoding="utf-8").read()
    assert "SB_FIELDS = r.fields" in src
    for k in S.STYLE_KEYS:
        assert f"'{k}'" not in src or k in ("font_name",), \
            f"{k} 被写死进页面了，应该从 /api/subtitle/styles 拿"


def test_加粗是三态不是勾选框():
    """勾选框做不了三态 —— 「没动过」和「明确不加粗」长得一样，
    于是没动过也会被当成覆盖发出去，把样式里的加粗关掉。"""
    import io
    src = io.open("web/index.html", encoding="utf-8").read()
    assert "按样式（" in src, "bool 字段没做成三态下拉"


# --------------------------------------------------------------------------
# 剧维度（2026-08-27：「字幕的配置应该在剧的维度」）
# --------------------------------------------------------------------------
#
# 继承链和「本剧的提示词」同一条：内置 → 全局基础（设置页）→ 本剧。
# LLM 凭据和超时留在全局，剧级盖不了。

def test_剧级键和面板字段一一对应():
    """对不上的表现是「页面上有、存不下」或者「能存但界面上没有」。"""
    assert {f["key"] for f in S.PROJECT_FIELDS} == set(S.PROJECT_KEYS)


def test_每个设置项都归了类():
    """漏一个的表现是它既不在剧级面板、也没被标成全局专属 ——
    只能改 config.json，而页面上找不到。"""
    assert set(S.DEFAULTS) == set(S.PROJECT_KEYS) | set(S.GLOBAL_ONLY_KEYS)


def test_llm凭据和超时不许做成剧级():
    """key 做成剧级的话，40 部剧要填 40 遍，而且 key 会散落在
    40 个项目目录里跟着备份到处走。"""
    assert S.GLOBAL_ONLY_KEYS == {
        "llm_api_base", "llm_api_key", "llm_model", "timeout"}
    assert not (S.PROJECT_KEYS & S.GLOBAL_ONLY_KEYS)


def test_不传pj只到全局那一层(tmp_path):
    st = S.merged({"subtitle": {"enabled": True}})
    assert st["enabled"] is True


def test_剧级盖全局(tmp_path):
    pj = Project(str(tmp_path))
    pj.save_meta({"subtitle": {"enabled": True, "style": "本剧的样式"}})
    st = S.merged({"subtitle": {"enabled": False, "style": "全局的样式"}}, pj)
    assert st["enabled"] is True and st["style"] == "本剧的样式"


def test_剧级没填就继承(tmp_path):
    pj = Project(str(tmp_path))
    pj.save_meta({"subtitle": {"enabled": True}})       # 只覆盖 enabled
    st = S.merged({"subtitle": {"asr": "jianying"}}, pj)
    assert st["enabled"] is True          # 剧级
    assert st["asr"] == "jianying"        # 继承全局


def test_剧级盖不了llm凭据(tmp_path):
    """手改过 meta、或者从别处拷来的项目里可能带着 llm_api_key。
    认它的话，凭据就有了第二个家。"""
    pj = Project(str(tmp_path))
    pj.save_meta({"subtitle": {"llm_api_key": "sk-项目里的", "timeout": 99}})
    st = S.merged({"subtitle": {"llm_api_key": "sk-全局的", "timeout": 3600}}, pj)
    assert st["llm_api_key"] == "sk-全局的"
    assert st["timeout"] == 3600


def test_没覆盖和覆盖成相同值要分得开(tmp_path):
    """分不开的话，改了全局默认之后，那些「其实只是当时跟全局一样」的剧
    不会跟着变，而人以为它们是继承的。"""
    pj = Project(str(tmp_path))
    S.save_project(pj, {"asr": "bijian"})              # 显式设成和默认一样
    assert "asr" in S.project_values(pj)
    S.save_project(pj, {"asr": None})                  # 清除
    assert "asr" not in S.project_values(pj)


def test_送None是清除覆盖(tmp_path):
    pj = Project(str(tmp_path))
    S.save_project(pj, {"enabled": True})
    assert S.project_values(pj) == {"enabled": True}
    S.save_project(pj, {"enabled": None})
    assert S.project_values(pj) == {}


def test_空串不是清除(tmp_path):
    """style / language 的空串本身就是合法取值 ——
    拿空串当清除的话，「用 VideoCaptioner 默认样式」这个选择存不下来。"""
    pj = Project(str(tmp_path))
    S.save_project(pj, {"style": ""})
    assert S.project_values(pj) == {"style": ""}


def test_剧级面板送上来的全局键一律忽略(tmp_path):
    pj = Project(str(tmp_path))
    S.save_project(pj, {"llm_api_key": "sk-x", "timeout": 1, "enabled": True})
    assert S.project_values(pj) == {"enabled": True}


def test_run按剧读设置(tmp_path, monkeypatch):
    """**不传 pj 的表现是「这部剧填的东西一律不生效」**，
    而页面上照旧显示着填好的值。"""
    monkeypatch.setattr(S, "find_cli", lambda: [])
    pj = Project(str(tmp_path))
    pj.save_meta({"subtitle": {"enabled": True}})
    os.makedirs(pj.p("06_成片"), exist_ok=True)
    open(pj.p("06_成片", "X_EP01_MASTER_V01_FIXED.mp4"), "wb").write(b"x")
    # 全局是关的，这部剧开了 —— 应该真的去跑（然后因为没装而报没装）
    r = S.run(pj, {"subtitle": {"enabled": False}})
    assert r["problems"], "剧级开关没被读到，整步被当成没开"


def test_status按剧读设置(tmp_path):
    pj = Project(str(tmp_path))
    pj.save_meta({"subtitle": {"enabled": False}})
    os.makedirs(pj.p("06_成片"), exist_ok=True)
    open(pj.p("06_成片", "X_EP01_MASTER_V01_FIXED.mp4"), "wb").write(b"x")
    st = S.status(pj, {"subtitle": {"enabled": True}})   # 全局开、本剧关
    assert st["todo"] == 0 and "没开" in st["reason"]


def test_流水线按剧决定要不要排字幕(tmp_path):
    """只看全局的话，这部剧关了字幕照样会被排进计划 ——
    不报错，只是做出来的东西不对。"""
    from core import pipeline, pipeline_v34
    import inspect
    for mod in (pipeline, pipeline_v34):
        sig = inspect.signature(mod._subtitle_on)
        assert "pj" in sig.parameters, f"{mod.__name__}._subtitle_on 不认项目"
        src = inspect.getsource(mod.plan)
        assert "_subtitle_on(pj)" in src, f"{mod.__name__}.plan 没把项目传进去"


def test_剧级面板有取消勾选就送null():
    """只送勾了的字段的话，取消勾选永远不生效 ——
    页面显示「跟随全局」，而 meta 里那个旧覆盖一直在起作用。"""
    import io
    src = io.open("web/index.html", encoding="utf-8").read()
    assert "(f.key in SP.own) ? SP.own[f.key] : null" in src


def test_勾自定义时初值取继承值():
    """取类型默认值的话，勾一下就悄悄把设置变了 ——
    而人只是想看看能改什么。"""
    import io
    src = io.open("web/index.html", encoding="utf-8").read()
    assert "SP.own[k] = SP.global[k]" in src


def test_设置页说清自己是新剧默认():
    import io
    src = io.open("web/index.html", encoding="utf-8").read()
    assert "新剧的默认值" in src
    assert "所有剧共用，剧级盖不了" in src


# --------------------------------------------------------------------------
# 执行分支的契约
# --------------------------------------------------------------------------
#
# 「字幕失败不阻断交付」是特意设计的行为，之前**只有注释没有测试** ——
# 而它极易被后来的人"顺手改对称"：别的 deliver 分支失败都 append 到
# failed 并 return "failed"，d3 不这么做看着像漏了。改了之后的表现是
# 「一集字幕没配上 → 整轮判失败」，而成片明明是好的。

def _branch_src(mod_path: str, marker: str, end: str) -> str:
    """截出某个分支的源码，**剥掉注释**。

    剥注释不是洁癖：这个分支的注释里原文写着「也不 return "failed"」，
    不剥的话字面命中，测试查什么都是「有」—— 一个恒真的断言，
    看着是绿的但什么都没查。（写这条测试时真的先踩了一次。）
    """
    import io
    src = io.open(mod_path, encoding="utf-8").read()
    i = src.index(marker)
    j = src.index(end, i)
    return chr(10).join(ln.split("#", 1)[0]
                        for ln in src[i:j].splitlines())


def test_v34字幕分支不把整轮判失败():
    br = _branch_src("core/pipeline_v34.py", 'elif sid == "d3":', "\n            else:")
    assert "failed.append" not in br, "字幕失败被记进 failed —— 整轮会判失败"
    assert 'return "failed"' not in br, "字幕失败直接 return failed"
    assert 'state="warn"' in br, "字幕有问题时没标 warn，人看不出这一步有事"


def test_通用体系字幕分支也一样():
    br = _branch_src("core/pipeline.py", 'elif s["kind"] == "subtitle":',
                     "\n            # ---- 拼接")
    assert "failed.append" not in br
    assert 'return "failed"' not in br
    assert 'state="warn"' in br


def test_两套体系的字幕分支都真的调run():
    """分支里忘了调 run 的表现是「这一步秒过、标 ok、什么都没做」。"""
    for path, marker, end in (
            ("core/pipeline_v34.py", 'elif sid == "d3":', "\n            else:"),
            ("core/pipeline.py", 'elif s["kind"] == "subtitle":',
             "\n            # ---- 拼接")):
        br = _branch_src(path, marker, end)
        assert "_sub.run(pj" in br, f"{path} 的字幕分支没调 subtitle.run"


def test_warn状态在前端有独立样式():
    """warn 落到默认的 pend 的话，「做完了但有几集没配上」
    在页面上长得和「还没开始」一模一样，人会一直等一个已经结束的步骤。"""
    import io
    src = io.open("web/index.html", encoding="utf-8").read()
    assert "warn:'warn'" in src, "PILL 映射里没有 warn"
    assert ".s-warn{" in src, "没有 s-warn 的样式"
