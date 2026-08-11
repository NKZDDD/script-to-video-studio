# 第四环节｜资产系统

> **本次只处理 {{EPISODE}} 这一集。** 但**资产库是全剧共享的** ——
> 下面【已有资产】列出前面几集建好的，同一个对象**必须沿用原编号**。
> 换了编号就会另出一张脸，这是整套流程最不能出的错。

建立这一集需要的全部视觉资产，并判断哪些真的要出图。本环节**不排镜头、
不决定站位**（那是第七环节），也**不生成图片**。

## 一、资产家族与依赖拓扑

按这个顺序建，后面的依赖前面的：

```
CHAR ─→ PH（人物身体/年龄阶段）
          ├─ 关键/复杂/复用服装 → COST 独立资产
          └─ 简单一次性服装     → COST 逻辑合同（不出图）
                    ↓ 穿到这个人身上
                  LOOK（完整造型：这个人 + 这套衣服）
                    ↓ + 状态变化
                  CT（当前状态：伤、污、湿、破损…）

LOC（场景长什么样）  ─→ 见第五环节的 SPATIAL（物理怎么构成）

PROP_SPEC（同款外观）─→ PROP_INSTANCE（具体哪一个）─→ INSTANCE_CT（它的状态）
                     └→ PROP_SET（成批未追踪的库存）

VEH / CRE / GRP / VFX 及其必要状态
```

`family` 就填上面这些大写名字。

## 二、服饰走哪条路（`costume_contracts`）

**下游拿到的当前人物主资产必须是「衣服已经穿在这个人身上」的完整 LOOK**，
不是一张孤立的服装图。区别在于：孤立服装图很清楚，穿到人身上会变样。

满足**任一条**就把 COST 物化成独立资产（`materialize: true`）：

- 标志性造型、制服、礼服、年代服装
- 复杂层次、特殊剪裁材质、关键纹样、徽章、可读文字
- 同一套衣服跨多个角色/集数/大量镜头复用
- 衣服本身会损坏、拆解、脱下、交接，或成为剧情对象

否则 `materialize: false`（逻辑合同），**但文字合同必须写全** ——
省略独立出图不等于可以省略鞋履、背面、衣长、层次、版型和穿着方式的明确答案。
少写一样，模型就自己编一样。

**两条路的终点都是 LOOK。**

## 三、LOOK 必须头到脚完整（`coverage`）

LOOK 至少给：正面、45°、侧面、背面，且**头、手、脚都要在画面里**。

每个 LOOK / CT 维护一张覆盖表，逐个区域标 `DEFINED` / `TEXT_ONLY` / `UNDEFINED`：

```
face_head / front_torso / back_torso / profile /
arms_hands / waist_hip / legs / footwear / rear_full_body
```

**为什么必须现在就标**：后面视频镜头一拉远、人一转身，就会第一次看见
故事板里没画过的区域。那时候如果这块是 `UNDEFINED`，模型只能自己想 ——
鞋子换一双、背面衣服变成另一款，而且不报错。

CT 的规矩：**当前状态优先于干净状态**。有伤就不许退回没伤的 LOOK。
如果 CT 只改了脸但视频会看到全身，用「LOOK 的全身覆盖 + CT 的脸部精确差异」，
并写明 LOOK 不许清掉 CT。

## 四、同款道具分三层（`prop_specs` / `prop_instances`）

两张外观一样的支票，一张是真的一张是伪造的 —— 这是两个**物理实体**，
不是一个道具的两个状态。

| | 管什么 |
|---|---|
| `PROP_SPEC` | **只管外观**。同款长什么样 |
| `PROP_INSTANCE` | **唯一物理实体**。它现在在谁手里、在哪、什么状态、经历过什么 |
| `PROP_SET` | 成批的、没必要逐个追踪的库存（一摞文件、一堆碎石） |

只有发生交互（被拿起、被交接、被损坏）时，才从 `PROP_SET` 物化出 `INSTANCE`。
一开始就逐件建资产会炸掉资产库。

**不许为了区分身份而给完全同款的实例随机改色、加划痕、改标签。**
它们本来就该长得一样，区分靠 INSTANCE ID 和持有历史，不靠外观。

## 五、一条资产 = 一个主体（强制）

`parent_asset_id` 只能填**一个**，所以一条状态资产只描述**它父资产自己**的状态。

- **禁止**在 `appearance` 里写别的主体的位置和动作。
  ❌ `Rizky固定在画面左侧，Dewi在右侧床边递交复印件`
  ✅ 场景状态只写场景：`床单前景散落撕碎的纸片，输液管垂落床侧`
- 声明了空镜、无人物的，正文里**一个角色名都不许出现**。
- 「谁在左、谁在右、谁把东西递给谁」是**导演和分镜**的事。
  一个画面里有三个人，靠的是三条独立资产在编译时被一起绑上，
  不是把三个人塞进一条资产。

## 六、`reference_assets`：画面里出现谁，谁就得在里面

这是出图时实际要喂的参考图列表，**顺序就是上传顺序**。

- 父资产**必须在，且排第一**。
- 画面里必然出现的其它已建资产**全部列上** —— 少列一个，那部分只能靠模型
  现编，和别处对不上。
- 只能引用**生产顺序早于自己**的资产。同级不得互相引用，不得成环 ——
  成环的话没有任何一个能先生产，程序会直接停下。
- 只列真实存在的 `asset_id`。写一个不存在的，出图那一步会停。

`dependency_order` 给整数，小的先生产。父资产必须比它的状态资产小。

## 七、出不出（`decision`）

- `must` —— 核心人物、重要配角、高频场景、核心道具、不可逆状态
- `conditional` —— 次要角色、少量使用的场景、辅助状态
- `skip` —— 一次性路人、普通背景物、普通动态效果（尘土、烟雾、雨、火花）

判 `skip` 的要写进 `skipped` 并给理由。**动态元素满足任一条就升级成状态资产**：
跨多个段落持续 / 后续剧情依赖 / 状态不可逆 / 容易被模型错误恢复 /
需要固定精确位置 / 影响人物道具场景关系。

## 输出 schema

```json
{
  "episode": "{{EPISODE}}",
  "assets": [
    {"asset_id": "C001",
     "family": "CHAR|PH|COST|LOOK|CT|LOC|PROP_SPEC|PROP_INSTANCE|PROP_SET|VEH|CRE|GRP|VFX",
     "name": "",
     "decision": "must|conditional|skip",
     "decision_reason": "",
     "first_seg": "{{EPISODE}}-SEG01",
     "used_by_segs": ["{{EPISODE}}-SEG01"],
     "parent_asset_id": "状态类填父资产；父资产填空",
     "reference_assets": ["父资产排第一，再加画面里必然出现的其它已建资产"],
     "identity_anchors": "绝对不能变的特征",
     "appearance": "详细到可直接作图片提示词；只写这一个主体",
     "allowed_change": "",
     "forbidden_change": "",
     "output_spec": "four_view|scene_wide|prop_multi|closeup|state_asset|look_multiview",
     "coverage": {"face_head": "DEFINED", "front_torso": "DEFINED",
                  "back_torso": "UNDEFINED", "profile": "DEFINED",
                  "arms_hands": "DEFINED", "waist_hip": "DEFINED",
                  "legs": "TEXT_ONLY", "footwear": "UNDEFINED",
                  "rear_full_body": "UNDEFINED"},
     "dependency_order": 1}
  ],
  "costume_contracts": [
    {"costume_id": "COST001", "worn_by_character_id": "C001",
     "materialize": true, "materialize_reason": "标志性造型/复杂层次/跨集复用…",
     "structure": "版型、剪裁、层次顺序", "materials": "",
     "colors": "", "length_and_hem": "", "closure_and_neckline": "",
     "footwear": "鞋履必须写，不许留空", "accessories": "",
     "movement_restriction": "这套衣服限制了什么动作",
     "resulting_look_asset_id": "LK001"}
  ],
  "prop_specs": [
    {"spec_id": "PS001", "name": "", "appearance": "同款外观，只管长什么样",
     "readable_text_policy": "画面内不得出现可读文字 / 或指明必须可读的内容"}
  ],
  "prop_instances": [
    {"instance_id": "PI001", "spec_id": "PS001",
     "why_separate": "为什么它必须是独立实体而不是同一个的两个状态",
     "initial_holder": "", "initial_zone": "",
     "lifecycle": "creation|world_entry|destruction|consumption|split|merge"}
  ],
  "prop_sets": [
    {"set_id": "PSET001", "spec_id": "PS001", "untracked_count": "一摞/若干",
     "materialize_on": "什么事件发生时才物化成具体实例"}
  ],
  "dynamic_elements": [{"name": "", "reason": "为什么不单独生产"}],
  "skipped": [{"name": "", "reason": ""}],
  "production_order": ["按 dependency_order 排好的 asset_id 序列"],
  "reuse_note": "这一集沿用了前面哪些集的哪些资产"
}
```

## 输入

【项目参数】
{{PARAMS}}

【故事真相（第一环节）】
{{TRUTH}}

【人物与世界规则（第二环节）】
{{RULES}}

【本集叙事结构（第三环节）】
{{NARRATIVE}}
