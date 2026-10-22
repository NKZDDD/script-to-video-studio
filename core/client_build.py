"""Release fingerprints detect damaged distributions; they are not remote attestation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from . import build_info, paths

VERSION = "2026.10.22.3"
MANIFEST = "studio-build.json"


def fingerprint(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def identity():
    if not paths.FROZEN:
        return {"version": VERSION, "flavor": build_info.only() or "all", "build_sha256": "source"}
    root = Path(paths.PROGRAM_DIR).resolve()
    try:
        data = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
        files = data["files"]
        if (data["version"] != VERSION or not isinstance(files, dict) or not files
                or not {"Respect-Studio.exe", "Respect-Studio-CLI.exe"} <= files.keys()):
            raise ValueError()
        for name, digest in files.items():
            rel = PurePosixPath(name)
            if (rel.is_absolute() or ".." in rel.parts or "\\" in name or ":" in name
                    or not re.fullmatch(r"[a-f0-9]{64}", digest)):
                raise ValueError()
            file = (root / name).resolve()
            if not file.is_relative_to(root) or not file.is_file():
                raise ValueError()
            with file.open("rb") as fp:
                if hashlib.file_digest(fp, "sha256").hexdigest() != digest:
                    raise ValueError()
        # The server compares the digest of this manifest with the registered build.
        digest = fingerprint(files)
        if digest != data["build_sha256"]:
            raise ValueError()
        return {"version": VERSION, "flavor": build_info.only() or "all", "build_sha256": digest}
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("程序文件校验失败，请重新下载安装；配置和项目不受影响") from None


def headers(info=None):
    info = info or identity()
    return {"X-Studio-Version": info["version"], "X-Studio-Flavor": info["flavor"],
            "X-Studio-Build": info["build_sha256"]}
