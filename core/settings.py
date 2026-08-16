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
    {"key": "source_type", "label": "原始文本类型", "type": "enum",
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
    {"key": "visual_medium", "label": "视觉媒介", "type": "enum",
     "options": ["live_action", "3d", "2d", "mixed"],
     "zh": {"live_action": "真人写实", "3d": "3D", "2d": "2D", "mixed": "混合"},
     "default": "live_action", "source": "settings", "group": "项目"},
    {"key": "visual_style", "label": "视觉风格", "type": "text",
     "hint": "电影写实 / 都市情感 / 末日废土 / 古装写实…",
     "source": "settings", "group": "项目"},
    {"key": "cultural_setting", "label": "文化与地域设定", "type": "text",
     "source": "settings", "group": "项目",
     "why": "skill 明写：**不要把提示词语言等同于文化设定**。"
            "地域、服饰、医院、建筑、货币、称谓只服从这一项和故事真相。"},
    {"key": "dialogue_language", "label": "对白语言", "type": "text",
     "default": "中文", "source": "settings", "group": "语言"},
    {"key": "instruction_language", "label": "Prompt 语言", "type": "text",
     "default": "中文", "source": "settings", "group": "语言",
     "why": "只管提示词用什么语言写，**不决定剧里的地域和文化**（见上一条）。"},
    {"key": "video_audio_mode", "label": "音频模式", "type": "enum",
     "options": ["native_audio", "silent_video", "separate_audio"],
     "zh": {"native_audio": "视频模型原生音频", "silent_video": "无声视频",
            "separate_audio": "后期单独制作"},
     "default": "native_audio", "source": "settings", "group": "语言"},
    {"key": "native_audio_transition_support", "label": "原生音频转场支持",
     "type": "enum", "options": ["yes", "no", "unknown"],
     "zh": {"yes": "支持", "no": "不支持", "unknown": "未知"},
     "default": "unknown", "source": "settings", "group": "语言"},
    {"key": "costume_asset_mode", "label": "服装资产方式", "type": "enum",
     "options": ["auto", "separate_cost", "direct_look"],
     "zh": {"auto": "自动判断", "separate_cost": "服装单独建资产",
            "direct_look": "直接做完整造型"},
     "default": "auto", "source": "settings", "group": "资产",
     "why": "skill 第 5 章：简单服装走 LOGICAL_ONLY 文字契约、不出图；"
            "关键或复用的才建 Canonical 服装资产。选 direct_look 资产条数最少。"},
    {"key": "existing_canon", "label": "已有 Canonical 资产", "type": "text",
     "source": "settings", "group": "资产",
     "why": "**目前只作为说明发给模型，还不能真正继承编号** —— "
            "第四环节缺 EXISTING_CANONICAL 那一档，补上之前别指望它复用。"},
    {"key": "output_depth", "label": "输出深度", "type": "enum",
     "options": ["production_ready", "plan", "analysis"],
     "zh": {"production_ready": "可直接生产", "plan": "只到方案",
            "analysis": "只做分析"},
     "default": "production_ready", "source": "settings", "group": "项目"},

    # ---- 字幕：**skill 的参数表里没有这一项**，是本地扩展 ----
    # 加它是因为踩过坑：用户把「字幕：需要，烧录进画面」写进全局提示词，
    # 和 `_common` 第 10 条「画面内禁止出现任何文字、字幕」直接矛盾，
    # 结果字幕消失且不报错。有了这个字段，第 10 条按取值生成。
    {"key": "subtitle", "label": "需要字幕", "type": "bool",
     "default": False, "source": "settings", "group": "字幕", "local": True},
    {"key": "subtitle_lang", "label": "字幕语言", "type": "text",
     "default": "中文", "when": "subtitle", "source": "settings",
     "group": "字幕", "local": True},
    {"key": "subtitle_burn", "label": "烧录进画面", "type": "bool",
     "default": False, "when": "subtitle", "source": "settings",
     "group": "字幕", "local": True,
     "why": "勾上之后程序会自动改写「画面内禁止出现文字」那条规则，"
            "不用你手动去改提示词 —— 手改两处散文必然打架。"},
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
    {"key": "narration_voice", "label": "旁白是谁的声音", "type": "text",
     "when": "narration", "source": "settings", "group": "旁白", "local": True,
     "hint": "写角色名或编号，如「女主 C001」—— 旁白的声线要和她本人一致",
     "why": "不指定的话，同一部剧里旁白的声线会在不同段落之间飘。"},
    {"key": "narration_on_screen", "label": "旁白时画面里的人要不要动嘴",
     "type": "enum", "options": ["no_lip_sync", "lip_sync"],
     "zh": {"no_lip_sync": "不动嘴（画外音）", "lip_sync": "对口型（当台词说）"},
     "default": "no_lip_sync", "when": "narration",
     "source": "settings", "group": "旁白", "local": True,
     "why": "**这一条是旁白最容易出错的地方。** 默认不动嘴 —— "
            "内心独白配上对口型，看起来就是角色在自言自语。"},

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
    {"key": "scstate_materialization_policy", "label": "场景状态图物化策略",
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
    {"key": "storyboard_materialization_policy", "label": "故事板物化策略",
     "type": "enum", "options": ["anchor_only", "selected_kf", "full_storyboard"],
     "zh": {"anchor_only": "只出入口/结果/高风险锚点",
            "selected_kf": "出挑选过的关键帧",
            "full_storyboard": "整套故事板都出"},
     "default": "anchor_only", "source": "settings", "group": "图像减压",
     "why": "V6.0 第 15 章：**禁止默认让图片模型一次生成三格**。"
            "每个 KF 都有文字 Canon，只有通过门控的才逐张生成独立 Anchor。"},
    {"key": "storyboard_max_kf_per_sheet", "label": "每张故事板最多几格",
     "type": "int", "default": 3, "source": "settings", "group": "图像减压",
     "why": "**V6.0 定的 3，V6.1 保留。** 我们模板里原来写的「3×3=9」是自编的，V5.6 没给过数字上限。实跑撞过 16 格 —— 模型记不住那么多"
            "场次的世界状态，所有格子的 source_scstate 全填成第一个。"
            "更多关键时刻用有序 Continuation Sheet，不是塞进一张。"},
    {"key": "image_complexity_budget", "label": "单图复杂度预算", "type": "enum",
     "options": ["conservative", "standard", "expanded"],
     "zh": {"conservative": "保守（人少、手部少、构图简单）",
            "standard": "标准", "expanded": "放宽"},
     "default": "conservative", "source": "settings", "group": "图像减压",
     "why": "超预算时 V6.0 要求 SPLIT_ANCHOR / 局部重绘 / LOGICAL_ONLY / "
            "DEFER_TO_VIDEO，**不许靠缩小人物或多塞 Panel 硬塞**。"},

    {"key": "video_execution_reliability", "label": "视频模型执行可靠度",
     "type": "enum", "options": ["high", "medium", "low", "unknown"],
     "zh": {"high": "高（Start 或 Start/End 就够）",
            "medium": "中（2–4 张时间锚点）",
            "low": "低（必要时上完整故事板）", "unknown": "未知"},
     "default": "unknown", "source": "settings", "group": "视频承载",
     "why": "V6.0 第 16 章按它选 Adaptive Execution Set。"
            "**未知不许冒充 HIGH** —— 冒充的代价是参考图不够，视频自己编。"},
    {"key": "video_reliability_evidence", "label": "可靠度依据", "type": "enum",
     "options": ["user_verified", "project_pilot_verified",
                 "model_profile_only", "unverified"],
     "zh": {"user_verified": "你实测过", "project_pilot_verified": "本项目试跑验证过",
            "model_profile_only": "只是模型档案上写的", "unverified": "没验证"},
     "default": "unverified", "source": "settings", "group": "视频承载",
     "why": "上面那一档是凭什么定的。没验证过就别按 HIGH 走。"},
    {"key": "image_composite_reliability", "label": "图片合成可靠度",
     "type": "enum", "options": ["high", "medium", "low", "unknown"],
     "zh": {"high": "高", "medium": "中", "low": "低", "unknown": "未知"},
     "default": "unknown", "source": "settings", "group": "视频承载",
     "why": "多人同框、手部、道具交接这类合成做不稳时，"
            "该把活交给视频而不是硬出图。"},
    # V6.1 把 V6.0 的 `approved_video_boundary_reuse`（SEGBOUND，允许把上一段
    # 视频过了 QC 的尾帧当下一段入口）**整条废掉**，换成这一项。
    # 废掉的原因实跑撞到了：尾帧当参考 + 故事板被减压掉之后，整段没有任何
    # 图片持有 Camera/Blocking/Time 权威，模型只能照着一张动作中间帧自己编。
    {"key": "canonical_boundary_policy", "label": "相邻段落边界方式",
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

    {"key": "location_view_production_mode", "label": "场景机位生产方式",
     "type": "enum",
     "options": ["auto", "compatible_view_batch", "single_view_only"],
     "zh": {"auto": "自动判断", "compatible_view_batch": "兼容机位合并出图",
            "single_view_only": "一次只出一个机位"},
     "default": "auto", "source": "settings", "group": "场景机位"},
    {"key": "view_batch_output_mode", "label": "合并出图的产物形式",
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
    {"key": "redundancy_overlap_heuristic", "label": "机位重复判定阈值",
     "type": "text", "default": "0.80", "source": "settings", "group": "场景机位",
     "why": "重叠高于它又没有独有 Zone/关系/消费者的机位会被判成重复图。"},
    {"key": "derived_view_min_resolution", "label": "裁切后最低分辨率",
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
    {"key": "native_multishot_support", "label": "多镜头能力档", "type": "text",
     "source": "derived", "group": "已冻结"},
    {"key": "transition_execution_mode", "label": "转场执行模式", "type": "text",
     "source": "derived", "group": "已冻结"},
    {"key": "spatial_consistency_mode", "label": "空间一致性档", "type": "text",
     "source": "derived", "group": "已冻结",
     "why": "本分支停在 text_only（纯文字坐标合同）。geo_proxy 明确不做 —— "
            "出图模型做不出可靠的几何代理，硬出一张会得到「看起来权威、"
            "实际不准」的参照，比没有更糟。"},
    {"key": "id_policy", "label": "ID 策略", "type": "text",
     "source": "derived", "group": "已冻结"},
    # V6.0 新增、且 skill 只给了一个取值的几条 —— 列出来是为了「都看得见」，
    # 没有可选项所以不做成可编辑。
    {"key": "location_view_coverage_policy", "label": "机位覆盖策略",
     "type": "text", "source": "derived", "group": "已冻结"},
    {"key": "view_distinctness_policy", "label": "机位去重策略",
     "type": "text", "source": "derived", "group": "已冻结"},
    {"key": "story_first_prompt_order", "label": "Story-First 提示词顺序",
     "type": "text", "source": "derived", "group": "已冻结",
     "why": "V6.0 要求 SCSTATE / 故事板 / 视频提示词一律先写"
            "「完整场景剧情 → 确切视觉时刻 → 前/现/后 → 主叙事对象 → "
            "六字段身份映射」，最后才写技术合同。"
            "**隐藏 ID 之后仍必须能理解原文确切时刻与因果。**"},
    {"key": "video_reference_policy", "label": "视频参考集策略",
     "type": "text", "source": "derived", "group": "已冻结",
     "why": "参考数量**不以模型上限为目标**；同一时间窗口只能有一个 "
            "Temporal Primary。"},
    {"key": "scstate_spatial_slice_policy", "label": "场景状态分片策略",
     "type": "text", "source": "derived", "group": "已冻结",
     "why": "同一 CVS 横跨远距离、不同高度、Barrier 或不相容动作轴时，"
            "建立 Zone-Coherent Slice；其他 Zone 实体登记为 OFF-FRAME ACTIVE，"
            "**不得为了同框移动或融合**。"},
    # ---- V6.1 新增的三条固定策略 ----
    {"key": "generated_video_frame_reference_policy",
     "label": "生成的视频帧可否当参考", "type": "text",
     "source": "derived", "group": "已冻结",
     "why": "**V6.1 定死为 forbidden。** AI 视频生成帧只能作为 QC 证据，"
            "禁止注册为下一 SEG 的 Reference、Temporal Primary 或 Canonical 入口。"
            "违反时 skill 要求返回 GENERATED_FRAME_REFERENCE_FORBIDDEN。"},
    {"key": "reference_dimension_coverage_gate", "label": "六维覆盖门控",
     "type": "text", "source": "derived", "group": "已冻结",
     "why": "**图像减压不得降低一致性维度。** Identity、LOOK/CT、Spatial/Geometry、"
            "Position/Blocking、State/Temporal、Prop/Count/Holder 六维是不可降级"
            "底座，删图之前必须逐维确认仍有唯一来源 —— 连 HIGH 可靠度也不例外。"
            "缺维时返回 REFERENCE_DIMENSION_COVERAGE_GAP。"},
    {"key": "position_contract_policy", "label": "位置合同策略", "type": "text",
     "source": "derived", "group": "已冻结",
     "why": "V6.1：没有被批准的移动事件，World Position、Zone、Anchor、Support、"
            "Orientation 一律不可变 —— **删掉 SCSTATE 图片不等于删掉这些合同**。"},
    {"key": "reveal_coverage_policy", "label": "首次显露覆盖策略",
     "type": "text", "source": "derived", "group": "已冻结",
     "why": "视频首次显露的身体或服饰区域必须已有视觉覆盖，"
            "否则限制 Camera 或阻断生产。"},
    {"key": "external_transition_editing", "label": "外部补转场",
     "type": "text", "source": "derived", "group": "已冻结"},
    {"key": "external_shot_assembly", "label": "外部镜头拼接",
     "type": "text", "source": "derived", "group": "已冻结"},
    {"key": "asset_registry_path", "label": "资产注册表位置", "type": "text",
     "source": "derived", "group": "已冻结"},
    {"key": "registry_snapshot_id", "label": "注册表快照", "type": "text",
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
    "video_reference_policy": "adaptive_authority_complete_nonconflicting_set",
    "generated_video_frame_reference_policy": "forbidden",
    "reference_dimension_coverage_gate": "required",
    "position_contract_policy": "immutable_without_authorized_movement",
    "scstate_spatial_slice_policy": "zone_coherent_when_required",
    "reveal_coverage_policy": "require_coverage_or_constrain_camera",
    "external_transition_editing": "FORBIDDEN",
    "external_shot_assembly": "FORBIDDEN",
}

BY_KEY = {f["key"]: f for f in FIELDS}


def placeholder_of(key: str) -> str:
    """字段名 → 模板占位符。`subtitle_burn` → `SUBTITLE_BURN`。"""
    return key.upper()


PLACEHOLDERS = tuple(placeholder_of(f["key"]) for f in FIELDS)


# ---------------------------------------------------------------- 读写

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
    """
    v = load(pj)
    if not v.get("subtitle") or not v.get("subtitle_burn"):
        return ("画面内禁止出现任何文字、字幕、水印、UI 面板"
                "（系统界面等文字元素走后期合成）。")
    lang = v.get("subtitle_lang") or "中文"
    return (f"**本项目字幕烧录进画面**：{lang}字幕，放在画面底部安全区内，"
            f"不遮挡人物面部。\n"
            f"除字幕外，画面内仍然禁止出现其他文字、水印、UI 面板。")


def narration_rule(pj: Project) -> str:
    """旁白那一段的正文 —— 和字幕规则一样，**按取值生成**。

    不生成而是丢几个占位符进模板的话，没旁白的项目会看到
    「本项目：否　声音属于：　画面处理：」这种半截句子 ——
    模型读到空标签会自己去填，那比不给更糟。
    """
    v = load(pj)
    if not v.get("narration"):
        return ("**本项目没有旁白。** 一句画外音都不要加 —— "
                "剧本里的第一人称叙述按台词或表演处理，不要凭空造一个旁白声线。")
    style = {"first_person_inner": "第一人称内心独白",
             "third_person": "第三人称旁白",
             "mixed": "内心独白与第三人称混合"}.get(
                 v.get("narration_style"), "第一人称内心独白")
    who = (v.get("narration_voice") or "").strip()
    lip = v.get("narration_on_screen") == "lip_sync"
    return (f"**本项目有旁白**：{style}。\n"
            + (f"旁白是 **{who}** 的声音 —— 声线要和这个角色本人一致，"
               f"不要换人。\n" if who else
               "（没指定是谁的声音 —— 同一部剧里保持同一个声线，别在段落之间飘。）\n")
            + (f"旁白时画面里的人**要对口型**（按台词处理）。"
               if lip else
               f"旁白时画面里的人**不动嘴** —— 那是画外音，不是他在说话。"))


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
        mark = "" if k in said else "　←（默认，未指定）"
        if k not in said:
            ndef += 1
        rows.append(f"- {f['label']}：{show(f, v)}{mark}")
    if not rows:
        return "（本项目还没填基础信息，按上面的通用默认执行。）"
    tail = ("\n\n画面比例、单段时长、出图尺寸、参考图上限一律以"
            "【项目参数】为准，这里不重复声明。")
    if ndef:
        tail += (f"\n带「默认，未指定」的 {ndef} 项是系统默认值，**不是用户的决定** —— "
                 f"如果剧本内容明显和它冲突（比如这是一部动画而媒介写着真人写实），"
                 f"以剧本为准并在输出里说明。")
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
