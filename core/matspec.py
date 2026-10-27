# -*- coding: utf-8 -*-
"""生产材料的**契约**：我要吃什么结构。给 codex 照着产。

用户原话（2026-08-24）：「我觉得逻辑反了，不应该是我去解析 codex 生产的结果，
应该是我告诉他我要什么样的结构」「告诉 codex 我要吃什么结构或是你会怎么样去
解析结构你需要什么，才可以完整的把图片和视频处理好」。

对。反过来做的代价已经看到了：解析器要认全角等号、要分节（正文里也写
`Image N`，不分节就数出两倍张数）、要剔掉 `---` 分隔线（不剔的话一条没写
提示词的单元会解析出 `prompt == "---"`，非空，检查不出来）。
每一条都是在猜对方的排版，而猜错都是静默的。

## 这份契约从代码生成，不手写

手写的规范和实际要求**一定会飘**：改了 produce 忘了改文档，然后 codex 照着
过期的规范产一份，导入时才发现。所以这里的字段清单、目录、上限都是从
`produce` 实际读的字段、`matimport.out_path` 实际的落点、服务商声明的
`max_refs` 现算出来的 —— 规范和程序永远是同一份东西。
"""

from __future__ import annotations

import json
from typing import Optional

from .reference_contract import RULES as REFERENCE_RULES, SCOPE as CONTRACT_SCOPE, REVISION as CONTRACT_REVISION
from . import independent_c, single_handoff

# ====================================================================
# 这里有**两套字段**，而且名字不一样。混着写就是 codex 实测撞上的那个歧义：
#
#   MATERIAL_FIELDS —— 你（codex）要写进 jsonl 的字段。导入**之前**的形状。
#   TASK_FIELDS     —— 程序把它变成任务之后的字段。导入**之后**的形状。
#
# 举三个真会写错的：材料写 `filename`，任务里叫 `output`（路径由程序算）；
# 材料的参考图用 `key`，任务里转成 `asset_id` + `file_ref`；
# 材料写顶层 `size` / `duration`，任务里进 `params.size` / `params.duration`。
#
# 上一版把 TASK_FIELDS 当成「每一类各要什么」写进契约，于是示例（材料形状）
# 和字段说明（任务形状）对不上 —— codex 原话：「可能生成『语法正确、程序
# 不认』的文件」。对。所以这两套现在分两节写，任务那一节明说是给你对号的。
# ====================================================================

# 你要写的字段。**这张表是权威**：每一项都由 `matimport._from_json` 实际读，
# 有测试逐条验证「文档里写的字段解析器真的认」。
MATERIAL_FIELDS = {
    "image": [
        ("kind", True, '固定写 `"image"`。认不出的值**不建任务**，'
                       '不会被猜成图片'),
        ("key", True, "这一条的唯一编号，全局唯一。别处引用它时一字不差。"
                      "落哪个目录按它的家族前缀算"),
        ("filename", True, "产物文件名（带后缀）。**只给文件名，不要给路径** ——"
                           "目录由程序按家族算"),
        ("prompt", True, "完整可投喂的提示词正文。程序落成 txt，出图时读它"),
        ("size", False,
         "**这一张**的画幅。每张各写各的，不用全剧统一 —— 这一张要什么形状"
         "由你定，程序只负责把它换成目标服务商认的写法。"
         "写 `ratio` 也认；不写就落到申报头的 `image_size`。" + chr(10) +
         "  · 写**形状**：`9:16` / `16:9` / `1:1` / `1024x1536` 这类。"
         "程序会按目标服务商的规范换成它认的写法（有的家只收比例、"
         "有的只收像素），你不用管哪家收什么。" + chr(10) +
         "  · **别写 `1K` / `2K` / `4K`。** 那是分辨率档位，说不了形状 —— "
         "发出去形状由服务商自己定，图出得来、形状不是你要的、不报错。"
         "现在遇到这种会直接停下，不会蒙一个。" + chr(10) +
         "  · 这一家没有你写的形状时程序会换最接近的**并且出声**，"
         "但形状确实变了。常见画幅命中率高。" + chr(10) +
         "  · 交接板 A/B 区图的形状和用它出片的那一段 `ratio` 不一致时，视频那一步"
         "要裁或者加黑边，而那一步不会说话 —— 导入时会提醒一句，不拦。"),
        ("reference_images", False, "有序参考图，见下面「参考图怎么写」"),
        ("view", False, 'c_then_a_b 的单图身份：C / A / B，不是 region；详见独立单图交接扩展。'),
        ("c_key", False, '独立 A/B 直接引用的同版 C key；须与 reference_images 一致。'),
        ("handoff_mode", False, '按模式填写：新单图 C/A/B 都写 `"c_then_a_b"`，并写 view；'
         '旧独立 C → AB 模式仅 C 写 `"independent_c"`。两种模式的独立图都不填 region/board/parent；'
         '新单图落入交接图目录，旧独立 C 落入场景状态图目录。原 ABC 整板及区域图不填此字段。'),
        ("handoff_type", False, '兼容旧材料：未填 handoff_mode 时，`"scene_c"` / `"independent_c"` 按独立 C 识别。'
         '新材料优先使用 handoff_mode；其他值不会被当成独立 C。'),
        # region/board are legacy board fields; boundary also pairs new A/B.
        ("region", False,
         "**只有交接板的区域图写它**：`A` / `B` / `C`。写了就落 "
         "`04_交接板/区域图/`；新单图模式不写它，改由 handoff_mode/view 路由。" + chr(10) +
         "  程序**按这个字段认区域图，不按 key 前缀** —— 整板和它的三张"
         "区域图同属 ABC 家族、key 也长得像，靠前缀分不开，而分错的后果是"
         "区域图落进整板目录，「视频该引哪张」从目录上就看不出来了。"),
        ("board", False,
         "**区域图专用**：它派生自哪张整板的 `key`。写 `parent` 也认。" + chr(10) +
         "  区域图的**唯一图片输入就是这张整板** —— `reference_images` 里"
         "有且只能有它一条。多投一张会重定空间或外观的图，派生就飘了，"
         "而飘了不报错：图看着像那么回事，空间已经不对。"),
        ("boundary", False,
         "交接边界 ID，比如 `EP01_SEG01_TO_SEG02`。新 c_then_a_b 模式的 A/B 必填，"
         "程序以它配对 A/B 并核对同版 c_key；C 可被多个有依据的边界共用。旧整板模式用作边界说明。"),
        ("name", False, "人看的名字，比如「林溪身份根」。写 `goal` 也认"),
        ("family", False, "家族标记。程序不读它（先按模式和区域标记路由，其余看 `key` 的前缀），"
                          "写着方便你自己核对"),
    ],
    "video": [
        ("kind", True, '固定写 `"video"`'),
        ("key", True, "**必须是 `EP01-SEG01` 这个形状** —— 拼接按这个前缀"
                      "挑本集分段"),
        # **不是必填**：解析器会从 `EP01-SEG01` 这个 key 里认出来。
        # 标成必填的话，schema 会拒掉一份程序其实认的材料 ——
        # 「校验不通过、程序却认」和反过来一样糟，都是文档和代码对不上。
        ("episode", False, "`EP01`。不写会从 `key` / `filename` 里认，"
                           "认不出才报缺 —— 所以 key 用规定的形状就不用写它"),
        ("seg", False, "`SEG01`。同上"),
        ("filename", True, "产物文件名（带后缀）。视频一律落 `05_分段视频/`，"
                           "不看 key 的前缀"),
        ("prompt", True, "完整可投喂的提示词正文"),
        ("handoff_refs", False,
         "本段引用的**交接图**（新模式独立 A/B/C，旧模式区域图或独立 C），按 `image_n` 顺序上传，"
         "就是模型看到的 Image 1..N 的前几张。每项加一个 `role`：" + chr(10) +
         "  · `IN_B` —— 入站独立 B（新模式）或 B 区域图（旧模式），定本段**首镜**" + chr(10) +
         "  · `IN_C` —— 入站独立 C，或旧 ABC 的 C 区域图，定本段**开场空间**" + chr(10) +
         "  · `OUT_A` —— 出站独立 A（新模式）或 A 区域图（旧模式），定本段**结尾**" + chr(10) +
         "  **不是必填，可以一个都没有。** 交接板只给需要承接的边界做："
         "首段没有入板、末段没有出板、天然转场两边都不做 —— "
         "那些情况这里就是空的，**不算缺项**。" + chr(10) +
         "  **c_then_a_b 按 OUT_A→A、IN_B→B、IN_C→C 引用独立图，不填 board；**"
         "旧模式引用对应区域图或独立 C。整板只留在上游，不喂给视频。" + chr(10) +
         "  三个 role 各自最多一个：同一新模式边界 A/B/C 是同一时刻；入站与出站可属不同边界，语义不能互换，"
         "同一个 role 给两张的话程序分不出该信哪张。" + chr(10) +
         "  老材料写 `storyboard_refs` 也照旧认（同义），"
         "但新材料用 `handoff_refs`。"),
        ("reference_images", False,
         "交接板之后的补图（人物身份/服饰、道具外形等），编号接着排"),
        ("duration", False, "秒数。不写就用申报头里的 `seg_duration`"),
        ("ratio", False, "画幅。不写就用申报头里的 `ratio`"),
    ],
    "manifest": [
        ("kind", True, '固定写 `"manifest"`，放在第一行'),
        ("total", False, "这一份里一共几条（不含申报头自己）"),
        ("image", False, "其中图片几条"),
        ("video", False, "其中视频几条"),
        ("episodes", False, "共几集"),
        ("segs_per_episode", False, '每集几段。一个数，或者 `{"EP01": 4}`'),
        ("params", False, "项目参数，见「项目参数」那一节"),
    ],
}

# 参考图元素的字段（`reference_images` / `handoff_refs` 里每一项）。
# image_n/key resolve the reference; handoff_refs additionally use role/board.
REF_FIELDS = [
    ("image_n", True, "**实际上传顺序**，从 1 连续排。跳号或重号会被拦"),
    ("key", True, "指向本材料里另一条的 `key`。写 `asset_id` 也认（同义）"),
    # `role` 从「程序不读」升级成「handoff_refs 里必读」：
    # IN_B / IN_C / OUT_A 表示引用用途，不是三个必然不同的时刻。
    # 在 `reference_images` 里它仍然不读。
    ("role", False, "**只有 `handoff_refs` 里读它**：`IN_B` / `IN_C` / `OUT_A`。"
                    "放在 `reference_images` 里不读"),
    ("board", False, "旧整板模式可标明整板 key；旧独立 C 的 IN_C 可省略，若填写须与入站 AB 的 C 来源一致。新 c_then_a_b 不填。"),
]

# 写着不报错，但**程序一个都不读**，也不影响出图出片。想自己核对就写，
# 不想写就不写 —— 别为它们花时间。
REF_FIELDS_IGNORED = ["who", "controls", "not_controls", "scope"]

# 程序把材料变成任务之后的字段（`core/produce.py` 里 `task.get` 的全集）。
# **这一节是给你对号用的，不是让你写的。** 名字和上面那张表故意不一样：
# `output` 是程序按家族算出来的路径，`asset_id` 是程序从 `key` 转的。
TASK_FIELDS = {
    "image": [
        ("key", "照抄材料的 `key`"),
        ("prompt_ref", "程序落的提示词 txt 路径 —— 你只要给 `prompt` 正文"),
        ("reference_images[].asset_id", "从材料参考图的 `key` 转来"),
        ("reference_images[].file_ref", "程序算出的那张图的落点"),
        ("output", "新 c_then_a_b 先按 view 路由到 04_交接图/C、A、B；旧区域图/独立 C 按其标记路由，其他图片按 key 家族与 filename 算路径"),
        ("params.size", "从材料的 `size` / `ratio` 或申报头来"),
    ],
    "video": [
        ("key", "照抄材料的 `key`（`EP01-SEG01`）"),
        ("prompt_ref", "同上"),
        ("handoff_refs[]", "从材料的 `handoff_refs` 转来，带上 role 和算好的落点"),
        ("storyboard_refs[]", "同一份东西的老名字，值一样 —— 出片那一层"
                              "和产物页还在读它"),
        ("output", "一律 `05_分段视频/<filename>`"),
        ("params.duration", "从材料的 `duration` 或申报头的 `seg_duration` 来"),
        ("params.ratio", "从材料的 `ratio` 或申报头来"),
    ],
}

# 老名字还有人用的话指到任务那套 —— 但契约正文已经分开写了。
NEEDS = TASK_FIELDS

# 每一类落哪个目录 —— **按体系分两份**。用户原话（2026-08-25）：
# 「需要按照项目体系来给两份契约」。同一份发给两套的代价很具体：
# 通用十二环节压根没有场景状态图这一步，契约里写着它，codex 就会产一批
# 这套体系用不上的东西 —— 而且它占着参考图的名额。
_ASSET_LINE = ("02_固定资产/<家族目录>/  提示词 → 03_提示词/资产生产提示词/")
_SB_LINE = "04_故事板/  提示词 → 03_提示词/故事板提示词/"
_BOARD_LINE = "04_交接板/  提示词 → 03_提示词/交接板提示词/"
_REGION_LINE = "04_交接板/区域图/  提示词 → 03_提示词/交接板提示词/"
_VIDEO_LINE = "05_分段视频/  提示词 → 03_提示词/视频提示词/"
_SCSTATE_LINE = "03b_场景状态图/  提示词 → 03_提示词/场景状态提示词/"
_HANDOFF_LINE = "04_交接图/C、A、B/  提示词 → 03_提示词/交接图提示词/"

# 家族前缀 → 落哪个子目录。**和 matimport.out_path 是同一张表**，
# 所以契约说的落点就是实际落点。
_FAMILIES_V34 = [
    ("CHAR / PH", "人物身份资产"), ("LOOK / LK", "人物造型资产"),
    ("CT", "连续状态资产"), ("COST", "服饰资产"), ("LOC", "场景资产"),
    ("PROP", "道具资产"), ("VEH", "载具资产"), ("CRE", "生物资产"),
    ("GRP", "群体资产"), ("VFX", "特效资产"),
    # v7.0：故事板整条换成 ABC 交接板。整板和区域图**分目录**：
    # 一个是上游（不喂视频），一个是视频真正引用的 —— 混在一起，
    # 「哪张能喂视频」只能靠文件名认，而认错不报错。
    ("ABC（整板）", "04_交接板"), ("ABC + region 字段", "04_交接板/区域图"),
    ("SCSTATE / SCST", "03b_场景状态图"),
]
# 通用十二环节自己跑 LLM 时只建六个资产目录（core/stages._CAT_DIR）——
# 这六个和下面这六个家族一一对上，所以导入的落点和它自己跑出来的一致。
# 造型/服饰/载具/特效那几类它没有，别让 codex 产。
_FAMILIES_V61 = [
    ("CHAR / PH", "人物身份资产"), ("LOC", "场景资产"), ("PROP", "道具资产"),
    ("CT", "连续状态资产"), ("GRP", "群体资产"), ("CRE", "生物资产"),
    ("SBSHEET / SBPKG", "04_故事板"),
]

SYSTEMS = {
    "v34": {
        "label": "电影级十七章（v7.0 老李）",
        # 中间产物的文件名按体系写。两套的环节号和产物名完全不同
        # （n1..n14 / s1..s12），写错的代价不是报错 —— 是 codex 拿着一份
        # 「看起来很具体」的说明去核对，发现对不上，然后不知道该信哪句。
        "stage_count": 17,
        "midfiles": "`n1_truth.json`、`n9_shots.json`、`n10_segs.json` 之类",
        "dirs": {"资产（人物/造型/连续状态/服饰/场景/道具/载具/生物/群体/特效）":
                 _ASSET_LINE,
                 "场景状态图": _SCSTATE_LINE,
                 "独立交接图（c_then_a_b，HANDOFF）": _HANDOFF_LINE,
                 "旧模式交接板整板": _BOARD_LINE,
                 "旧模式交接板区域图（A/B/C）": _REGION_LINE,
                 "分段视频": _VIDEO_LINE},
        "families": _FAMILIES_V34,
        "note": "这套体系有场景状态图（同一个场景在不同剧情时刻的状态）。"
                "当前独立单图交接按 c_then_a_b 编译；原 ABC 整板及独立 C → AB 模式继续兼容。"
                "视频引用哪类交接图取决于明确选择的模式，不强制新增故事板。",
    },
    "v61": {
        "label": "通用十二环节",
        "stage_count": 12,
        "midfiles": "`s1_global.json`、`s2_segments.json`、`s8_compile.json` 之类",
        "dirs": {"资产（人物/场景/道具/连续状态/群体/生物）": _ASSET_LINE,
                 "独立交接图（c_then_a_b，HANDOFF）": _HANDOFF_LINE,
                 "故事板": _SB_LINE,
                 "分段视频": _VIDEO_LINE},
        "families": _FAMILIES_V61,
        "note": "**这套体系没有场景状态图**，也没有造型 / 服饰 / 载具 / 特效"
                "这几类资产 —— 别产，产了这套体系用不上，"
                "而它还占着参考图的名额。",
    },
}

# 老调用还在用 DIRS 的话给它电影级那份 —— 但新代码走 SYSTEMS。
DIRS = SYSTEMS["v34"]["dirs"]

# 项目参数：**由材料自己申报，程序不拿它当限制**。用户原话（2026-08-25）：
# 「项目参数也写进契约但是是他给你的不能做任何的限制」。
# 和申报头同一个原则：查「你有没有兑现你自己说的」，
# 不是「你符不符合我设的」。所以这些字段程序只读、只显示、只当默认值用 ——
# 和页面上的项目参数不一样也照导，不夹、不改、不拦。
PARAMS = [
    ("episodes", "总集数"),
    ("episode_seconds", "每集多少秒"),
    ("segs_per_episode", "每集几段（可以给一个数，也可以给 {\"EP01\": 4} 这种）"),
    ("seg_duration", "单段几秒（每条视频也能各自写 duration，那个更优先）"),
    ("ratio", "画幅，比如 9:16"),
    ("image_size", "出图尺寸的**兜底值**，比如 9:16 或 1024x1536。"
                   "每条 image 都能自己写 `size`，那个更优先 —— "
                   "画幅本来就该按这一张画什么来定，不是全剧一个数。"
                   "别写 1K/2K/4K 这种分辨率档位（说不了形状）"),
    ("pacing", "剧情节奏速度"),
    ("subtitle", "要不要字幕（画面里本来就该有的字不算字幕）"),
    ("note", "别的想说的，自由写"),
]

# 验收会跑哪几条 —— 写进契约，让它**产之前**就知道会被怎么查。
AUDIT = [
    ("引用链闭合", "任何 asset_id 都必须是本材料里另一条的 key。"
                   "引了一个没人产出的 → 出图时报「参考图不存在」，"
                   "而那张图压根没人做"),
    ("编号连续", "image_n 从 1 连续排，不跳号、不重号。"
                 "跳号 → 出图前那道检查直接拦住，整条不出"),
    ("每集段号连续", "SEG01、SEG02… 不缺号。缺一段 = 成片短一截，"
                     "而拼接那一步不会说话"),
    ("key 全局唯一", "同一个 key 只许出现一次。重了 → 提示词按 key 落盘，"
                     "**后一条盖掉前一条**，被盖那条的提示词永远丢了，"
                     "而两条任务都还在，其中一条读的是别人的提示词"),
    ("filename 全局唯一", "重了 → 产物落同一个路径，后出的盖掉先出的。"
                          "而「这条做过没有」看的是产物在不在，"
                          "于是两条都算做完，实际只剩一张"),
    ("和申报头对得上", "总条数、图片/视频条数、集数、每集段数，"
                       "都要和第一行的申报一致。**这是唯一能查出「少产了」的检查** —— "
                       "该 84 条只给 80 条时，剩下的内部完全自洽，"
                       "引用链、编号、段号全绿，没有任何一处会说话"),
    # ---- ABC 交接板（电影级 v7.0）。每一条都是**结构性**的：
    #      程序能查、查出来的都是「导进去之后才发现」的那种。
    #      「一段该不该有交接板」不查 —— 那是你按剧情定的。
    ("区域图只能引一张整板", "写了 `region` 的那条，`reference_images` 里"
                             "**有且只能有一条**，且必须是整板。"
                             "多投一张会重定空间或外观的图，派生就飘了 —— "
                             "而飘了不报错：图看着像那么回事，空间已经不对"),
    ("整板不许引区域图", "整板的参考图里不能出现任何 `region` 图。"
                         "整板是区域图的上游，反过来引就是让偏差逐板传递，"
                         "而每一板都「看起来跟上一板一致」，没有一处会说话"),
    ("board 要指到真整板", "区域图的 `board` 必须是本材料里一条**不带 region** "
                           "的 ABC。指到别的东西 → 出图时报「参考图不存在」，"
                           "而那张图压根没人做"),
    ("视频不引整板", "新 c_then_a_b 按 OUT_A→A、IN_B→B、IN_C→C 引独立图；"
                       "旧模式引区域图，IN_C 也可引 handoff_mode=independent_c 的独立 C。"
                       "引到整板 → 一张三区图整个喂给视频模型，"
                       "画面会去学那张板的分格排版，**片子出得来、构图是错的**"),
    ("三个 role 各自最多一个", "`IN_B` / `IN_C` / `OUT_A` 是三种用途。"
                               "同一新模式边界的 A/B/C 是同一时刻，视频入站与出站可以跨边界；"
                               "语义不能互换。同一个 role 给两张，程序分不出"
                               "该信哪张 —— 而它不会挑，会两张都发"),
    ("交接板引用会报出来", "每段引了哪几个 role → **提醒**（不拦）。"
                            "一段有零个、一个、两个还是三个交接板引用，"
                            "是你按剧情定的（首段没入板、末段没出板、"
                            "天然转场两边都没有）—— 程序不懂内容，不判对错。"
                            "报它是因为「本来要给、实际漏了」这种漏产"
                            "从别处一点都看不出来"),
    ("kind 只认三个值", "`image` / `video` / `manifest`。认不出的**不建任务** —— "
                        "猜成图片的话，一条视频会被当资产图出掉，"
                        "任务标成功，成片里少一段而没人报错"),
    ("每张图说得出是谁", "提示词正文里每张参考图要有 `Image N = <key>` "
                          "加一句这张图是谁 / 是什么 —— 出图前那道检查查的就是"
                          "这一句。**只有这一条是硬的**（多人场景说不清谁是谁，"
                          "模型必然张冠李戴）；权威划分、时间、适用范围那几行"
                          "缺了只提醒，不拦"),
]


def limits_note(limits: Optional[dict]) -> str:
    """参考图上限那句话。**`render()` 已经不调它了** —— 别当成漏接。

    用户原话（2026-09-02）：「希望你删掉这个限制，skill 那边自己会做，
    所以你这边只需要删除」。删的是**契约正文里那个数**：一段配几张是 codex
    按剧情定的，它的 skill 知道目标模型的容量；我们再写一个数进去，
    除了多一处会过期的事实（模型换一个上限就变，逐模型还各不相同），
    还会反过来压它 —— 鹤整家声明 9、而 2.5 系列每个模型声明 30。

    函数和导入时那道检查（`matimport` 的 `REF_OVER_LIMIT`）**都留着**：
    删的是「告诉 codex 一个数」，不是「不再拦超限的材料」。
    """
    if not limits:
        return ("（当前没配出图/出片服务商，所以给不出具体数字 —— "
                "配好之后重新导出这份契约，这里会写上真实上限）")
    bits = [f"{'出图' if k == 'image' else '出片'}一次最多 {v} 张"
            for k, v in sorted(limits.items())]
    return "**本项目当前的上限：" + "；".join(bits) + "**（按你配的首选那一家算的）"


_TYPES = {
    "kind": {"type": "string", "enum": ["image", "video", "manifest"]},
    "image_n": {"type": "integer", "minimum": 1},
    "duration": {"type": "number"},
    "total": {"type": "integer"}, "image": {"type": "integer"},
    "video": {"type": "integer"}, "episodes": {"type": "integer"},
    "segs_per_episode": {"type": ["integer", "object"]},
    "params": {"type": "object"},
    "reference_images": {"type": "array", "items": {"$ref": "#/$defs/ref"}},
    "handoff_refs": {"type": "array", "items": {"$ref": "#/$defs/ref"}},
    "storyboard_refs": {"type": "array", "items": {"$ref": "#/$defs/ref"}},
    "region": {"type": "string", "enum": ["A", "B", "C"]},
    "view": {"type": "string", "enum": ["C", "A", "B"]},
    "c_key": {"type": "string"},
    "handoff_mode": {"type": "string", "enum": [independent_c.MODE, single_handoff.MODE]},
    "handoff_type": {"type": "string"},
    "board": {"type": "string"}, "boundary": {"type": "string"},
}


def _prop(name: str) -> dict:
    return _TYPES.get(name) or {"type": "string"}


def json_schema() -> str:
    """一行一条的 JSON Schema。**从 MATERIAL_FIELDS 现算**，不手写。

    codex 实测后要的就是这个（原话：「最稳妥的做法是提供实际的
    core/matspec.py、JSON Schema，或者一份已经成功导入的完整 JSONL 样例」）。
    手写一份的话，改了字段表忘了改 schema，就又回到「文档和程序对不上」——
    而那正是它这次撞上的问题。
    """
    def one(kind):
        # `kind` 用 const 而不是 enum —— **三个分支都写 enum 的话，
        # 一条 image 也满足 manifest 分支**（它只要求 kind，额外字段又不禁），
        # 于是 oneOf 匹配到两个，校验器判不合法。const 才能把分支分开。
        rows = MATERIAL_FIELDS[kind]
        props = {f: dict(_prop(f), description=why) for f, _n, why in rows}
        props["kind"] = {"const": kind}
        return {
            "type": "object",
            "properties": props,
            "required": [f for f, n, _w in rows if n],
        }
    sch = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$comment": "Studio 契约说明版本 " + CONTRACT_REVISION + "；引用图关系仍须由导入器检查。",
        "title": "生产材料的一行",
        "oneOf": [one("manifest"), one("image"), one("video")],
        "$defs": {"ref": {
            "type": "object",
            "properties": {f: _prop(f) for f, _n, _w in REF_FIELDS},
            "required": ["image_n"],
            "anyOf": [{"required": ["key"]}, {"required": ["asset_id"]}],
        }},
    }
    return json.dumps(sch, ensure_ascii=False, indent=2)


def jsonl_schema(system: str = "v34") -> str:
    """推荐格式：一行一条 JSON。**按体系两份。**

    以前只有一份，两套体系共用 —— 而 v7.0 把电影级的故事板整条换成了
    ABC 交接板，通用十二环节没这回事。共用一份的话，总有一套拿到的示例
    在教它产一批本体系用不上的东西。本函数保留旧模式字段示例；
    当前独立单图示例由 single_jsonl_schema 提供，不能混用。

    这份旧样例演示可导入的字段形状：引用链闭合、编号连续、申报与任务数一致。
    提示词仍为占位内容，不能直接用于生产。完整新模式示例由 single_jsonl_schema 提供。

    第四样：**画幅每张各写各的**。人物立绘 3:4、空间母图和整板 16:9、
    A/B 区图 9:16（成片画幅）、C 区图 16:9（要看清整个空间）—— 全写同一个
    值的话，codex 照样例产就是全剧一个数，而画幅本来该按这一张画什么定。

    第五样：**交接板按需**。示例演示同一块板的两端 —— SEG01 用它的 A 区
    定结尾，SEG02 用它的 B/C 区定开头和空间。SEG01 只有一条 `OUT_A`，
    因为它前面没有段（首段无入板）。写死三条的话，codex 照样例产就会每段
    都凑三条，而天然转场那些边界根本没有板可引。
    """
    man = {
        "kind": "manifest",
        "total": 8, "image": 6, "video": 2,
        # 两段 —— 因为这份示例要演示**同一块板的两端**：
        # SEG01 用它的 A 区定结尾，SEG02 用它的 B/C 区定开头和空间。
        "episodes": 1, "segs_per_episode": {"EP01": 2},
        "params": {"episode_seconds": 30, "seg_duration": 15,
                   "ratio": "9:16", "image_size": "9:16",
                   "pacing": "中速", "subtitle": True},
    }
    # 共同空间母图。整板以它当空间上游 —— **不是凭空想一个场景**。
    scstate = {
        "kind": "image",
        "key": "PRJ_X__SCSTATE_EP01_SEG02_W01_R01",
        "family": "SCSTATE",
        "name": "EP01-SEG02 开场空间状态",
        "filename": "PRJ_X__SCSTATE_EP01_SEG02_W01_R01.png",
        # 空间母图要看清全场，所以用横的；成片是 9:16。
        # **画幅按这一张画什么定，不是全剧一个值。**
        "size": "16:9",
        "reference_images": [],
        "prompt": "（完整可投喂提示词正文，一个字都不要省）",
    }
    BOARD = "PRJ_X__ABC_EP01_SEG01_TO_SEG02_R01"
    # 整板：**一个交接边界一张**，A/B/C 三区一次生成。
    # 不是每段一张 —— 只有需要承接的边界才做。
    board = {
        "kind": "image",
        "key": BOARD,
        "family": "ABC",
        "boundary": "EP01_SEG01_TO_SEG02",
        "name": "SEG01→SEG02 交接板（A前尾 / B后首 / C后段空间）",
        "filename": f"{BOARD}.png",
        # 整板是容器，按排版和清晰度选比例，不必等于成片。
        "size": "16:9",
        "reference_images": [
            {"image_n": 1, "key": "PRJ_X__SCSTATE_EP01_SEG02_W01_R01"},
            {"image_n": 2, "key": "PRJ_X__CHAR_001_R02"}],
        "prompt": "（完整可投喂提示词正文，一个字都不要省）",
    }

    def _region(g, what):
        """区域图：**唯一图片输入就是那张整板**，不补投别的。"""
        return {
            "kind": "image",
            "key": f"PRJ_X__ABC_EP01_SEG01_TO_SEG02_{g}_R01",
            "family": "ABC",
            "region": g,
            "board": BOARD,
            "name": f"交接板 {g} 区独立图（{what}）",
            "filename": f"PRJ_X__ABC_EP01_SEG01_TO_SEG02_{g}_R01.png",
            # A/B 是成片构图，跟成片画幅；C 要看清整个空间，用横的。
            "size": "9:16" if g in ("A", "B") else "16:9",
            "reference_images": [{"image_n": 1, "key": BOARD}],
            "prompt": "（完整可投喂提示词正文，一个字都不要省）",
        }

    ra, rb, rc = (_region("A", "前段结尾"), _region("B", "后段开头"),
                  _region("C", "后段空间"))
    img = {
        "kind": "image",
        "key": "PRJ_X__CHAR_001_R02",
        "family": "CHAR",
        "name": "林溪身份根",
        "filename": "PRJ_X__CHAR_001_R02.png",
        # 人物立绘，竖幅但更窄 —— 和成片的 9:16 故意不一样。
        "size": "3:4",
        "reference_images": [],
        "prompt": "（完整可投喂提示词正文，一个字都不要省）",
    }
    # 这一段（SEG02）**有入板、没出板** —— 它后面那个边界是天然转场。
    # 所以只有 IN_B / IN_C 两条，没有 OUT_A。样例就该演示「按需」这件事：
    # 写死三条的话，codex 照样例产就会每段都凑三条，
    # 而天然转场那些边界根本没有板可引。
    vid = {
        "kind": "video",
        "key": "EP01-SEG02",
        "episode": "EP01",
        "seg": "SEG02",
        "filename": "PRJ_X__VIDEO_EP01_SEG02_R01.mp4",
        "duration": 15,
        "ratio": "9:16",
        "handoff_refs": [
            {"image_n": 1, "key": "PRJ_X__ABC_EP01_SEG01_TO_SEG02_B_R01",
             "role": "IN_B"},
            {"image_n": 2, "key": "PRJ_X__ABC_EP01_SEG01_TO_SEG02_C_R01",
             "role": "IN_C"}],
        "reference_images": [
            {"image_n": 3, "key": "PRJ_X__CHAR_001_R02"}],
        "prompt": "（完整可投喂提示词正文）",
    }
    # 边界的**前一段**：它只有出板 A（定本段结尾）。
    # 前面没有段，所以没有入板 —— 这就是「首段无入板」。
    vid_prev = {
        "kind": "video",
        "key": "EP01-SEG01",
        "episode": "EP01",
        "seg": "SEG01",
        "filename": "PRJ_X__VIDEO_EP01_SEG01_R01.mp4",
        "duration": 15,
        "ratio": "9:16",
        "handoff_refs": [
            {"image_n": 1, "key": "PRJ_X__ABC_EP01_SEG01_TO_SEG02_A_R01",
             "role": "OUT_A"}],
        "reference_images": [
            {"image_n": 2, "key": "PRJ_X__CHAR_001_R02"}],
        "prompt": "（完整可投喂提示词正文）",
    }
    import json as _j
    if system != "v34":
        # 通用十二环节：没有场景状态图，也没有交接板 —— 故事板那套照旧。
        sb = [{"kind": "image",
               "key": f"PRJ_X__SBSHEET_EP01_SEG01_{g}_R01",
               "family": "SBSHEET",
               "name": f"EP01-SEG01 故事板 {g}",
               "filename": f"PRJ_X__SBSHEET_EP01_SEG01_{g}_R01.png",
               "size": "9:16", "reference_images": [],
               "prompt": "（完整可投喂提示词正文，一个字都不要省）"}
              # **两张，不是一张。** 样例给一张的话，codex 照样例产就是一张，
              # 而那正是「视频只有一个参考图」的来处 —— 样例本身在教它。
              for g in ("A", "B")]
        man61 = dict(man, total=4, image=3, video=1,
                     segs_per_episode={"EP01": 1}, params=dict(man["params"], episode_seconds=15))
        vid61 = {
            "kind": "video", "key": "EP01-SEG01",
            "episode": "EP01", "seg": "SEG01",
            "filename": "PRJ_X__VIDEO_EP01_SEG01_R01.mp4",
            "duration": 15, "ratio": "9:16",
            "storyboard_refs": [
                {"image_n": 1, "key": "PRJ_X__SBSHEET_EP01_SEG01_A_R01"},
                {"image_n": 2, "key": "PRJ_X__SBSHEET_EP01_SEG01_B_R01"}],
            "reference_images": [{"image_n": 3, "key": "PRJ_X__CHAR_001_R02"}],
            "prompt": "（完整可投喂提示词正文）",
        }
        return chr(10).join(_j.dumps(r, ensure_ascii=False)
                            for r in (man61, img, *sb, vid61))
    return chr(10).join(_j.dumps(r, ensure_ascii=False)
                        for r in (man, scstate, img, board, ra, rb, rc,
                                  vid_prev, vid))


def single_jsonl_schema() -> str:
    """Runnable material example for the current mode, with no prompt placeholders."""
    c = "PRJ_DEMO__ABC_SPACE_R01"
    boundary = "EP01_SEG01_TO_SEG02"
    a, b = (f"PRJ_DEMO__ABC_{boundary}_R01_{view}" for view in ("A", "B"))
    rows = [{"kind": "manifest", "total": 5, "image": 3, "video": 2,
             "episodes": 1, "segs_per_episode": {"EP01": 2},
             "params": {"episode_seconds": 30, "image_size": "16:9", "ratio": "16:9", "seg_duration": 15}}]
    for key, view, prompt in (
        (c, "C", "真人电影质感，生成无人庭院的独立现场 C，东侧石门、西侧树木、中央石桌。"
         "日光柔和，平视展示完整空间布局；一张单幅图，不分格，不添加文字。"),
        (a, "A", f"Image 1 = {c} 同一时刻的无人庭院现场，东侧石门、西侧树木、中央石桌。\n"
         "生成单幅 A：石门近景，摄影机位于树旁朝东拍摄；保持同一时刻现场及原物体位置，石桌位于画外。"
         "真人电影质感，不分格，不添加文字。"),
        (b, "B", f"Image 1 = {c} 同一时刻的无人庭院现场，东侧石门、西侧树木、中央石桌。\n"
         "生成单幅 B：石桌中景，摄影机位于石门旁朝西拍摄；保持同一时刻现场及原物体位置，树木在石桌后方。"
         "真人电影质感，不分格，不添加文字。"),
    ):
        row = {"kind": "image", "key": key, "filename": key + ".png", "handoff_mode": "c_then_a_b",
               "view": view, "size": "16:9", "prompt": prompt, "reference_images": []}
        if view != "C":
            row.update(boundary=boundary, c_key=c, reference_images=[{"image_n": 1, "key": c}])
        rows.append(row)
    for seg, refs, mapping, motion, continuity in (
        ("SEG01", [{"image_n": 1, "key": a, "role": "OUT_A"}],
         f"Image 1 = {a} 庭院石门近景，前段结尾构图，保持石门朝向与现场位置。",
         "[镜头1] 从庭院树旁朝石门平稳前进，光影随真实移动自然变化，最后抵达参考图的机位与构图。",
         "结尾抵达交接 A 的现场状态，下一段从同一时刻的 B 取景接续，物体不换位。"),
        ("SEG02", [{"image_n": 1, "key": b, "role": "IN_B"}, {"image_n": 2, "key": c, "role": "IN_C"}],
         f"Image 1 = {b} 庭院石桌中景，本段首镜构图，石门旁朝西的机位。\n"
         f"Image 2 = {c} 同一庭院的独立现场 C，保持开场石门、树木和石桌的位置关系。",
         "[镜头1] 从参考 B 的构图开始，镜头向石桌缓缓推进，树木始终位于石桌后方，微风带动树叶。",
         "开场继承前段交接时刻的现场，随后自然推进；不把 C 额外拍成一个镜头。"),
    ):
        prompt = ("【画幅风格】\n真人电影质感，柔和日光，自然色彩。\n【参考图】\n" + mapping +
                  "\n【站位声明】\n无人庭院，石门在东、树木在西、石桌居中，保持共同现场。\n【时间轴分镜】\n" + motion +
                  "\n【接续状态】\n" + continuity + "\n【音效】\n仅自然风声与树叶摩擦声，无语言人声。"
                  "\n【强制禁止项】\n不增人物、字幕、旁白或配乐，不改变现场物体数量与位置，不出现分格画面。")
        rows.append({"kind": "video", "key": "EP01-" + seg, "filename": "EP01-" + seg + ".mp4",
                     "episode": "EP01", "seg": seg, "duration": 15, "ratio": "16:9", "prompt": prompt,
                     "handoff_refs": refs, "reference_images": []})
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)


# 材料交到哪。**约定一个确定的名字**，别让人每次去翻文件对话框 ——
# 而且这个名字要和扫描口认的后缀对得上（`/api/material/scan`）。
MAT_DIR = "00_生产材料"
MAT_NAME = "生产材料.jsonl"


def render(limits: Optional[dict] = None, project_name: str = "",
           system: str = "v34") -> str:
    """生成契约正文（md）。**按体系出两份** —— 两套的活不一样多。"""
    sysm = SYSTEMS.get(system) or SYSTEMS["v34"]
    L = []
    a = L.append
    a("# 生产材料契约"
      + (f"｜{project_name}" if project_name else "")
      + f"｜{sysm['label']}")
    a("")
    a(CONTRACT_SCOPE)
    a("")
    a(f"**这一份是给「{sysm['label']}」用的。** 两套体系的活不一样多，"
      "别拿另一套的契约去产 —— " + sysm["note"])
    a("")
    a("这份是**程序要吃的结构**，从代码现算出来的（`core/matspec.py`）——"
      "所以它和实际要求永远是同一份东西，不会飘。")
    a("")
    a("## 一句话")
    a("")
    a("你产出的每一条 = **一个可以直接投喂的生产任务**。"
      "程序不再做任何 LLM 环节，拿到你的材料就直接出图出片。")
    a("")
    a("## 这份契约管什么、不管什么")
    a("")
    a("**只管数据形状**：字段名、编号怎么排、引用指向谁、参数的合法取值 —— "
      "因为程序真的要读它们，读不对就发不出去或者发错。")
    a("")
    a("**不管你怎么创作**：哪些边界要交接板、补图补什么、画幅选竖还是横、"
      "提示词怎么写 —— 那些是你的判断，程序不懂内容，也不该拿它的偏好当规矩。"
      "上一版里混进了一整套分镜方法论（「不许退化成一张起始图」"
      "「补图要证明有独有作用」之类），已经撤掉 —— 那是在替你做决定，"
      "而且没有任何程序逻辑依赖它。")
    a("")
    a("所以下面每一条要求，都能回答「程序哪一行代码要读它」。"
      "答不上来的，不该出现在这里。")
    a("")
    a("## 交出来的就一个文件")
    a("")
    a(f"一个 `{MAT_NAME}`（名字随你，后缀用 `.jsonl`）。**你不用知道项目在"
      f"哪，也不用建任何目录** —— 产完把这个文件丢进程序（页面上「选文件」"
      f"导入，或者放进项目的 `{MAT_DIR}/` 点扫描），"
      f"落盘和分发是程序的事。")
    a("")
    a("提示词的 txt、任务表、目录结构，全部由程序从这一份里生成。"
      "**你自己往那些目录里放东西不会被读** —— 程序只认这一份材料。")
    a("")
    a("下面「落点」那一节写的是**程序会把产物放到哪**，"
      "是给你对号用的，不是让你去创建的。")
    a("")
    a("## 格式：一行一条 JSON（JSONL）")
    a("")
    a("**别用 md 散文。** 散文要靠猜排版：全角等号还是半角、"
      "`Image N` 在参考图那一节还是正文里复述、`---` 是分隔线还是内容 ——"
      "每一处猜错都是静默的（比如张数数成两倍，然后「超上限」全员误报）。"
      "JSONL 没有这些歧义，错在哪一行、哪个字段都能指出来。")
    a("")
    a("### 当前 c_then_a_b 模式示例")
    a("")
    a("下面是用于导入验证的完整小场景：3 张独立交接图、2 段各 15 秒的视频，合计 30 秒。"
      "示例说明文件结构、角色与引用关系；正式项目须按已确认的剧本、导演设计和项目锁编译，"
      "不能用示例内容或时长替换实际设计。导入通过不代表媒体已生成或实图验收通过。")
    a("```jsonl")
    a(single_jsonl_schema())
    a("```")
    a("")
    a("### 旧模式字段示例（仅兼容参考）")
    a("")
    a("以下 " + ("ABC 整板与区域图" if system == "v34" else "通用故事板") +
      " 示例只演示旧模式字段形状，含提示词占位内容，不是当前 c_then_a_b 的生产模板；"
      "只有明确沿用该旧模式时参考它，并补齐本项目完整提示词。")
    a("")
    a("```jsonl")
    a(jsonl_schema(system))
    a("```")
    a("")
    if system != "v61":
        a(independent_c.RULES)
    a(single_handoff.RULES)
    a("## 第一行：申报头（必须有）")
    a("")
    a("材料第一行是一条 `{\"kind\": \"manifest\", ...}`，"
      "写清这一份里有几条、图片和视频各几条、共几集、每集几段。")
    a("")
    a("**这是唯一能查出「少产了」的东西。** 没有它的话，该 84 条只给 80 条时，"
      "剩下那 80 条引用链闭合、编号连续、段号连续 —— 全绿，"
      "程序一句话说不出来，直到成片短一截。")
    a("")
    a("边界也说清：对账查的是「你有没有兑现你自己说的」。"
      "申报 84、实际 84、但本该 88 —— 这个查不出来，那是你自己的判断，"
      "程序无从知道。")
    a("")
    a("## 项目参数：由你申报，程序不拿它当限制")
    a("")
    a("申报头里带一个 `params`，把这部剧的生产参数写进去：")
    a("")
    for k, why in PARAMS:
        a(f"- `{k}` —— {why}")
    a("")
    a("**这些是你给程序的，不是程序给你的限制。** 和页面上填的项目参数"
      "不一样也照导 —— 不夹、不改、不拦。程序只做三件事：显示出来给人看、"
      "当没写 `duration` / `ratio` 那几条的默认值、以及拿 `segs_per_episode` "
      "和实物对账。")
    a("")
    a("所以集数、每集多少秒、每集切几段，是**你按剧情定的**。"
      "程序无从判断 21 集对不对 —— 它只能查你有没有兑现自己申报的数。")
    a("")
    a("## 你要写的字段")
    a("")
    a("**这一节是权威。** 每一项都由导入器实际读，有测试逐条验证"
      "「文档里写的字段解析器真的认」。")
    a("")
    _LAB = {"image": "图片", "video": "视频", "manifest": "申报头（第一行）"}
    for kind, rows in MATERIAL_FIELDS.items():
        a(f"### {_LAB[kind]}")
        a("")
        for f, need, why in rows:
            a(f"- `{f}`{'（必填）' if need else ''} —— {why}")
        a("")
    a("### 参考图元素（`reference_images` / `handoff_refs` 里每一项）")
    a("")
    for f, need, why in REF_FIELDS:
        a(f"- `{f}`{'（必填）' if need else ''} —— {why}")
    a("")
    a("程序读取 image_n 与 key/asset_id，并读取 handoff_refs 的 role 及适用旧模式的 board。"
      "以下字段不参与上述结构判定："
      + "、".join(f"`{f}`" for f in REF_FIELDS_IGNORED)
      + "。这些信息可按 Agent 内部设计与审核需要保存，不作为 Studio 强制交付字段。")
    a("")
    a("表外的字段同理：不读，也不报错。")
    a("")
    a("### 机器可校验的 JSON Schema")
    a("")
    a("```json")
    a(json_schema())
    a("```")
    a("")
    a("## 程序会把它变成什么（给你对号用的，不是让你写的）")
    a("")
    a("导入之后每条会变成一个任务，**字段名和上面那张表不一样** ——"
      "这是两套 schema，别照着这一节写材料：")
    a("")
    for kind, rows in TASK_FIELDS.items():
        a(f"### {_LAB[kind]}任务")
        a("")
        for f, why in rows:
            a(f"- `{f}` —— {why}")
        a("")
    a("最容易写错的三个：材料写 `filename`，任务里叫 `output`（路径由程序算）；"
      "材料的参考图用 `key`，任务里转成 `asset_id` + `file_ref`；"
      "材料写顶层 `size` / `duration`，任务里进 `params.size` / `params.duration`。")
    a("")
    a("## 参考图怎么写")
    a("")
    a("`image_n` **就是实际上传顺序**：第 1 个元素就是 Image 1。"
      "视频的交接图排在前面（`handoff_refs`），"
      "补图接着排（`reference_images`）。")
    a("")
    a("提示词正文里**每张要有一句「这是谁」** —— 出图前那道检查查的就是它："
      "多人场景里只写编号、不写是谁，模型必然张冠李戴。")
    a("")
    a("```")
    a("Image 1 = 实际ID 身份描述及用途")
    a("```")
    a("")
    a("**硬要求只有这一行。** 下面这几项写了更稳（多图时模型不容易平均融合），"
      "缺了只提醒、不拦 —— 怎么写、写多细是你的判断：")
    a("")
    a("```")
    a("  故事时间 / 当前状态：…")
    a("  有权控制：…    无权控制：…    适用范围：…")
    a("```")
    a("")
    a("出图前有**两道**检查：第一道查编号和上传顺序对不对，"
      "第二道查每个编号后面有没有说清这张图是谁。"
      "只写编号会被第二道拦下 ——「只说这张图控制服饰」不够，"
      "它不知道是哪个人，多人场景必然张冠李戴。")
    a("")
    a(REFERENCE_RULES)
    a("")
    a("## 视频的参考图：两个数组，一个编号序列")
    a("")
    a("`handoff_refs` 排在前面，`reference_images` 接着排 —— "
      "两个数组拼起来就是模型看到的 Image 1..N，`image_n` 从 1 连续排。"
      "程序按这个顺序上传，不重排、不去重、不补号。")
    a("")
    a("**几张、选哪几张，是你的判断。** 程序检查引用数量、连续编号、引用目标存在，"
      "以及所选模式下的 role、同版 C 来源、边界配对与依赖关系。"
      "参考图上限是**这一次能装多少**（服务商的容量），不是目标也不是下限。")
    a("")
    a("## 输出目录与分类")
    a("")
    for what, where in sysm["dirs"].items():
        a(f"- **{what}** → `{where}`")
    a("")
    a("**视频不看 key 的前缀** —— `kind: \"video\"` 就落 `05_分段视频/`。"
      "契约要求视频的 key 是 `EP01-SEG01`（拼接按这个前缀挑本集分段），"
      "那个形状里本来就没有家族前缀。")
    a("")
    a("图片先按结构化模式路由：c_then_a_b + view → 04_交接图/C、A、B，分类为 HANDOFF；"
      "此模式的 C 不放进 SCSTATE。"
      + ("旧独立 C → 03b_场景状态图，旧 region → 04_交接板/区域图。" if system != "v61" else "")
      + "其他图片再按 key 里的家族前缀算落点。JSONL 只要给 `filename`。"
      "**这套体系认这些前缀**（不在表里的会落到人物身份资产，"
      "所以别用表外的前缀）：")
    a("")
    for fam, where in sysm["families"]:
        a(f"- `{fam}` → `{where}`")
    a("")
    a("## 会被怎么验收")
    a("")
    a("导入前会跑这几条，**有一条不过就不导** ——"
      "导进去之后它们已经变成任务了，那时候改要连带清任务。")
    a("")
    for name, why in AUDIT:
        a(f"- **{name}** —— {why}")
    a("")
    a("**参考图张数不写在这儿。** 一段配几张、有没有超目标模型的容量，"
      "由你那边判断 —— 我们不写一个数进来压你：模型换一个上限就变，"
      "而且逐模型各不相同（同一家里整家声明 9、而它的 2.5 系列每个模型"
      "声明 30 —— 按整家那个数写进契约，等于让你少产 21 张的余量）。")
    a("")
    a("## 不需要的东西")
    a("")
    a(f"程序原来那 {sysm.get('stage_count', '')} 个环节的中间产物"
      f"（{sysm.get('midfiles', '')}）不是本次 JSONL 导入的必交文件。"
      "Agent 仍须保存并审核必要的设计、依据和内部台账；内部记录与外部交付格式分开，"
      "不能把不需要交给 Studio 理解为不需要完成内部工作。")
    a("")
    a("## 程序查不了的，靠你保证")
    a("")
    a("这几件**没有任何一处会说话**，出错要到看图看片才发现：")
    a("")
    a("- **提示词正文必须完整可投喂。** 不要写「同上」「同前」「略」「待补」，"
      "不要为了省篇幅截断。残篇会一路跑到出图那一刻 —— 图也出得来，只是不对。"
      "（用长度或占位词去猜都抓不准，所以程序不做这个检查。）")
    a("- **参考图要引对的那一个。** 引用链闭合只证明「这个 key 有人产出」，"
      "不证明「这一段该引它」。EP05 的场景引了 EP12 的房间，验收全绿。")
    a("- **时间连不连得上、总时长对不对。** 原来靠中间产物查的那几道"
      "（时间线跳秒、装箱漏镜头）在这条路上不存在。")
    a("")
    a("代价要说清：靠那些字段的几道闸门（时间线跳秒、装箱漏镜头、"
      "判定打架、集数对账）**查不了了**。所以上面「会被怎么验收」那几条"
      "是这条路上全部的检查 —— 剩下的靠你自己保证。")
    return "\n".join(L) + "\n"
