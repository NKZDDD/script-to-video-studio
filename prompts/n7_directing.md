# 第七环节｜导演设计

> **本次只处理 {{EPISODE}} 这一集。**

决定**人站在哪、怎么动、演什么**。本环节**不选机位、不定景别、不写画面构图** ——
那是第九环节。

## 一、你能改什么，不能改什么

| 你决定 | 你不许碰 |
|---|---|
| 走位（谁在哪、怎么移动） | 故事真相和因果 |
| 表演意图 | 人物的固有特征和能力限制 |
| 叙事焦点、导演概念 | 空间的固定结构 |
| 场次内的节奏 | 当前已经生效的状态 |

**最容易越界的一条：不能为了画面好看去挪 Canon 空间。**
病床在哪是第五环节定的。走位不成立时，改走位，不改病床。

## 二、走位（`blocking`）—— 用坐标，不用画面左右

每个人物在每个节拍写清：

- `zone_id` / `anchor` —— 在哪个区域、靠近哪个锚点
- `root_xyz` —— 双脚（或身体根部）的实际坐标
- `body_orientation_yaw` —— 身体朝向多少度
- `gaze_target` —— 看着谁/什么
- `posture` —— 站/坐/跪/躺/倚靠
- `hand_occupancy` —— 左右手各拿着什么（空手也要写「空」）
- `route` —— 移动的话，走哪条通路（用第五环节的 route_id）

**画面左右不是走位。** 「她在他左边」取决于机位；
「她在 A 区，坐标 [1.2, 3.0, 0]，他在 [2.8, 3.0, 0]」才是走位。

### 排走位之前先过五道检查

一条不过就换方案，**不要硬排一个物理上做不到的走位**：

1. 当前的 LOOK/CT 允不允许这个动作？（孕妇不能剧烈奔跑，湿滑的鞋不能急停）
2. 通路现在通不通？（有没有被临时障碍挡住）
3. 手上有没有空？（两只手都拿着东西就不能再接一样）
4. 人物关系和情绪支不支持这个距离和接触？
5. 关键动作在空间里够不够得着？

## 三、表演意图（`performance_intent`）—— 写得能演，不写情绪标签

```
❌ 很害怕
✅ 努力控制恐惧：说话比平时快，说到一半突然停住，右手一直按着桌沿
```

每条写清：内在目标、潜台词、注意力在哪、身体紧张度、呼吸、说话方式、
情绪怎么变、克制还是爆发、反应延迟多久。

**反应延迟是最容易漏的一项**，而它决定了后面镜头要留多长。
听完消息立刻反应和愣两秒再反应，是完全不同的表演，也是完全不同的时长。

## 四、场次和节拍合同

每个场次答完：进入状态 / 目标 / 冲突 / 战术推进 / 转折 / 结果 / 退出状态 /
情绪走向 / 空间怎么用 / 关键揭示在哪。

每个节拍答完：触发 / 人物意图 / 战术动作 / 反作用力 / 有意义的变化 /
表演结果 / 状态改变 / 画面优先级。

**空间戏剧化使用**（`spatial_dramatic_use`）这一项别跳过：
同一场戏，两个人隔着玻璃门和面对面站着，是完全不同的戏。
写清这一场为什么用这个空间关系。

## 五、稳定状态和过渡

走位定的是**稳定状态**：这一拍开始时大家在哪，结束时大家在哪。
中间怎么走过去，是第九环节和视频执行的事。

**不要在这里设计中间过程。** 你只需要保证：
起点成立、终点成立、从起点到终点在物理上走得通。

## 输出 schema

```json
{
  "episode": "{{EPISODE}}",
  "scene_directing": [
    {"scene_id": "SC01",
     "entry_state": "开场时世界处于什么状态",
     "objective": "", "conflict": "",
     "tactic_progression": ["按先后"],
     "turn": "", "outcome": "", "exit_state": "",
     "emotional_arc": "",
     "spatial_dramatic_use": "这一场为什么用这个空间关系",
     "key_reveal": "这一场揭示了什么；没有填空"}
  ],
  "beat_directing": [
    {"beat_id": "SC01-B1",
     "trigger": "", "character_intent": "", "tactic_or_action": "",
     "counterforce": "", "meaningful_change": "",
     "performance_result": "", "state_delta": "",
     "visual_priority": "这一拍画面上最要紧的"}
  ],
  "blocking": [
    {"beat_id": "SC01-B1",
     "character_id": "C001",
     "spatial_id": "SP001",
     "zone_id": "A",
     "anchor": "BED_01",
     "root_xyz": [2.4, 1.2, 0],
     "body_orientation_yaw": 90,
     "gaze_target": "C005 的脸",
     "posture": "站立|坐|跪|躺|倚靠",
     "hand_occupancy": {"left": "空", "right": "PI001 授权书"},
     "route": "R1 或空（不移动）",
     "distance_to": [{"character_id": "C005", "meters": 1.5}],
     "contact": "有身体接触就写清哪里；没有填空",
     "feasibility_checked": "五道检查里哪几条是这一拍的风险点"}
  ],
  "performance_intent": [
    {"beat_id": "SC01-B1", "character_id": "C001",
     "internal_objective": "", "subtext": "", "attention_target": "",
     "body_tension": "", "breath_pattern": "", "speech_behavior": "",
     "emotional_change": "", "restraint_or_release": "",
     "reaction_latency": "反应延迟多久 —— 决定后面镜头留多长"}
  ],
  "blocked_by_physics": [
    {"beat_id": "", "wanted": "本来想排的走位",
     "why_impossible": "五道检查里哪一条不过", "resolution": "改成了什么"}
  ]
}
```

## 输入

【项目参数】
{{PARAMS}}

【本集叙事结构（第三环节）】
{{NARRATIVE}}

【人物与世界规则（第二环节）】
{{RULES}}

【本集空间主表（第五环节）】
{{SPATIAL}}

【本集连续性总账（第六环节）】
{{LEDGER}}
