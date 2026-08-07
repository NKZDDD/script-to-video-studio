
---

# 程序传输适配（不改变上文业务规则）

上文 TXT 是本环节唯一业务规范，必须逐条执行，不得套用“基础资产 + 连续性锚点”等其他模型。
上文 A—Q 定义本环节必须完整覆盖的语义内容，下面的 JSON Schema 定义这些语义内容
在程序中的承载结构。请逐项完成 A—Q 要求，将结果完整映射到对应 JSON 字段，最终返回
一个符合下面 Schema 的 JSON 对象。

## 依赖图必须可生产（强制）

- 每个 `reference_assets` 只能指向已经排在自己之前、能够先完成生产的资产。
- 同一生产层级的资产不得互相引用；严禁 A 引用 B、同时 B 又引用 A。
- 后续状态需要继承前一状态时，必须给后续状态更大的 `dependency_order`，形成单向链。
- 复杂状态引用多个来源时，全部来源都必须位于更早层级；不得为了补齐画面反向引用本状态或同级状态。
- 整个父资产与状态资产依赖图必须是有向无环图。无法确定先后时，回退到基础父资产，不能制造循环等待。

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
