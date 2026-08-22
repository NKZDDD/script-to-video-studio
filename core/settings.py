# -*- coding: utf-8 -*-
"""项目基础信息：把【AI电影级短剧项目基础信息】那 25 项变成有类型的字段。

## 为什么不是让人往提示词里粘一段文字

粘文字踩过一次很实在的坑：用户把整块【基础信息】加进全局提示词，
里面写着「字幕：需要，烧录进画面」，而 `_common.md` 第 10 条写着
「画面内禁止出现任何文字、字幕、水印、UI 面板」——
两段散文直接打架，结果本该在图里的字幕消失了，**而且没有任何报错**。

散文之间的矛盾没法检测。改成有类型的字段之后，这条变成
「一个字段两种取值」，第 10 条按取值**条件渲染**，矛盾从根上不存在。

## 三种来源，只有一个真相

全部 25 项都进这张表（用户要的是「都看得见」），但**来源分三种**：

    settings  存在 project.json 的 settings 里，这里是唯一存放处
    params    镜像已有的生产参数（画幅、单段秒数、出图尺寸）——
              **写回 params，不另存一份**。存两份迟早对不上，
              而且是静默对不上：模型信文本、程序用参数，谁都不报错
    derived   程序算出来的（单集时长来自 episodes.duration_sec、
              参考图上限来自服务商注册表），**只读**

## 环节要哪些字段，不用维护表

每个字段有自己的占位符（`{{SUBTITLE_BURN}}` 这种）。
**模板里写了才收到** —— 谁用了哪个，扫模板就知道（见 `used_by()`）。
不做「哪个环节要哪些」的手写表：这个仓库里已经栽过一次，
`required_vars` 的注释写着「再手抄一份表，迟早和依赖表对不上，
然后校验就成了摆设」。

单独一个占位符而不是塞进 `{{PARAMS}}` 那个大包，也是为了这个 ——
大包只能答「谁用了 PARAMS」，答不出「谁用了字幕」。
"""

from __future__ import annotations

from typing import Any, Optional

from .store import Project

# ---------------------------------------------------------------- 字段表
#
# key          存取用的名字，同时决定占位符（大写）
# type         bool / enum / text / int / list
# source       settings（存这儿）/ params（镜像生产参数）/ derived（只读）
# when         只在另一个字段为真时才显示和渲染
# group        页面上的分组
# **字段表直接照 skill 的「开始前解析项目参数」那 30 项**（SKILL.md 第 28 行起）。
# key 用 skill 的原名，枚举值用 skill 的原值 —— 自己另起一套名字，
# 对着 skill 排查问题时每次都要先做一遍翻译。
FIELDS: list = [
    # ================================================== 我们还没有的（要你填）
    {"key": "source_type", "label": "原文是什么", "type": "enum",
     "options": ["screenplay", "novel", "outline", "existing_assets"],
     "zh": {"screenplay": "剧本", "novel": "小说", "outline": "大纲",
            "existing_assets": "已有资产"},
     "default": "screenplay", "source": "settings", "group": "项目"},
    {"key": "adaptation_authority", "label": "剧情优化权限", "type": "enum",
     "options": ["preserve", "optimize_pacing", "authorized_rewrite"],
     "zh": {"preserve": "严格保持原文", "optimize_pacing": "允许优化节奏与镜头",
            "authorized_rewrite": "允许适度改编"},
     "default": "optimize_pacing", "source": "settings", "group": "授权",
     "why": "skill 第 0 章：**不得在下游自行扩大改编权限**。"
            "定紧了节奏可能拖沓，定松了模型会改人物关系和结局。"},
    # 填空 + 下拉：像视觉风格一样。以前是四选一的枚举，用户要填的
    # 形式（水墨动画、黏土定格…）根本不在选项里；媒介是制作决策，
    # 用户自己的说法就是标准答案，不该被四个选项框死。
    # 种子就是原枚举的四个中文说法 —— 页面、MEDIUM_ZH、老值翻译共用一套词。
    {"key": "visual_medium", "label": "拍成什么形式", "type": "text",
     "hint": "真人短剧 / 3D漫剧 / 二维动画 / 混合形式…",
     "default": "真人短剧", "source": "settings", "group": "项目",
     "suggest": True,
     "seeds": ["真人短剧", "3D漫剧", "二维动画", "混合形式"],
     "why": "自由填写。常用：真人短剧 / 3D漫剧 / 二维动画 / 混合形式；"
            "用户写了别的（水墨动画、黏土定格…）照抄原话，不要归并到这四个里。"},
    {"key": "visual_style", "label": "视觉风格", "type": "text",
     "hint": "电影写实 / 都市情感 / 末日废土 / 古装写实…",
     "source": "settings", "group": "项目",
     # 填空 + 下拉：自由文本，但输入框带历史下拉（以前用过的值 + 几个起手选项）。
     # 只属于另一种媒介的词（3D / 真人 / 动画…）不放进 seeds ——
     # 媒介归「拍成什么形式」管，这里出现的每个词都得和它兼容。
     "suggest": True,
     "seeds": ["电影写实", "都市情感", "末日废土", "古装写实"]},
    {"key": "cultural_setting", "label": "文化与地域设定", "type": "text",
     "source": "settings", "group": "项目",
     "why": "skill 明写：**不要把提示词语言等同于文化设定**。"
            "地域、服饰、医院、建筑、货币、称谓只服从这一项和故事真相。"},
    {"key": "dialogue_language", "label": "对白语言", "type": "text",
     "default": "中文", "source": "settings", "group": "语言"},
    {"key": "instruction_language", "label": "提示词写成什么语言", "type": "text",
     "default": "中文", "source": "settings", "group": "语言",
     "why": "只管提示词用什么语言写，**不决定剧里的地域和文化**（见上一条）。"},
    {"key": "video_audio_mode", "label": "声音怎么来", "type": "enum",
     "options": ["native_audio", "silent_video", "separate_audio"],
     "zh": {"native_audio": "视频模型原生音频", "silent_video": "无声视频",
            "separate_audio": "后期单独制作"},
     "default": "native_audio", "source": "settings", "group": "语言"},
    {"key": "native_audio_transition_support", "label": "视频模型能不能做声音转场",
     "type": "enum", "options": ["yes", "no", "unknown"],
     "zh": {"yes": "支持", "no": "不支持", "unknown": "未知"},
     "default": "unknown", "source": "settings", "group": "语言"},
    {"key": "costume_asset_mode", "label": "衣服要不要单独出图", "type": "enum",
     "options": ["auto", "separate_cost", "direct_look"],
     "zh": {"auto": "自动判断", "separate_cost": "服装单独建资产",
            "direct_look": "直接做完整造型"},
     "default": "auto", "source": "settings", "group": "资产",
     "why": "skill 第 5 章：简单服装走 LOGICAL_ONLY 文字契约、不出图；"
            "关键或复用的才建 Canonical 服装资产。选 direct_look 资产条数最少。"},
    {"key": "existing_canon", "label": "已经定稿、要继续沿用的资产", "type": "text",
     "source": "settings", "group": "资产",
     "why": "**目前只作为说明发给模型，还不能真正继承编号** —— "
            "第四环节缺 EXISTING_CANONICAL 那一档，补上之前别指望它复用。"},
    {"key": "output_depth", "label": "这次做到哪一步", "type": "enum",
     "options": ["production_ready", "plan", "analysis"],
     "zh": {"production_ready": "可直接生产", "plan": "只到方案",
            "analysis": "只做分析"},
     "default": "production_ready", "source": "settings", "group": "项目"},

    # ---- 字幕：**skill 的参数表里没有这一项**，是本地扩展 ----
    #
    # 加它是因为踩过坑：用户把「字幕：需要，烧录进画面」写进全局提示词，
    # 和 `_common` 第 10 条「画面内禁止出现任何文字、字幕」直接矛盾，
    # 结果字幕消失且不报错。有了这个字段，第 10 条按取值生成。
    #
    # **这里原来有四项，是我做复杂了。** 曾经是：需要字幕 / 字幕语言 /
    # 字幕印不印进画面 / 「画面里本来就该有的文字」（自由文本）。
    # 用户原话：「实际上画面上的字都是要有的，我需要控制的只是有没有字幕」。
    # 对 —— 手机屏幕、招牌、信件、报纸、弹幕这些是**剧情本身**，
    # 不是一个要不要的选择；让人一部剧一部剧去枚举它们，
    # 漏一类就被那条禁令悄悄拦掉（不报错，只是画面里没有）。
    # 所以剧情文字改成**一律允许**，只剩「要不要字幕」这一个开关。
    #
    # 「印不印进画面」也一起去掉：本程序做的是出图出片，
    # 画面里有字幕就是烧录进去的，没有第二种做法 ——
    # 留着那一栏只会让人以为还有个后期通道。
    {"key": "subtitle", "label": "画面里要不要字幕", "type": "bool",
     "default": False, "source": "settings", "group": "字幕", "local": True,
     "why": "只管字幕。剧情本身要求的文字（手机屏幕、招牌、信件、报纸、"
            "弹幕这些）一律允许，不用你在这儿声明。"
            "勾上之后程序会自动改写「画面内禁止出现文字」那条规则，"
            "不用你手动去改提示词 —— 手改两处散文必然打架。"},
    {"key": "subtitle_lang", "label": "字幕语言", "type": "text",
     "default": "中文", "when": "subtitle", "source": "settings",
     "group": "字幕", "local": True},
    # ---- 旁白 / 画外音：**skill 的参数表里也没有这一项**，本地扩展 ----
    #
    # 它和「音频模式」是两回事，混了会出错：
    #   音频模式  = 声音**从哪来**（视频模型原生 / 无声 / 后期做）
    #   旁白      = 有没有一个**不在画面里说话的人**在讲
    #
    # 这一项不说清的后果很具体：第一人称小说改的短剧，正文大半是
    # 「我抱着他退到窗边」这种内心独白 —— 不告诉模型那是旁白，
    # 它会把独白当成**角色开口说的台词**，于是画面里的人一直在自言自语。
    {"key": "narration", "label": "有旁白 / 画外音", "type": "bool",
     "default": False, "source": "settings", "group": "旁白", "local": True,
     "why": "和「音频模式」是两件事：那个管声音从哪来，这个管**有没有人在画外讲**。"
            "第一人称小说改编时不填这一项，内心独白会被当成角色台词，"
            "画面里的人会一直在自言自语。"},
    {"key": "narration_style", "label": "旁白形式", "type": "enum",
     "options": ["first_person_inner", "third_person", "mixed"],
     "zh": {"first_person_inner": "第一人称内心独白",
            "third_person": "第三人称旁白", "mixed": "混合"},
     "default": "first_person_inner", "when": "narration",
     "source": "settings", "group": "旁白", "local": True},
    {"key": "narration_voice", "label": "旁白用谁的声音", "type": "text",
     "when": "narration", "source": "settings", "group": "旁白", "local": True,
     "hint": "写角色名或编号，如「女主 C001」—— 旁白的声线要和她本人一致",
     "why": "不指定的话，同一部剧里旁白的声线会在不同段落之间飘。"},
    {"key": "narration_on_screen", "label": "念旁白时人物要不要动嘴",
     "type": "enum", "options": ["no_lip_sync", "lip_sync"],
     "zh": {"no_lip_sync": "不动嘴（画外音）", "lip_sync": "对口型（当台词说）"},
     "default": "no_lip_sync", "when": "narration",
     "source": "settings", "group": "旁白", "local": True,
     "why": "**这一条是旁白最容易出错的地方。** 默认不动嘴 —— "
            "内心独白配上对口型，看起来就是角色在自言自语。"},
    # ---- 对白怎么呈现：解说剧要的那一条，**skill 的参数表里也没有** ----
    #
    # 和上面那一项差一层，混了就漏：
    #   narration_on_screen = **旁白**念的时候人物动不动嘴
    #   dialogue_mode       = **对白**（剧本里带引号那些）是谁在说
    #
    # 解说剧是「全片一个解说在讲」，连角色的对白也由画外音念出来，
    # 画面里没有人开口。而剧本里照旧写着带引号的对白 ——
    # 实测「烟火尽头」896 段里有 121 段带引号的对白、136 段第一人称独白。
    # 只勾「旁白不动嘴」的话，那 121 段照旧会被当成台词对口型，
    # 于是全片一半画外音一半对口型，看着就是坏的，而且不报错。
    {"key": "dialogue_mode", "label": "对白怎么呈现", "type": "enum",
     "options": ["in_scene", "voice_over_only"],
     "zh": {"in_scene": "角色开口说（对口型）",
            "voice_over_only": "全部画外音，人物不开口"},
     "default": "in_scene", "source": "settings", "group": "旁白", "local": True,
     "why": "解说剧选「全部画外音」—— 连剧本里带引号的对白也由解说念，"
            "画面里没有人开口。和上面那一项不是一回事：那个管旁白，这个管对白。"},

    {"key": "special_notes", "label": "特殊要求", "type": "text",
     "source": "settings", "group": "授权", "local": True,
     "hint": "选角、颜值、镜头偏好、禁忌…例：男女主颜值按偶像明星级别；不要俯拍",
     "why": "这里写的东西会进**每一次调用**的系统提示词，全部环节都看得到 —— "
            "所以适合放「整部剧都要遵守」的要求（选角标准、镜头偏好、禁忌）。"
            "只针对某一个环节的要求别写这儿，去「提示词」页改那一份模板。"},

    # ============================================ V6.0 新增：图像减压与视频承载
    #
    # V6.0 的主题是「图像减压、视频执行承载强化」。核心是
    # **Image Materialization Gate**：逻辑 Canon 必须完整，但**不等于每个状态都出图**。
    # 只有命中「新身份/新 LOOK、首次显露、不可逆结果、关键 Location/Hero Prop、
    # 跨 SEG 边界、实测高风险」才物化，其余保持 LOGICAL_ONLY 或 DEFER_TO_VIDEO。
    #
    # 这一组直接冲着我们撞过的两个问题来：
    #   · 资产提示词一次写不完（物化门控后要写的条数大幅下降）
    #   · 故事板一张纸画 16 格（V6.0 把上限从 3×3=9 改成 **每张 3 格**）
    {"key": "scstate_materialization_policy", "label": "场景状态要不要出图",
     "type": "enum", "options": ["logical_first", "risk_based", "always_visual"],
     "zh": {"logical_first": "默认只写逻辑合同，不出图",
            "risk_based": "按风险决定出不出图",
            "always_visual": "每个状态都出图（V5.6 的老行为）"},
     # skill 推荐 risk_based，不是 logical_first。logical_first 是最激进的一端，
     # 配上 storyboard=anchor_only 会出现「整段没有任何图片持有
     # Camera/Blocking/Time 权威」——实跑撞到过，故事板被吃光。
     "default": "risk_based", "source": "settings", "group": "图像减压",
     "why": "V6.0 第 9 章：SCSTATE 默认是 CVS 派生的**逻辑合同**，"
            "不要求每个 CVS 出图。中间动作、姿势、反应优先标 DEFER_TO_VIDEO。"},
    {"key": "storyboard_materialization_policy", "label": "故事板怎么承载",
     "type": "enum", "options": ["mandatory_temporal_spine",
                                 "ordered_continuation_sheets",
                                 "ordered_kf_anchors"],
     "zh": {"mandatory_temporal_spine": "由模型按实测选承载（推荐）",
            "ordered_continuation_sheets": "有序多张连续 Sheet（每张≤3格）",
            "ordered_kf_anchors": "有序独立关键帧锚点（一张一格）"},
     "default": "mandatory_temporal_spine", "source": "settings",
     "group": "图像减压",
     "why": "**V6.2 第 19 章改了这一条的性质。** V6.0/6.1 是「出多少张」"
            "（anchor_only / selected_kf / full_storyboard），可以退化到只出一张；"
            "V6.2 定死每个 SEG 必须有覆盖**完整关键时间推进**的故事板骨架，"
            "所以这里选的不再是「出几张」而是「用哪种载体承载」——"
            "有序多张 Sheet，还是有序独立关键帧锚点。两种等价，"
            "按你的模型实测挑：多格 Sheet 理解不稳、容易糊格或泄露未来状态的，"
            "用独立锚点。选「由模型按实测选」就让第十二环节自己判。"},
    {"key": "storyboard_max_kf_per_sheet", "label": "每张故事板最多几格",
     "type": "int", "default": 3, "source": "settings", "group": "图像减压",
     "why": "**V6.0 定的 3，V6.1 保留。** 我们模板里原来写的「3×3=9」是自编的，V5.6 没给过数字上限。实跑撞过 16 格 —— 模型记不住那么多"
            "场次的世界状态，所有格子的 source_scstate 全填成第一个。"
            "更多关键时刻用有序 Continuation Sheet，不是塞进一张。"},
    {"key": "image_complexity_budget", "label": "单张图能有多复杂", "type": "enum",
     "options": ["conservative", "standard", "expanded"],
     "zh": {"conservative": "保守（人少、手部少、构图简单）",
            "standard": "标准", "expanded": "放宽"},
     "default": "conservative", "source": "settings", "group": "图像减压",
     "why": "超预算时 V6.0 要求 SPLIT_ANCHOR / 局部重绘 / LOGICAL_ONLY / "
            "DEFER_TO_VIDEO，**不许靠缩小人物或多塞 Panel 硬塞**。"},

    {"key": "video_execution_reliability", "label": "视频模型靠不靠谱",
     "type": "enum", "options": ["high", "medium", "low", "unknown"],
     "zh": {"high": "高（Start 或 Start/End 就够）",
            "medium": "中（2–4 张时间锚点）",
            "low": "低（必要时上完整故事板）", "unknown": "未知"},
     "default": "unknown", "source": "settings", "group": "视频承载",
     "why": "V6.0 第 16 章按它选 Adaptive Execution Set。"
            "**未知不许冒充 HIGH** —— 冒充的代价是参考图不够，视频自己编。"},
    {"key": "video_reliability_evidence", "label": "上面这个结论是怎么来的", "type": "enum",
     "options": ["user_verified", "project_pilot_verified",
                 "model_profile_only", "unverified"],
     "zh": {"user_verified": "你实测过", "project_pilot_verified": "本项目试跑验证过",
            "model_profile_only": "只是模型档案上写的", "unverified": "没验证"},
     "default": "unverified", "source": "settings", "group": "视频承载",
     "why": "上面那一档是凭什么定的。没验证过就别按 HIGH 走。"},
    {"key": "image_composite_reliability", "label": "出图模型合成多人/手部靠不靠谱",
     "type": "enum", "options": ["high", "medium", "low", "unknown"],
     "zh": {"high": "高", "medium": "中", "low": "低", "unknown": "未知"},
     "default": "unknown", "source": "settings", "group": "视频承载",
     "why": "多人同框、手部、道具交接这类合成做不稳时，"
            "该把活交给视频而不是硬出图。"},
    # V6.1 把 V6.0 的 `approved_video_boundary_reuse`（SEGBOUND，允许把上一段
    # 视频过了 QC 的尾帧当下一段入口）**整条废掉**，换成这一项。
    # 废掉的原因实跑撞到了：尾帧当参考 + 故事板被减压掉之后，整段没有任何
    # 图片持有 Camera/Blocking/Time 权威，模型只能照着一张动作中间帧自己编。
    {"key": "canonical_boundary_policy", "label": "两段之间怎么接",
     "type": "enum",
     "options": ["canonical_cut_pair", "shared_stable_anchor",
                 "motivated_hard_cut", "opaque_buffer_pair"],
     "zh": {"canonical_cut_pair": "预编译一对出/入锚点",
            "shared_stable_anchor": "共用一个稳定锚点",
            "motivated_hard_cut": "有动机的硬切",
            "opaque_buffer_pair": "不透明缓冲对（遮挡过渡）"},
     "default": "canonical_cut_pair", "source": "settings", "group": "视频承载",
     "why": "V6.1：边界锚点在**两条视频生产之前**由 Story Truth、CVS、Spatial、"
            "当前 LOOK/CT 和 Prop Ledger 编译出来（BNDPLAN / BNDANCHOR），"
            "**不得从上一条视频的尾帧提取或反向生成**。"},

    {"key": "location_view_production_mode", "label": "同一场景的多个机位怎么出",
     "type": "enum",
     "options": ["auto", "compatible_view_batch", "single_view_only"],
     "zh": {"auto": "自动判断", "compatible_view_batch": "兼容机位合并出图",
            "single_view_only": "一次只出一个机位"},
     "default": "auto", "source": "settings", "group": "场景机位"},
    {"key": "view_batch_output_mode", "label": "合并出的图怎么存",
     "type": "enum",
     "options": ["separate_files", "atlas_with_lossless_crop", "unsupported"],
     "zh": {"separate_files": "分成独立文件",
            "atlas_with_lossless_crop": "一张 Atlas + 无损裁切",
            "unsupported": "不支持合并"},
     "default": "separate_files", "source": "settings", "group": "场景机位",
     "why": "V6.0：VIEWPACK **只有生产容器的 Authority**，下游默认引用"
            "裁切后的独立 LOC_VIEW —— 不许把整张多机位 Atlas 当一个机位参考。"},
    {"key": "view_batch_max_views", "label": "一次最多合并几个机位",
     "type": "int", "default": 3, "source": "settings", "group": "场景机位"},
    {"key": "redundancy_overlap_heuristic", "label": "多像算重复机位",
     "type": "text", "default": "0.80", "source": "settings", "group": "场景机位",
     "why": "重叠高于它又没有独有 Zone/关系/消费者的机位会被判成重复图。"},
    {"key": "derived_view_min_resolution", "label": "裁出来的单张最低多少像素",
     "type": "text", "source": "settings", "group": "场景机位"},

    # ================================================== 镜像生产参数（写回原处）
    {"key": "aspect_ratio", "label": "画面比例", "type": "text",
     "source": "params", "maps_to": "ratio", "group": "生产参数",
     "why": "和「出图尺寸」是两个独立的值，方向不一致时出片会裁掉两边。"},
    {"key": "image_size", "label": "出图尺寸", "type": "text",
     "source": "params", "maps_to": "image_size", "group": "生产参数"},
    {"key": "seg_duration", "label": "SEG 单条固定时长（秒）", "type": "int",
     "source": "params", "maps_to": "duration", "group": "生产参数",
     "why": "**这是一个容器的容量，不是一集多长。** 混为一谈会让第九环节"
            "把整集压进 15 秒 —— 实跑撞过。"},
    # ---- 总时长 / 集数 / 每集时长：**三个量互相决定** ----
    #
    # 以前只有「每集多少秒」一个旋钮，而集数是**环节1 按剧本自带的章节切的**。
    # 两者没有任何关联，于是用户实遇：选了 60 秒一集，程序照着剧本里的 21 章
    # 切成 21 集 = 21 分钟 —— 而他要的是「按总时长切」。
    #
    # 用户原话：「节奏已经被我修改了，所以集数就要跟着逻辑变，
    # 总时间和总集数、每集的时间应该是互相影响的逻辑才对」。对。
    #
    # 规则：填两个算第三个；填一个另外两个由环节1 定；三个都填而且乘不通 → 报冲突。
    {"key": "total_seconds", "label": "全剧总时长（秒，0 = 不指定）",
     "type": "int", "default": 0, "source": "settings", "group": "生产参数",
     "local": True,
     "why": "和「每集多少秒」一起填就能算出集数 —— **那时候集数按这个算，"
            "不按剧本里的章节数**。剧本写着 21 章但你要 60 分钟、每集 60 秒，"
            "那就是 60 集，环节1 会照 60 集去切。\n"
            "只填这一个：集数和每集时长由环节1 在这个总长里分配。"},
    {"key": "episode_count", "label": "总集数（0 = 不指定）",
     "type": "int", "default": 0, "source": "settings", "group": "生产参数",
     "local": True,
     "why": "**填了就是硬的**：环节1 必须切成这么多集，不看剧本里有几章。\n"
            "和总时长一起填 → 每集时长 = 总时长 ÷ 集数；"
            "和每集时长一起填 → 总时长 = 集数 × 每集时长。"},
    {"key": "pacing", "label": "剧情节奏速度", "type": "enum",
     "options": ["compact", "standard", "unhurried"],
     "zh": {"compact": "紧凑（只留主线因果，压掉描写）",
            "standard": "标准",
            "unhurried": "舒展（留呼吸和情绪停顿）"},
     "default": "standard", "source": "settings", "group": "生产参数",
     "local": True,
     "why": "**只在总时长没指定时起作用** —— 那时候环节1 要自己判断这部剧该多长，"
            "这一项告诉它往哪边靠。总时长填了就以总时长为准，节奏只影响"
            "同样长度里塞多少事件。"},
    {"key": "episode_seconds", "label": "每集固定多少秒（0 = 让环节1 按剧情定）",
     "type": "int", "default": 0, "source": "settings", "group": "生产参数",
     "local": True,
     "why": "**填了就是硬的**：程序在切集之后直接把每集的 duration_sec 覆盖成这个数，"
            "不经过模型。写在「特殊要求」里不管用 —— 那是给模型看的提示，"
            "而环节1 的原则是「按剧情事件定秒数」，它会按自己的判断给数。\n"
            "0 = 不指定，照旧由环节1 定（每集可以不一样长）。\n"
            "**改它要重跑环节1**，而且如果环节2 已经切过段，段数会跟着变 —— "
            "那时必须从环节2 重跑，不能只改这个数就去出片。\n"
            "每集想不一样长：留 0，然后手改 01_剧本与分段/episodes.json 里各集的 "
            "duration_sec。"},
    {"key": "project_id", "label": "项目编号", "type": "text",
     "source": "params", "maps_to": "project_code", "group": "生产参数"},

    # ================================================== 程序算的 / 已冻结（只读）
    {"key": "episode_duration", "label": "单集目标时长（秒）", "type": "int",
     "source": "derived", "group": "程序算的",
     "why": "第一环节看完全篇按剧情定的，存在集清单里。改它要重跑第一环节。"},
    {"key": "reference_capacity_per_call", "label": "模型单次参考图上限",
     "type": "int", "source": "derived", "group": "程序算的",
     "why": "从服务商注册表读当前选的模型，比手填准。"},
    {"key": "target_image_model", "label": "图片生成模型", "type": "text",
     "source": "derived", "group": "程序算的"},
    {"key": "target_video_model", "label": "AI 视频模型", "type": "text",
     "source": "derived", "group": "程序算的"},
    {"key": "production_scope", "label": "生产范围", "type": "text",
     "source": "derived", "group": "程序算的",
     "why": "在「分析这几集 / 只生产这几集」那两个框里定。"},
    {"key": "current_episode", "label": "当前集", "type": "text",
     "source": "derived", "group": "程序算的"},
    # 下面这几项 skill 要求冻结，我们在第 0 章 freeze_capability 里冻了。
    # 列出来是为了「都看得见」，改要去能力冻结那儿改，不在这里编辑。
    {"key": "native_multishot_support", "label": "视频模型一次能不能出多镜头", "type": "text",
     "source": "derived", "group": "已冻结"},
    {"key": "transition_execution_mode", "label": "转场谁来做", "type": "text",
     "source": "derived", "group": "已冻结"},
    {"key": "spatial_consistency_mode", "label": "空间用什么方式保证一致", "type": "text",
     "source": "derived", "group": "已冻结",
     "why": "本分支停在 text_only（纯文字坐标合同）。geo_proxy 明确不做 —— "
            "出图模型做不出可靠的几何代理，硬出一张会得到「看起来权威、"
            "实际不准」的参照，比没有更糟。"},
    {"key": "id_policy", "label": "编号规则", "type": "text",
     "source": "derived", "group": "已冻结"},
    # V6.0 新增、且 skill 只给了一个取值的几条 —— 列出来是为了「都看得见」，
    # 没有可选项所以不做成可编辑。
    {"key": "location_view_coverage_policy", "label": "机位按什么排",
     "type": "text", "source": "derived", "group": "已冻结"},
    {"key": "view_distinctness_policy", "label": "重复机位怎么判",
     "type": "text", "source": "derived", "group": "已冻结"},
    {"key": "story_first_prompt_order", "label": "提示词先写剧情还是先写技术要求",
     "type": "text", "source": "derived", "group": "已冻结",
     "why": "V6.0 要求 SCSTATE / 故事板 / 视频提示词一律先写"
            "「完整场景剧情 → 确切视觉时刻 → 前/现/后 → 主叙事对象 → "
            "六字段身份映射」，最后才写技术合同。"
            "**隐藏 ID 之后仍必须能理解原文确切时刻与因果。**"},
    {"key": "video_reference_policy", "label": "视频参考图怎么挑",
     "type": "text", "source": "derived", "group": "已冻结",
     "why": "**V6.2 改成两层。** 第一层是必须给的故事板时间骨架，"
            "第二层才是「证明得出独有作用」的补图。参考数量**不以模型上限为目标**；"
            "补图不许和故事板争夺时间、动作阶段、走位或镜头顺序。"},
    # ---- V6.2 新增的六条固定策略（第 19 章） ----
    {"key": "storyboard_video_reference_policy",
     "label": "视频必须带故事板吗", "type": "text",
     "source": "derived", "group": "已冻结",
     "why": "**V6.2 定死为 mandatory_temporal_spine。** 每个 SEG 的视频必须携带"
            "覆盖**完整关键时间推进**的故事板视觉载体 —— 不许退化成只有一张起始图、"
            "只有 SCSTATE、只有 LOOK 或只有文字提示词。"
            "缺了返回 VIDEO_STORYBOARD_SPINE_MISSING。"
            "**这一条取代了 V6.1 按可靠度分路的做法**：可靠度只决定承载颗粒度、"
            "补图数量和提示词冗余度，不决定给不给故事板。"},
    {"key": "storyboard_reference_admission_gate",
     "label": "故事板进视频前要逐项审核吗", "type": "text",
     "source": "derived", "group": "已冻结",
     "why": "故事板在 Canon 里存在**不等于**它的图片自动有资格当视频参考。"
            "进视频前逐项审：叙事准确、身份、当前 LOOK/CT、几何、World Position、"
            "支撑/通路/屏障、时间状态与未来状态禁运、道具实例/数量/持有人、"
            "动作阶段、机位观察、可读性。任一关键项错就返回 "
            "STORYBOARD_REFERENCE_ADMISSION_FAILED —— "
            "**禁止用正确的 LOOK / LOC_VIEW 或文字去压过一张错的故事板。**"},
    {"key": "effective_reference_selection_gate",
     "label": "补图要证明有独有作用吗", "type": "text",
     "source": "derived", "group": "已冻结",
     "why": "故事板骨架之外的每张补图必须同时满足五条：通过准入、"
            "解决故事板没覆盖的**独有** Authority 缺口、不控制镜头/走位/时间/动作阶段、"
            "有明确适用时间窗、对模型可读。任一不满足返回 "
            "VIDEO_REFERENCE_UNIQUE_UTILITY_UNPROVEN。"
            "「为了填满上限」「多一张更保险」都是明确禁止的动机。"},
    {"key": "video_prompt_detail_mode",
     "label": "视频提示词写到什么密度", "type": "text",
     "source": "derived", "group": "已冻结",
     "why": "**V6.2 定死为 director_level_expanded。** 视频提示词不许只有字段标题、"
            "镜头标签或一句动作摘要 —— 要把整段拆成逐时间窗执行卡，"
            "每窗写动作阶段、微表演、眼神呼吸、身体重心、接触与延迟反应、"
            "镜头景别运动焦点、切换动机、声音同步和窗口出口。"
            "**但细化不等于堆形容词**：只有长篇形容词、重复禁令或每窗都一样的描述，"
            "不算高密度，返回 VIDEO_PROMPT_EXECUTION_DETAIL_INSUFFICIENT。"},
    {"key": "micro_performance_contract",
     "label": "微表演要写成可拍的行为吗", "type": "text",
     "source": "derived", "group": "已冻结",
     "why": "「细腻」来自角色目标、压抑与泄露，不来自堆砌随机动作。"
            "要写可拍的行为（「先保持面对记者，眼睛先右移，停顿半秒后才转头」），"
            "不是只写「紧张」「悲伤」「电影感表演」。"
            "只有情绪词时返回 VIDEO_PERFORMANCE_CONTRACT_INSUFFICIENT。"},
    {"key": "action_phase_physical_response_contract",
     "label": "高风险动作要逐阶段展开吗", "type": "text",
     "source": "derived", "group": "已冻结",
     "why": "攻击、跌倒、搀扶、交接、拥抱、起身、开门、上下车这类动作按需要展开"
            "准备→启动→路径→接触→跟随→反应延迟→恢复/失衡→稳定结果。"
            "硬规则：受动者不得在接触前完整反应；道具不得在手闭合前换持有人；"
            "跌倒必须有失衡与落地过程；完成后不得重演。"
            "缺了返回 VIDEO_ACTION_PHASE_INCOMPLETE。"},
    {"key": "cinematic_camera_grammar_contract",
     "label": "镜头要写清叙事功能吗", "type": "text",
     "source": "derived", "group": "已冻结",
     "why": "每个镜头或切换要说明叙事功能、来源 KF、景别角度、机位、构图层次、"
            "运动起止、轴线与画面方向、焦点、显露范围、切换动机与机制、"
            "新镜头透露了什么信息。**镜头只投影 Canonical World**，"
            "不移动人物、门窗、家具、Zone 或道具；"
            "不许为了「更有张力」加没有功能的推拉摇移。"
            "只有景别和运动时返回 VIDEO_CAMERA_GRAMMAR_INSUFFICIENT。"},
    {"key": "scstate_spatial_slice_policy", "label": "一个场景太大时怎么拆",
     "type": "text", "source": "derived", "group": "已冻结",
     "why": "同一 CVS 横跨远距离、不同高度、Barrier 或不相容动作轴时，"
            "建立 Zone-Coherent Slice；其他 Zone 实体登记为 OFF-FRAME ACTIVE，"
            "**不得为了同框移动或融合**。"},
    # ---- V6.1 新增的三条固定策略 ----
    {"key": "generated_video_frame_reference_policy",
     "label": "能不能拿生成好的视频截图当参考", "type": "text",
     "source": "derived", "group": "已冻结",
     "why": "**V6.1 定死为 forbidden。** AI 视频生成帧只能作为 QC 证据，"
            "禁止注册为下一 SEG 的 Reference、Temporal Primary 或 Canonical 入口。"
            "违反时 skill 要求返回 GENERATED_FRAME_REFERENCE_FORBIDDEN。"},
    {"key": "reference_dimension_coverage_gate", "label": "删图前必须逐项确认还有出处",
     "type": "text", "source": "derived", "group": "已冻结",
     "why": "**图像减压不得降低一致性维度。** Identity、LOOK/CT、Spatial/Geometry、"
            "Position/Blocking、State/Temporal、Prop/Count/Holder 六维是不可降级"
            "底座，删图之前必须逐维确认仍有唯一来源 —— 连 HIGH 可靠度也不例外。"
            "缺维时返回 REFERENCE_DIMENSION_COVERAGE_GAP。"},
    {"key": "position_contract_policy", "label": "人物位置能不能随便改", "type": "text",
     "source": "derived", "group": "已冻结",
     "why": "V6.1：没有被批准的移动事件，World Position、Zone、Anchor、Support、"
            "Orientation 一律不可变 —— **删掉 SCSTATE 图片不等于删掉这些合同**。"},
    {"key": "reveal_coverage_policy", "label": "第一次露出的部位没定过怎么办",
     "type": "text", "source": "derived", "group": "已冻结",
     "why": "视频首次显露的身体或服饰区域必须已有视觉覆盖，"
            "否则限制 Camera 或阻断生产。"},
    {"key": "external_transition_editing", "label": "允不允许后期补转场",
     "type": "text", "source": "derived", "group": "已冻结"},
    {"key": "external_shot_assembly", "label": "允不允许后期拼镜头",
     "type": "text", "source": "derived", "group": "已冻结"},
    {"key": "asset_registry_path", "label": "资产台账存在哪", "type": "text",
     "source": "derived", "group": "已冻结"},
    {"key": "registry_snapshot_id", "label": "台账快照", "type": "text",
     "source": "derived", "group": "已冻结"},
]

# 这几项是程序恒定的，skill 要求冻结但我们没有别的选择 —— 直接给死值。
# 后四条是 V6.0 新增的策略，skill 里只给了一个取值，没有可选项。
FIXED_DERIVED = {
    "id_policy": "FQID_CANONICAL_REVISION_REQUIRED",
    "spatial_consistency_mode": "text_only",
    "transition_execution_mode": "MODEL_NATIVE_ONLY",
    "location_view_coverage_policy": "demand_driven_dynamic",
    "view_distinctness_policy": "unique_spatial_authority_required",
    "story_first_prompt_order": "required",
    # V6.1 从「最小充分」改成「权威完整」—— 一个词的改动，方向是反的：
    # 不是「能删就删」，是「六维都有来源的前提下才允许删」。
    # V6.2 又改一次，方向同样是反的：从「自适应地挑一套不冲突的」
    # 改成「**故事板骨架是必给的**，补图才是挑的」。
    "video_reference_policy":
        "mandatory_storyboard_plus_selective_effective_supplemental",
    # ---- V6.2 第 19 章新增 ----
    "storyboard_video_reference_policy": "mandatory_temporal_spine",
    "storyboard_reference_admission_gate": "required",
    "effective_reference_selection_gate": "required",
    "video_prompt_detail_mode": "director_level_expanded",
    "micro_performance_contract": "required",
    "action_phase_physical_response_contract": "risk_driven_required",
    "cinematic_camera_grammar_contract": "required",
    "generated_video_frame_reference_policy": "forbidden",
    "reference_dimension_coverage_gate": "required",
    "position_contract_policy": "immutable_without_authorized_movement",
    "scstate_spatial_slice_policy": "zone_coherent_when_required",
    "reveal_coverage_policy": "require_coverage_or_constrain_camera",
    "external_transition_editing": "FORBIDDEN",
    "external_shot_assembly": "FORBIDDEN",
}

BY_KEY = {f["key"]: f for f in FIELDS}

# 「每部剧真的要改的」——只有这些默认展开，其余折叠起来。
#
# 判据不是「重不重要」，是**换一部剧会不会改**：
#   · 视觉风格、文化设定、语言、字幕、旁白 —— 每部剧都不一样
#   · 特殊要求 —— 那个自由输入口，什么都能写
#   · 改编权限、拍成什么 —— 开工前定一次，也属于每部剧要过一遍的
#
# 折叠起来的不是「不重要」，是**大多数剧不用动**：
#   · 图像减压 / 视频承载 / 场景机位 —— 调优旋钮，跑顺了再碰
#   · 生产参数 —— 画幅、时长在「项目参数」那块本来就有一份
#   · 程序算的、已冻结 —— 只读，看看而已
#
# 全铺出来的后果实际发生过：用户说「过于复杂了」。
# 56 个字段一屏铺开，人找不到该改哪个，于是一个都不改。
BASIC_KEYS = (
    "visual_medium", "visual_style", "cultural_setting",
    "adaptation_authority",
    "dialogue_language",
    # 长度计划四件套。按上面那条判据（「换一部剧会不会改」）它们最该展开 ——
    # 总时长和节奏是**每部剧开工第一件要定的事**，而且它们决定集数：
    # 折在「生产参数」里的后果实际发生过 —— 用户选了「每集 60 秒」，
    # 而集数还是按剧本里的 21 章切的，因为他没找到总时长那一栏。
    "total_seconds", "episode_count", "episode_seconds", "pacing",
    "subtitle", "subtitle_lang",
    "narration", "narration_style", "narration_voice", "narration_on_screen",
    # 对白怎么呈现：解说剧和正常剧就差这一项，每部剧都要过一遍。
    # 加它的时候漏了这张表，于是它一直折着，而它正是解说剧唯一要改的开关。
    "dialogue_mode",
    "special_notes",
)


def tier_of(key: str) -> str:
    """basic = 默认展开；advanced = 收起来。"""
    return "basic" if key in BASIC_KEYS else "advanced"


def placeholder_of(key: str) -> str:
    """字段名 → 模板占位符。`subtitle_burn` → `SUBTITLE_BURN`。"""
    return key.upper()


PLACEHOLDERS = tuple(placeholder_of(f["key"]) for f in FIELDS)


# ---------------------------------------------------------------- 读写

# 取值改过名的字段：老项目里存的旧值 → 现在的值。
#
# V6.2 把「故事板出多少张」改成了「故事板怎么承载」——三个旧取值都表达
# 「可以少出几张」，而 V6.2 定死必须覆盖完整时间推进，只是载体可以选。
# 所以旧的两个「出得少」的档位一律翻成有序独立锚点（一张一格，最省容量
# 又能覆盖完整推进），「整套都出」翻成有序多张 Sheet。
_RENAMED_VALUES = {
    # 2026-08-22 「拍成什么形式」从枚举改成填空：老项目存的枚举 key
    # 翻成中文说法。**不翻的后果很静默**：字段已是自由文本，"3d" 会被
    # medium_rule 当成没见过的词丢给默认 —— 项目明明是 3D 漫剧，
    # 提示词里却写「视频类型：真人短剧」。
    "visual_medium": {
        "live_action": "真人短剧", "3d": "3D漫剧",
        "2d": "二维动画", "mixed": "混合形式",
    },
    "storyboard_materialization_policy": {
        "anchor_only": "ordered_kf_anchors",
        "selected_kf": "ordered_kf_anchors",
        "full_storyboard": "ordered_continuation_sheets",
    },
}


# 已经从页面上去掉、但**老项目里可能存过值**的字段。
#
# `load()` 只走 FIELDS，所以字段一删，存过的值就再也读不到了 ——
# 而那是用户亲手填的东西（比如「弹幕字号不要太小」），
# 悄悄丢掉正是这个项目里最要防的那一类。所以原样带出来。
#
#   on_screen_text  剧情文字改成一律允许之后不再需要声明，但填过的要求还算
#   subtitle_burn   本程序里「画面里有字幕」就是烧录，没有第二种做法
RETIRED_KEYS = ("on_screen_text", "subtitle_burn")


def load(pj: Project) -> dict:
    """这个项目已经填的设定。没填过的字段用默认值。

    老项目一个字段都没有 —— 全部走默认值，行为和加这套东西之前**完全一致**
    （比如 subtitle 默认 False，`_common` 第 10 条照旧原样渲染）。
    """
    saved = (pj.meta() or {}).get("settings") or {}
    out = {}
    for f in FIELDS:
        if f["source"] != "settings":
            continue
        v = saved.get(f["key"])
        out[f["key"]] = f.get("default", "") if v is None else v
        # 取值改过名的，把老项目里存的那个翻译过来。**不翻译的后果很静默**：
        # 模板里会渲染出 `当前策略：anchor_only` —— V6.2 不认这个词，
        # 模型只能猜，而页面上那个下拉显示的是空（选项里没有这一项）。
        ren = _RENAMED_VALUES.get(f["key"], {})
        if out[f["key"]] in ren:
            out[f["key"]] = ren[out[f["key"]]]
    for k in RETIRED_KEYS:
        if k in saved:
            out[k] = saved[k]
    return out


def save(pj: Project, values: dict) -> dict:
    """只写 source=settings 的字段。

    params 那几项**不在这里写** —— 它们的家在 meta["params"]，
    写两份就会出现「页面显示 9:16、实际按 16:9 跑」而且没人发现。
    """
    meta = dict(pj.meta() or {})
    cur = dict(meta.get("settings") or {})
    for k, v in (values or {}).items():
        f = BY_KEY.get(k)
        if f and f["source"] == "settings":
            cur[k] = v
    meta["settings"] = cur
    pj.save_meta(meta)
    return load(pj)


def visible(values: dict) -> list:
    """当前取值下真正生效的字段（`when` 没满足的不算）。"""
    return [f for f in FIELDS
            if not f.get("when") or bool(values.get(f["when"]))]


# ---------------------------------------------------------------- 给模板用

def mapping(pj: Project, params: Optional[dict] = None,
            derived: Optional[dict] = None) -> dict:
    """占位符 → 值。三种来源在这里汇成一份。

    `when` 没满足的字段渲染成空字符串，而不是漏掉这个键 ——
    漏掉的话模板里的 `{{SUBTITLE_LANG}}` 会原样出现在提示词里，
    模型会把它当成一个要填的空位或者一句奇怪的指令。
    """
    vals = load(pj)
    params = params or (pj.meta() or {}).get("params") or {}
    derived = derived or {}
    live = {f["key"] for f in visible(vals)}
    out = {}
    for f in FIELDS:
        k = f["key"]
        if f["source"] == "params":
            # skill 的名字和我们参数的名字不一样（aspect_ratio ↔ ratio）。
            # 按 maps_to 去取 —— 直接拿 skill 的名字查会全部取空，
            # 而取空不报错，只是模板里那个占位符变成空字符串。
            v = params.get(f.get("maps_to") or k, "")
        elif f["source"] == "derived":
            v = derived.get(k, FIXED_DERIVED.get(k, ""))
        else:
            v = vals.get(k, "")
        if k not in live:
            v = ""
        # 布尔和枚举要渲染成人话（True → 是；preserve → preserve（严格保持原文）），
        # 它们在模板里是中文句子的一部分。
        # **数字和自由文本保持原样** —— 转成字符串会让拿这个值算数的地方出错。
        if v is None or v == "":
            out[placeholder_of(k)] = ""
        elif f["type"] in ("bool", "enum"):
            out[placeholder_of(k)] = show(f, v)
        else:
            out[placeholder_of(k)] = v
    return out


def show(f: dict, v: Any) -> str:
    """一个值给人/给模型看时怎么写。

    枚举同时给 skill 的原值和中文：`optimize_pacing（允许优化节奏与镜头）`。
    只给中文的话，对着 skill 排查时每次都要翻译一遍；
    只给英文的话，页面上没人看得懂。
    """
    if v is True:
        return "是"
    if v is False:
        return "否"
    zh = (f.get("zh") or {}).get(v)
    return f"{v}（{zh}）" if zh else str(v)


def subtitle_rule(pj: Project) -> str:
    """「画面内能不能有文字」这条规则的正文 —— **按字幕设定生成**。

    这是这套东西存在的直接理由。原来 `_common.md` 第 10 条是一句
    无条件的散文：

        画面内禁止出现任何文字、字幕、水印、UI 面板

    用户把【基础信息】（里面写着「字幕：需要，烧录进画面」）加进全局提示词
    之后，两段话直接矛盾，**结果字幕消失了而且没有任何报错**。
    散文之间的矛盾检测不了；变成一个字段两种取值就不会矛盾。

    **剧情本身要求的文字一律允许，不再由用户逐部剧声明。**
    用户原话：「实际上画面上的字都是要有的，我需要控制的只是有没有字幕」。
    以前那句无条件禁令会把手机屏幕上的短信、店铺招牌、信件正文、报纸标题、
    弹幕一起禁掉 —— 而禁掉是**静默的**：图出来了、字没了、不报错。
    让人去枚举「这部剧允许哪几类文字」也不成立：漏一类就悄悄少一类。

    所以只剩一个开关：字幕。
    """
    v = load(pj)
    # 剧情里该有的字：**举例子，不是白名单** —— 写成白名单就又回到
    # 「漏一类少一类」，而这条规则的重点是「属于剧情的字都算」。
    diegetic = ("画面内允许出现**剧情本身要求的文字**"
                "（手机与电脑屏幕上的内容、店铺招牌、路牌、信件与文件正文、"
                "报纸标题、弹幕这一类；判据是它在故事里真的存在，"
                "剧中人看得见）。")
    # 老项目里可能填过「画面里本来就该有的文字」。那一栏已经去掉了，
    # 但**填过的话不许悄悄丢掉** —— 用户可能在里面写了别处没有的要求
    # （比如「弹幕字号不要太小」）。照抄进来。
    legacy = (v.get("on_screen_text") or "").strip()
    if legacy:
        diegetic += f"这部剧另外要求：{legacy}。"
    if v.get("subtitle"):
        lang = v.get("subtitle_lang") or "中文"
        return (diegetic
                + f"另外要有{lang}字幕，直接印进画面。"
                + "除此之外禁止出现水印、UI 面板，以及任何不属于剧情的叠加文字。")
    return (diegetic
            + "**不要字幕。** 除剧情文字之外，禁止出现字幕、水印、UI 面板，"
              "以及任何不属于剧情的叠加文字。")


# 这几项是**制作决策**，剧本里没有答案 —— 一部玄幻剧可以拍真人也可以拍 3D，
# 一部小说可以配旁白也可以全对白。所以即使它们还是默认值，
# **也不许模型「按剧本推翻」**。
#
# 起因是一句我自己写的话。brief 的结尾原来是：
#
#   带「默认，未指定」的 N 项是系统默认值，不是用户的决定 ——
#   如果剧本内容明显和它冲突（**比如这是一部动画而媒介写着真人写实**），
#   以剧本为准并在输出里说明。
#
# 那个例子字面就是「看到不像真人的剧本就把真人改成动画」。实跑照做了：
# 项目选的是 live_action，出来的资产提示词写着「高质量3D漫剧风格、
# 精致影视级角色建模」—— 而且不报错，因为程序没有任何一处检查媒介。
NOT_FROM_SCRIPT = (
    "visual_medium", "visual_style",
    "subtitle", "subtitle_lang",
    # 这两项已经从页面上去掉了（剧情文字一律允许、字幕就是烧录）。
    # 名单里留着：老项目存过值，而这一类**不许让模型按剧本推翻**。
    "subtitle_burn", "on_screen_text",
    "narration", "narration_style", "narration_voice", "narration_on_screen",
    # **这一项尤其不能让剧本推翻。** 解说剧的剧本里照旧写满带引号的对白
    # （「烟火尽头」121 段），模型看了只会得出「这部剧有对白」——
    # 而「让谁来念」是制作决策，剧本里没有答案。
    "dialogue_mode",
    "video_audio_mode",
)


# 「视频类型」怎么写 —— 用户自己的说法，页面和提示词共用这一套词。
MEDIUM_ZH = {"live_action": "真人短剧", "3d": "3D漫剧",
             "2d": "二维动画", "mixed": "混合形式"}


def medium_rule(pj: Project) -> str:
    """一句「视频类型：xxx」。**原样给出去，不替用户丰富提示词。**

    以前 `visual_medium` 只作为【项目基础信息】里的一行值出现，没有任何模板
    把它当约束 —— 实跑选了真人写实，出来的资产提示词写着「高质量3D漫剧风格、
    精致影视级角色建模」，而且不报错。所以它得像字幕、旁白那样每次调用都发。

    但**只发事实，不写禁令**。我一度在这里生成一大段
    「禁止出现 3D 渲染、CG 建模、动画、插画、卡通、Unreal/Blender…」——
    那是替用户做提示词工程，而他要的是把「视频类型：真人短剧」原原本本递过去。
    多写的每一句都是我们在猜他想要什么，猜错了他还得回来改我们的措辞。
    """
    v = load(pj)
    raw = str(v.get("visual_medium") or "").strip()
    # 2026-08-22 起是自由文本：用户写什么发什么（「视频类型：水墨定格动画」
    # 也是一句合法的媒介声明）。load() 已把老枚举 key 翻成中文，
    # MEDIUM_ZH.get 再兜一道 —— 防哪条路绕过翻译直接读到存量值。
    return f"视频类型：{MEDIUM_ZH.get(raw) or raw or '真人短剧'}"


def plan_lengths(vals: dict) -> dict:
    """总时长 / 集数 / 每集时长 —— 三个量互相决定，算出缺的那一个。

    返回 `{total, count, per, given, conflict}`（秒 / 集 / 秒）。0 = 没定。

    规则：
        填两个   → 算第三个
        填一个   → 另外两个由环节1 定（但填的那个是硬的）
        填三个   → 乘不通就报冲突，**不静默挑一个**
        都不填   → 全由环节1 定（老行为）

    三个都填而且乘不通时以「集数 × 每集」为准 —— 那两个直接决定怎么切，
    而总时长只是个结果。但必须说出来：悄悄改掉用户填的数字，
    比报错难查得多。
    """
    def _n(k):
        try:
            return max(0, int(float(vals.get(k) or 0)))
        except (TypeError, ValueError):
            return 0

    total, count, per = _n("total_seconds"), _n("episode_count"), _n("episode_seconds")
    given = [k for k, v in (("总时长", total), ("集数", count), ("每集", per)) if v]
    per_only = bool(per and not total and not count)
    conflict = ""
    if total and count and per:
        want = count * per
        # 容差给一集：整数除不尽时不该判成冲突
        if abs(want - total) > per:
            conflict = (f"你填的三个数乘不通：{count} 集 × {per} 秒 = {want} 秒，"
                        f"而总时长填的是 {total} 秒（差 {abs(want - total)} 秒）。"
                        f"**按「集数 × 每集」算了** —— 那两个直接决定怎么切，"
                        f"总时长只是结果。要按总时长走就把集数或每集时长清成 0。")
        total = want
    elif total and per:
        count = max(1, round(total / per))
    elif total and count:
        per = max(1, round(total / count))
    elif count and per:
        total = count * per
    return {"total": total, "count": count, "per": per,
            "given": given, "conflict": conflict, "per_only": per_only}


def length_plan(pj: Project) -> str:
    """发给环节1 的那段话：总时长/集数/每集时长谁说了算。

    合成一段而不是丢三个数过去 —— 模型要的是**关系**：
    「集数是算出来的，不是数剧本里有几章」这句话才是重点，
    而三个孤立的数字说不出这件事。
    """
    v = load(pj)
    p = plan_lengths(v)
    if not p["given"]:
        return (f"全剧总时长、集数、每集时长都没指定 —— 三个都由你按剧情事件定。"
                f"节奏速度：{_zh_of('pacing', v)}。")
    if p["per_only"]:
        return (f"每集 {p['per']} 秒（你指定）。\n"
                f"**集数由你算**：看完剧本后估这部剧总该多长"
                f"（按剧情事件密度估，不按剧本章节数，不按字数 —— "
                f"1700 字可能是 5 个紧凑事件，也可能是 12 个），"
                f"集数 = round(你估的总时长 ÷ {p['per']})，"
                f"然后切成那个集数。\n"
                f"**必须切成你算出来的那个集数，不多不少** —— "
                f"不是数剧本里有几章。"
                f"剧本自带的章节编号只是参考："
                f"章节比算出来的集数多就合并相邻章节，"
                f"少就在剧情转折处再切开"
                f"（新目标出现 / 新威胁 / 重要信息揭示 / 关系变化 / 完成一次反转）。\n"
                f"每集的 duration_sec 填 {p['per']}。"
                f"节奏速度：{_zh_of('pacing', v)}。")
    rows = []
    if p["total"]:
        rows.append(f"全剧总时长 {p['total']} 秒"
                    + ("（你指定）" if "总时长" in p["given"] else "（算出来的）"))
    if p["count"]:
        rows.append(f"集数 {p['count']} 集"
                    + ("（你指定）" if "集数" in p["given"] else "（算出来的）"))
    if p["per"]:
        rows.append(f"每集 {p['per']} 秒"
                    + ("（你指定）" if "每集" in p["given"] else "（算出来的）"))
    out = "；".join(rows) + "。"
    if p["count"]:
        out += (f"\n**必须切成 {p['count']} 集，不多不少** —— "
                f"这个数是按时长算出来的，**不是数剧本里有几章**。"
                f"剧本自带的章节编号只是参考，节奏由上面这几个数定。"
                f"章节比 {p['count']} 多就合并，少就在剧情转折处再切开。")
    if not p["total"] or not p["per"]:
        out += f"\n没定的那几项由你按剧情定；节奏速度：{_zh_of('pacing', v)}。"
    if p["conflict"]:
        out += "\n⚠ " + p["conflict"]
    return out


def narration_rule(pj: Project) -> str:
    """旁白那几行 —— **原样给出去，不替用户丰富提示词。**

    按取值生成（而不是丢几个占位符进模板）只为一件事：没旁白的项目不该
    看到「本项目：否　声音属于：　画面处理：」这种半截句子 ——
    模型读到空标签会自己去填。除此之外一个字都不多写。

    我原来在这儿写的是散文：「声线要和这个角色本人一致，不要换人」
    「那是画外音，不是他在说话」—— 那是替用户做提示词工程。
    他要的是把「旁白：有（第一人称内心独白）」原原本本递过去。
    """
    v = load(pj)
    if not v.get("narration"):
        rows = ["旁白 / 画外音：无"]
    else:
        rows = [f"旁白 / 画外音：有（{_zh_of('narration_style', v)}）"]
        who = (v.get("narration_voice") or "").strip()
        if who:
            rows.append(f"旁白声音：{who}")
        rows.append(f"念旁白时人物：{_zh_of('narration_on_screen', v)}")
    # 对白那一条**和旁白开关无关**，所以放在 if 外面。解说剧可能整部剧
    # 一句「旁白」都不标，全靠解说把对白念出来 —— 那时旁白开关是关的，
    # 而「人物不开口」照样必须说。放进 if 里就是漏掉这种最常见的解说剧。
    rows.append(f"对白呈现：{_zh_of('dialogue_mode', v)}")
    return "；".join(rows)


def _zh_of(key: str, vals: dict) -> str:
    """枚举值的中文说法。取字段表里的 zh，没有就用原值。"""
    f = next((x for x in FIELDS if x["key"] == key), None)
    raw = vals.get(key) or (f or {}).get("default") or ""
    return ((f or {}).get("zh") or {}).get(raw, str(raw))


def brief_block(pj: Project, params: Optional[dict] = None,
                derived: Optional[dict] = None) -> str:
    """整块项目基础信息，渲染成给模型看的文字。

    **默认值也列出来，但标明是默认。** 这是 skill 第 0 章的要求：
    「只在缺失值会实质改变结果且无法安全推断时询问；其他情况采用
    **明确标注的默认值**」。

    两个极端都不行：
      · 全不列 —— 模型不知道该按什么媒介、什么权限做，自己猜
      · 列了不标 —— 你做 3D 漫剧而没填，系统提示词里自信地写着
        「视觉媒介：真人写实」，读起来像是你的决定

    生产参数那几项**不在这里重复**：它们已经在 `{{PARAMS}}` 里发过一遍，
    两处写法万一不一致，模型信哪个都不对。
    """
    vals = load(pj)
    said = (pj.meta() or {}).get("settings") or {}
    live = {f["key"] for f in visible(vals)}
    rows, ndef = [], 0
    for f in FIELDS:
        k = f["key"]
        if f["source"] != "settings" or k not in live:
            continue
        v = vals.get(k)
        if v is False and k not in said:
            continue                    # 没填的布尔项：默认规则本来就是否
        if not str("" if v is None else v).strip() and not isinstance(v, bool):
            continue                    # 没默认值又没填的自由文本，不占篇幅
        # 制作决策不标「默认」—— 标了就等于告诉模型「这一项可以按剧本推翻」，
        # 而这类东西剧本里根本没有答案（见 NOT_FROM_SCRIPT）。
        mark = "" if (k in said or k in NOT_FROM_SCRIPT) else "　←（默认，未指定）"
        if k not in said and k not in NOT_FROM_SCRIPT:
            ndef += 1
        rows.append(f"- {f['label']}：{show(f, v)}{mark}")
    if not rows:
        return "（本项目还没填基础信息，按上面的通用默认执行。）"
    tail = ("\n\n画面比例、单段时长、出图尺寸、参考图上限一律以"
            "【项目参数】为准，这里不重复声明。")
    if ndef:
        # **这里不许再举「媒介」当例子。** 原话是「比如这是一部动画而媒介
        # 写着真人写实」—— 那字面就是一句「看到不像真人的剧本就改成动画」的
        # 指令，而实跑照做了：项目选真人写实，出来的提示词是 3D 漫剧建模。
        # 举例只举**剧本里真的有答案**的那类。
        tail += (f"\n带「默认，未指定」的 {ndef} 项是系统默认值，**不是用户的决定** —— "
                 f"如果剧本内容明显和它冲突（比如剧本本身就是外语对白，"
                 f"而对白语言写着中文），以剧本为准并在输出里说明。\n"
                 f"**没标「默认」的那几项一律照做，不许按剧本推翻** —— "
                 f"拍成什么形式、视觉风格、字幕、旁白、声音怎么来这些是"
                 f"**制作决策**，剧本里没有答案。")
    return ("以下是**本项目**的设定，优先级高于上面的通用默认：\n\n"
            + "\n".join(rows) + tail)


def schema_block() -> str:
    """给抽取模型看的字段说明 —— **从 FIELDS 生成，不手写**。

    手写一份的话，字段表一改这里就落后，而落后的表现是「新字段永远抽不出来」
    且不报错 —— 用户填了、模型没认出来、值悄悄丢了。
    """
    rows = []
    for f in FIELDS:
        if f["source"] == "derived":
            continue                    # 只读的抽了也没处放
        bits = [f"- `{f['key']}`（{f['label']}）"]
        if f["type"] == "enum":
            opts = "、".join(
                f"`{o}`" + (f"={f['zh'][o]}" if (f.get("zh") or {}).get(o) else "")
                for o in f["options"])
            bits.append(f"  取值只能是：{opts}")
        elif f["type"] == "bool":
            bits.append("  填 true / false")
        elif f["type"] == "int":
            bits.append("  填整数")
        if f["source"] == "params":
            bits.append("  ⚠ 这一项会**改变生产参数**，拿不准就留空")
        if f.get("when"):
            bits.append(f"  只在 `{f['when']}` 为真时才有意义")
        if f.get("why"):
            bits.append("  " + f["why"].replace("\n", " "))
        rows.append("\n".join(bits))
    return "\n".join(rows)


def extract_vars(pj: Project, raw: str, rules: str) -> dict:
    """抽取那一次调用的占位符。"""
    return {"SETTINGS_SCHEMA": schema_block(),
            "CURRENT_RULES": rules,
            "RAW_TEXT": raw}


def sanitize(values: dict) -> tuple:
    """把模型给的值过一遍。返回 (可用的, 被丢掉的原因)。

    **不信任模型的输出。** 枚举值填错、只读字段被填、布尔给成字符串
    都可能发生，而这些值会直接改变生产结果 —— 静默接受的话，
    页面上显示的和实际跑的就不是一回事。
    """
    ok, dropped = {}, []
    for k, v in (values or {}).items():
        f = BY_KEY.get(k)
        if not f:
            dropped.append(f"{k}：不是已知字段")
            continue
        if f["source"] == "derived":
            dropped.append(f"{k}：这一项是程序算的，不接受外部赋值")
            continue
        if v is None or str(v).strip() == "":
            continue                    # 没抽出来，不是错
        if f["type"] == "enum" and v not in f["options"]:
            dropped.append(f"{k}：`{v}` 不在允许的取值里（{'、'.join(f['options'])}）")
            continue
        if f["type"] == "bool":
            v = str(v).strip().lower() in ("true", "1", "yes", "是", "需要")
        elif f["type"] == "int":
            try:
                v = int(str(v).strip())
            except ValueError:
                dropped.append(f"{k}：`{v}` 不是整数")
                continue
        ok[k] = v
    return ok, dropped


# `_common` 是**每一次调用都发**的系统提示词，而 {{PROJECT_BRIEF}} 会把
# 所有填过的 settings 字段列进去。所以那些字段是**全环节生效**的，
# 哪怕没有任何一份业务模板单独写 {{DIALOGUE_LANGUAGE}}。
#
# 这一条一开始漏了，页面上把「对白语言」「音频模式」这类标成
# 「暂未被任何模板使用」—— **说反了**：它们一直在起作用，
# 只是走的是 brief 这条路。标错比不标更糟，人会以为填了没用。
BRIEF_PLACEHOLDER = "PROJECT_BRIEF"


def used_by() -> dict:
    """哪个设定影响哪几个环节 —— **扫模板得出，不手写表**。

    两条路都算：
      · 某份模板单独写了 {{X}}      → 只影响那几个环节
      · 走 {{PROJECT_BRIEF}}        → 进系统提示词，**全部环节**都看得到

    手写的对照表和实际模板迟早对不上，然后它就成了误导。
    """
    import os
    import re

    from .stages import PROMPT_DIR      # 打包后指向解压目录，别自己拼路径
    here = PROMPT_DIR
    want = {placeholder_of(f["key"]) for f in FIELDS}
    out: dict = {p: [] for p in want}
    brief_in = []
    for fn in sorted(os.listdir(here)):
        if not fn.endswith(".md"):
            continue
        with open(os.path.join(here, fn), encoding="utf-8") as fh:
            text = fh.read()
        found = set(re.findall(r"\{\{([A-Z_]+)\}\}", text))
        if BRIEF_PLACEHOLDER in found:
            brief_in.append(fn[:-3])
        for v in found:
            if v in want:
                out[v].append(fn[:-3])
    # settings 类字段一律进 brief；params/derived 不进（它们在 {{PARAMS}} 里
    # 或者是只读展示），所以不能无差别地都加上。
    for f in FIELDS:
        if f["source"] != "settings":
            continue
        p = placeholder_of(f["key"])
        for name in brief_in:
            if name not in out[p]:
                out[p].append(f"{name}（全环节）")
    return out


def unused() -> list:
    """定义了但一份模板都没用的设定 —— 填了也没人看，页面上要标出来。"""
    return sorted(k for k, v in used_by().items() if not v)
