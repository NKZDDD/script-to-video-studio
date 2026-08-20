# 第十三环节｜视频执行计划与提示词

> **本次只编译这一段：{{SEGMENT}}。** 其它段不用管。

把故事板变成**一次生成就出完整成片**的执行计划和提示词。

## 一、这一段要一次出完

```
一个视频目标 = 一次模型生成
             = 有序的多个镜头
             + 原生转场
             + 表演 / 机位 / 声音
             = 完整成片
```

**不许**输出分镜头素材、候选片段、转场占位，或者任何「留给后期处理」的东西。
项目是 `MODEL_NATIVE_ONLY`，外部剪辑和后期补转场都是禁止的。

## 一（补）｜这次的模型能力

{{CAPABILITY}}

档位是 `UNSUPPORTED` 时，**不要写多镜头切换**：改成单镜头连续的机位和走位，
或者在同一次生成里用完整遮挡完成变化。仍然做不到就在 `capability_note` 里
写明 `MODEL_NATIVE_TRANSITION_BLOCKED` 并说清卡在哪 ——
**不许改用外部剪辑**，那是项目配置层面的决定，不在你这里切换。

## 二、视频只能执行，不能重新导演

**可以做的**：关键帧之间的合法中间运动、表演动作与微表情、
身体惯性与呼吸步态、衣料头发环境的自然物理、已冻结机位计划的连续执行、
已批准状态变化的发生与结果。

**不能做的**：改人物身份、服装、伤口、道具或地点；自由想象故事板裁切外
没定义过的身体、服饰或空间；新建关键剧情事实、文字、持有人或空间关系；
重新决定镜头顺序、动作顺序或结局；把后期状态提前融合。

### 关键帧之间是「表演重建」，不是像素变形

```
✅ 关键帧状态 A ──表演过程──→ 关键帧状态 B
❌ 关键帧 A 的像素 ──形变──→ 关键帧 B 的像素
```

明确禁止：故事板格子形变、静态人物平移或缩放、角色融化变形、
前后帧平均融合、**把整张故事板的网格边框标签排版生成进视频**、
把硬切理解成两个机位之间的连续运镜。

## 三、时间窗口（绝对秒数在这里冻结）

第十二环节写的是相对位置（「本段早段」），**这里把它变成秒数**。

逐个窗口写：时间范围、进入的关键帧和状态集、镜头与机位、
世界坐标里的机位路径、**镜头显露包络**、覆盖状态、
转场编号与角色、遮挡与切换点、人物意图、动作因果、首次接触、
状态激活事件、目标状态、**禁止出现的未来状态**、声音提示、退出状态。

所有窗口的时长加起来必须等于 {{DURATION}} 秒，**转场占的时间算在里面**。

## 四、镜头显露包络与首次显露闸门

这是视频这一层特有的风险：**故事板只画了半身，视频镜头一拉远就看见了下半身。**

每个窗口先算「这段时间里镜头运动、人物转身或起身**可能第一次看见什么**」：

```
初始取景 / 最大后退 / 环绕范围 / 人物转身范围 /
站坐跪的变化 / 肢体伸展 / 正侧背显露 / 鞋履显露 / 遮挡解除
```

然后拿它和当前造型的覆盖表比对，结果只有三种：

| 结果 | 怎么办 |
|---|---|
| `COVERED` | 可以生成 |
| `SUPPLEMENTAL_REFERENCE_REQUIRED` | 补一张当前 LOOK/CT 的覆盖图，并写严格权限 |
| `CAMERA_CONSTRAINED` | 没有合法覆盖资产，**机位必须待在已定义的范围内** |

**不许以「出现概率不大」为理由跳过。** 覆盖没过之前，
视频不得扩大构图、后退、环绕、让人物转身或起身到会显示未定义区域的程度。

## 五、参考图：**权威完整、互不冲突**的执行集

> V6.1 把这一章的判据从「最小充分」改成 **Authority-Complete**。
> 这不是措辞调整，方向是反的：
> 以前是「能删就删、挑到够用就停」，现在是
> **「六个维度都有唯一来源」才允许删**。
>
> 改的原因是实跑撞到的：减压过头之后整段没有任何图片持有
> Camera/Blocking/Time 权威，视频模型只能自己编。

### 5.0 先填六维覆盖矩阵，**填完才准删图**

删任何一张图之前，逐维确认它仍有来源（图片或逻辑合同都算）：

| 维度 | 谁提供 | 本段来源 |
|---|---|---|
| Identity（是谁） | 故事板 / LOOK-CT / 角色图 | |
| LOOK/CT（穿什么、什么状态） | 当前完整 LOOK 或 CT | |
| Spatial / Geometry（地方长什么样） | LOC_VIEW / GEO Proxy / 空间主表 | |
| Position / Blocking（人在哪、面朝哪） | CVS / SCSTATE 逻辑合同 / 故事板 | |
| State / Temporal（此刻是哪个状态） | 状态门控 / 时间窗口 | |
| Prop / Count / Holder（东西几件、在谁手上） | 道具总账 / 故事板 | |

**任何一维空着就输出 `REFERENCE_DIMENSION_COVERAGE_GAP` 并停下。**
六维是不可降级底座 —— **可靠度再高也不例外**。图片可以少，
但「谁、穿什么、在哪、朝哪、什么状态、拿着什么」必须每一项都说得出出处。

### 5.0b **生成的视频帧一律不许当参考**

上一条视频的任何一帧 —— 尾帧、截图、抽帧 —— **只能作为 QC 证据**，
不许注册成这一段的 Reference、Temporal Primary 或 Canonical 入口。

发现来源是视频抽帧，立刻输出 `GENERATED_FRAME_REFERENCE_FORBIDDEN` 并停下。

为什么这条是硬的：视频帧是**模型执行的产物**，不是 Canon。
拿它当下一段的权威，等于让上一次的执行误差变成下一段的事实，
一段段传下去 —— 而且每一段看起来都"接得上"，错误是累积的、静默的。
动作中间帧、半眨眼、运动模糊被当成入口状态就更糟。

**跨段入口只读预编译的边界锚点**（`BNDPLAN` / `BNDANCHOR`，第十环节定的）：
它是在两条视频生产**之前**，由故事真相、CVS、空间主表、当前 LOOK/CT
和道具总账编译出来的，不是从谁的尾帧里提取的。

### 5.1 组合权威在故事板，但故事板可能是文字合同

```
Temporal Primary  = 本段故事板执行图 或 已批准的边界锚点（同一时间窗口只能有一个）
Identity          = 当前完整 LOOK / CT，仅在故事板未清楚显示时
Geometry          = 已批准的 LOC_VIEW / GEO Proxy，仅在机位揭示超出故事板覆盖时
Prop              = 道具细节，仅在关键文字或材质不可辨识时
State Result      = 结果锚点，仅在不可逆结果必须冻结时
```

**同一时间窗口只能有一个 Temporal Primary。** 两张图都想管"这一刻是什么样"，
模型会取平均 —— 出来的既不是 A 也不是 B。

> **V6.1 这里原来允许「故事板根本没有图」** —— 物化门控把大部分 KF 判成
> `TEXT_CANON_ONLY` 时，SBPKG 是一份纯文字合同，视频只拿文字当组合权威。
>
> **V6.2 第 19 章把这条禁掉了**：每个正式生产 SEG 必须有覆盖完整关键
> 时间推进的**视觉**载体。所有 KF 照旧保留文字 Canon，但入口、转折、
> 接触/激活、不可逆结果、出口、高风险走位这几类必须有图。
> 只有文字 → `VIDEO_STORYBOARD_SPINE_MISSING`。

Identity 和 LOOK/CT 照旧**必须有图** —— 那两维靠文字撑不住，
模型会照着描述重新长一张脸。

**删掉 SCSTATE 的图，不等于删掉它的 Source CVS、Zone、Anchor、
Support、Route、Orientation。** 那些是位置合同，没有被批准的移动事件就不可变，
和出不出图无关。

### 5.2 参考图分两层：骨架必给，补图才是挑的（V6.2 第 19 章）

`{{VIDEO_REFERENCE_POLICY}}`

**第一层 —— 强制故事板时间骨架，不可省。**
把本段第十二环节的**全部有序 Sheet**（或同一 SBPKG 的有序独立 KF 锚点）
按 `order` 上传。它覆盖的是：先发生什么后发生什么、每个 Beat 谁施动谁受动、
关键动作阶段与稳定结果、镜头顺序与切换动机。

> **V6.1 这里原来是按可靠度分路的**：`high` 走 `START_ONLY`（只给一张起始图）、
> `medium` 走 2–4 张时间锚点、`low` 才给完整故事板。
>
> **V6.2 把这条改掉了。** 原话：可靠度只决定故事板**承载颗粒度**、
> 补图数量与提示词冗余度，**不决定是否提供故事板**。
> 所以 `{{VIDEO_EXECUTION_RELIABILITY}}` 再高也不能退化成只有一张起始图 ——
> 那种情况输出 `VIDEO_STORYBOARD_SPINE_MISSING`。

**每张骨架图进视频前逐项准入**（`storyboard_reference_admission_gate`）：
叙事准确、身份、当前 LOOK/CT、几何、World Position/Zone/Anchor/朝向、
支撑/通路/屏障/门户、时间状态与未来状态禁运、道具实例/数量/持有人、
动作阶段、镜头观察、可读性（脸、手、关键细节）。

任一关键项错 → `STORYBOARD_REFERENCE_ADMISSION_FAILED`。
修的顺序是：先判上游文字 Canon 对不对，上游对就建新的故事板版本，
上游错就先修有权威的那一层。
**禁止用正确的 LOOK、LOC_VIEW 或文字提示词去「压过」一张错的故事板。**

**第二层 —— 补图必须证明有独有作用**（`effective_reference_selection_gate`）。
每张候选同时满足五条，否则删掉并输出 `VIDEO_REFERENCE_UNIQUE_UTILITY_UNPROVEN`：

```
CORRECT        通过准入与 Canonical 解析
UNIQUE         解决骨架**没覆盖**的独有 Authority 缺口
NONCONFLICTING 不控制镜头、走位、时间或动作阶段
WINDOWED       有明确的适用视频时间窗
LEGIBLE        对模型可见，且足以解决那个缺口
```

合法的补图触发器：主角身份在骨架里不够清晰或跨镜头易漂；当前完整 LOOK/CT 的
身体/背面/下装/鞋履/伤势区域将**首次显露**；镜头显露进入骨架没充分说明
但空间已批准的新 Zone；Hero 道具的文字/结构/尺寸/状态结果在骨架里不可辨；
不可逆状态结果需要独立清晰权威。

**禁止的补图动机**：为了填满上限；「可能有用」「多一张更保险」；
和骨架表达完全相同的状态；错的故事板还没修；让 LOOK 重新控制姿态或走位；
让 LOC_VIEW 重新控制当前构图；让道具 SPEC 重新决定持有人、数量或动作阶段。

**SCSTATE 默认不上传视频。** 同一稳定状态不许用 SCSTATE、故事板、LOOK
和场景 PR 重复投票。

**参考图上限是容量上限，不是要装满的目标，也不是可以减到零的许可。**

【本次参考图上限】{{REF_LIMIT}}

超过 5 张时必须逐张写 `Unique Missing Authority` —— 这一张缺的是哪一项、
为什么故事板给不了。证明不出来的删掉。

### 5.3 不要同时传 Clean LOOK 和未来 CT

同一段内发生 Clean LOOK → CT 激活时，优先靠故事板的有序关键帧和时间门控表达。
除非已经验证这个模型支持 Reference Time Scope，**不要同时上传干净全身图和
未来带伤全身图**——会导致伤口提前出现，或者两个状态被抹平成一个。
确需补图时只补故事板表达不了的局部覆盖，并写绝对时间范围。

### 5.4 写法

```
Image 1 = SB_{{EPISODE}}_{{SEGMENT}} 本段故事板执行图
  是谁/是什么 + 画面可见内容：本段有序关键帧网格，可见 C001 与 C005，
    KF01 C001 坐于床沿、C005 立于床尾，KF03 起身后两人面对面
  故事时间 / 当前状态：{{SEGMENT}} 全程，从起始稳定状态到结束状态
  有权控制：关键帧的视觉状态、人物与空间组合、机位、动作顺序、
    道具外观与实例绑定、数量、时间状态、转场机制与锚点
  无权控制：故事板的网格、边框、格子编号、排版；
    自然的中间动作、呼吸微表情、衣料细微物理
  适用范围：本段全部镜头与转场
  MUST TRANSFORM：把静态关键帧变成连续表演
```

**冲突时按这个顺序让步**（V6.1 定的，从高到低）：

```
故事因果 > 边界/入口/出口 > 人物身份与 LOOK/CT > 世界位置与空间
  > 强制音频 > 动作完成 > 镜头复杂度 > 装饰性细节
```

**允许简化镜头，不许删关键因果、声音、位置合同或结果。**
镜头复杂度排在倒数第二 —— 做不出来就用更简单的机位，
但不能因此改人在哪、穿什么、发生了什么。

补充图只管视觉覆盖，不管机位/时间/走位。补了全身图之后镜头构图被重置，
就是这条没写清。

补充图必须显式写「无权控制：机位、走位、动作阶段、时间」。
补充图一旦被要求控制姿态、机位、走位或动作时间，
输出 `REFERENCE_AUTHORITY_CONFLICT` 并停下，不要释放视频提示词。

## 六（前）｜导演级执行密度（V6.2 第 19 章）

`{{VIDEO_PROMPT_DETAIL_MODE}}`

**提示词不许只有字段标题、镜头标签或一句动作摘要。** 把这一段按因果、表演、
镜头和状态变化**动态**划分时间窗：普通 Beat 可以 2–5 秒，高风险接触、交接、
跌倒或状态激活细分到 0.5–1.5 秒。**不要机械地固定窗口数量。**

不够可执行 → `VIDEO_PROMPT_EXECUTION_DETAIL_INSUFFICIENT`。

### 时间窗执行卡：每个窗口至少写这些

```
TIME WINDOW                     这一窗的起止秒
STORY / BEAT PURPOSE            这一窗要完成什么剧情任务
ACTIVE CHARACTER / LOOK / CT    谁在场、当前造型与状态
WORLD POSITION / ZONE / ANCHOR / SUPPORT / ORIENTATION
PROP INSTANCE / COUNT / HOLDER
PRIMARY NARRATIVE SUBJECT       这一窗镜头和观众注意力归谁
TRIGGER                         什么触发了这一窗
ACTION CAUSALITY                谁因为什么做了什么
ACTION PHASE                    准备/启动/路径/接触/跟随/反应/恢复/稳定结果
PHYSICAL PATH / COMPLETION CONDITION
MICRO-PERFORMANCE
EYE-LINE / ATTENTION
CAMERA / COMPOSITION / FOCUS
CUT OR TRANSITION MOTIVATION    为什么在这里切
DIALOGUE / VOCAL DELIVERY / LIP SYNC
SFX / AMBIENCE / MUSIC
STATE DELTA                     这一窗结束时变了什么
NEXT TRIGGER
FORBIDDEN FUTURE STATE
```

**逐窗重复当前持续状态和关键位置，不许写「同上」「保持一致」。**

### 微表演：写可拍的行为，不是情绪词

`{{MICRO_PERFORMANCE_CONTRACT}}`

「细腻」来自角色目标、压抑与泄露，不来自堆砌随机动作。关键 Beat 至少挑几项
和剧情相关的：内心目标、潜台词、注意力对象、眼动先于头动、呼吸变化、
面部肌肉张力、眨眼/吞咽/迟疑、手与手指行为、身体张力与重心转移、
反应延迟、克制/泄露/释放、说话前的准备与说完后的收尾。

写成这样：「先保持面对记者，眼睛先右移，停顿半秒后才转头」。
**不要只写「紧张」「悲伤」「电影感表演」** → `VIDEO_PERFORMANCE_CONTRACT_INSUFFICIENT`。

背景人物只给符合空间与事件的低优先级自然反应，不许抢主叙事对象。

### 动作阶段与物理反应

`{{ACTION_PHASE_PHYSICAL_RESPONSE_CONTRACT}}`

攻击、跌倒、搀扶、交接、拥抱、起身、转身、开门、上下车这类高风险动作按需展开：

```
准备 → 启动 → 路径 → 接触 → 跟随 → 反应延迟 → 恢复/失衡 → 稳定结果
```

逐阶段写施动者、受动者、身体或道具路径、接触位置、重量与惯性、完成条件、
声音和状态变化。硬规则：

- **受动者不得在接触前完整反应**
- **道具不得在手闭合前换持有人**
- **跌倒必须有失衡、支撑失败与落地过程**
- 完成之后不许重演；状态只在激活动作完成后生效
- 中间姿势不建立新的稳定 Canon，除非剧情需要

缺了 → `VIDEO_ACTION_PHASE_INCOMPLETE`。

### 镜头语法

`{{CINEMATIC_CAMERA_GRAMMAR_CONTRACT}}`

每个镜头或切换必须说明：叙事功能、来源 KF/时间窗、景别/角度/镜头意图、
机位与看向、构图与景深层次与主体优先级、运动起点/路径/速度变化/停点、
视线与轴线与画面方向、焦点与拉焦、显露包络、切换动机与剪辑关系、
转场机制与遮挡与切点、新镜头透露了什么信息。

**镜头只投影 Canonical World** —— 不移动人物、门窗、家具、Zone 或道具。
不许为了「更有张力」加没有功能的推拉摇移；镜头复杂度必须服务 Beat、
表演或信息 → 否则 `VIDEO_CAMERA_GRAMMAR_INSUFFICIENT`。

### 细化 ≠ 堆形容词

只有长篇形容词、重复禁令，或者每个窗口的描述完全一样 —— **不算高密度**。

模型负载过高时，优先减掉装饰性背景动作、复杂景深、无必要拉焦、
无功能运镜、重复反应镜头。**不许删**关键因果、故事板骨架、身份、
LOOK/CT、World Position、动作完成、必需音频或 Canonical 结果。

### 冲突时的保护顺序

```
1. 故事真相 / 故事板时间骨架
2. 边界 / 入口 / 出口
3. 人物身份 + 当前 LOOK / CT
4. World Position / 空间 / 几何
5. 状态 / 道具实例 / 数量 / 持有人
6. 必需对白 / 音频
7. 动作完成 / 物理因果
8. 表演意图 / 反应优先级
9. 镜头复杂度 / 焦点效果
10. 装饰细节 / 背景微动作
```

允许把复杂运镜降成稳定机位、把多次拉焦降成单一焦点、把装饰动作交给自然生成；
**禁止用「简化镜头」当作人物瞬移、动作缺失或状态重置的理由。**

## 六、提示词必须包含的执行块

```
【一次输出完整成片】
在一次生成里输出这一段的完整成片，包含全部有序镜头和转场。
不允许外部剪辑、拼接、插入转场或后期处理。

【镜头时间轴】
逐镜头写：时间、状态、机位、动作、退出条件

【原生转场窗口】
逐转场展开完整合同：机制、时间范围、切点或切换点、
切走的构图与动作、触发、遮挡类型与覆盖度、运动矢量、
接进来的构图与动作、转场前只允许什么状态、转场后只允许什么状态

【遮挡式状态切换】
只有起点状态 → 遮挡建立 → 100% 遮挡 → 切换点 → 只有终点状态

【不许镜头形变】
硬切就是真的切。不许在两个独立机位之间做动画或形变。
遮挡式转场只能在批准的遮挡点切换。

【镜头显露包络】
初始取景、最大显露范围、人物转身或姿态变化、需要的身体与服饰区域

【首次显露锁】
下半身、背面、鞋履、手第一次出现时，照当前造型的覆盖图复现。
不许发明、简化、改款或替换。

【取景扩张禁令】
机位和表演必须待在已批准的覆盖范围内。没定义的区域，不要显露。

【不依赖外部剪辑】
返回的视频必须已经包含完整的镜头序列、转场、时间和声音连续性。

【跨段边界】
入口状态来自预编译的边界锚点（BNDPLAN/BNDANCHOR），不是上一条视频的尾帧。
出口要停在**已完成动作、对白与状态结果之后的稳定拍子**上 ——
不许停在动作中间、半眨眼、运动模糊或遮挡未解除的时刻。

【生成帧禁入】
这一段生成出来的任何一帧都不许进入下一段的参考清单。
视频完成之后只做 QC 和「要不要重生成」的判断。
```

## 六（补）｜Story-First：剧情写在技术合同前面

V6.1 要求提示词按这个顺序写，**技术合同放最后**：

```
完整场景剧情 → 确切视觉时刻 → 前一刻/此刻/下一刻
  → 主叙事对象 → 六字段身份映射 → 之后才是技术合同
```

判据很直接：**把所有 ID 遮住之后，仍然要能读出这一刻在演什么、
谁是主角、因为什么、下一刻禁止发生什么。**读不出来就是写反了 ——
技术合同堆在前面时，模型先读到一堆约束，等读到剧情已经在填格子了。

## 七、声音

`native_audio` 模式下，J-Cut、L-Cut、声音匹配、声音掉落、环境音过渡
**必须在同一次生成里完成**。

- **J-Cut**：目标画面的声音可以先进来，但**目标的视觉状态仍然受未来状态禁令约束** ——
  先听见医院的声音可以，先看见医院不行。
- **L-Cut**：前一场的声音可以延续过来，但**前一场的人物和地点不许在画面里重现**。

`silent_video` 模式只做视觉转场，**不许生成随机声音**。

### 旁白 / 画外音

{{NARRATION_RULE}}

有旁白时，**旁白和台词是两种东西，不许混**：

| | 谁在说 | 画面里的嘴 |
|---|---|---|
| 台词 | 画面里的角色 | 对口型 |
| 旁白 / 内心独白 | 画外（通常是主角回忆的声音） | **不动** |

**这一条最容易做错**：第一人称小说改编的剧本，正文大半是
「我抱着他退到窗边」「再醒来的时候，我躺在柔软的床上」这种独白。
把它当成台词写进提示词，出来的画面就是角色在自言自语 ——
而且**看起来不像错**，只是很怪。

写进提示词时逐条标清楚：哪几句是台词（谁说、对口型），
哪几句是旁白（画外、不动嘴）。**没有旁白的项目就一句都不要写旁白**，
别自己加一个「深沉男声」。

### 关于换行（很重要，漏了下游没法逐项校验）

上面那些 `Image N = …` 和它下面的六项，**必须逐项分行**，
在 JSON 字符串里用 `\n` 表示换行。像这样：

```
"prompt": "…前面的内容…\nImage 1 = C002 甲\n  是谁/是什么 + 画面可见内容：成年男性正面半身\n  故事时间 / 当前状态：基准身份，未受伤\n  有权控制：脸型、五官、肤色、发型\n  无权控制：这张图的姿势、机位、构图、背景\n  适用范围：本次生成这一张\n…后面的内容…"
```

**不许把六项合并成一段连续文字。** 合并之后：
· 两张以上参考图时，分不清哪一项归哪张图 —— 模型会把几张平均融合
· 程序没法逐项校验，会把这一条拦下来重做

实测踩过：64 份提示词全写成了单段文字，一个 `\n` 都没有，
结果 41 条在出图那一步被拦住。

## 输出 schema

```json
{
  "episode": "{{EPISODE}}",
  "segment": "{{SEGMENT}}",
  "video_plan": [
    {"seg_id": "{{SEGMENT}}",
     "total_duration": {{DURATION}},
     "windows": [
       {"window_id": "W01",
        "time_range": [0.0, 2.4],
        "entry_kf": "KF01", "entry_cvs": "CVS_{{EPISODE}}_SC01_01",
        "entry_active_states": ["进入这个窗口时已经生效的状态"],
        "shot_id": "SH_{{EPISODE}}_001",
        "camera_path_world": "机位在世界坐标里怎么走；固定就写固定",
        "camera_reveal_envelope": "这段时间可能第一次看见什么",
        "visual_coverage_status": "COVERED|SUPPLEMENTAL_REFERENCE_REQUIRED|CAMERA_CONSTRAINED",
        "character_intent": "", "action_causality": "",
        "first_contact": "首次接触在第几秒",
        "state_activation_event": "这个窗口里哪个状态开始生效",
        "target_state": "窗口结束时的状态",
        "forbidden_future_state": ["这个窗口绝对不许出现的"],
        "transition_id": "TR_{{EPISODE}}_001",
        "transition_role": "NONE|EXIT|WINDOW|ENTRY",
        "sound_cue": "",
        "exit_state": "",
        "_V6.2 导演级 —— 下面几项每个窗口都要填，不许写「同上」": "",
        "beat_purpose": "这一窗要完成什么剧情任务",
        "primary_narrative_subject": "这一窗镜头和观众注意力归谁",
        "trigger": "什么触发了这一窗",
        "action_phase": "准备|启动|路径|接触|跟随|反应延迟|恢复或失衡|稳定结果",
        "physical_path": "身体或道具的路径、接触位置、重量与惯性",
        "completion_condition": "这个动作算完成的判据",
        "micro_performance": "可拍的行为：眼动/呼吸/手指/重心/迟疑/克制或泄露",
        "eye_line_attention": "看谁、注意力在哪；眼动先于头动写清楚",
        "camera_grammar": "叙事功能、景别角度、机位与看向、构图层次、运动起止、轴线与画面方向、焦点与拉焦、显露包络",
        "cut_motivation": "为什么在这里切；不切就写 NONE",
        "dialogue_delivery": "台词原文、说话人、语速停顿情绪；口型只绑当前说话人",
        "sfx_ambience_music": "动作声与物理事件同步；Room Tone 随场景延续",
        "state_delta": "这一窗结束时变了什么",
        "next_trigger": "下一窗靠什么起来",
        "position_state_restated": "当前 Zone/Anchor/支撑/朝向，逐窗重写一遍"}
     ],
     "transition_windows": [
       {"transition_id": "TR_{{EPISODE}}_001",
        "mechanism": "NATIVE_CUT", "execution_mode": "MODEL_NATIVE_ONLY",
        "time_range": [2.4, 2.4], "duration": 0.0,
        "exit_composition": "", "exit_action": "",
        "trigger": "", "shield_type": "", "shield_coverage": "",
        "camera_motion_vector": "",
        "state_switch_point": "",
        "entry_composition": "", "entry_action": "",
        "from_only_state": [""], "target_only_state": [""],
        "forbidden_state_mixing": [""],
        "audio_bridge": "",
        "completion_condition": "什么算做成了",
        "failure_signature": "做失败会长什么样（给人工验收看）"}
     ],
     "storyboard_spine": {
       "sbpkg_id": "SBPKG_{{SEGMENT}}",
       "carrier_mode": "ORDERED_CONTINUATION_SHEETS|ORDERED_KF_ANCHORS",
       "temporal_coverage": "COMPLETE",
       "_注": "images 的条数必须**等于第十二环节这一段实际产出的 Sheet 数** —— 少一张就是骨架断了一段，模型不知道那段时间发生了什么",
       "images": [
         {"order": 1, "sheet_id": "SHEET_A", "kf_range": "KF01-KF03",
          "time_range": "本段 0-12 秒",
          "spine_role": "ENTRY",
          "admission_status": "ADMITTED 或 FAILED + 哪一项不过"},
         {"order": 2, "sheet_id": "SHEET_B", "kf_range": "KF04-KF06",
          "time_range": "本段 12-30 秒",
          "spine_role": "RESULT|EXIT",
          "admission_status": "ADMITTED"}
       ],
       "coverage_note": "这几张按顺序覆盖了本段哪些关键时刻；有没有哪个关键时间窗没覆盖"
     },
     "_注": "骨架那几张**全部**要在这里逐张列出，`image_n` 从 1 连续排，顺序等于 order。补图排在骨架之后",
     "reference_order": [
       {"image_n": 1, "asset_id": "SBPKG_{{SEGMENT}}_SHEET_A",
        "asset_name": "本段故事板第 1 张（前段）",
        "reference_role": "STORYBOARD_SPINE",
        "who_what_visible": "这张图是谁/是什么 + 画面里能看见什么（必填）",
        "story_time_state": "故事时间与当前状态（必填）",
        "unique_authority_contribution": "**补图必填**：这一张解决的是哪一项骨架给不了的权威缺口",
        "admission_status": "ADMITTED 或 FAILED + 哪一项不过",
        "must_preserve": "", "must_transform": "",
        "must_not_copy": "", "does_not_control": "",
        "applicable_time_window": "0-12 秒"},
       {"image_n": 2, "asset_id": "SBPKG_{{SEGMENT}}_SHEET_B",
        "asset_name": "本段故事板第 2 张（后段）",
        "reference_role": "STORYBOARD_SPINE",
        "who_what_visible": "", "story_time_state": "",
        "unique_authority_contribution": "",
        "admission_status": "ADMITTED",
        "must_preserve": "", "must_transform": "",
        "must_not_copy": "", "does_not_control": "",
        "applicable_time_window": "12-30 秒"},
       {"image_n": 3, "asset_id": "LK002",
        "asset_name": "补图示例：当前造型（只在骨架说不清身份时才加）",
        "reference_role": "IDENTITY",
        "who_what_visible": "", "story_time_state": "",
        "unique_authority_contribution": "骨架里正脸不够清晰，跨镜头容易漂",
        "admission_status": "ADMITTED",
        "must_preserve": "", "must_transform": "",
        "must_not_copy": "", "does_not_control": "",
        "applicable_time_window": "全段"}
     ],
     "rejected_references": [
       {"asset_id": "", "why": "为什么删掉：和骨架表达同一状态 / 证明不出独有作用 / 准入没过"}
     ],
     "quality_priority_note": "如果过载，先简化什么、绝不删什么",
     "capability_note": "按目标模型的多镜头能力档位，这一段是否需要降级；降到哪一档",
     "video_prompt": "完整提示词正文，含上面全部执行块，可直接复制投喂视频模型"}
  ],
  "time_budget_check": "各窗口时长 + 转场时长 = 总时长，对得上吗"
}
```

## 输入

【项目参数】
{{PARAMS}}

【本集镜头与剪辑时间（第九环节）】
{{SHOTS}}

【本集 SEG 装箱（第十环节）】
{{SEGS}}

【本段故事板包（第十二环节）】
{{STORYBOARD}}
