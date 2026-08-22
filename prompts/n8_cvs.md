# 第八环节｜标准视觉状态与视觉过渡

> **本次只处理 {{EPISODE}} 这一集。**
> {{BATCH_SCOPE}}

把「某一刻整个世界物理上是什么样」冻结下来（CVS），
再把「从这一刻怎么变成下一刻」写清（VT）。

## 一、CVS 里绝对不许有镜头

这是本环节唯一的硬边界，也是最容易被写回去的一条。

**禁止出现的字段和说法**：

```
景别 / 机位 / 镜头角度 / 构图 / 镜头运动 / 画面左右 / 前景后景
```

为什么：CVS 是**物理真相**。混进镜头概念之后，切一次镜头就会
静默改变「人物实际站在哪」—— 这正是人物位置漂移的根源。

对照着写：

```
✅ 物理方向（属于 CVS）：从病床向房门移动
❌ 画面方向（属于镜头）：从画面左侧移到右侧
```

画面左右是第九环节拿机位去投影这套坐标算出来的，不是反过来。

## 二、CVS 写什么

每个 CVS 是**一个稳定时刻**的完整世界快照：

**人物**逐个写：当前生效的视觉资产（CT 优先于 LOOK 优先于 PH）、
所在区域和锚点、根部坐标、姿态、支撑点、身体朝向、视线目标、
手上占用、身体状况、**当前仍然生效的持续状态清单**。

最后那一项要**展开写，不要只给一个 CT 编号**：

```
当前视觉根：C001_LK02_CT03
仍然生效：
  - 左额伤口
  - 左脸轻微血迹
  - 右颈医用贴片
  - 右袖口破损
  - 裙摆泥污
尚未激活：
  - 后续手术绷带（禁止出现）
```

**道具**逐个写实例编号、外观规格、存在状态、当前状态、持有人、哪只手、
归属、容器、区域锚点坐标、朝向、可见性档位。

**关系走位**写：人物之间的距离、朝向、高度关系、接触、视线、移动耦合。

## 三、可见性五档

每条持续状态、每个道具都标一档：

```
明确可见 / 部分可见 / 被遮挡但仍存在 / 画外但仍存在 / 尚未激活（禁止出现）
```

**「逻辑完整」不要求一张画面把所有状态都展示出来。**
不要为了展示左手的伤而破坏走位 —— 标「被遮挡但仍存在」，
后面重新看得见时恢复同一个状态。

## 四、什么时候该建一个新 CVS

**默认门槛**：相邻两个 CVS 至少在下面四类里有**两类**明显变化：

1. 道具状态
2. 持有人/归属
3. 人物走位/空间关系
4. 持续性的表演或情绪结果

**不算数的差异**：文字位置差几毫米、签名签到一半、微表情、
手指轻微移动、纸张微移、看不见的纹理。

### 单一变化的例外

「至少两类」会漏掉关键的单一变化。同时满足下面四条时，
允许只有一类变化也建 CVS，并在 `single_delta_override` 里写明理由：

1. 这个变化是关键结果
2. 视觉上清晰可辨，或直接控制后续执行
3. 会持续下去、改变风险或叙事理解、成为后续因果的前提
4. 删掉它会让后续丢失必须继承的状态

建之前问一句：**删掉这个 CVS，后面会不会失去必须继承的世界状态？**
不会 → 不建。

## 五、视觉过渡（VT）

VT 连接**两个稳定的 CVS**：

```
CVS_A（稳定）── VT ──→ CVS_B（稳定）
```

写清：起点、触发事件、动作因果、物理过程、同步发生的状态变化、
首次接触点、完成条件、终点、**不可逆的结果**。

中间过程（正在拔针、纸撕到一半、脚刚离地、拳头击中的瞬间）
**通常不建 CVS** —— 它们由故事板的动作阶段和视频执行负责。

但**关键结果必须进终点 CVS**。墙从完好到炸开，中间的爆炸半程可以不画，
但终点必须定义墙的损坏结果，不能让视频自由决定。

## 六、位置也是状态：没有移动事件就不许换位

**人物的 Zone、Anchor、支撑关系、姿态、朝向，和伤口、服装一样是持续状态。**
下一个 CVS 里没有合法移动事件，这五项就**逐项照抄上一个 CVS**。

画面里暂时看不见某个人，**不许**把他的 Anchor 改到「更方便拍摄」的位置。

### 位置要变，必须写成一条完整的移动

```
坐在桌后
→ 起身完成（解除支撑）
→ 沿桌后通道走
→ 穿过批准的桌端空隙
→ 进入观众区
→ 到位并建立站姿
```

禁止的写法：

```
坐在桌后 → 下一个 CVS 直接在前排过道争吵
```

所以位置一变，对应那条 VT 必须填全：`authorized_movers`（谁被批准移动）、
`release_support_action`（怎么起来的）、`route_id`（走哪条路）、
`barrier_or_portal_crossing`（穿哪个口）、`completion_condition`（什么算到位）。
**`authorized_movers` 里没有这个人，就不许改他的位置。**

### 三条最容易犯的

- **不许穿过实心物**。会议桌、墙、病床护栏、座椅排都是空间拓扑，不是背景装饰。
  跨越只能走批准的门、桌端空隙，或者剧情明确写了的破坏事件。
- **坐/躺/跪不是普通姿态**，它们绑着一个支撑实体。
  没有 `release_support_action` 就不许把人画成站在别处 ——
  **镜头拉远也取消不了座椅关系。**
- **情绪升级、对白变化、把照片摔出去、换机位，都不自动授权换位。**
  为了让三个人同框、为了露全身、为了做对峙构图**把人挪到房间中央** ——
  这是模型最爱走的捷径，也是这一节要拦的东西。
  剧情没给移动时间就保留原 Anchor，或者回去改导演的走位设计。

## 输出 schema

```json
{
  "episode": "{{EPISODE}}",
  "cvs": [
    {"cvs_id": "CVS_{{EPISODE}}_SC01_01",
     "story_time": "", "reality_thread": "RT_MAIN",
     "scene_id": "SC01", "beat_id": "SC01-B1",
     "location_id": "S001", "spatial_id": "SP001",
     "current_environment_state": "光线、天气、环境音这类整体状态",
     "characters": [
       {"character_id": "C001",
        "active_visual_asset_id": "当前生效的 CT/LOOK/PH，取最高一层",
        "world_position_state": {
          "zone": "A", "anchor_id": "BED_01",
          "root_or_pivot_xyz_m": [2.4, 1.2, 0],
          "orientation_yaw_deg": 90,
          "posture_class": "STANDING|SEATED|KNEELING|LYING|MOVING",
          "support_binding_id": "坐/躺/跪/被扶时必填，指向支撑实体；站着填空",
          "support_relation": "SEATED_ON|LYING_ON|LEANING_ON|STANDING_ON|HELD_BY|NONE",
          "contact_points": "双脚着地 / 右手扶床沿",
          "current_barrier_side": "在哪个障碍物的哪一侧，比如 DESK_01 的后侧",
          "movement_state": "STABLE|TRANSITIONING",
          "last_authorized_movement_event_id": "上一次合法移动的事件号，没动过就空"
        },
        "gaze_target": "",
        "hand_occupancy": {"left": "空", "right": "PI001"},
        "physical_condition": "",
        "persistent_states": [
          {"what": "左额伤口", "visibility": "明确可见|部分可见|被遮挡|画外|尚未激活"}
        ]}
     ],
     "props": [
       {"instance_id": "PI001", "spec_id": "PS001",
        "existence_status": "存在|已损毁|已消耗",
        "current_state": "完整|破损|签字后…",
        "holder": "C001", "hand": "右手", "owner": "C005", "container": "",
        "zone_id": "A", "anchor": "", "pivot_xyz": [2.5, 1.3, 0.9],
        "orientation": "", "physical_relation": "被握在手中",
        "visibility": "明确可见"}
     ],
     "prop_count_lock": [
       {"spec_id": "PS001", "active_total": 2,
        "reconciliation": "可见1 + 部分0 + 遮挡1 + 画外0 = 2"}
     ],
     "relational_blocking": [
       {"from": "C001", "to": "C005", "distance_m": 1.5,
        "facing": "面对面", "height_relation": "同高",
        "contact": "无", "eye_line": "互看", "movement_coupling": "无"}
     ],
     "active_spatial_constraints": ["通路 R1 被碎玻璃挡住"],
     "forbidden_state": ["这一刻绝对不许出现的未来状态"],
     "entry_condition": "从哪个 CVS 或事件进入",
     "exit_condition": "什么发生时离开这个状态",
     "delta_from_previous": ["相对上一个 CVS 变了哪几类"],
     "single_delta_override": "只有一类变化时写明为什么仍然要建；否则空"}
  ],
  "vt": [
    {"vt_id": "VT_{{EPISODE}}_01",
     "source_cvs": "CVS_{{EPISODE}}_SC01_01",
     "target_cvs": "CVS_{{EPISODE}}_SC01_02",
     "trigger_event": "EV012",
     "action_causality": "谁做了什么导致什么",
     "physical_process": "物理上怎么发生的",
     "synchronized_state_deltas": ["同时改变的几件事"],
     "first_contact": "第一次接触发生在哪个点",
     "completion_condition": "什么算完成",
     "authorized_movers": ["这条过渡里**允许改变位置**的人物ID；没人移动就空数组"],
     "release_support_action": "从坐/躺/跪起来时必填：怎么解除支撑、什么算起完",
     "route_id": "跨 Zone 时必填：走第五环节登记过的哪条通路",
     "barrier_or_portal_crossing": "跨 Zone 时必填：穿的是哪个门/桌端空隙",
     "movement_cause": "为什么移动 —— 必须是剧情原因，不能是「为了同框」",
     "minimum_action_time": "这段移动物理上至少要多久，用于核对镜头窗口够不够",
     "irreversible_result": "不可逆的结果 —— 必须已经写进 target_cvs"}
  ],
  "camera_free_check": "确认所有 CVS 里没有景别/机位/构图/画面左右字段"
}
```

## 输入

【项目参数】
{{PARAMS}}

【本集资产（第四环节）】
{{ASSETS}}

【本集空间主表（第五环节）】
{{SPATIAL}}

【本集连续性总账（第六环节）】
{{LEDGER}}

【本集导演设计（第七环节）】
{{DIRECTING}}

【上一批末尾的 CVS（分批时给原文；第一批时这里是一句说明）】
{{PREV_CVS}}
