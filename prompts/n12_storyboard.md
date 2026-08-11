# 第十二环节｜故事板包编译

> **本次只编译这一段：{{SEGMENT}}。** 其它段不用管。

把这一段编译成**一个故事板包**：有序的关键帧、每帧的合同、转场锚点，
以及一段可直接投喂出图模型的完整提示词。

## 一、故事板 = 场景状态图 + 新的观察

```
SCSTATE（世界是什么样）
+ 新的机位观察
        ↓
    关键帧
```

**SCSTATE 决定**：世界状态、身份、当前造型、几何、稳定走位、道具状态。
**故事板决定**：景别、机位、角度、构图、景深、焦点、动作阶段。

两条硬规矩：

- **不许复制 SCSTATE 的中性机位。** 每一格必须是**新的观察**，
  否则等于把同一张图重画几遍。
- **不许重新融合人物、场景、道具和状态。** 那些已经在 SCSTATE 里定好了。

机位必须落在第五环节登记过的观察方向里。需要一个没登记过的方向时，
把那一格标成 `NEW_VIEW_REQUIRED` 并说明 —— **不要在这里第一次发明
房间另一侧长什么样**。

## 二、格数是动态的

**不要凑满 3×3。** 3×3 是常用的高密度上限，不是必须填满的格子。
5 格、6 格、8 格够用就用几格。

每一格必须**新增**点什么：新的动作阶段、新的信息、新的反应、
新的空间关系、新的状态价值。
**没有新增价值的格子一律删掉** —— 重复的情绪格只会稀释信息。

一段放不下时用**续页**：

```
SBPKG_{{SEGMENT}}
├── SHEET_A: KF01-KF06
└── SHEET_B: KF07-KF11
```

两张页**不构成两套真相**，不许重新编号、重新设计或改顺序。

## 三、每一格要写什么

关键的几项：

- `action_phase` —— 从这个词表里选，**相邻两格不能重复同一个阶段**：

  ```
  动作前 / 动作开始 / 过渡 / 首次接触 / 动作完成 / 动作后 / 反应 / 稳定退出状态
  ```

  到了「动作完成」之后，后面只能是「动作后 / 反应 / 稳定退出」——
  **签完字不能再签一次，跌倒完不能再跌一次，拔完针不能再拔一次。**

- `visible_states` —— 这一格里哪些持续状态明确可见、哪些部分可见、
  哪些被遮挡但仍存在、哪些在画外、哪些**尚未激活（禁止出现）**

- `entity_world_xyz` —— 人物和关键道具的世界坐标。
  **切镜不许静默改变真实位置** —— 机位只是投影，不是重排世界。

- `prop_instance` / `visibility_bucket` / `count_lock` —— 用实例编号追踪，
  外观规格只提供长什么样。遮挡、离画、装进容器**都不改变存在数量**。

- `temporal_position` —— 写「本段早段、受伤事件之后、治疗完成之前」这种相对位置，
  **不要写绝对秒数** —— 绝对秒数在第十三环节冻结，两边都写会打架。

## 四、转场锚点

**硬切**只需要三样：切走那一格、精确切点、接进来那一格。
并且必须写明 **`不许在两个机位之间生成连续运镜`** ——
不写的话模型会把两个机位平滑地连起来，那就不是切了。

**遮挡 / 甩镜 / 光学**转场在执行上不可替代时，才增加中间锚点：

```
切走的那一格 → 触发帧 → 遮挡最深 / 模糊最强 / 光最亮的那一帧 → 接进来的那一格
```

中间这些帧是**执行锚点，不是世界状态**。默认标
`世界真相权力 = 无` —— 不许把遮挡帧理解成一个新的地点或新的人物状态。

## 五、参考图角色映射（Image 编号必须写）

**出图模型收到的是 N 张没有标签的图。** 它不认识 `SCST_EP01_SC01_01`
这个编号，只知道第 1 张、第 2 张。

默认的参考图顺序：

```
Image 1 = 本段的 SCSTATE（主参考）
Image 2.. = 只在 SCSTATE 覆盖不足时补：关键道具的可读细节、
            特殊服装细节、需要加强的人物身份
```

**默认不要把所有原子资产再传一遍** —— 那正是以前参考图互相打架的原因。

逐张写，编号顺序**严格等于 `reference_order` 的顺序**，六项写全：

```
Image 1 = SCST_{{EPISODE}}_SC01_01 本段场景状态图
  是谁/是什么 + 画面可见内容：本段稳定世界状态的中性验证图，
    可见 C001 与 C005 两人、病床、监护仪；C001 坐于床沿，C005 立于床尾
  故事时间 / 当前状态：{{SEGMENT}} 起始稳定状态，事件触发之前
  有权控制：人物身份、当前造型与状态、道具状态与持有人、空间几何、稳定走位
  无权控制：这张图的中性机位、取景和构图；动作阶段、时间、表演
  适用范围：本段全部关键帧的世界状态基准
  MUST TRANSFORM：按每一格的机位、景别、构图重新观察
```

**一张图里有多个人物时**，`是谁/是什么` 必须逐个点名并说清各自在画面里的位置，
不许写成「两个人在病房里」。这一项漏掉的后果是模型张冠李戴 —— 实跑撞过。

硬规矩：有几张写几条、不许跳号、不许合并、`Image N = <ID> <名称>` 两样都要。

【本次参考图上限】{{REF_LIMIT}}

**绝对不能把「本段故事板」自己写进参考图** —— 故事板还没出，
拿它当自己的参考是循环，程序会因为指不到文件而停在出图这一步。

## 六、提示词结构

按这个顺序写：

1. 任务与本包身份（包编号、页范围、格数）
2. 参考图角色映射（上面那套）
3. 参考图防火墙（每张只控制指定的格和维度）
4. 世界规则与全局风格
5. 空间与世界坐标锁
6. 机位权限与视角覆盖状态
7. 逐格执行（每格：编号、镜头、景别、机位、构图、动作阶段、表演、
   可见状态、道具实例、世界坐标）
8. 原生转场合同
9. 连续性与时间锁（哪些必须继承、哪些禁止提前出现）
10. 输出格式（几格、怎么排、格左上角标镜头编号、编号是画面里唯一允许的文字）

## 输出 schema

```json
{
  "episode": "{{EPISODE}}",
  "segment": "{{SEGMENT}}",
  "sbpkg": [
    {"sbpkg_id": "SBPKG_{{SEGMENT}}",
     "sheets": [{"sheet_id": "SHEET_A", "kf_range": "KF01-KF06", "layout": "2行3列"}],
     "kf_count": 6,
     "kf": [
       {"kf_id": "KF01",
        "sheet_id": "SHEET_A",
        "source_scstate": "SCST_{{EPISODE}}_SC01_01",
        "source_cvs": "CVS_{{EPISODE}}_SC01_01",
        "reality_thread": "RT_MAIN",
        "temporal_position": "本段早段、签字之前",
        "shot_id": "SH_{{EPISODE}}_001", "shot_size": "中景",
        "camera_position_xyz": [4.2, 0.8, 1.6], "look_at_xyz": [2.4, 1.2, 1.4],
        "camera_angle": "平视", "composition": "", "visual_focus": "",
        "action_phase": "动作前|动作开始|过渡|首次接触|动作完成|动作后|反应|稳定退出状态",
        "performance": "",
        "visible_states": [
          {"what": "左额伤口", "bucket": "明确可见|部分可见|被遮挡|画外|尚未激活"}
        ],
        "props": [
          {"instance_id": "PI001", "state": "", "holder": "C001", "hand": "右手",
           "container": "", "visibility_bucket": "明确可见"}
        ],
        "count_lock": "PS001：可见1 + 遮挡1 = 2",
        "entity_world_xyz": [{"id": "C001", "xyz": [2.4, 1.2, 0], "yaw": 90}],
        "spatial_id": "SP001", "loc_view": "SP001_V1",
        "view_coverage_status": "COVERED|NEW_VIEW_REQUIRED",
        "camera_reveal_envelope": "这一格镜头运动/人物转身可能显露的最大范围",
        "required_coverage": "COVERED|SUPPLEMENTAL_REFERENCE_REQUIRED|CAMERA_CONSTRAINED",
        "forbidden_future_state": ["这一格绝对不许出现的未来状态"],
        "entry_condition": "", "exit_condition": "",
        "outgoing_transition_id": "TR_{{EPISODE}}_001",
        "incoming_transition_id": "",
        "transition_role": "EXIT|TRIGGER|SHIELD_OR_PEAK|ENTRY|NONE",
        "world_truth_authority": "NONE 或写明例外"}
     ],
     "reference_order": [
       {"image_n": 1, "asset_id": "SCST_{{EPISODE}}_SC01_01",
        "asset_name": "本段场景状态图",
        "who_what_visible": "这张图是谁/是什么 + 画面里能看见什么（必填；多人必须逐个点名并说清各自位置）",
        "story_time_state": "故事时间与当前状态（必填）",
        "must_preserve": "", "must_transform": "",
        "must_not_copy": "", "does_not_control": "",
        "applicable_kf": "全部 或 KF01-KF03"}
     ],
     "transition_contracts": [
       {"transition_id": "TR_{{EPISODE}}_001",
        "mechanism": "NATIVE_CUT", "execution_mode": "MODEL_NATIVE_ONLY",
        "outgoing_kf": "KF03", "incoming_kf": "KF04",
        "cut_at_or_switch_point": "",
        "anchor_frames": ["遮挡式才有：触发帧、遮挡最深帧"],
        "do_not_interpolate_camera": true,
        "forbidden_state_mixing": []}
     ],
     "size": "{{IMAGE_SIZE}}",
     "filename": "SBPKG_{{SEGMENT}}_SHEET_A.png",
     "storyboard_prompt": "完整提示词正文，按上面十部分的顺序写，可直接复制投喂图片模型"}
  ],
  "kf_rationale": "为什么是这个格数（每格新增了什么价值）"
}
```

## 输入

【项目参数】
{{PARAMS}}

【本集镜头与剪辑时间（第九环节）】
{{SHOTS}}

【本集 SEG 装箱（第十环节）】
{{SEGS}}

【本段场景状态图（第十一环节）】
{{SCSTATE}}
