# -*- coding: utf-8 -*-
"""页面里的 JS 能不能解析。

## 为什么值得单独一个文件

语法错的后果不是「某个功能坏了」，是**一行 JS 都不执行**：
页面停在初始占位符（项目列表「加载中…」），按钮全没反应，
而且**什么都不报** —— 连页面自己的错误捕获（boot().catch、
unhandledrejection、请求超时）都装不上，因为它们就在这段脚本里。

真的打进 exe 过一次：用户在两台机器上各撞了一次，两边控制台都干净。
起因很低级 —— 用 shell 改 index.html 时字符串里的 `\n` 被吃成了真换行：

    alert(lines.join('
    '));

## 为什么提取脚本这件事要有唯一实现

第一版是打包脚本和测试**各写一份正则**。打包那份的转义写坏了，
`findall` 返回空列表，于是它「检查了 0 段脚本」然后报成功 ——
**闸门装上了，但永远不会拦**。测试那份是好的，所以测试绿着，
而真正要拦的地方是瞎的。

所以两边都从这里取。
"""

from __future__ import annotations

import re
import shutil
import subprocess

SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S)


def scripts(html: str) -> list:
    """页面里所有内联 <script> 的内容。"""
    return SCRIPT_RE.findall(html or "")


def check(html: str) -> tuple:
    """返回 (是否通过, 说明)。没装 node 时**如实说没验**，不冒充通过。"""
    blocks = scripts(html)
    if not blocks:
        # 一段都没找到 = 提取坏了。别报成功 —— 那正是上次的失败方式。
        return False, "页面里一段 <script> 都没找到 —— 提取逻辑坏了，不是页面没脚本"
    node = shutil.which("node")
    if not node:
        return True, f"没装 node，{len(blocks)} 段脚本**未检查**（装了 node 才验得了）"
    for i, src in enumerate(blocks, 1):
        r = subprocess.run([node, "--check", "-"], input=src,
                           capture_output=True, text=True, encoding="utf-8")
        if r.returncode:
            return False, f"第 {i} 段 <script> 语法错：\n" + (r.stderr or "").strip()
    return True, f"{len(blocks)} 段脚本都能解析"
