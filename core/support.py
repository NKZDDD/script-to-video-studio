# -*- coding: utf-8 -*-
"""一键打包排错资料。

出问题时「该发哪个文件」这件事，用户是猜不出来的 —— 而猜错的代价是
来回好几轮：发了截图看不出模型回了什么，发了产物看不出是哪一步断的。
真正有用的那几份（失败原文、诊断记录、用量账本、当时的配置）散在
四个地方，还有一份**必须脱敏**才能外发。

所以做成一个按钮：点一下拿到一个 zip，直接发。

**脱敏是这个模块存在的主要理由。** config.json 里有 API key 和对象存储的
访问密钥，而它恰恰是排错最需要的一份（用的哪家、哪个模型、流式开没开、
超时多少）。人工删 key 迟早会漏一次，而外发的东西收不回来。
"""

from __future__ import annotations

import io
import json
import os
import platform
import sys
import time
import zipfile

# 这些键一律不出包。宁可多删，也别漏一个 ——
# 外发的东西收不回来，漏一次就得换所有的 key。
SECRET_KEYS = ("api_key", "key", "secret", "secret_key", "access_key",
               "token", "password", "passwd", "authorization", "cookie")

# 打进去的那几份。都在项目里，不含剧本正文和成片。
PARTS = [
    ("07_检查与记录/failures.json", "每条失败的诊断记录"),
    ("07_检查与记录/execution_log.jsonl", "每一步做了什么"),
    ("07_检查与记录/usage.jsonl", "每次调用的耗时和 token"),
    ("project.json", "项目冻结的参数"),
]

RAW_DIR = "07_检查与记录/失败原文"      # 模型实际回了什么，整个目录都带上


def _is_secret(key: str) -> bool:
    k = str(key).lower()
    return any(s in k for s in SECRET_KEYS)


def redact(obj):
    """把任何看着像密钥的值换成占位符，结构原样保留。

    保留结构是有意的：开发者要能看出「填了没填」——
    整个删掉的话，「key 没配」和「key 配错了」就分不出来了。
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _is_secret(k) and isinstance(v, str):
                out[k] = f"（已隐去，{len(v)} 位）" if v else "（空）"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def environment() -> dict:
    """跑在什么环境上。版本对不上是常见原因，而人一般不会主动说。"""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    commit = ""
    head = os.path.join(here, ".git", "HEAD")
    try:
        with io.open(head, encoding="utf-8") as f:
            ref = f.read().strip()
        if ref.startswith("ref: "):
            with io.open(os.path.join(here, ".git", ref[5:]), encoding="utf-8") as f:
                commit = f.read().strip()[:12]
        else:
            commit = ref[:12]
    except OSError:
        commit = "（exe 里没有 git 信息）"
    return {
        "打包时间": time.strftime("%Y-%m-%d %H:%M:%S"),
        "程序版本": commit,
        "运行方式": "exe" if getattr(sys, "frozen", False) else "源码",
        "Python": sys.version.split()[0],
        "系统": f"{platform.system()} {platform.release()}",
    }


def bundle(project_root: str, config: dict, dest: str) -> dict:
    """把排错要用的东西打成一个 zip。返回 {path, files, note}。"""
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    added, missing = [], []
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("环境.json",
                   json.dumps(environment(), ensure_ascii=False, indent=2))
        # 配置**必须**脱敏。它是最有用的一份，也是唯一有密钥的一份。
        z.writestr("配置（已脱敏）.json",
                   json.dumps(redact(config or {}), ensure_ascii=False, indent=2))
        added += ["环境.json", "配置（已脱敏）.json"]

        for rel, _why in PARTS:
            src = os.path.join(project_root, *rel.split("/"))
            if os.path.isfile(src):
                z.write(src, rel)
                added.append(rel)
            else:
                missing.append(rel)

        raw = os.path.join(project_root, *RAW_DIR.split("/"))
        if os.path.isdir(raw):
            for name in sorted(os.listdir(raw)):
                p = os.path.join(raw, name)
                if os.path.isfile(p):
                    z.write(p, f"{RAW_DIR}/{name}")
                    added.append(f"{RAW_DIR}/{name}")
        else:
            missing.append(RAW_DIR + "/")

        z.writestr("这个包里有什么.txt", _manifest(added, missing))
    return {"path": dest, "files": len(added), "missing": missing,
            "size": os.path.getsize(dest)}


def _manifest(added: list, missing: list) -> str:
    raw_n = sum(1 for a in added if a.startswith(RAW_DIR))
    lines = [
        "这个包是给开发者排错用的。",
        "",
        "== 里面有什么 ==",
        "  环境.json              程序版本、跑在源码还是 exe、系统",
        "  配置（已脱敏）.json      用的哪家、哪个模型、流式/超时怎么设的",
    ]
    lines += [f"  {rel:<38}{why}" for rel, why in PARTS
              if rel in added]
    if raw_n:
        lines.append(f"  {RAW_DIR}/ ({raw_n} 份)　模型实际回了什么 —— "
                     f"排「JSON 解析不了」只能靠这个")
    lines += [
        "",
        "== 不在里面 ==",
        "  · API key、对象存储密钥 —— 已全部替换成「（已隐去，N 位）」，",
        "    保留了长度是为了让人看出「填了没填」，值本身出不去。",
        "  · 剧本正文、图片、视频 —— 太大，而且排错一般用不上。",
        "    如果开发者要，再单独发。",
    ]
    if missing:
        lines += ["", "== 这几份没找到（可能是还没跑到那一步）=="]
        lines += [f"  · {m}" for m in missing]
    return "\n".join(lines) + "\n"
