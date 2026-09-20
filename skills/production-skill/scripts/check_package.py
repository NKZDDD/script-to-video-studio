#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V7.0 静态包检查（局部已知回归，不能证明全规则无冲突）：
① 可灵：只能出现在 LEGACY/移除声明行，不得作为正式支持模型
② 角色资产：CURRENT 标准只能是 4 View（"三视图"仅允许出现在 LEGACY/历史说明行）
③ Prompt 格式命名：当前七栏目定义必须存在
④ LEGACY：V3 空间系统与 CHANGELOG 必须带 LEGACY 标记
⑤ README/SKILL：版本号与 Seedance 2.5/2.0 关键产品规则一致
⑥ 视频提示词参考图：Image N 显式绑定规则存在，主模板不回归 @ 资产写法
外加原有静态检查：SKILL frontmatter、references 完整性。"""
import os, re, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILED = []

def read(rel):
    with open(os.path.join(BASE, rel), "r", encoding="utf-8") as f:
        return f.read()

def scan_md_files():
    out = []
    for root, _dirs, files in os.walk(BASE):
        for fn in files:
            if fn.endswith(".md"):
                out.append(os.path.join(root, fn))
    return out

def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)

# ---------- 原有静态检查 ----------
skill = read("SKILL.md")
check("SKILL.md frontmatter name", bool(re.search(r"^name:\s*v7\.0老李\s*$", skill, re.M)))
refs = [f for f in os.listdir(os.path.join(BASE, "references")) if f.endswith(".md")]
check("references 文件数（39 份；不等于内容完整性）", len(refs) == 39, f"实际 {len(refs)} 份")

# ---------- ① 可灵残留 ----------
KLING_OK = re.compile(r"LEGACY|不再|已移除|历史|移除可灵|去可灵")
bad_kling = []
for p in scan_md_files():
    for i, line in enumerate(read(os.path.relpath(p, BASE)).splitlines(), 1):
        if "可灵" in line and not KLING_OK.search(line):
            bad_kling.append(f"{os.path.relpath(p, BASE)}:{i}")
check("① 可灵不作为正式支持模型（仅 LEGACY/移除声明行可提及）", not bad_kling, "; ".join(bad_kling[:5]))
check("① model-adapters 含可灵 LEGACY 声明", "LEGACY·可灵" in read(os.path.join("references", "model-adapters.md")))

# ---------- ② 4 View 口径 ----------
VIEW_OK = re.compile(r"LEGACY|旧称|旧「|历史")
bad_view = []
for p in scan_md_files():
    rel = os.path.relpath(p, BASE)
    if rel.startswith("CHANGELOG"):  # 历史沿革文件整体已带 LEGACY 声明
        continue
    for i, line in enumerate(read(rel).splitlines(), 1):
        if "三视图" in line and not VIEW_OK.search(line):
            bad_view.append(f"{rel}:{i}")
check("② CURRENT 角色资产标准仅 4 View（三视图仅存于 LEGACY 说明行）", not bad_view, "; ".join(bad_view[:5]))
check("② asset-spatial-ledger 定义 4 View 资产参考图", "角色 4 View 资产参考图（CURRENT 标准" in read(os.path.join("references", "asset-spatial-ledger.md")))

# ---------- ③ Prompt 格式命名 ----------
all_md = "\n".join(read(os.path.relpath(p, BASE)) for p in scan_md_files())
_ban=[l for l in all_md.splitlines() if re.search(r"八段式|六栏|九栏", l) and not re.search(r"禁|别名", l)]
check("③ 禁用「八段式/六栏/九栏」别名（禁令声明行除外）", not _ban, "; ".join(_ban[:3]))
ma = read(os.path.join("references", "model-adapters.md"))
sections = ("画幅风格", "参考图", "站位声明", "时间轴分镜", "接续状态", "音效", "强制禁止项")
check("③ 当前七栏目定义存在（model-adapters）",
      all(f"【{section}】" in ma for section in sections))

# ---------- ④ LEGACY 标记 ----------
check("④ spatial-reference-system-V3 带 LEGACY/备查标记",
      "LEGACY / 备查（V6.8 标记）" in read(os.path.join("references", "spatial-reference-system-V3.md")))
check("④ CHANGELOG 带 LEGACY/HISTORY 声明", "LEGACY/HISTORY 声明" in read("CHANGELOG.md"))

# ---------- ⑤ README/SKILL 一致 ----------
readme = read("README.md")
check("⑤ 首个主标题版本一致（SKILL/README 均 V7.0）",
      all(bool(re.search(r"^# .*\bV7\.0\b", doc, re.M)) for doc in (skill, readme)))
check("⑤ 模型支持口径一致（2.5/2.0/即梦，无可灵）",
      all(("Seedance 2.5" in doc and ("Seedance 2.0" in doc or "2.5/2.0" in doc)) for doc in (skill, readme))
      and not [l for l in readme.splitlines() if "可灵" in l and not re.search(r"移除|残留|LEGACY", l)])
check("⑤ 主入口保留项目锁路由", "项目锁" in skill and "model-adapters.md" in skill)

# ---------- ⑥ Image N 参考图模板 ----------
engine = read(os.path.join("references", "seedance-render-engine.md"))
check("⑥ Image N 显式参考图模板已固化",
      bool(re.search(r"Image N = 资源\s*key，这张图是用途", ma))
      and "Image 1 = PRJ__ABC_EP01_SEG01_TO_SEG02_B_R01" in ma
      and "model-adapters.md" in engine)
prompt_examples = [body for body in re.findall(r"```text\s*\n(.*?)```", ma, re.S)
                   if "【参考图】" in body and "【画幅风格】" in body]
check("⑥ 可复制示例七栏目顺序一致、分镜无时间码",
      bool(prompt_examples) and all(
          re.findall(r"^【([^】]+)】", body, re.M) == list(sections)
          and not re.search(r"^\s*\d{1,2}:\d{2}\s*[-~—]", body, re.M)
          for body in prompt_examples))
check("⑥ 主模板不再用 @ 资产作为视频参考图",
      not re.search(r"【(?:参考图|场景资产|核心人物)】\s*\n(?:-\s*)?@", engine))

# Observable parser regressions; load source without writing __pycache__.
board = {"__name__": "board_check"}
exec(compile(read("scripts/build_board_lite.py"), "build_board_lite.py", "exec"), board)
fence = "```"
sample = ("# 回归\n【画幅】：16:9\n【模型版本】：2.5\n## API模板\n"
          + fence + "json\n{\"preserve\":true}\n" + fence
          + "\n## P4 投喂提示词\n### 段10\n" + fence + "text\n[镜头1] TEN\n" + fence
          + "\n### 段1\n" + fence + "text\n[镜头1] ONE\n" + fence
          + "\n" + fence + "text\n短附录\n" + fence + "\n")
parsed = board["parse_md"](sample)
check("⑦ 看板围栏保留、段名不串段、多围栏不丢失",
      any('preserve' in block.get('v', '') for entry in parsed['extras'] for block in entry['body'])
      and parsed['segments'][1]['raw'] == '[镜头1] ONE\n\n短附录')
check("⑦ 无时间码镜头不伪造秒数",
      parsed['segments'][1]['shots'][0]['start'] is None)
dialogue_sample = ('【时间轴分镜】\n[镜头1]\n画面：门前双人中景。\n'
                   '发声角色：甲，现场对白。\n对白原文：“进来。”\n'
                   '口型与反应：甲闭口后乙回应。\n发声角色：乙，现场对白。\n对白原文：“好。”\n'
                   '[镜头2]\n画面：甲看向门内。\n发声角色：甲，内心独白。\n'
                   '内心独白原文：“终于来了。”\n口型与反应：保持闭口。\n'
                   '[镜头3]\n画面：门扇轻摇。\n环境与动作声：风声。\n'
                   '【音效】\n环境与动作声：全段说明不得归入镜头3。')
multi = board['parse_md']('# 测试\n## P4 投喂提示词\n### 段1\n'
                          + fence + 'text\n' + dialogue_sample + '\n' + fence)['segments'][0]
check('⑦ 多人分行对白与内心声完整保留且不串栏目',
      len(multi['shots']) == 3 and multi['shots'][0]['text'].count('发声角色：') == 2
      and '对白原文：“好。”' in multi['shots'][0]['text']
      and '内心独白原文：“终于来了。”' in multi['shots'][1]['text']
      and '对白原文' not in multi['shots'][2]['text']
      and '全段说明' not in multi['shots'][2]['text']
      and multi['raw'] == dialogue_sample
      and all(s['start'] is None for s in multi['shots']))
audit_module = {"__name__": "reference_check"}
exec(compile(read("scripts/audit_material_refs.py"), "audit_material_refs.py", "exec"), audit_module)
check("⑧ 空材料与非法kind明确失败",
      bool(audit_module['audit']([])['errors'])
      and bool(audit_module['audit']([{'kind': 'nonsense'}])['errors']))

# ---------- 汇总 ----------
print("-" * 40)
if FAILED:
    print(f"[-] {len(FAILED)} 项未通过: {FAILED}")
    sys.exit(1)
print(f"[+] 静态包与上述局部回归通过（references {len(refs)} 份 · V7.0）")
print("范围限制：未验证全规则语义一致、完整V3.0导入、实际请求上传、图像空间或视频直拼效果。")
