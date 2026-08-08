# 第六环节｜段落资产绑定

> **本次只处理 {{EPISODE}} 这一集。** 下面给的输入都是这一集的，所有段落 id 必须是 `{{EPISODE}}-SEGnn`；
> 集号写错，后面的绑定、分镜、出图出片会全部对不上号。


本环节**不生产新资产**，只决定每个段落使用哪些资产、以什么顺序上传。

## 空间母资产继承（强制）
读取资产清单里的 `space_masters` 和 `space_continuity_chains`。同一空间的相邻SEG必须沿用
相同区域、入口出口、连接方向和大型固定物体；上一SEG退出位置必须接到下一SEG进入位置。
空间母资产是结构记录，不作为参考图片上传，上传其对应的基础场景资产。

## 人物空间连续性继承（强制）

读取资产清单中的 `character_space_bindings`。当前 SEG 有直接记录时原样绑定；上一 SEG 的记录通过
`inherit_to_seg` 指向当前 SEG 时，也必须继承其退出位置、朝向、固定物关系和人物相对位置。
没有 `change_trigger` 或剧情明确移动过程时，不得自行改变站位、坐卧状态、身体朝向、视线方向或人物关系。
这类记录是结构化连续性约束，不作为参考图片上传；若环节4另建了对应状态资产，再按普通状态资产加入参考图。
当记录的 `continuity_state_asset_id` 非空时，该状态资产必须加入当前 SEG 的 `reference_images`，
并在 `character_space_context` 原样保留此 ID，不能只保留人物基础身份图而丢掉已生产的空间状态。

## 参考图优先级
1. 人物或对象身份资产 → 2. 连续性状态资产 → 3. 场景资产 → 4. 道具资产 → 5. 提示词控制的临时动态

## 冲突处理（强制）
- 身份资产与状态资产的**脸**不一致 → 以身份资产为准
- 身份资产与状态资产的**姿态或持续状态**不一致 → 以状态资产为准
- 场景参考与状态图中的**建筑**不一致 → 以基础场景资产为准
- 道具父资产与本段状态不一致 → 以本段指定状态为准

## 编号规则
**每个段落重新从 Image 1 开始编号**；不建立整集永久编号，避免上传大量无关资产。单段参考图控制在 5 张以内为宜（过多会导致身份混合）。

## 输出 schema

```json
{
  "bindings": [
    {
      "id": "{{EPISODE}}-SEG01",
      "reference_images": [
        {"image_n": 1, "asset_id": "C001", "control_scope": "该图控制什么（身份/姿态/空间/道具外观）"}
      ],
      "priority_note": "本段的优先级或冲突说明，可空",
      "space_context": {
        "space_master_id": "SP001；不涉及填空",
        "space_region_id": "A；不涉及填空",
        "entry_from": "从哪个区域或入口进入",
        "exit_to": "从哪个区域或出口离开",
        "fixed_orientation": "必须继承的方向和固定结构"
      },
      "character_space_context": [
        {
          "character_asset_id": "C001",
          "continuity_state_asset_id": "对应状态资产ID；未生产时填空",
          "position": "当前SEG中的精确位置",
          "facing": "身体朝向与主要视线方向",
          "fixed_object_relations": [
            {"object_asset_id": "P001；没有资产ID时填空", "object_name": "", "relation": ""}
          ],
          "relative_character_positions": [
            {"character_asset_id": "C002", "relation": ""}
          ],
          "entry_state": "从直接记录或上一SEG退出状态继承的进入位置与姿态",
          "exit_state": "本SEG结束时的位置与姿态",
          "inherited_from_seg": "从哪个SEG继承；没有则填空",
          "inherit_to_seg": "下一SEG编号；没有则填空",
          "inheritance_rule": "必须延续到下一SEG的内容",
          "change_trigger": "允许空间变化的剧情原因；没有则填空"
        }
      ],
      "entry_state_assets": ["进入状态用到的资产ID"],
      "exit_state_assets": ["退出状态用到的资产ID"],
      "excluded_assets": ["本段明确不应上传的无关资产ID及原因"]
    }
  ]
}
```

## 输入

【本集段落表】
{{SEGMENTS}}

【本集状态时间线】
{{STATES}}

【资产清单】
{{ASSETS}}
