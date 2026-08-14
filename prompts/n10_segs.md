# 第十环节｜SEG 包装

> **本次只处理 {{EPISODE}} 这一集。**

把已经设计好的镜头装进**固定时长的生产容器**。视频模型一次只能生成
{{DURATION}} 秒，所以要装箱。

**SEG 是技术容器，不是叙事单位。** 场次和节拍是剧情决定的，SEG 是装箱决定的。
**绝对不许倒过来**：不许因为「这一段只剩 3 秒」就砍掉一个转折。

## 〇、这一集该装出几箱

本集总时长 **{{EPISODE_DURATION}} 秒**，一箱 {{DURATION}} 秒
→ 预计 **{{SEGMENTS_TARGET}} 箱**（{{SEGMENTS_WHY}}）。

这是预期值，不是硬指标 —— 边界校正之后多一箱少一箱都正常。
但**只装出 1 箱是不正常的**（除非本集本来就只有一箱那么长）：
那说明第九环节把整集压进了一个容器，得回去重跑第九环节，
不要在这里将就装箱。八个场次挤在一箱里，往下每一步都会崩。

## 一、装箱规则

按镜头顺序往容器里放，放满 {{DURATION}} 秒就换下一个。
但边界要**校正**，不能硬切在任意位置。

### 边界必须避开的五种情况

1. **把一个动作的准备和接触拆到两次生成里**
   （伸手去抓在这一段，抓住在下一段 —— 下一段的模型不知道刚才伸的是哪只手）
2. **起因在这一段、结果由下一段重新决定**
3. **在一句关键台词中间切断**
4. **让下一段重新初始化人物、道具或空间**
5. **把一次原生转场拆到两次生成里** ← 这条最要紧，见下

### 边界应该放在哪

**转场开始之前的稳定状态，或者转场完成之后的稳定状态。**

## 二、转场必须完整归属一个 SEG

项目是 `MODEL_NATIVE_ONLY` —— 一次生成出一整段带转场的成片，不做外部拼接。

所以一次转场的这四部分必须在**同一个 SEG 里**：

```
切走的动作 + 转场窗口 + 状态切换点 + 接进来的建立
```

**跨 SEG 的转场意味着需要外部拼接**，和 `MODEL_NATIVE_ONLY` 直接冲突。

### 转场吃掉的时间算在 SEG 里

一次遮挡转场占 1.2 秒，这 1.2 秒就是从 {{DURATION}} 秒里出的，
不能额外附加。装不下的时候，按这个顺序调整：

1. 重新分配镜头时长（砍掉可有可无的停顿）
2. 调整 SEG 边界
3. 减少非关键镜头

**不许压缩关键动作、对白或状态成立**到看不清的程度。
三条都调完还装不下，说明镜头设计本身太密，回第九环节减镜头。

## 三、起因和结果尽量在同一段

```
枪响 → 中枪成立 → 这一段结束时人已经受伤
```

下一段从**受伤状态继续**，不重演开枪。

只有叙事本身要求延迟揭晓时，才允许起因和可见结果分开 ——
那时候客观事实仍然要在总账里写明。

## 四、进入和退出状态

每个 SEG 冻结：

- `entry_cvs` —— 这一段开始时的世界状态（必须等于上一段的 `exit_cvs`）
- `exit_cvs` —— 结束时的世界状态

**接不上就是装箱错了。** 上一段结束时她站在门口，这一段不能从床边开始，
除非中间有一个 SEG 表现了她走过去。

## 五、平行线程

同一段里有多条现实线程（现实/回忆/梦境）时，每条写清自己的进入状态。

**画面最后停在 A 线，不代表 B 线被重置。** 下一段从 B 线开始时，
读的是 B 线自己的最新状态。

## 输出 schema

```json
{
  "episode": "{{EPISODE}}",
  "seg_duration": {{DURATION}},
  "segs": [
    {"seg_id": "{{EPISODE}}-SEG01",
     "duration": {{DURATION}},
     "story_time_range": "",
     "included_scenes": ["SC01"],
     "included_beats": ["SC01-B1", "SC01-B2"],
     "included_shots": ["SH_{{EPISODE}}_001", "SH_{{EPISODE}}_002"],
     "entry_thread": "RT_MAIN",
     "entry_cvs": "CVS_{{EPISODE}}_SC01_01",
     "exit_thread": "RT_MAIN",
     "exit_cvs": "CVS_{{EPISODE}}_SC01_02",
     "active_thread_states": [
       {"thread_id": "RT_MEMORY", "latest_state": "这条线当前停在哪；没有填空"}
     ],
     "primary_dramatic_task": "这一段要完成的一件事",
     "state_change_ownership": ["这一段负责让哪几个状态成立"],
     "dialogue": "本段全部台词，按顺序",
     "sound_plan": "环境音、音乐提示、声音过渡",
     "model_native_transition_ids": ["TR_{{EPISODE}}_001"],
     "transition_ownership": [
       {"transition_id": "TR_{{EPISODE}}_001",
        "exit_action_in_seg": true, "window_in_seg": true,
        "switch_point_in_seg": true, "entry_establish_in_seg": true,
        "time_cost": 1.2}
     ],
     "time_budget": {"shots": 13.8, "transitions": 1.2, "total": 15.0},
     "boundary_rationale": "为什么边界切在这里（落在哪个稳定状态上）"}
  ],
  "boundary_adjustments": [
    {"seg_id": "", "problem": "装不下什么", "action": "按 1/2/3 哪一条调的",
     "what_was_cut": "砍了什么；没砍关键内容"}
  ],
  "continuity_check": [
    {"from_seg": "{{EPISODE}}-SEG01", "to_seg": "{{EPISODE}}-SEG02",
     "exit_cvs": "", "entry_cvs": "", "matches": true,
     "note": "接不上的话写清缺了什么"}
  ],
  "total_segs": 0,
  "total_duration": 0
}
```

## 输入

【项目参数】
{{PARAMS}}

【本集镜头与剪辑时间（第九环节）】
{{SHOTS}}
