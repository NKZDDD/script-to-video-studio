# 第一环节｜整剧全局解析

先理解整部剧本，再处理单集和单段，防止局部优化破坏后续剧情。

## 解析范围标记（必须判定）
- 提供了完整系列 → `full_series + canonical`
- 只有单集 → `full_episode + provisional`（暂定，后续集数到位需复核）
- 测试片段 → `test_sample + local_only`

## 集边界识别（`episode_ranges`，最重要的一项）

剧本文件的写法千差万别：可能写「第 1 集」「EP1」「Episode 01」「第一集」，
也可能只用空行、标题样式或场次编号分隔，还可能整部剧本前面挂着一大段
项目推介、人物小传、卖点分析之类**不是剧本正文**的内容。

你要做的是**看懂这一份的写法**，然后：

1. 找到剧本正文真正开始的位置，把前面的推介/简介/说明部分排除掉
   （这些内容可以拿来判断视觉基调和人物设定，但不属于任何一集的正文）。
2. 逐集给出 `start_anchor`：**该集正文第一行，从原文逐字照抄**。
   - 必须是原文里**真实存在、且唯一**的一整行，程序要靠它精确切分。
   - 不要改写、不要补全、不要翻译、不要加编号，连空格和标点都照原样。
   - 如果某集的第一行在全文中重复出现，往下多抄一行，直到这段文本唯一。
3. 只有一集时也要给一条；确实分不出集（如测试片段）就给空数组。

**这一步定错了，后面所有环节都会跟着错**——集切歪了，段落、状态、分镜全部错位。
拿不准某处是不是新一集开始时，宁可合并，不要凭猜测切开。

## 输出 schema

```json
{
  "project_name": "",
  "scope": "full_episode + provisional",
  "story_type": "",
  "visual_style": "（若项目参数已指定则原样采用，否则据剧本判断）",
  "worldview": "",
  "era": "",
  "power_system": "",
  "main_conflict": "",
  "main_line": "",
  "characters": [
    {"id": "C001", "name": "", "role": "主角/重要配角/群体/生物",
     "appearance": "外貌服装的详细描述，要详细到可直接作图片提示词",
     "faction": "", "goal": "", "arc": "", "episodes": ""}
  ],
  "relations": [{"from": "C001", "to": "C002", "relation": ""}],
  "scenes": [{"id": "S001", "name": "", "description": "环境/光线/陈设的详细描述"}],
  "props": [{"id": "P001", "name": "", "description": "", "is_key": true}],
  "foreshadowings": [{"item": "", "setup_at": "", "payoff_at": ""}],
  "immutable_facts": ["不可更改的剧情事实"],
  "long_term_states": ["需要长期维护的连续状态"],
  "episode_ranges": [
    {"episode": "EP01", "title": "本集标题（没有就填空）",
     "start_anchor": "该集正文第一行，从原文逐字照抄，必须唯一",
     "range": "人看的范围描述，如「开场到雨夜弃局」",
     "entry_state": "", "exit_state": ""}
  ],
  "visual_tone": {
    "type_position": "", "atmosphere": "", "color_system": "",
    "lighting": "", "character_texture": "", "scene_texture": "",
    "camera_tendency": "", "quality": "", "forbidden": "", "reference_taste": "",
    "compressed": "一行压缩版，供所有图片/视频提示词的【全局风格】直接引用",
    "compressed_variants": [{"scope": "适用段落范围或线名", "text": ""}]
  }
}
```

> `compressed_variants` 用于同一剧存在刻意对立的视觉线（如底层线 vs 顶层线）；只有一条线时给空数组。
> 人物 `appearance` 与场景 `description` 会被后续环节直接复制进图片提示词，务必写死稳定特征（年龄、脸型、发型发色、服装款式颜色材质、体态气质）。

## 输入

【项目参数】
{{PARAMS}}

【完整剧本】
{{SCRIPT}}
