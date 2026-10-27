"""Shared writing rules matching the existing reference identity reader."""

MARKER = "<!-- studio-reference-punctuation-v1 -->"

# A documentation revision, independent of the installed EXE and project locks.
REVISION = "2026.10.27.2"
SCOPE_MARKER = "<!-- studio-contract-scope-2026-10-27-2 -->"
SCOPE = f"""{SCOPE_MARKER}
## 契约版本与模式适用范围

契约说明版本：{REVISION}。独立单图交接要求 Studio 2026.10.27.1 或更高版本；说明版本不作为客户端升级门槛，也不增加 JSONL manifest 必填字段。

先按项目已确认的交接模式读取对应规则。新建 Liam-2 / Liam-3 项目采用已确认的 c_then_a_b 时使用下表第一行；旧项目不因更新契约自动切换工艺。没有交接需求时不强制建立交接图。

下表的旧整板流程用于对应旧电影级项目；通用十二环节原有故事板流程继续保留，实际目录以本契约的「输出目录与分类」为准。

| 模式 | 图片标记与依赖 | 视频引用 | 输出分类 |
|---|---|---|---|
| c_then_a_b：同版 C → 独立 A/B | C/A/B 都填 handoff_mode=c_then_a_b、view=C/A/B；A/B 同 boundary、同 c_key，分别直接引用同版 C；不填 region/board/parent | OUT_A→A、IN_B→B、IN_C→C，不填 board | HANDOFF，04_交接图/C、A、B；C 不是 SCSTATE |
| independent_c：独立 C → AB 双联板 → 区域 A/B | 仅 C 填 handoff_mode=independent_c（兼容 scene_c）；AB 整板引用 C，区域 A/B 通过 region/board 引用整板 | OUT_A/IN_B 引区域 A/B，IN_C 引对应独立 C | C 在 03b_场景状态图；整板和区域在 04_交接板 |
| 原 ABC 整板 → 区域 A/B/C | 不填新 handoff_mode；区域通过 region/board 引用同版整板 | OUT_A/IN_B/IN_C 引对应区域 | 04_交接板及其区域图目录 |

同一 c_then_a_b 交接边界的 A/B/C 是同一时刻现场的不同取景；一段视频的入站 B/C 与出站 A 可以属于不同边界、不同时间。三个 role 表示用途，各自最多一张，不能解释成三个必然不同的时刻。

输出格式、字段、目录和 Image N 映射按本次契约及对应 Schema；旧技能中的整板、区域提取、容器尺寸条款仅用于相应旧模式。若技能快照或旧正文与上表冲突，按实际模式适用本节与对应模式章节，不混用字段，不改写项目锁、历史任务或成品。

参考图行统一为 Image N = 实际ID 身份描述，ID 后用空格，保持真实编号、身份、用途和上传顺序。JSONL 的 key 与发布包的资源 asset_id 按各自格式填写。

Agent 的导演设计、人物资产、七段式视频提示词、内部设计台账及材料审核继续执行。Studio 不要求交付中间文件，不代表 Agent 可以省略内部工作。内部审核器若只识别旧格式，应对齐适配并报告未验证项，不能伪造外部字段、把旁白改成对白或绕过审核。内部通过与 Studio 实际导入通过分别记录。
"""

RULES = """## 参考图身份映射与标点
<!-- studio-reference-punctuation-v1 -->

每张参考图单独起一行，统一写成 `Image N = 资产ID 身份描述`。
资产 ID 后用空格接身份描述；必须在同一行、首个中文/英文分号（；或 ;）、中文句号（。）或换行之前，说清这张图是谁或是什么。
不要写成 `Image 1 = CHAR_A；成年男性`，也不要把身份描述另起一行。不能只写编号或只写“控制服饰”。

标点用途：逗号连接身份、年龄、服装等描述；分号可在身份描述之后分隔控制范围；中文句号结束句子；换行分隔映射行和后续说明。
这不是对提示词全文的切句或截断规则：程序读取完整提示词，标点边界仅用于此处的身份校验。后续状态、控制范围等说明仍保留在提示词中。

合规示例（示例 ID 必须替换为本项目实际 ID）：
```text
Image 1 = CHAR_A 傅凛成，29岁成年男性，灰色运动装；只控制人物身份与服装，不复制背景。
Image 2 = LOC_A 派出所调解室，固定房间几何与陈设；只控制空间和陈设。
Image 3 = PROP_A 黑色翻盖手机，银色边框；只控制道具外观。
```

编号和资产 ID 必须与实际上传顺序、任务参考图一致；视频先 handoff_refs 后 reference_images，连续编号。
修订旧提示词时，保留原项目编号、生产路径和锁定选项，仅在受影响任务中调整映射行的标点与身份表述；不要全篇替换标点，不改变剧情、对白和其他生成要求。身份不明时先核对项目资产资料，不编造身份或调换参考图。
补充契约不会自动改写已有提示词。Agent 发布项目必须生成新发布版本及对应文件散列；使用 Studio 直接编辑时保存新版本。不要覆盖已接收的发布、旧成品或运行中批次的提示词快照。
"""
