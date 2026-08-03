# -*- coding: utf-8 -*-
"""LLM 分析引擎客户端（OpenAI 兼容 /v1/chat/completions，任意中转站）。

用于环节1-8的剧情分析与提示词编译：固定提示词模板 → LLM → JSON 校验 → 失败带错误反馈重试。
这是"调用API分析剧本"，编排本身是确定性代码。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

import requests


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> Any:
    """从 LLM 回复中提取 JSON：```json 围栏优先，其次首个 {..} / [..] 平衡块。"""
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        return json.loads(m.group(1).strip())
    # 平衡扫描
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start < 0:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    raise LLMError("回复中未找到可解析的 JSON")


def check_keys(data: Any, required: list) -> list:
    """轻量结构校验：required 形如 ["a", "b[]", "b[].x"]。返回缺失项列表。"""
    missing = []
    for spec in required:
        parts = spec.split(".")
        nodes = [data]
        ok = True
        for part in parts:
            is_list = part.endswith("[]")
            key = part[:-2] if is_list else part
            nxt = []
            for n in nodes:
                if not isinstance(n, dict) or key not in n:
                    ok = False
                    break
                v = n[key]
                if is_list:
                    if not isinstance(v, list) or not v:
                        ok = False
                        break
                    nxt.extend(v)
                else:
                    nxt.append(v)
            if not ok:
                break
            nodes = nxt
        if not ok:
            missing.append(spec)
    return missing


class LLM:
    def __init__(self, api_key: str, base_url: str, model: str,
                 timeout: int = 600, proxy: str = ""):
        if not api_key:
            raise LLMError("缺少 llm_api_key")
        self.api_key = api_key
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.timeout = timeout
        self.proxy = proxy

    def chat(self, system: str, user: str, retries: int = 3) -> str:
        body = {
            "model": self.model,
            "messages": ([{"role": "system", "content": system}] if system else [])
                        + [{"role": "user", "content": user}],
            "stream": False,
        }
        last = None
        for attempt in range(retries):
            try:
                r = requests.post(
                    self.base_url + "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}",
                             "Content-Type": "application/json"},
                    json=body, timeout=self.timeout,
                    proxies={"http": self.proxy, "https": self.proxy} if self.proxy else None,
                )
                if r.status_code in (429, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(2 ** attempt * 2)
                    continue
                if r.status_code >= 400:
                    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1"):
                        r.encoding = "utf-8"
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:400]}")
                data = r.json()
                choices = data.get("choices") or []
                if not choices:
                    raise LLMError(f"无 choices: {json.dumps(data, ensure_ascii=False)[:300]}")
                content = (choices[0].get("message") or {}).get("content") or ""
                if not content.strip():
                    raise LLMError("回复内容为空")
                return content
            except (requests.RequestException, ValueError) as exc:
                last = exc
                if attempt < retries - 1:
                    time.sleep(2 ** attempt * 2)
                    continue
                raise LLMError(f"网络/解析错误: {exc}") from exc
        raise LLMError(f"重试耗尽: {last}")

    def json_call(self, system: str, user: str, required: Optional[list] = None,
                  json_retries: int = 2, log=print) -> Any:
        """要求 JSON 输出；解析失败或缺键时把错误反馈给模型重试（≤json_retries）。"""
        attempt_user = user
        for attempt in range(1 + json_retries):
            text = self.chat(system, attempt_user)
            try:
                data = extract_json(text)
                missing = check_keys(data, required or [])
                if missing:
                    raise LLMError(f"输出缺少必需字段: {missing}")
                return data
            except (json.JSONDecodeError, LLMError) as exc:
                if attempt >= json_retries:
                    raise LLMError(f"JSON 输出校验失败（已重试{json_retries}次）: {exc}") from exc
                log(f"    JSON 校验失败，反馈重试 {attempt + 1}/{json_retries}: {str(exc)[:150]}")
                attempt_user = (user + "\n\n【上次输出的问题】" + str(exc)[:400]
                                + "\n请严格只输出一个符合要求的 JSON（用 ```json 围栏包裹），不要输出其他内容。")
        raise LLMError("不可达")
