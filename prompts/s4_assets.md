# 第四环节｜资产提取与生产判断

> **本次只处理 {{EPISODE}} 这一集。** `used_by_segs` 里只写 `{{EPISODE}}-SEGnn`，
> 不要写别的集的段落。但**资产库是全剧共享的**，见下面「跨集复用」——
> 前面几集已经建过的对象，必须沿用原编号，不能重新建。

## 五类资产
1. **身份资产**（固定"这个对象是谁"）：主要人物、重要配角、固定群体、特殊生物、长期坐骑或召唤物
2. **环境资产**（固定"故事发生在哪里"）：长期场景、反复出现的房间、村庄、街道、宫殿、战场、系统空间
3. **固定剧情道具**（固定"重要物品长什么样"）：武器、文件、信物、产品、车辆、容器、系统界面、特殊标志
4. **连续性状态资产**：固定单个对象当前状态；必要时也可固定多个已建资产之间的关键关系
5. **动态元素**（通常不生产，由提示词控制）：普通尘土、烟雾、临时光线、雨水、火花、衣摆/头发摆动

## 动态元素升级为连续状态资产的条件（满足任一即升级）
跨越多个段落持续 / 后续剧情依赖 / 状态不可逆 / 容易被模型错误恢复 / 需要固定精确位置 / 影响人物道具场景关系

## 连续性状态资产分为两种（强制）

### A. 单体连续性状态资产：`state_type: "single"`

只固定一个已有对象自身发生的变化，例如人物受伤/换装、场景破坏、道具断裂。

- `parent_asset_id` 必须填唯一父资产。
- `reference_assets` 必须以父资产开头，通常只有父资产这一张。
- `appearance` / `allowed_change` 只写父资产自身的变化，不写其他主体的位置和动作。
- 不得改变父资产的身份、面孔、身体比例、基础服装、场景结构或道具基础模型。

### B. 组合连续性锚点：`state_type: "composite"`

固定两个或以上**已经建好**的资产之间、跨多个镜头或段落持续的关键关系。
它不是任何一个对象自身的变化，因此：

- `parent_asset_id` 必须填空。
- `reference_assets` 至少两个，按画面控制优先级列出全部来源资产。
- `output_spec` 必须是 `state_composite`。
- 只允许固定位置、姿势、距离、面向、接触、持有关系、固定座位或群体阵型；
  禁止重新设计任何来源资产。

优先建立组合锚点：多人固定关系跨多个SEG持续；扶/抱/背/抓/控制/骑乘等高风险接触；
围堵、围桌、座位、人墙、战斗阵型；复杂动作结果容易穿模；道具容易换手；或实测已经
发生身份交换、人数变化、站位漂移。普通对话、并排站立、一次性低风险动作仍交给环节7。

如果单体变化和多主体关系同时存在，**分别建立**单体状态资产和组合锚点，不要塞进一条。

示例：

```json
{
  "asset_id": "CA001",
  "category": "state",
  "state_type": "composite",
  "parent_asset_id": "",
  "reference_assets": ["S001", "C002", "C005", "P001"],
  "appearance": "固定Rizky在病床左侧、Dewi位于右侧床边递交复印件的持续关系",
  "allowed_change": "位置、姿势、距离、面向、接触和持有关系",
  "forbidden_change": "所有来源资产的身份、外观、基础结构、颜色和材质",
  "output_spec": "state_composite"
}
```

`scene_wide` 始终是严格空镜；需要人物参与的关系资产必须使用 `state_composite`，
不能把人物塞进空镜，也不能为了迁就空镜规格删掉有生产价值的关系。

## 生产顺序（五批，依赖资产必须先完成）
1 主要人物与固定群体 → 2 特殊生物与坐骑 → 3 基础场景 → 4 固定剧情道具 →
5 单体连续性状态资产 → 组合连续性锚点

## 输出 schema

```json
{
  "assets": [
    {
      "asset_id": "C001",
      "category": "identity|environment|prop|state|dynamic",
      "state_type": "非state填空；state只能填single|composite",
      "batch": 1,
      "name": "",
      "parent_asset_id": "single填唯一父资产ID；composite和基础资产填空",
      "reference_assets": ["single先填父资产；composite填至少两个来源资产；基础资产填空数组"],
      "decision": "must|conditional|skip",
      "decision_reason": "",
      "used_by_segs": ["{{EPISODE}}-SEG01"],
      "identity_anchors": "跨版本绝对不变的特征",
      "appearance": "详细到可直接作图片提示词的描述",
      "allowed_change": "允许改变的内容",
      "forbidden_change": "禁止改变的内容",
      "output_spec": "four_view|scene_wide|prop_multi|closeup|state_composite"
    }
  ],
  "skipped": [{"name": "", "reason": "为什么不生产（走提示词控制）"}]
}
```

> 只有 `decision` 为 `must` 或 `conditional` 的资产会进入第五环节生产。配角、群演、临时道具应判为 `skip` 并写进 `skipped`。

## 跨集复用（强制）

资产库是**全剧共享**的，不按集重建。下面的【已有资产】列出前面几集已经建好的资产：

- 同一个对象**必须沿用已有的 `asset_id`**，绝对不要为它新建一个编号。
  同一个角色在 EP01 是 `C001`，在 EP07 也必须是 `C001` —— 换了编号就会另出一张脸，
  跨集人物就不一致了，这是整套流程最不能出的错。
- 沿用时 `identity_anchors`、`appearance`、`state_type` 和 `reference_assets` 也照抄已有的，
  不要重写、不要"润色"。
  本集要是有合理的外观变化（换装、受伤、复原），**不要改身份资产**，
  另建一条 `category: "state"` 的状态资产挂在它下面。
- `used_by_segs` 只填本集用到它的段落，不用累计前面几集的。
- 只有本集真正新出现的对象才新建编号，编号接着已有的往下排，不要撞号。

人物编号优先跟【全局解析】里 `characters[].id` 对齐（那份是全剧的）。

## 输入

【全局解析】
{{GLOBAL}}

【已有资产（前面几集已建好，同一对象必须沿用其 asset_id）】
{{KNOWN_ASSETS}}

【本集段落表】
{{SEGMENTS}}

【本集状态时间线】
{{STATES}}
