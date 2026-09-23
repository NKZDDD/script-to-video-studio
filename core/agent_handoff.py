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

from . import agent_projects as A
from .store import LOCK, Project, read_json

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
    return f"""# Agent 生产任务（交接包）

这是 Respect Studio 项目「{pj.meta().get('title', '')}」（{lock['project_id']}）的交接包。Studio 不在你这台机器上：

- 把交接包解压后的根目录当作项目根目录，下文和契约里的相对路径都相对它。
- 先读 00_项目说明/production-contract.md、project-lock.json、production.schema.json，以及完整的 00_项目说明/skill/production-skill/。
- 原文在 01_剧本与分段/原始素材.txt。若原文为空，先要求用户补齐。
- 已有版本在 03_提示词/releases/（只读，不要改）；定向修订要求在 07_检查与记录/agent-revisions/。
- 本次新版本号从 {revision} 开始，连续发多个版本时依次递增。
- 交接包不含已生成的图片和视频。output、file_ref 仍按契约写项目相对路径，Studio 那边会对上实际文件。
- 不能改项目锁，也不能自行调用收费的生图 / 视频接口。

完成后，把 03_提示词/releases/{revision}/ 整个目录（含 READY.json）打包成一个 zip 交给用户，由用户在 Studio 的「Agent 契约」页点「导入 Agent 发布包」。
只有发布目录会被带回，交接包里其他文件的改动都不会生效。

锁定选项（用户在创建向导中确认过）：
```json
{json.dumps(lock['options'], ensure_ascii=False, indent=2)}
```

完成后列出发布版本、结构审计结果、需要用户查看的视觉审计项。
"""


def export_package(pj: Project) -> dict:
    if pj.meta().get("workflow") != "agent":
        raise ValueError("旧项目不走 Agent 发布，没有交接包")
    lock = read_json(pj.p("00_项目说明", "project-lock.json"))
    if not isinstance(lock, dict):
        raise ValueError("项目锁缺失，不能导出交接包")
    revision = next_revision(pj)
    root = Path(pj.root)
    files = {"AGENT_TASK.md": template(pj, lock, revision).encode("utf-8"),
             "00_项目说明/production-contract.md": A.contract(pj, lock, remote=True).encode("utf-8")}
    for rel in ("00_项目说明/project-lock.json", "00_项目说明/production.schema.json",
                "01_剧本与分段/原始素材.txt"):
        files[rel] = (root / rel).read_bytes()
    for folder in ("00_项目说明/skill", RELEASES, "07_检查与记录/agent-revisions"):
        base = root / folder
        for p in sorted(base.rglob("*")) if base.is_dir() else []:
            if p.is_file() and "__pycache__" not in p.parts:
                files[p.relative_to(root).as_posix()] = p.read_bytes()
    files["交接包说明.json"] = json.dumps({
        "project_id": lock["project_id"], "title": pj.meta().get("title", ""),
        "next_revision": revision, "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "contains": "文字材料，不含图片和视频"}, ensure_ascii=False, indent=2).encode("utf-8")
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
