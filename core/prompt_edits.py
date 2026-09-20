"""Manual task prompt revisions; never mutate accepted or running job snapshots."""
from __future__ import annotations

import copy
from pathlib import PurePosixPath
import re
import time
import uuid

from . import agent_projects as A
from .store import LOCK, read_json, write_json


def _state(pj, key):
    publication = A.scan(pj)
    plan = pj.tasks()
    rows = A.task_rows(plan, legacy=True)
    matches = [t for t in rows if t["key"] == key]
    if len(matches) != 1:
        raise ValueError("任务不存在或编号重复，请刷新后重新选择")
    task = matches[0]
    raw = A.inside(pj.root, task["prompt_ref"]).read_bytes()
    text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    affected = {key}
    outputs = {t["output"]: t["key"] for t in rows}
    while True:
        following = {t["key"] for t in rows if any(
            outputs.get(r.get("file_ref") or r.get("url")) in affected for r in A.references(t))}
        if following <= affected:
            break
        affected.update(following)
    return plan, rows, task, text, {
        "key": key, "text": text, "version": A.digest([plan, raw.hex()]),
        "revision": publication.get("revision", "legacy"), "output": task["output"],
        "affected": [{"key": t["key"], "kind": t["kind"], "output": t["output"]}
                     for t in rows if t["key"] in affected],
    }


def read(pj, key):
    with LOCK:
        return _state(pj, key)[4]


def _revise_outputs(pj, plan, affected, tag):
    rows = A.task_rows(plan, legacy=True)
    occupied = {t["output"].casefold() for t in rows}
    replacements = {}
    for t in rows:
        if t["key"] not in affected:
            continue
        old = PurePosixPath(t["output"])
        stem = re.sub(r"_EDIT_[A-Za-z0-9-]+_[0-9a-f]{8}$", "", old.stem)[:140]
        suffix = A.digest(t["key"])[:8]
        new = old.with_name(f"{stem}_EDIT_{tag}_{suffix}{old.suffix}").as_posix()
        while new.casefold() in occupied or A.inside(pj.root, new).exists():
            new = old.with_name(f"{stem}_EDIT_{tag}_{uuid.uuid4().hex[:8]}{old.suffix}").as_posix()
        replacements[t["output"]] = new
        occupied.add(new.casefold())

    def replace(value):
        if isinstance(value, list):
            return [replace(v) for v in value]
        if isinstance(value, dict):
            return {k: (replacements.get(v, v) if isinstance(v, str) and k in
                        ("output", "file_ref", "url", "storyboard_ref", "aux_reference") else replace(v))
                    for k, v in value.items()}
        return value
    return replace(plan), replacements


def _new_release(pj):
    base = A.inside(pj.root, "03_提示词/releases")
    base.mkdir(parents=True, exist_ok=True)
    number = max((int(p.name[1:]) for p in base.iterdir() if re.fullmatch(r"r\d{4,}", p.name)), default=0) + 1
    while True:
        revision = f"r{number:04d}"
        folder = A.inside(pj.root, f"03_提示词/releases/{revision}")
        try:
            folder.mkdir()
            return revision, folder
        except FileExistsError:
            number += 1


def save(pj, key, text, expected_version):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("提示词不能为空")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    with LOCK:
        if pj.meta().get("archived"):
            raise ValueError("请先恢复归档项目，再编辑提示词")
        original, rows, _, previous, current = _state(pj, key)
        if not expected_version or expected_version != current["version"]:
            raise ValueError("材料或提示词已更新，本次未保存；请复制当前编辑内容，再重新打开对比后保存")
        if text == previous:
            return {"ok": True, "changed": False, "revision": current["revision"], "affected": []}
        affected = {t["key"] for t in current["affected"]}
        # Read every original prompt before writing or activating anything. Unchanged
        # bytes must stay exact, otherwise the existing publication fingerprint changes.
        prompts = {t["key"]: A.inside(pj.root, t["prompt_ref"]).read_bytes() for t in rows}
        prompts[key] = text.encode("utf-8")
        agent = pj.meta().get("workflow") == "agent"
        edit_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        if agent:
            revision, folder = _new_release(pj)
        else:
            revision = "manual-" + edit_id
            folder = A.inside(pj.root, "03_提示词/manual-edits/" + edit_id)
            folder.mkdir(parents=True)
        plan, replacements = _revise_outputs(pj, copy.deepcopy(original), affected, revision)
        record_dir = A.inside(pj.root, "07_检查与记录/prompt-edits/" + edit_id)
        record = {"task": key, "base_revision": current["revision"], "revision": revision,
                  "affected": [t["key"] for t in current["affected"]], "outputs": replacements,
                  "created_at": time.time()}
        write_json(str(record_dir / "before-tasks.json"), original)
        write_json(str(record_dir / "edit.json"), record)
        for kind in A.LEGACY_KINDS:
            for i, t in enumerate(plan.get(kind + "_tasks", [])):
                if agent or t["key"] == key:
                    target = folder / ("videos" if kind == "video" else "images") / f"{kind}-{i:05d}.txt"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(prompts[t["key"]])
                    t["prompt_ref"] = pj.rel(str(target))
        if agent:
            plan["revision"] = revision
            write_json(str(folder / "tasks.json"), plan)
            lock = read_json(pj.p("00_项目说明", "project-lock.json"))
            ready = {k: lock[k] for k in ("schema", "project_id", "options_hash", "skill_hash")}
            ready.update(revision=revision, files=A.file_manifest(folder), source="studio.manual", base_revision=current["revision"])
            write_json(str(folder / "READY.json"), ready)
            try:
                A.validate_release(pj, revision)
                state = A.scan(pj)
                if state.get("revision") != revision:
                    raise ValueError("新版本未激活，请刷新检查发布状态：" + "; ".join(e["message"] for e in state["errors"]))
            except Exception:
                # Leave diagnostic files, but don't expose a rejected edit as a ready release.
                (folder / "READY.json").rename(folder / "REJECTED.json")
                raise
        else:
            plan["studio_prompt_revision"] = revision
            write_json(str(folder / "tasks.json"), plan)
            pj.save_tasks(plan)
        return {"ok": True, "changed": True, "revision": revision,
                "affected": [{**t, "output": replacements[t["output"]]} for t in current["affected"]]}
