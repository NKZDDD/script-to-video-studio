# -*- coding: utf-8 -*-
"""V3.4 电影级体系的环节图。

这个文件只放**数据**：有哪些环节、谁依赖谁、每个环节必须产出什么字段。
不放执行逻辑 —— 执行在 stages.py，出图出片在 produce.py。

把环节图单独拿出来是有意的：换体系时要改的就是这一张表加一批模板，
而不是去 2000 行的 stages.py 里翻。对照 `环节映射表.md` 看。

命名用 `n` 前缀（new），和 main 上 V6.1 的 `s1..s12` 区分开 ——
两套 ID 混在同一个项目目录里时，产物文件名不会撞。
"""

from __future__ import annotations

# 环节定义：驱动前端流程图与执行按钮。
#   kind: llm=调模型  image/video=调服务商  program=纯代码  local=本机处理
#   scope: series=全剧一次  episode=逐集  segment=逐段
#   out:  产物文件名（stage_data 的键）；不落 JSON 产物的填空
STAGES = [
    # ---- 第 0 章：不调模型，项目创建时冻结 --------------------------------
    {"id": "n0", "no": 0, "name": "项目初始化与能力冻结", "kind": "program",
     "scope": "series", "out": "",
     "note": "目标视频模型、单段秒数、画幅、转场执行模式、多镜头能力档位"},

    # ---- 全剧一次：故事层 --------------------------------------------------
    {"id": "n1", "no": 1, "name": "源解析与故事真相", "kind": "llm",
     "scope": "series", "out": "n1_truth"},
    {"id": "n2", "no": 2, "name": "人物与世界规则", "kind": "llm",
     "scope": "series", "out": "n2_rules"},

    # ---- 全剧一次：叙事与视觉基座 ------------------------------------------
    # 这一段原来是逐集的，V5.6 对照下来是错的。三条硬依据：
    #   · 「维护**唯一** Continuity Ledger」（SKILL.md 第 8 节）
    #   · 「LONG_TERM：**跨 Episode** 或长期存在」（references/03）
    #   · 「按 Project → Episode → Scene → Beat 解析」（SKILL.md 第 3 节）
    #
    # 逐集做的实际后果：EP05 的账本对 EP01 一无所知 —— 主角在第 1 集留下的
    # 永久伤疤，到第 5 集这本账里根本不存在。模板里写着「LONG_TERM 跨集持续」，
    # 而接线交付不了。
    #
    # 还有一层：跨集连续性和**逐集并发**天然冲突。要求 EP05 看得到 EP01 的账，
    # 但四集是并排跑的。把全剧级的东西全部前移，逐集层就不再有共享状态，
    # 并发才是安全的 —— 这也是资产提示词重复编写那个坑的根源。
    {"id": "n3", "no": 3, "name": "叙事结构（场次与节拍）", "kind": "llm",
     "scope": "series", "out": "n3_narrative"},
    {"id": "n4", "no": 4, "name": "资产系统", "kind": "llm",
     "scope": "series", "out": "n4_assets"},
    # n4 只产出资产表（外观、锚点、依赖），不产出能直接投喂出图模型的正文。
    # 分成两个环节：资产表要全剧一次定完（同一个角色只出一张图），
    # 提示词正文另算 —— 两件事的输出量差一个量级，合在一起一次答不完。
    {"id": "n4b", "no": 4, "name": "资产生产提示词编译", "kind": "llm",
     "scope": "series", "out": "n4b_asset_prompts"},
    {"id": "n5", "no": 5, "name": "空间主表", "kind": "llm",
     "scope": "series", "out": "n5_spatial"},
    {"id": "n6", "no": 6, "name": "连续性总账", "kind": "llm",
     "scope": "series", "out": "n6_ledger"},

    # ---- 逐集：导演与镜头层 ------------------------------------------------
    # 从这里开始才逐集。Scene/Beat 归哪一集是上面 n3 定的，
    # 这一层只做「把本集的场次变成走位、状态、镜头」，集与集之间不共享状态。
    {"id": "n7", "no": 7, "name": "导演设计", "kind": "llm",
     "scope": "episode", "out": "n7_directing"},
    {"id": "n8", "no": 8, "name": "标准视觉状态与视觉过渡", "kind": "llm",
     "scope": "episode", "out": "n8_cvs"},
    {"id": "n9", "no": 9, "name": "摄影镜头与剪辑时间", "kind": "llm",
     "scope": "episode", "out": "n9_shots"},
    {"id": "n10", "no": 10, "name": "SEG 包装", "kind": "llm",
     "scope": "episode", "out": "n10_segs"},

    # ---- 逐段：编译层 ------------------------------------------------------
    {"id": "n11", "no": 11, "name": "场景状态图编译（SCSTATE）", "kind": "llm",
     "scope": "segment", "out": "n11_scstate"},
    {"id": "n12", "no": 12, "name": "故事板包编译", "kind": "llm",
     "scope": "segment", "out": "n12_storyboard"},
    {"id": "n13", "no": 13, "name": "视频执行计划与提示词", "kind": "llm",
     "scope": "segment", "out": "n13_video"},

    # ---- 生产执行：skill 不算章，但程序必须有 -----------------------------
    {"id": "p1", "no": 5, "name": "资产图生产", "kind": "image",
     "scope": "series", "out": "", "task_key": "asset_tasks",
     "note": "资产库全剧共享，等所有集的 n4 齐了再出，按依赖分层"},
    {"id": "p2", "no": 11, "name": "场景状态图生产", "kind": "image",
     "scope": "episode", "out": "", "task_key": "scstate_tasks",
     "note": "V6.1 没有这一层。故事板改成主要参考它，减少多张原子资产互相打架"},
    {"id": "p3", "no": 12, "name": "故事板生产", "kind": "image",
     "scope": "episode", "out": "", "task_key": "storyboard_tasks"},
    {"id": "p4", "no": 13, "name": "视频生产", "kind": "video",
     "scope": "episode", "out": "", "task_key": "video_tasks"},

    # ---- 收尾 --------------------------------------------------------------
    {"id": "n14", "no": 14, "name": "漏洞审计", "kind": "llm",
     "scope": "episode", "out": "n14_audit"},
    {"id": "d1", "no": 15, "name": "人工复核清单", "kind": "local",
     "scope": "episode", "out": "d1_review"},
    {"id": "d2", "no": 16, "name": "排序拼接与交付", "kind": "local",
     "scope": "episode", "out": ""},
]

# stage_id → (模板名, 依赖的已存产物, 必需输出字段)
#
# 必需字段是**校验**用的：模型没给就重试并把缺的字段名反馈回去。
# 只列「缺了下游一定跑不动」的，不是把 schema 抄一遍 ——
# 列太多会让模型为了凑字段而胡编，也会让老产物重跑直接失败。
LLM_SPEC = {
    "n1": ("n1_truth", [], [
        "entities[]", "entities[].entity_id", "entities[].aliases",
        "events[]", "events[].event_id", "events[].story_time",
        "story_truth", "reality_threads[]?", "episode_ranges[]",
    ]),
    "n2": ("n2_rules", ["n1_truth"], [
        "characters[]", "characters[].character_id", "characters[].long_term_motive",
        "characters[].relationships", "characters[].arc",
        "characters[].physical_limits", "characters[].performance_boundary",
        "world_rules[]",
        # cultural_rules 是**对象**不是数组（模板 schema 里就是一个 dict）。
        # 写成 cultural_rules[] 的话 check_keys 要求「非空 list」，
        # 模型照着 schema 答出一个 dict 就永远过不了 —— 重试两次然后失败，
        # 报错还说「输出缺少必需字段」，让人以为是模型不听话。
        "cultural_rules",
    ]),
    "n3": ("n3_narrative", ["n1_truth", "n2_rules"], [
        # scenes[].episode 是**必需**的：叙事结构改成全剧一次做之后，
        # 逐集环节靠它挑出「本集该做哪几场」。缺了的话 n7 拿到全剧的场次，
        # 会把别的集的戏排进这一集 —— 而且不报错。
        "scenes[]", "scenes[].scene_id", "scenes[].episode",
        "scenes[].objective", "scenes[].turn",
        "scenes[].outcome", "scenes[].entry_state", "scenes[].exit_state",
        "beats[]", "beats[].beat_id", "beats[].meaningful_change",
        "beats[].change_kind", "beats[].state_delta", "beats[].shot_need",
    ]),
    "n4": ("n4_assets", ["n1_truth", "n2_rules", "n3_narrative"], [
        "assets[]", "assets[].asset_id", "assets[].family", "assets[].name",
        "assets[].decision", "assets[].decision_reason",
        "assets[].parent_asset_id", "assets[].reference_assets",
        "assets[].identity_anchors", "assets[].appearance",
        "assets[].output_spec", "assets[].dependency_order",
        "costume_contracts[]?", "prop_specs[]?", "prop_instances[]?",
        # 是数组。不标 [] 只要求「键存在」，模型给个空数组也算过 ——
        # 而空的生产顺序等于没有依赖分层，出图会乱序。
        "production_order[]",
    ]),
    "n4b": ("n4b_asset_prompts", ["n1_truth", "n4_assets"], [
        "asset_prompts[]", "asset_prompts[].asset_id",
        "asset_prompts[].reference_assets", "asset_prompts[].reference_role_map",
        "asset_prompts[].output_spec", "asset_prompts[].filename",
        "asset_prompts[].prompt",
    ]),
    "n5": ("n5_spatial", ["n3_narrative", "n4_assets"], [
        "spatial_masters[]", "spatial_masters[].spatial_id",
        "spatial_masters[].world_origin", "spatial_masters[].axis",
        "spatial_masters[].unit", "spatial_masters[].zones",
        "spatial_masters[].anchors", "spatial_masters[].routes",
        "spatial_masters[].landmarks", "spatial_masters[].fixed_structures",
        "loc_views[]?",
    ]),
    "n6": ("n6_ledger", ["n3_narrative", "n4_assets", "n5_spatial"], [
        "ledger[]", "ledger[].event_id", "ledger[].affected_entity",
        "ledger[].state_dimension", "ledger[].result_value",
        "ledger[].activation_event", "ledger[].persistence_class",
    ]),
    "n7": ("n7_directing", ["n3_narrative", "n2_rules", "n5_spatial", "n6_ledger"], [
        "scene_directing[]", "beat_directing[]",
        # 字段名要和模板 schema 里的**一模一样**：模板写的是 zone_id。
        # 写成 zone 的话模型答对了也过不了。
        "blocking[]", "blocking[].character_id", "blocking[].zone_id",
        "blocking[].anchor", "blocking[].root_xyz", "blocking[].body_orientation_yaw",
        "performance_intent[]",
    ]),
    "n8": ("n8_cvs", ["n4_assets", "n5_spatial", "n6_ledger", "n7_directing"], [
        "cvs[]", "cvs[].cvs_id", "cvs[].story_time", "cvs[].location_id",
        "cvs[].characters", "cvs[].props", "cvs[].relational_blocking",
        "cvs[].forbidden_state",
        "vt[]?", "vt[].vt_id", "vt[].source_cvs", "vt[].target_cvs",
        "vt[].trigger_event", "vt[].irreversible_result",
    ]),
    "n9": ("n9_shots", ["n7_directing", "n8_cvs"], [
        "shots[]", "shots[].shot_id", "shots[].source_cvs", "shots[].shot_size",
        "shots[].camera_position_xyz", "shots[].screen_direction",
        "shots[].estimated_duration", "shots[].dramatic_function",
        "transitions[]?", "transitions[].transition_id", "transitions[].from_shot",
        "transitions[].to_shot", "transitions[].mechanism",
        "transitions[].cinematic_grammar", "transitions[].execution_mode",
        "timing_plan[]",
    ]),
    "n10": ("n10_segs", ["n9_shots"], [
        "segs[]", "segs[].seg_id", "segs[].duration", "segs[].included_shots",
        "segs[].entry_cvs", "segs[].exit_cvs",
        "segs[].model_native_transition_ids", "segs[].boundary_rationale",
    ]),
    "n11": ("n11_scstate", ["n4_assets", "n5_spatial", "n8_cvs", "n10_segs"], [
        "scstates[]", "scstates[].scstate_id", "scstates[].source_cvs",
        "scstates[].reference_assets", "scstates[].prompt!",
    ]),
    # V6.2：提示词和参考图顺序从**包一级下移到 sheet 一级** ——
    # 一个 SEG 要 1..N 张有序 Sheet（或有序独立 KF 锚点）才能覆盖完整时间推进。
    # 只校验包一级的话，模型写了 sheets[] 但每张都没有 storyboard_prompt 也算过 ——
    # 那时装配层一张出图任务都建不出来，而这**不报错，只是没有故事板**。
    "n12": ("n12_storyboard", ["n9_shots", "n10_segs", "n11_scstate"], [
        "sbpkg[]", "sbpkg[].sbpkg_id", "sbpkg[].kf",
        "sbpkg[].sheets[]", "sbpkg[].sheets[].sheet_id",
        "sbpkg[].sheets[].reference_order",
        "sbpkg[].sheets[].storyboard_prompt!",
    ]),
    # video_prompt 后面那个 `!` 是**值不许为空**，不只是键要在。
    #
    # 实遇：模型返回 `"video_prompt": ""`，同时在 capability_note 里写
    # `VIDEO_PROMPT_RELEASE_BLOCKED`、在 time_budget_check 里写
    # 「因此不生成视频执行计划和可投喂提示词」—— **它明确拒绝生产**，
    # 理由也写清楚了（上游镜头时间轴超出 SEG 容器）。
    # 而「键存在」是满足的，于是校验通过、空提示词落盘、这一段算做完了。
    # 下游拿着空提示词去出片，而**前面一路没有报错**。
    "n13": ("n13_video", ["n9_shots", "n10_segs", "n12_storyboard"], [
        "video_plan[]", "video_plan[].seg_id", "video_plan[].windows",
        "video_plan[].reference_order", "video_plan[].video_prompt!",
    ]),
    "n14": ("n14_audit", ["n8_cvs", "n10_segs", "n12_storyboard", "n13_video"], [
        "findings[]?",
    ]),
}

# 这两个集合**从环节表推导**，不再手写。
# 手写的话它和 STAGES 是两处真相，改范围时必然漏一个 ——
# 漏掉的后果是产物存到错的目录，下游取到空字典然后自己编，全程不报错。
SEGMENT_STAGES = {s["id"] for s in STAGES
                  if s["kind"] == "llm" and s["scope"] == "segment"}
SERIES_STAGES = {s["id"] for s in STAGES
                 if s["kind"] == "llm" and s["scope"] == "series"}

# 出图出片各步消费 tasks.json 里的哪个键，以及排在谁后面。
PRODUCE_ORDER = ["p1", "p2", "p3", "p4"]


# 每个环节的模板里都能用的占位符，不用声明依赖。
# REF_LIMIT：这次生产用的模型一次能吃几张参考图。
# 服务商注册表里一直有 max_refs（灵感鸭 sora-2 只收 1 张、坤鸡 9 张），
# 但模型侧从来不知道 —— LLM 按剧情需要引 6 张，到出图那步才撞上限。
# V5.6 还特别强调：这个数是**容量上限，不是推荐装满的数量**。
#
# EPISODE_DURATION / SEGMENTS_TARGET / SEGMENTS_WHY：**这一集**多长、
# 该装成几个 SEG。和 DURATION 必须分开 —— DURATION 是一个容器的容量
# （视频模型一次最多生成多久），不是这一集多长。以前只有 DURATION，
# 后果实跑撞过一整轮：第九环节是整集级的，它只看得见 15，就把 8 个场次
# 压成 15 秒，然后第十环节只装出 1 个 SEG，往下全线崩且不报错。
# 项目基础信息的 50 多个字段每个都是一个占位符（{{VISUAL_STYLE}} 这种）。
# **从 settings 那张表推导，不在这里手抄一份** —— 手抄的迟早和字段表对不上，
# 然后「模板用了填不上的占位符」这道校验就成了摆设。
def _setting_placeholders() -> tuple:
    from . import settings as _st
    return _st.PLACEHOLDERS


COMMON_PLACEHOLDERS = ("PARAMS", "EPISODE", "SEGMENT", "DURATION", "SCRIPT",
                       "IMAGE_SIZE", "SEG_COUNT", "CAPABILITY", "REF_LIMIT",
                       "EPISODE_DURATION", "SEGMENTS_TARGET", "SEGMENTS_WHY",
                       "NARRATION_RULE", "MEDIUM_RULE",
                       # 总时长 / 集数 / 每集时长三个量互相决定，合成的那一段。
                       # 只有环节1 用得上，但白名单是全局的。
                       "LENGTH_PLAN",
                       # 分批跑的两个：这一批做什么范围、前面几批做过什么。
                       # 没分批时也有值（「一次处理全剧」/「这是第一次排」），
                       # 所以任何模板用它们都不会渲染成空。
                       "BATCH_SCOPE", "DONE_SCENES",
                       ) + _setting_placeholders()


# 每个环节实际需要上游产物的**哪几部分**。
#
# 为什么要有这张表：mapping() 原来是 jd(obj) 整块塞，一个字节不筛。
# 实测环节1 吐出 2.8 万字，环节2 的提示词 3.1 万字里 92% 就是它 ——
# 而环节2 连剧本都不吃，只吃这一份。后果有两条，都是真金白银：
#
#   1. 大输入 + 大输出同时发生，把网关的上限试出来了。环节1 能过
#      （小输入大输出），环节2 就断在中途，三次重试三次断。
#   2. n1_truth 有 4 个下游、其中 3 个是逐集的。一集重复发 3 遍同一份东西，
#      40 集就是 340 万字纯重复。
#
# 裁剪原则：**宁可少裁**。只裁明确用不上的部分。
#   keep  只留这几个顶层键（用于「这个环节只要一小块」）
#   drop  去掉这几处（支持 `a[].b` 这种嵌套路径）
# 表里没有的组合一律整块发送 —— 加错一条的代价是模型少了输入却不报错，
# 所以默认必须是「不裁」。
PRODUCT_NEEDS = {
    # 环节4下只要视觉基调（它的模板标的就是【视觉基调】），
    # 不需要实体表、事件链、切集边界。这是最大的一块浪费。
    ("n4b", "n1_truth"): {"keep": [
        "project_name", "story_type", "cultural_setting", "dialogue_language",
        "worldview", "era", "main_conflict", "open_design"]},

    # 逐条的依据（裁剪是「悄悄少发」，半年后要能查为什么）：
    #   episode_ranges       切集用的行号边界，切完就没人需要了
    #   open_design          skill 说它是「未描述的鞋款、墙面材质、非关键手机型号」
    #                        这类纯视觉决定，**不改变故事**（references/01:271）——
    #                        写人物动机和世界规则用不上
    #   events[].state_deltas 逐事件的外观增量，是第六环节账本要的；
    #                        人物弧光靠 action/result 就够
    ("n2", "n1_truth"): {"drop": ["episode_ranges", "open_design",
                                  "events[].state_deltas"]},
    # n3 **必须**留着 episode_ranges：它改成全剧级之后要给每一场标集号
    # （scenes[].episode 是必需字段），裁掉集清单它就不知道有哪几集了。
    # 这是重定级时留下的洞，实跑撞过：模型只吐 373 字就交白卷，
    # 报「输出缺少必需字段 scenes[]」—— 一边加了「必须标集号」、
    # 一边把集清单裁掉，而且完全不报错。
    # tests/test_trim_vs_template.py 现在会守着这一条。
    ("n3", "n1_truth"): {"drop": ["events[].state_deltas", "open_design"]},
    # skill 第 3 章「叙事结构」只讲 Scene/Beat 的定义，
    # 服饰/建筑/货币/称谓一个字没提 —— 那些归资产层（SKILL.md:82、89）。
    #   cultural_rules   服饰/建筑/货币/称谓/礼节，资产层要的
    #   forbidden_global 画面禁令，不影响场次怎么分
    #   separation_note  环节2 写给人看的自述（「我把哪些状态挪走了」），
    #                    不是给下游的数据
    # 不裁的话 RULES 在 n3 的提示词里独占 39%（实测 27,789 字）。
    ("n3", "n2_rules"): {"drop": ["cultural_rules", "forbidden_global",
                                  "separation_note"]},
    # 资产系统要看状态变化（连续性状态资产就是从这儿来的），所以留着 state_deltas
    ("n4", "n1_truth"): {"drop": ["episode_ranges"]},

    # 空间主表这两份一直是整块发的 —— 实遇一次输入 72008 字（约 36004 token），
    # 三次全挂在传输层（流式卡住 / 中途切断 / 900 秒一个字没回）。
    #
    # 保留的都说得出用途：模板原话是「**先从场次、走位、镜头/关键帧需求和显露
    # 包络提取真实空间需求**」（n5_spatial.md:110）——
    #   scenes[].location_hint  哪一场在哪儿，跨场次路线靠它
    #   scenes[].entry/exit_state  进出场时人在哪，走位
    #   beats[].tactic_or_action  zone 要「支持什么剧情行为」，靠它
    #   beats[].shot_need         模板明说要镜头需求
    #   assets[].appearance       地点长什么样
    #   prop_instances[].initial_zone  道具初始在哪个区，和分区对得上
    #
    # 裁掉的都是别的层要的：
    ("n5", "n3_narrative"): {"drop": [
        # 人物弧光 —— 空间不关心谁成长了
        "episode_arcs",
        # 戏剧张力的收尾，没有空间信息
        "scenes[].unresolved_tension",
        # 逐拍的表演与外观增量：state_delta 是第六环节账本要的
        # （n2/n3 那两条注释已经这么定过），performance_result / change_kind /
        # meaningful_change 是「这一拍演成什么样」，不改变房间的几何
        "beats[].state_delta", "beats[].meaningful_change",
        "beats[].change_kind", "beats[].performance_result",
    ]},
    ("n5", "n4_assets"): {"drop": [
        # 服饰的结构、材质、领口、鞋履 —— 纯人物外观，和房间无关
        "costume_contracts",
        # 道具外观与可读文字策略是出图那一层的；**实例留着**
        # （它带 initial_zone / initial_holder，和分区对得上）
        "prop_specs", "prop_sets",
        # 出图那一层的字段：能改什么、不能改什么、出图规格、覆盖范围、身份锚点
        "assets[].allowed_change", "assets[].forbidden_change",
        "assets[].output_spec", "assets[].coverage",
        "assets[].identity_anchors",
        # 生产顺序和自述 —— 给人看的，不是给空间用的
        "production_order", "blueprint_note",
    ]},
}


def needs_of(stage_id: str, out_name: str) -> dict:
    """这个环节要上游产物的哪几部分。没登记就是整块要。"""
    return PRODUCT_NEEDS.get((stage_id, out_name)) or {}


def placeholder_of(out_name: str) -> str:
    """产物名 → 模板里引用它的占位符。`n4_assets` → `ASSETS`。

    定成可推导的而不是手写一张对照表：手写的表迟早和依赖表对不上，
    对不上的后果是模板里的 `{{ASSETS}}` 永远填不上、原样发给模型 ——
    模型看到一个大括号占位符，通常会假装那里有内容继续往下编。
    """
    return out_name.split("_", 1)[1].upper() if "_" in out_name else out_name.upper()


def placeholders_for(stage_id: str) -> set:
    """这个环节的模板里**允许**出现哪些占位符。"""
    if stage_id not in LLM_SPEC:
        return set(COMMON_PLACEHOLDERS)
    _, deps, _ = LLM_SPEC[stage_id]
    return set(COMMON_PLACEHOLDERS) | {placeholder_of(d) for d in deps}


def by_id() -> dict:
    return {s["id"]: s for s in STAGES}


def llm_stages() -> list:
    return [s for s in STAGES if s["kind"] == "llm"]


def scope_of(stage_id: str) -> str:
    return by_id().get(stage_id, {}).get("scope", "episode")


def check_graph() -> list:
    """环节图自检。返回问题清单（空 = 没问题）。

    这张表是手写的，写错了不会立刻炸 —— 会在跑到第 12 个环节时才发现依赖指向
    一个不存在的产物。所以在测试里把它整个走一遍。
    """
    problems = []
    ids = by_id()
    outs = {s["out"]: s["id"] for s in STAGES if s["out"]}

    for sid, (tpl, deps, req) in LLM_SPEC.items():
        if sid not in ids:
            problems.append(f"{sid} 在 LLM_SPEC 里但不在 STAGES 里")
            continue
        if ids[sid]["kind"] != "llm":
            problems.append(f"{sid} 在 LLM_SPEC 里，但 kind 是 {ids[sid]['kind']}")
        if ids[sid]["out"] != tpl:
            problems.append(f"{sid} 的 out={ids[sid]['out']} 和模板名 {tpl} 不一致 —— "
                            f"产物文件名和模板名保持同名，找起来才不用记两套")
        if not req:
            problems.append(f"{sid} 没有必需输出字段，模型答歪了没人拦")
        for d in deps:
            if d not in outs:
                problems.append(f"{sid} 依赖 {d}，但没有任何环节产出它")

    for s in llm_stages():
        if s["id"] not in LLM_SPEC:
            problems.append(f"{s['id']} 是 llm 环节但没有 LLM_SPEC")

    # 依赖必须指向**更早**的环节，否则永远等不到
    order = {s["id"]: i for i, s in enumerate(STAGES)}
    for sid, (_, deps, _) in LLM_SPEC.items():
        for d in deps:
            src = outs[d]
            if order.get(src, 0) >= order.get(sid, 0):
                problems.append(f"{sid} 依赖 {src} 的产物，但 {src} 排在它同位或之后")

    # 范围只有一条硬规矩：**全剧级不能依赖逐集/逐段的产物**。
    # 全剧级只跑一次、排在最前面，那时候连有几集都还没切出来。
    #
    # 反过来是允许的，别写成对称规则（第一版就写错了）：
    #   逐集依赖逐段  合法 —— 聚合读本集全部段，比如 n14 审计要看所有段的故事板
    #   逐段依赖逐集  合法 —— 读更宽的上下文
    # 「跑的时候产物在不在」由上面的顺序检查负责，不归范围管。
    rank = {"series": 0, "episode": 1, "segment": 2}
    for sid, (_, deps, _) in LLM_SPEC.items():
        if scope_of(sid) != "series":
            continue
        for d in deps:
            if rank[scope_of(outs[d])] > 0:
                problems.append(
                    f"{sid} 是全剧级却依赖 {outs[d]}（{scope_of(outs[d])}）—— "
                    f"它只跑一次且排在最前面，那时候这份产物还不存在")

    for s in STAGES:
        if s["scope"] not in rank:
            problems.append(f"{s['id']} 的 scope={s['scope']} 不认识")
        if s["kind"] in ("image", "video") and not s.get("task_key"):
            problems.append(f"{s['id']} 是生产环节却没写 task_key，装配时不知道读哪一批")

    seen = set()
    for s in STAGES:
        if s["id"] in seen:
            problems.append(f"环节 id 重复：{s['id']}")
        seen.add(s["id"])
    return problems
