"""Offline handoff for an Agent running on another machine.

Export: the text an Agent needs (contract, lock, schema, full skill snapshot, source,
previous releases, revision requests) as one zip, with relative-path instructions.
Import: release folders from the Agent's zip land in 03_提示词/releases/ only after a
complete extraction, then go through the same READY validation as a local release.
No images or videos travel in either direction.
"""
from __future__ import annotations

import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import time
import uuid
import zipfile

from . import agent_projects as A, matspec
from .store import LOCK, Project, read_json, read_text

RELEASES = "03_提示词/releases"
MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES = 5000, 50 * 1024 * 1024, 200 * 1024 * 1024


def _revisions(pj: Project) -> list:
    root = Path(pj.p(*RELEASES.split("/")))
    return sorted((p.name for p in root.glob("r*") if p.is_dir() and re.fullmatch(r"r\d{4,}", p.name)),
                  key=lambda s: int(s[1:]))


def next_revision(pj: Project) -> str:
    names = _revisions(pj)
    active = (read_json(pj.p("07_检查与记录", "active-plan.json"), {}) or {}).get("revision")
    top = max([int(n[1:]) for n in names] + ([int(active[1:])] if active else []) + [0])
    return f"r{top + 1:04d}"


def template(pj: Project, lock: dict, revision: str) -> str:
    return f"""# 交给 Codex 的交接包

项目「{pj.meta().get('title', '')}」（{lock['project_id']}）。这个压缩包里已经有做事需要的全部文字，**不要再向用户要剧本、契约或分析文件**。

先读这些文件，都在包内：

1. `剧本.txt`：完整原文。
2. `生产材料契约.md`：要交回的 `生产材料.jsonl` 的字段和形状。
3. `01_剧本与分段/`：项目里已经有的分析产物（没有就跳过）。
4. `00_项目说明/skill/production-skill/`：完整生产技能。按它做分析，不要另找规则。
5. `00_项目说明/project-lock.json`：用户锁定的画幅、时长、风格。不能改。

两条交回方式，用户都能导入：

- 按生产材料契约交一个 `.jsonl`。
- 或者按技能交发布目录 `03_提示词/releases/{revision}/`（含 READY.json），打成 zip。版本号从 {revision} 起，再发就递增。已有版本在包内的 `03_提示词/releases/`，只读。

不要调用收费的生图或视频接口。包内没有图片和视频；引用仍写项目相对路径。

锁定选项：
```json
{json.dumps(lock['options'], ensure_ascii=False, indent=2)}
```
"""


def export_package(pj: Project, limits=None) -> dict:
    if pj.meta().get("workflow") != "agent":
        raise ValueError("旧项目不走 Agent 发布，没有交接包")
    lock = read_json(pj.p("00_项目说明", "project-lock.json"))
    if not isinstance(lock, dict):
        raise ValueError("项目锁缺失，不能导出交接包")
    script = read_text(pj.p("01_剧本与分段", "原始素材.txt"))
    if not script.strip():
        raise ValueError("原文是空的。先在「Agent 契约」里补上剧本再导出，否则 Codex 还是没有可分析的原文")
    revision = next_revision(pj)
    root = Path(pj.root)
    system = pj.meta().get("system") or "v34"
    files = {"AGENT_TASK.md": template(pj, lock, revision).encode("utf-8"),
             "剧本.txt": script.encode("utf-8"),
             "生产材料契约.md": matspec.render(limits, pj.meta().get("title", ""), system).encode("utf-8"),
             "00_项目说明/production-contract.md": A.contract(pj, lock, remote=True).encode("utf-8")}
    for rel in ("00_项目说明/project-lock.json", "00_项目说明/production.schema.json",
                "01_剧本与分段/原始素材.txt"):
        files[rel] = (root / rel).read_bytes()
    analysis = root / "01_剧本与分段"
    for path in sorted(analysis.rglob("*")) if analysis.is_dir() else []:
        if path.is_file() and path.suffix.lower() in (".txt", ".md", ".json", ".jsonl"):
            files.setdefault(path.relative_to(root).as_posix(), path.read_bytes())
    for folder in ("00_项目说明/skill", RELEASES, "07_检查与记录/agent-revisions"):
        base = root / folder
        for p in sorted(base.rglob("*")) if base.is_dir() else []:
            if p.is_file() and "__pycache__" not in p.parts:
                files[p.relative_to(root).as_posix()] = p.read_bytes()
    files["交接包说明.json"] = json.dumps({
        "project_id": lock["project_id"], "title": pj.meta().get("title", ""),
        "next_revision": revision, "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "contains": "剧本、生产材料契约、已有分析和完整技能；不含图片和视频",
        "script": "剧本.txt", "material_contract": "生产材料契约.md"},
        ensure_ascii=False, indent=2).encode("utf-8")
    name = f"{lock['project_id']}_交接包_{time.strftime('%Y%m%d-%H%M%S')}.zip"
    rel = f"07_检查与记录/handoff/{name}"
    target = A.inside(pj.root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in files.items():
            z.writestr(path, data)
    tmp.replace(target)
    return {"rel": rel, "file": str(target), "next_revision": revision,
            "files": len(files), "bytes": target.stat().st_size}


def _member_name(info: zipfile.ZipInfo) -> str:
    """Windows 压缩工具常把中文名按本地编码存、又不设 UTF-8 标志，zipfile 会当成 cp437 解出乱码。"""
    name = info.filename
    if not info.flag_bits & 0x800:
        raw = name.encode("cp437", errors="ignore")
        for enc in ("utf-8", "gbk"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
    return name


def _safe_parts(name: str) -> tuple:
    name = name.replace("\\", "/")
    parts = PurePosixPath(name).parts
    if not parts or name.startswith("/") or re.match(r"^[A-Za-z]:", name) or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"压缩包里有不安全的路径：{name}")
    return parts


def _releases_in(z: zipfile.ZipFile) -> dict:
    """zip 里每个 READY.json 所在目录就是一个发布目录 → {revision: {相对发布目录的路径: ZipInfo}}。"""
    entries, count, total = {}, 0, 0
    for info in z.infolist():
        if info.is_dir():
            continue
        parts = _safe_parts(_member_name(info))
        if parts[0] == "__MACOSX":
            continue
        count += 1
        total += info.file_size
        if count > MAX_FILES or info.file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError("压缩包过大或文件过多，不像 Agent 发布包")
        entries[parts] = info
    found = {}
    for parts, info in entries.items():
        if parts[-1] != "READY.json":
            continue
        base = parts[:-1]
        try:
            ready = json.loads(z.read(info).decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError):
            raise ValueError(f"{'/'.join(parts)} 不是有效的 JSON")
        revision = ready.get("revision") if isinstance(ready, dict) else None
        if not isinstance(revision, str) or not re.fullmatch(r"r\d{4,}", revision):
            raise ValueError(f"{'/'.join(parts)} 缺少合法的 revision（应为 r0001 形式）")
        if base and re.fullmatch(r"r\d{4,}", base[-1]) and base[-1] != revision:
            raise ValueError(f"目录 {base[-1]} 与 READY.json 里的版本 {revision} 不一致")
        if revision in found:
            raise ValueError(f"压缩包里有两个 {revision}")
        found[revision] = {"/".join(p[len(base):]): i for p, i in entries.items() if p[:len(base)] == base}
    if not found:
        raise ValueError("压缩包里没有找到 READY.json。请让 Agent 把整个发布目录（含 READY.json）打包")
    return found


def import_package(pj: Project, raw: bytes) -> dict:
    """把 Agent 发布包里的版本放进 releases/，逐个走本机发布的同一套校验。

    校验不通过的版本不留在 releases/：留着的话每次扫描都报错，而且同一个版本号
    再也导不进来（已发布版本不可覆盖），Agent 改好之后只能被迫换号。
    """
    if pj.meta().get("workflow") != "agent":
        raise ValueError("旧项目不走 Agent 发布，不能导入发布包")
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise ValueError("不是有效的 zip 压缩包")
    with z, LOCK:
        releases = _releases_in(z)
        root = A.inside(pj.root, RELEASES)
        root.mkdir(parents=True, exist_ok=True)
        active = (read_json(pj.p("07_检查与记录", "active-plan.json"), {}) or {}).get("revision") or "r0000"
        imported, same, rejected, fresh = [], [], [], []
        for revision in sorted(releases, key=lambda s: int(s[1:])):
            target = root / revision
            if target.exists():
                old = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}
                if old == {rel: z.read(info) for rel, info in releases[revision].items()}:
                    same.append(revision)
                    continue
                raise ValueError(f"{revision} 已经存在且内容不同。已发布的版本不能覆盖，请让 Agent 用新的版本号 {next_revision(pj)} 重新发布")
            if int(revision[1:]) <= int(active[1:]):
                raise ValueError(f"{revision} 不比当前已接收的 {active} 新，Studio 不会接收它。请让 Agent 用 {next_revision(pj)} 重新发布")
            fresh.append(revision)
        for revision in fresh:
            members, target = releases[revision], root / revision
            staging = root / f".importing-{uuid.uuid4().hex[:8]}"
            staging.mkdir()
            try:
                for rel, info in members.items():
                    dest = A.inside(str(staging), rel)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(z.read(info))
                staging.replace(target)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            imported.append(revision)
        result = A.scan(pj)
        for revision in imported:
            error = next((e["message"] for e in result.get("errors", []) if e.get("revision") == revision), "")
            if error:
                shutil.rmtree(root / revision, ignore_errors=True)
                rejected.append({"revision": revision, "message": error})
    return {"imported": [r for r in imported if r not in {x["revision"] for x in rejected}],
            "unchanged": same, "rejected": rejected, "active": result.get("revision", "")}
