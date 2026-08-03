# 剧本 → AI视频 自动化生产台

skill《AI短剧自动化生产体系 V6.1》的**可执行具象化**：本地 Web 程序，编排逻辑是确定性代码，
只有剧情分析走 LLM API。前端选服务商 / 设并发，后端多线程跑，产物落盘成标准生产包。

```
Claude Code / Codex（skill 迭代设计）
        │  升级后把规则编译进 prompts/
        ▼
   本地生产台（本程序）───▶ 标准生产包
        ▲                        │
        └──── 拉回目录做验收优化 ◀┘
```

## 启动

```bash
pip install -r requirements.txt      # 只有 requests；Pillow/imageio-ffmpeg 可选
python run.py                        # 浏览器自动打开 http://127.0.0.1:8770
```

首次使用：**设置**页填服务商 key → 点「运行自检」（只拉模型列表，零生成费用）→ 四项全绿即可开工。

## 五个页签

| 页签 | 做什么 |
| --- | --- |
| **项目** | 项目列表 / 新建项目（贴剧本正文，自动建标准目录骨架） |
| **流程** | 项目参数 + 十二环节。环节 1-8 点「执行」调 LLM 分析，产物落 `01_剧本与分段/*.json` |
| **生产** | 资产图 / 故事板 / 分段视频三类任务。**每类独立选服务商、模型、并发数**，实时进度与日志 |
| **产物** | 图片视频缩略图预览；一键生成人工复核清单、硬切拼接成片 |
| **设置** | 服务商凭据、LLM 分析引擎、默认参数、自检 |

## 十二环节

| 环节 | 名称 | 类型 | 产物 |
| --- | --- | --- | --- |
| 1 | 整剧全局解析 | LLM | 全局剧情地图 + 视觉基调（含压缩版） |
| 2 | 节奏驱动段落划分 | LLM | 段落表（进入/退出状态、连接锚点、防剧透项） |
| 3 | 状态时间线管理 | LLM | 状态包 + 相邻段 exit/entry 一致性校验 |
| 4 | 资产提取与生产判断 | LLM | 五类资产 + must/conditional/skip 三分类 |
| 5 | 资产生产提示词编译 | LLM | 每资产一份完整提示词 |
| 5b | 资产图生产 | **出图** | `02_固定资产/` |
| 6 | 段落资产绑定 | LLM | 参考图映射表（每段从 Image 1 重新编号） |
| 7 | 高密度正式分镜 | LLM | 5-8 镜 / 180度轴线 / 七类硬切锚点 |
| 8 | 故事板与视频提示词编译 | LLM | 两类提示词 + **自动装配 tasks.json** |
| 9 | 故事板生成与固定 | **出图** | `04_故事板/` |
| 10 | 视频执行生成 | **出片** | `05_分段视频/` |
| 11 | 结果检查清单 | 本地 | 八层检查 + L1/L2/L3 分级，人工填结论 |
| 12 | 排序拼接与交付 | 本地 | `06_成片/` + concat 清单 |

## 执行纪律（与 skill 一致）

- 输出已存在 → **跳过不覆盖**（生成即固定）；要出修订版就改 tasks.json 的 output 为 `_V02`
- 技术失败**同参重试 ≤ 2**（可配）；内容质量不自动判定，只落记录
- 环节 10 是**纯执行节点**：不重新分析、不改写提示词
- 审美问题不重做；只对技术失败、结构性错误、连续性错误定向修订

## 新增服务商（不用改前端）

1. `core/providers/` 下新建 `xxx.py`，继承 `Provider`，实现 `capabilities()` 和 `generate_image/generate_video`
2. `core/providers/__init__.py` 的 `_CLASSES` 加一行

`capabilities()` 返回的模型列表、时长、画幅、备注会**自动出现在前端下拉框**。已内置：

| 服务商 | 出图 | 出片 | 实测备注 |
| --- | --- | --- | --- |
| 灵感鸭 lingganyaapi.com | ✅ gpt-image-2 | ⚠️ sora-2 通道随机失败 | 图片稳定，视频建议换家 |
| 派系 api.paisio.online | ✅ | ✅ sd2-pro-720p | 视频首选（实测 17/17 一次过）；也提供 claude/gpt 系 chat 模型 |

参考图统一用**压缩 data URI**（1024px JPEG q80）直传，绕开图床白名单问题。

## 目录

```
production_runner/
├── run.py                启动入口
├── config.json           凭据与默认值（已 gitignore，不外发）
├── prompts/              ★ skill 的编译产物：环节 1-8 固定提示词模板
│   ├── _common.md        全局原则 + 输出格式约束（注入每个环节）
│   └── s1..s8_*.md       每环节的规则 + JSON schema + 输入占位符
├── core/
│   ├── apiutil.py        HTTP/轮询/解析/data URI/落盘
│   ├── providers/        ★ 服务商可插拔层
│   ├── llm.py            OpenAI 兼容 chat + JSON 校验重试
│   ├── stages.py         ★ 12 环节确定性编排 + tasks.json 装配
│   ├── executor.py       线程池 + 任务态 + 重试纪律
│   └── store.py          项目目录读写 + 注册表 + 日志
├── server/app.py         stdlib HTTP（零额外依赖）REST
├── web/index.html        单页前端（原生 JS，无构建）
└── legacy/               旧版 CLI runner（仍可用，非主入口）
```

## skill ↔ 程序 的同步方式

skill 在 Claude Code 里迭代成熟后，把变化编译到两处：

- **规则/schema 变化** → 改 `prompts/sN_*.md`（程序无需改代码）
- **流程/字段变化** → 改 `core/stages.py` 的 `STAGES` 与 `build_tasks()`

反向：程序跑完把项目目录交给 Claude Code，用 skill 做环节 11 的验收与优化建议。

## 迁移到其他电脑

拷走整个 `production_runner/`（`config.json` 含 key，按需决定带不带）。目标机只需
Python 3.9+ 和 `pip install requests`。`projects/` 默认在上级目录，可在设置里改。
