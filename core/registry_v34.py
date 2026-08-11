# -*- coding: utf-8 -*-
"""Canonical 资产注册表：完整 ID、不可变版本、文件指纹。

V3.4 第 10 章要求三件事，这里一并落地：

  1. 生产字段只用**完整 Canonical Revision ID**（`PRJ_XX__CHAR_001_R01`），
     `C001`、`CT01`、「女主状态图」都不是合法 ID。
  2. **Canonical Revision 不可覆盖**：内容变了必须建新版本并显式回编下游。
  3. 参考图必须解析到**唯一文件 + 路径 + 指纹**，解析不了就阻断。

分工上有个关键决定：**完整 ID 由注册表分配，不让模型写。**
模型继续输出 `C001`、`ST007` 这种短号 —— 让它拼 `PRJ_XX__CHAR_001_R01`
既容易写错，它也不可能知道当前是第几版。V3.4 自己就是这个架构：
「Canonical Object ↓ Registry allocates immutable Revision ID」。

版本什么时候涨，是这里最容易搞混的一件事：

    出图失败了重试        **同一版**。那是重试，不是新版本。
    删掉文件重新出        **同一版**。人是想修一次失败，不是想改内容。
    内容真的要改          **显式 bump**，建 R02，并回编引用它的下游。

把「重试」也算成新版本的话，跑一次失败重试三次就攒出 R04，
版本号变成噪声，「这张故事板当时用的是哪一版人脸」就查不出来了。
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Optional

from .store import Project, read_json, write_json

REG = ("07_检查与记录", "canonical_registry.json")

# 短号前缀 → 资产家族。模型写的是短号，家族从 n4 的 family 字段来；
# 拿不到时按前缀兜底，免得整条链因为一个字段缺失就断掉。
_PREFIX_FAMILY = {"C": "CHAR", "S": "LOC", "P": "PROP", "ST": "CT",
                  "SP": "SPATIAL", "G": "GRP", "V": "VFX"}


def project_id(pj: Project) -> str:
    """项目命名空间。取项目编号，清成只剩大写字母数字下划线。

    多项目共用一个资产库时，命名空间是唯一能区分「甲剧的 C001 和
    乙剧的 C001」的东西。现在一项目一目录用不上，但 ID 一旦写进几百个
    产物文件名再想加前缀，就是全量重跑。
    """
    meta = pj.meta() or {}
    raw = str(meta.get("project_code") or meta.get("title") or "PRJ")
    s = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_").upper()
    return f"PRJ_{s}" if s else "PRJ_UNNAMED"


def family_of(asset: dict) -> str:
    fam = str(asset.get("family") or "").strip().upper()
    if fam:
        return fam
    aid = str(asset.get("asset_id") or "")
    for n in (2, 1):                        # ST 比 S 先匹配
        if aid[:n] in _PREFIX_FAMILY and (len(aid) <= n or aid[n].isdigit()):
            return _PREFIX_FAMILY[aid[:n]]
    return "ASSET"


def canonical_id(pj: Project, asset: dict, revision: int = 1) -> str:
    """完整 Canonical Revision ID。生产字段只能用这个。"""
    return (f"{project_id(pj)}__{family_of(asset)}_"
            f"{str(asset.get('asset_id') or '?')}_R{int(revision):02d}")


def load(pj: Project) -> dict:
    return read_json(pj.p(*REG), {}) or {}


def _save(pj: Project, reg: dict) -> None:
    write_json(pj.p(*REG), reg)


def entry(pj: Project, asset_id: str) -> dict:
    return load(pj).get(asset_id) or {}


def current_revision(pj: Project, asset_id: str) -> int:
    return int(entry(pj, asset_id).get("current_revision") or 1)


def register(pj: Project, asset: dict) -> dict:
    """把一个资产登进注册表（还没出图）。已经登过的不动。"""
    aid = str(asset.get("asset_id") or "")
    if not aid:
        raise ValueError("资产没有 asset_id，登不了记")
    reg = load(pj)
    if aid not in reg:
        reg[aid] = {"family": family_of(asset), "current_revision": 1,
                    "canonical_id": canonical_id(pj, asset, 1),
                    "revisions": {}}
        _save(pj, reg)
    return reg[aid]


def bump(pj: Project, asset_id: str, why: str) -> int:
    """内容要改了 —— 建新版本。

    必须写理由：几个月后看注册表，要能知道 R02 和 R01 差在哪、为什么换。
    旧版本的文件**不删** —— 那正是不可变的意思：已经出过的故事板还引用着它，
    删了就查不出「当时用的是哪一版」。
    """
    if not (why or "").strip():
        raise ValueError("建新版本必须写理由（改了什么、为什么改）")
    reg = load(pj)
    e = reg.get(asset_id)
    if not e:
        raise ValueError(f"注册表里没有 {asset_id}，先跑资产环节")
    nxt = int(e.get("current_revision") or 1) + 1
    e["current_revision"] = nxt
    e["canonical_id"] = re.sub(r"_R\d+$", f"_R{nxt:02d}", e.get("canonical_id", ""))
    e.setdefault("bumps", []).append(
        {"to": nxt, "why": why.strip(), "at": _now()})
    _save(pj, reg)
    return nxt


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def promote(pj: Project, asset_id: str, rel_path: str) -> dict:
    """出图成功之后，把这个文件登记成当前版本的 Canonical 文件。

    登记指纹是为了后面能查出「文件被人换过」—— 换过之后下游还照着旧的
    引用跑，出来的东西看着正常但用的是另一张图。
    """
    p = pj.p(*rel_path.split("/"))
    if not os.path.isfile(p):
        raise FileNotFoundError(f"要登记的文件不在：{rel_path}")
    reg = load(pj)
    e = reg.setdefault(asset_id, {"family": "ASSET", "current_revision": 1,
                                  "canonical_id": "", "revisions": {}})
    rev = str(e.get("current_revision") or 1)
    e["revisions"][rev] = {"file": rel_path, "sha256": sha256(p),
                           "size": os.path.getsize(p), "at": _now(),
                           "status": "CANONICAL"}
    _save(pj, reg)
    return e["revisions"][rev]


def resolve(pj: Project, asset_id: str) -> dict:
    """把一个短号解析成「完整 ID + 真实文件 + 指纹」。

    V3.4：任一 Reference 未解析时阻断，不得猜图继续。
    所以这里返回 ok=False 时调用方必须停，不能当成「没有参考图」继续跑。
    """
    e = entry(pj, asset_id)
    if not e:
        return {"ok": False, "why": f"注册表里没有 {asset_id}"}
    rev = str(e.get("current_revision") or 1)
    r = (e.get("revisions") or {}).get(rev)
    if not r:
        return {"ok": False, "canonical_id": e.get("canonical_id", ""),
                "why": f"{asset_id} 的第 {rev} 版还没出图"}
    p = pj.p(*r["file"].split("/"))
    if not os.path.isfile(p):
        return {"ok": False, "canonical_id": e.get("canonical_id", ""),
                "why": f"{asset_id} 登记的文件不在了：{r['file']}"}
    return {"ok": True, "asset_id": asset_id,
            "canonical_id": e.get("canonical_id", ""),
            "revision": int(rev), "file": r["file"],
            "sha256": r.get("sha256", ""), "size": r.get("size", 0)}


def verify(pj: Project, asset_id: str) -> dict:
    """解析 + 核指纹。文件被换过就报出来。"""
    r = resolve(pj, asset_id)
    if not r["ok"]:
        return r
    want = r.get("sha256") or ""
    if not want:
        return r                        # 老记录没指纹，不倒过来判失败
    got = sha256(pj.p(*r["file"].split("/")))
    if got != want:
        return {"ok": False, "canonical_id": r["canonical_id"],
                "why": (f"{asset_id} 的文件和登记的指纹对不上 —— 被换过或被改过。"
                        f"登记 {want[:12]}…，现在 {got[:12]}…。"
                        f"要用新内容就建新版本，别原地换文件："
                        f"原地换的话，已经引用过它的故事板还以为用的是旧那张")}
    return r


def manifest(pj: Project, asset_ids: list) -> dict:
    """一次调用的参考图清单。V3.4 的 REFERENCE INPUT MANIFEST。

    返回 {ok, images[], blocked[]}。有一张解析不了就 ok=False ——
    「声明了几张就必须解析出几张」，少一张不许凑合出图。
    """
    images, blocked = [], []
    for i, aid in enumerate(asset_ids, 1):
        r = verify(pj, aid)
        if r["ok"]:
            images.append({"image_n": i, "asset_id": aid,
                           "canonical_id": r["canonical_id"],
                           "revision": r["revision"], "file": r["file"],
                           "sha256": r["sha256"], "availability": "AVAILABLE"})
        else:
            blocked.append({"image_n": i, "asset_id": aid,
                            "canonical_id": r.get("canonical_id", ""),
                            "availability": "BLOCKED", "why": r["why"]})
    return {"ok": not blocked, "count": len(asset_ids),
            "images": images, "blocked": blocked}


def sync(pj: Project, assets: list) -> int:
    """把这一集的资产表登进注册表。返回新登记了几个。"""
    reg = load(pj)
    n = 0
    for a in assets:
        aid = str(a.get("asset_id") or "")
        if not aid or aid in reg:
            continue
        reg[aid] = {"family": family_of(a), "current_revision": 1,
                    "canonical_id": canonical_id(pj, a, 1), "revisions": {}}
        n += 1
    if n:
        _save(pj, reg)
    return n


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
