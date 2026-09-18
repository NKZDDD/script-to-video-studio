"""视频主参考的协议选择：新交接板字段优先，空列表也有明确含义。"""


def uses_handoff(task: dict) -> bool:
    # 新契约允许天然转场没有交接板。不能用 truthiness 再退回旧故事板字段。
    return "handoff_refs" in task


def primary_refs(task: dict) -> list:
    if uses_handoff(task):
        rows = task.get("handoff_refs") or []
    else:
        rows = task.get("storyboard_refs") or []
        if not rows and task.get("storyboard_ref"):
            rows = [{"order": 1, "sheet_id": "本段固定故事板",
                     "file_ref": task["storyboard_ref"]}]
    return sorted(rows, key=lambda row: row.get("order") or row.get("image_n") or 0)


def ref_file(row: dict) -> str:
    return str(row.get("url") or row.get("file_ref") or "")
