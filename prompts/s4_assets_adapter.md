
---

# 程序传输适配（不改变上文业务规则）

上文 TXT 是本环节唯一业务规范，必须逐条执行，不得套用“基础资产 + 连续性锚点”等其他模型。
上文定义本环节必须完整覆盖的语义内容，下面的 JSON Schema 定义这些语义内容
在程序中的承载结构。请逐项完成上文要求，将结果完整映射到对应 JSON 字段，最终返回
一个符合下面 Schema 的 JSON 对象。

## 依赖图必须可生产（强制）

- 每个 `reference_assets` 只能指向已经排在自己之前、能够先完成生产的资产。
- 同一生产层级的资产不得互相引用；严禁 A 引用 B、同时 B 又引用 A。
- 后续状态需要继承前一状态时，必须给后续状态更大的 `dependency_order`，形成单向链。
- 复杂状态引用多个来源时，全部来源都必须位于更早层级；不得为了补齐画面反向引用本状态或同级状态。
- 整个父资产与状态资产依赖图必须是有向无环图。无法确定先后时，回退到基础父资产，不能制造循环等待。

## 人物空间连续性 JSON 映射（强制）

- 上文要求的人物空间连续性必须逐人、逐 SEG 写入 `character_space_bindings`，不得只藏在 `appearance`、`story_function` 或自然语言说明中。
- `character_asset_id` 使用人物基础身份资产 ID；若该绑定满足上文生产条件并建立了连续性状态资产，必须在 `continuity_state_asset_id` 写入对应状态资产 ID，否则填空。`space_master_id` 与 `space_region_id` 必须指向已有或本次建立的真实空间母资产及区域。
- `position`、`facing`、与固定物体关系、人物相对位置、SEG 退出状态及下一 SEG 继承规则必须分别落入对应字段，不得合并省略。
- `inherit_to_seg` 只允许指向本集 SEG；没有跨段继承时填空字符串。无剧情触发的相邻 SEG 不得让位置、朝向或人物关系跳变。
- 这张表是供后续环节读取的结构化连续性记录，本身不等于必须生产图片。只有满足上文生产判断的空间状态，才另外建立 `category: "state"` 的资产；不要把每个普通站位都变成图片资产。

```json
{
  "assets": [
    {
      "asset_id": "C001",
      "category": "identity|group|creature|environment|prop|state|dynamic",
      "asset_type": "人物|群体|生物|基础场景|车辆|道具|连续性状态|动态元素",
      "name": "",
      "asset_level": "",
      "decision": "must|conditional|skip",
      "decision_reason": "",
      "first_seg": "{{EPISODE}}-SEG01",
      "used_by_segs": ["{{EPISODE}}-SEG01"],
      "parent_asset_id": "状态资产填写父资产；其他资产填空",
      "reference_assets": ["状态资产填写父资产及全部依赖资产；其他资产填空数组"],
      "space_master_id": "所属空间母资产编号；不涉及填空",
      "space_region_id": "所属区域编号；不涉及填空",
      "identity_anchors": "身份或基础结构绝对不能改变的内容",
      "appearance": "固定外观、结构、材质和当前状态",
      "fixed_content": ["上文要求固定的内容"],
      "story_function": "剧情功能",
      "state_changes": ["状态变化链；没有则空数组"],
      "allowed_change": "",
      "forbidden_change": "",
      "output_spec": "four_view|scene_wide|prop_multi|closeup|state_asset",
      "dependency_order": 1
    }
  ],
  "space_masters": [
    {
      "space_id": "SP001",
      "name": "",
      "decision": "must|conditional|skip",
      "used_by_segs": ["{{EPISODE}}-SEG01"],
      "regions": [
        {"region_id": "A", "name": "", "environment_asset_id": "S001", "fixed_features": []}
      ],
      "connections": [
        {"from": "A", "to": "B", "via": "门/走廊/电梯/楼梯/道路", "direction": "", "bidirectional": true}
      ],
      "fixed_directions": [],
      "main_entries": [],
      "main_exits": [],
      "fixed_large_objects": [],
      "movement_paths": [
        {"used_by_segs": ["{{EPISODE}}-SEG01"], "path": ["A", "B"], "continuity": ""}
      ],
      "forbidden_changes": []
    }
  ],
  "identity_asset_ids": [],
  "group_asset_ids": [],
  "space_master_ids": [],
  "environment_asset_ids": [],
  "vehicle_and_prop_asset_ids": [],
  "state_asset_ids": [],
  "dynamic_elements": [{"name": "", "reason": "不单独生产的原因"}],
  "reuse_relations": [{"asset_id": "", "reused_in": [], "rule": ""}],
  "parent_state_dependency_chains": [
    {"parent_asset_ids": [], "trigger": "", "state_asset_id": ""}
  ],
  "space_continuity_chains": [
    {"from_seg": "", "space_id": "", "exit_region": "", "to_seg": "", "entry_region": ""}
  ],
  "character_space_bindings": [
    {
      "character_asset_id": "C001",
      "character_name": "",
      "continuity_state_asset_id": "对应的连续性状态资产ID；未生产时填空",
      "seg_id": "{{EPISODE}}-SEG01",
      "space_master_id": "SP001",
      "space_region_id": "A",
      "position": "人物在空间中的精确位置",
      "facing": "身体朝向与主要视线方向",
      "fixed_object_relations": [
        {"object_asset_id": "P001；没有资产ID时填空", "object_name": "", "relation": ""}
      ],
      "relative_character_positions": [
        {"character_asset_id": "C002", "relation": ""}
      ],
      "exit_state": "本SEG结束时的位置、朝向和姿态",
      "inherit_to_seg": "{{EPISODE}}-SEG02；不继承时填空",
      "inheritance_rule": "下一SEG必须继承的空间内容",
      "change_trigger": "发生空间变化所需的剧情原因；没有则填空"
    }
  ],
  "must_produce_asset_ids": [],
  "conditional_asset_ids": [],
  "skipped": [{"name": "", "reason": ""}],
  "production_order": [],
  "output_register": {
    "identity_count": 0,
    "group_count": 0,
    "space_master_count": 0,
    "environment_count": 0,
    "vehicle_count": 0,
    "prop_count": 0,
    "state_count": 0,
    "must_count": 0,
    "conditional_count": 0,
    "skip_count": 0,
    "high_risk_assets": [],
    "high_risk_spaces": [],
    "cross_seg_spaces": [],
    "irreversible_states": []
  }
}
```

## 本次输入

本次只处理：`{{EPISODE}}`。所有 SEG 编号必须属于这一集。

【全剧解析】
{{GLOBAL}}

【已有资产；同一对象沿用原 asset_id】
{{KNOWN_ASSETS}}

【已有空间母资产；同一大型地点沿用原 space_id、区域与方向】
{{KNOWN_SPACES}}

【本集15秒段落表】
{{SEGMENTS}}

【本集状态时间线】
{{STATES}}
