# Codex 本机材料编译任务模板（协议提案示例）

本文件演示未来创建项目后自动生成的模板；当前 Studio 尚未实现此协议。尖括号参数应由创建向导替换为实际值，不应原样交给执行任务。

## 项目锁

- 项目 ID：<PROJECT_ID>
- 项目根目录：<PROJECT_ROOT_ABSOLUTE>
- 完整技能包：<PROJECT_ROOT_ABSOLUTE>/00_项目说明/skill/v7.0老李/
- 技能摘要：fc33ad2e2878dd72c23e20773d7ac9c028afe903d81e0e70a8b91d835382ed2d
- 输入原文：<SCRIPT_PATH_ABSOLUTE>
- 项目选项：<PROJECT_ROOT_ABSOLUTE>/00_项目说明/project-lock.json
- 输出契约：<PROJECT_ROOT_ABSOLUTE>/00_项目说明/production-contract.md
- 结构 schema：<PROJECT_ROOT_ABSOLUTE>/00_项目说明/production.schema.json
- 本次材料版本目录：<PROJECT_ROOT_ABSOLUTE>/03_提示词/releases/<REVISION>/
- 实际生成：由 Studio 执行，本次只交付 planned 材料。

## 执行要求

1. 核对主机可访问上述绝对路径、项目 ID 和版本；不可访问时报告具体路径，不改存其他工作区后声称项目已就绪。
2. 读取项目锁、完整 SKILL.md 和适用 references。沿用已确认默认值与用户覆盖，不反复询问已有选择。接口能力缺证据则标待核实，不据模型名称臆造限制。
3. 按 P0～P4 完成剧情/分段、必要基础资产、空间母图和按需 ABC、完整七段式视频提示词；依赖和实际 Image 顺序一致。材料不代表实图已生成，视觉与成片检查未执行时标待验收。
4. 直接在指定材料版本目录写 tasks.json 和约定的提示词文件。任务 output/file_ref 使用契约规定的相对项目路径；图片仍由 Studio 保存至 02_固定资产、03b_场景状态图、04_交接板，视频至 05_分段视频，成片至 06_成片。
5. 维持 key、资产版本、集/段 ID 和引用闭合。新版本不覆盖旧计划、用户勾选、运行记录、已生成媒体或原始剧本。已有资产的版本和用途符合时登记复用，不伪造实图路径或生成状态。
6. 按 schema 做结构校验，依据完整技能包做材料引用及 ABC 流程审计；需要内部适配时只在内部进行，不随意增删约定字段。语义/视觉/成片未验不能声称通过。
7. 全部文件写完且检查后，最后原子写 READY.json，包含 project_id、plan_revision、schema_version、skill_hash、options_hash、文件及 SHA-256、实际检查范围和未验证层。等待 Studio 验证，不直接修改 active-plan.json。
8. 只生成已选择的交付内容。图册、情绪曲线和离线看板默认不额外生成；必要内部记录和检查照常保留。

## 本次选择（创建时自动填充）

<EXPLICIT_OPTIONS_WITH_SOURCES>

## 修订时额外提供

<BASE_PLAN_REVISION>、<AFFECTED_TASK_KEYS>、<FAILURE_DETAILS>、<ALLOWED_CHANGE_SCOPE>。只修受影响项并复验其依赖，未受影响的资源和已完成结果保持原版本。
