# -*- coding: utf-8 -*-
"""在页面上直接改「发出去的那份提示词」。

跟「提示词模板」是两回事，别混：

  模板（core/prompts.py）  s5_asset_prompts.md 之类，是**怎么写**的规矩，
                           改它影响以后所有资产
  这里                     03_提示词/ 下那些 txt，是**这一条实际发出去的字**，
                           改它只影响这一条

出图被安全策略拦下、某个词模型不认、参考图对不上 —— 这些都是单条的毛病，
为它重跑一遍环节5 既慢又会把同批其它条一起重写。直接改这一条最省事。

改完立刻生效：worker 是在出图那一刻才 read_text(prompt_ref) 的，
不经过 tasks.json（那里存的是路径不是正文）。

**但这些 txt 是环节5/8 的产物，重跑会覆盖。** 所以手改过的都记一笔：
页面上打标，重跑覆盖前先备份到 _手改备份/ 并在日志里说一声。
不然改了半天，下次重跑环节8 全没了还不知道。
"""

from __future__ import annotations

import hashlib
import os
import time

from .store import Project, read_json, read_text, write_json, write_text

# 只允许改这三个目录下的 txt。都是「产物 → 下一步的输入」，改了立刻生效。
ALLOWED = ("03_提示词/资产生产提示词/",
           "03_提示词/故事板提示词/",
           "03_提示词/视频提示词/")

LEDGER = ("07_检查与记录", "手改提示词.json")
BACKUP = ("03_提示词", "_手改备份")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _norm(rel: str) -> str:
    return (rel or "").replace("\\", "/").strip().lstrip("/")


def check_rel(pj: Project, rel: str) -> str:
    """校验并返回绝对路径。不合法就抛。

    要挡的是「让服务端往项目外面写文件」：rel 是前端传上来的，
    ..\\..\\Windows\\System32 这种必须在这里断掉，不能指望调用方自觉。
    """
    r = _norm(rel)
    if not r.endswith(".txt"):
        raise ValueError(f"只能改 .txt：{rel}")
    if not r.startswith(ALLOWED):
        raise ValueError(
            "只能改 03_提示词 下的资产/故事板/视频提示词，"
            f"这个不在范围里：{rel}")
    full = os.path.abspath(os.path.join(pj.root, *r.split("/")))
    root = os.path.abspath(pj.root)
    if os.path.commonpath([full, root]) != root:
        raise ValueError(f"路径跑到项目外面去了：{rel}")
    return full


def ledger(pj: Project) -> dict:
    """手改记录。**只认还没被覆盖的那些** ——

    记录里存了当时的内容指纹，对不上说明后来重跑环节5/8 把它盖了，
    那这条手改就已经作废，不该再打「手改过」的标。自动失效，不用人去清。
    """
    raw = read_json(pj.p(*LEDGER), {}) or {}
    out = {}
    for rel, info in raw.items():
        p = os.path.join(pj.root, *_norm(rel).split("/"))
        if os.path.isfile(p) and _sha(read_text(p)) == info.get("sha"):
            out[rel] = info
    return out


def read_one(pj: Project, rel: str) -> dict:
    full = check_rel(pj, rel)
    text = read_text(full) if os.path.isfile(full) else ""
    return {"rel": _norm(rel), "text": text, "chars": len(text),
            "exists": os.path.isfile(full),
            "edited": ledger(pj).get(_norm(rel))}


def save_one(pj: Project, rel: str, text: str) -> dict:
    """存这一条。不做内容校验 —— 这是人工兜底的口子，人比规则清楚。"""
    full = check_rel(pj, rel)
    if not (text or "").strip():
        raise ValueError("提示词是空的，存下去这一条必然出不了图")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    write_text(full, text)
    r = _norm(rel)
    raw = read_json(pj.p(*LEDGER), {}) or {}
    raw[r] = {"at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "chars": len(text), "sha": _sha(text)}
    write_json(pj.p(*LEDGER), raw)
    return read_one(pj, r)


def guard_overwrite(pj: Project, rel: str, new_text: str, log=None) -> bool:
    """环节5/8 要覆盖一份手改过的 txt 之前调这个。

    不阻止覆盖 —— 产物就该由产它的环节说了算，拦下来只会让人以为环节没跑。
    但要**先备份、并且说出来**：几十条里被悄悄盖掉一条，
    等出图不对劲再回头找，那时候原文已经没了。
    """
    r = _norm(rel)
    info = ledger(pj).get(r)
    if not info:
        return False
    old = read_text(os.path.join(pj.root, *r.split("/")))
    if old == new_text:
        return False
    name = os.path.basename(r)
    stamp = time.strftime("%m%d-%H%M%S")
    dst = pj.p(*BACKUP, f"{os.path.splitext(name)[0]}.{stamp}.txt")
    write_text(dst, old)
    if log:
        log(f"⚠️ {name} 是 {info['at']} 手改过的，这次重跑把它覆盖了。"
            f"手改的那份备份在 {pj.rel(dst)}")
    return True
