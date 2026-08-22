# 第一环节｜源解析与故事真相

把原文解析成**唯一的故事真相**：谁是谁、发生了什么、什么时候发生、
谁知道什么、因果怎么成立。这一层是后面所有环节的地基，**不设计画面、不排镜头**。

先理解整部剧本，再处理单集和单段 —— 局部优化破坏后续剧情的错，只能在这里防住。

## 一、实体消歧（`entities`）

同一个人在原文里可能有很多叫法：本名、小名、职务、代称、别人对他的称呼、
翻译不一致造成的异体字。**必须合并成同一个实体**，把所有叫法收进 `aliases`。

不合并的后果不是报错，是**同一个人被当成两个人建两套资产**，出两张脸。

反过来也要防：**不要因为空间角色变化就重复创建同一个物理实体**。
「病房里的病床」和「走廊尽头那张床」如果是同一张，就是同一个实体。

`entity_kind` 分五类：`character`（人）/ `group`（群体）/ `creature`（生物）/
`location`（地点）/ `prop`（物件）。拿不准是不是同一个时，宁可标
`identity_unresolved: true` 并列出候选，**不要替故事做决定**。

## 二、事件原子（`events`）

把剧情拆成事件，每条写清 `story_time`（故事内时间，不是页码）、
施动者、受动者、动作、结果。

**一个事件会同时改变多样东西**，全部写进 `state_deltas`：

```
砸碎瓶子
├── 道具：完整 → 破碎
├── 持有人：女主 → 无
├── 空间：地面出现碎玻璃障碍
└── 人物：手可能被划伤
```

漏掉其中一条，后面就会出现「瓶子已碎但手里还拿着完整的瓶子」这种错位 ——
而且是各资产系统各自推演造成的，没人报错。

## 三、故事真相（`story_truth`）

区分四种东西，混在一起就会剧透：

| | 是什么 |
|---|---|
| `objective_facts` | 客观发生了什么 |
| `presented_truth` | 当前这个时间点，观众被允许知道什么 |
| `hidden_truth` | 已经发生但还没揭晓的 |
| `unresolved` | 故事本身就没定的 |

**生产当前这一集时只能用当前合法的 `presented_truth`。**
你现在知道结局，不等于第一集的提示词里可以出现结局的信息 ——
未来的伤势、未揭晓的身份、后面才有的道具，一个都不许提前进来。

### Story Unresolved 和 Visual Underspecified 是两回事

- **Story Unresolved**：故事有意保留或资料不足以确定的事实 →
  保持 `UNRESOLVED`，**禁止自己选一个答案**填上去。
- **Visual Underspecified**：故事没规定、但画面必须定的（没写的鞋款、
  墙面材质、非关键手机型号）→ 允许定，定了就写进 `open_design`，
  **之后全剧保持一致**，不许每张图重新随机。

## 四、现实线程（`reality_threads`）

客观现实、回忆、梦境、幻觉、想象、监控画面、伪造画面各自是独立线程。
同一个人可以在不同线程里有不同的合法状态，**但不得互相污染**。

不分线程的后果：回忆里的年轻造型混进现实镜头，或者梦里的伤混进醒着的段落。

## 五、集边界与每集时长（`episode_ranges`）

剧本文件的写法千差万别：可能写「第 1 集」「EP1」「Episode 01」，也可能只用
空行、标题样式或场次编号分隔，还可能整部剧本前面挂着一大段项目推介、
人物小传、卖点分析 —— **那些不是剧本正文**。

1. 找到正文真正开始的位置，把推介/简介/说明排除掉（它们可以拿来判断
   视觉基调和人物设定，但不属于任何一集的正文）。
2. 逐集给 `start_anchor`：**该集正文第一行，从原文逐字照抄**。
   - 必须是原文里真实存在、且唯一的一整行，程序要靠它精确切分。
   - 不要改写、补全、翻译、加编号，连空格和标点都照原样。
   - 如果这一行在全文中重复出现，往下多抄一行，直到唯一。
   - **章节标记不算正文第一行。** 独占一行的 `10`、`第三章`、`———`
     是分隔符不是内容，要抄的是**它下面第一句真正的正文**。
     实跑犯过：好几集的锚点写成了 `10`、`11`、`13` —— 两个字符在全文里
     不可能唯一，程序定位到错的行，从那一集起全部切歪，
     而后面每个环节都在错的分集上工作。
   - **少于 3 个字符、或者整条只有数字和符号，程序直接判这一集切不出来。**
     宁可多抄半句，别抄一个短标记。
3. 只有一集也要给一条；确实分不出集就给空数组。

**这一步定错了，后面所有环节都会跟着错。** 拿不准某处是不是新一集开始时，
宁可合并，不要凭猜测切开。

### 每集多长（`duration_sec`）

**你是唯一看得到全篇的环节，所以每集多长必须在这里定死。**
后面的环节只看得到一集，判断不了这一集在全剧里该占多长。

**只给秒数，不用算段数。** 段是纯技术单位 —— 视频模型一次只能生成
{{DURATION}} 秒，程序自己算 `段数 = duration_sec ÷ {{DURATION}}`。
以后换个一次能出 10 秒或 20 秒的模型，段数自动跟着变。

### 长度计划（**集数听这里的，不听剧本里的章节数**）

{{LENGTH_PLAN}}

上面说了「必须切成 N 集」的时候，`episode_ranges` 就给 N 条，不多不少：

- 剧本自带的章节比 N **多** → 合并相邻章节，按剧情连贯性并，别机械地按数量平分
- 比 N **少** → 在剧情转折处再切开（新目标出现 / 新威胁 / 重要信息揭示 /
  关系变化 / 完成一次反转），别在一句话中间切
- 每集的 `duration_sec` 就填上面那个「每集 N 秒」

**程序在切集之后会把秒数按上面这份计划覆盖一遍**，所以你填别的数没有用，
只会让你规划的事件密度和实际时长对不上。集数没切对则会当场报出来。

**按剧情决定秒数，不要按字数换算。** 正文里大半是场景、动作、环境描写，
不是要念出来的台词，字数和屏幕时间没有稳定比例 —— 1700 字可能是 5 个紧凑事件，
也可能是 12 个。看事件，别看字数。

顺序：先数这一集有几个必须保留的剧情事件 → 每个事件给它自己需要的时间
（一次对峙、一场雨夜奔逃、一个眼神确认，需要的秒数天差地别，别平均分配）→
和项目参数里的期望值对一下，偏离超过 ±40% 在 `pacing_note` 里说明理由。

区间 **60 到 600 秒**。超出先回头检查集边界是不是切错了。
各集之间不要求整齐，但不该出现 60 秒和 600 秒并存 —— 那通常是切错了。

## 输出 schema

```json
{
  "project_name": "",
  "scope": "full_series | full_episode | test_sample",
  "canon_status": "canonical | provisional | local_only",
  "story_type": "",
  "cultural_setting": "地域/文化设定，服饰、建筑、货币、称谓都服从它",
  "dialogue_language": "",
  "worldview": "",
  "era": "",
  "main_conflict": "",
  "entities": [
    {"entity_id": "E001", "entity_kind": "character|group|creature|location|prop",
     "canonical_name": "", "aliases": ["原文里出现过的全部叫法"],
     "first_appearance": "", "identity_unresolved": false,
     "unresolved_candidates": []}
  ],
  "events": [
    {"event_id": "EV001", "story_time": "故事内时间，不是页码",
     "reality_thread": "RT_MAIN",
     "agent_entity_id": "", "target_entity_id": "", "action": "", "result": "",
     "state_deltas": [
       {"affected_entity_id": "", "state_dimension": "外观|持有|空间|身体|环境",
        "previous_value": "", "result_value": ""}
     ],
     "causal_parent_event_ids": []}
  ],
  "story_truth": {
    "objective_facts": [""],
    "presented_truth": [{"story_time": "", "audience_knows": "", "characters_know": ""}],
    "hidden_truth": [{"fact": "", "reveal_at": ""}],
    "unresolved": ["故事本身没定的，禁止替它决定"],
    "causal_chain": [""],
    "immutable_facts": ["不可更改的剧情事实"]
  },
  "open_design": [
    {"design_question": "故事没规定但画面必须定的",
     "story_constraints": "", "selected_visual_answer": "",
     "effective_scope": "全剧|某几集|某场景"}
  ],
  "reality_threads": [
    {"thread_id": "RT_MAIN", "kind": "objective|memory|dream|hallucination|surveillance|fabricated",
     "description": "", "belongs_to_entity_id": ""}
  ],
  "episode_ranges": [
    {"episode": "EP01", "title": "本集标题（没有填空）",
     "start_anchor": "该集正文第一行，从原文逐字照抄，必须唯一",
     "range": "人看的范围描述，如「开场到雨夜弃局」",
     "entry_state": "", "exit_state": "",
     "key_events": ["本集必须保留的剧情事件，一条一个"],
     "reality_threads_used": ["RT_MAIN"],
     "duration_sec": 180,
     "pacing_note": "为什么是这个秒数（几个事件、哪个占大头、偏差理由）"}
  ],
  "visual_tone": {
    "atmosphere": "", "color_system": "", "lighting": "",
    "character_texture": "", "scene_texture": "", "quality": "", "forbidden": "",
    "compressed": "一行压缩版，供所有图片/视频提示词的【全局风格】直接引用",
    "compressed_variants": [{"scope": "适用范围或线名", "text": ""}]
  }
}
```

> `compressed_variants` 用于同一剧存在刻意对立的视觉线（如底层线 vs 顶层线）；
> 只有一条线时给空数组。

**`visual_tone` 从「视频类型 + 视觉风格」两项设定推导，不是从剧本猜。**
全局原则第 12 条给了这两项的原词：`compressed` 那一行必须把两项的关键词
都带上（例：视频类型「3D漫剧」、视觉风格「2.5D动漫风格」→ compressed
里「3D漫剧」「2.5D」两个词都要在）。**不许用别的媒介词替换**（把 2.5D
归并成 3D、把水墨归并成二维动画都不行）—— 这一行会被下游所有图片/视频
提示词直接引用，它丢一个词，全剧的图就都丢那个词，而且不报错。

## 输入

【项目参数】
{{PARAMS}}

【完整剧本】
{{SCRIPT}}
