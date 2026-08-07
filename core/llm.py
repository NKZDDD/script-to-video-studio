# -*- coding: utf-8 -*-
"""LLM 分析引擎客户端（OpenAI 兼容 /v1/chat/completions，任意中转站）。

用于环节1-8的剧情分析与提示词编译：固定提示词模板 → LLM → JSON 校验 → 失败带错误反馈重试。
这是"调用API分析剧本"，编排本身是确定性代码。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Optional

import requests


class LLMError(RuntimeError):
    pass


class LLMFatal(LLMError):
    """重试同一个提示词也解决不了的：超时、输出被长度上限截断。

    这类必须往上抛，不能被 json_call 的「反馈重试」吞掉 ——
    同一个提示词重试必然同样超时、同样截断，只是把钱花三倍。
    """


class LLMCancelled(LLMError):
    """用户点了取消。不是失败，不要重试，也不该记进 failures。"""


class _Retryable(RuntimeError):
    """429 / 5xx —— 值得等一会儿再试。"""


class _NoStream(RuntimeError):
    """这家不接受 stream 参数 —— 退回普通请求，不算失败。"""


class _NoStreamOptions(RuntimeError):
    """这家不认 stream_options（要 usage 回传的那个）—— 去掉它重试。"""


# 约 2 字/token。实测（paisio + gpt-5.6-sol，中文+印尼语混排的剧本）：
#   10022 字 → prompt_tokens 8440，减去网关固定注入的 3840 缓存前缀 = 4600 → 2.18
#    5033 字 → prompt_tokens 6454，同样减 3840 = 2614 → 1.93
# 注意那个 3840：**每次调用都有**，是网关自己的前缀，不是我们发的内容。
# 直接用 prompt_tokens 除字数会把比值算低近一倍（我第一版就错在这）。
# 这个常数只用来在发请求前给个量级提示，真实数字一律以回传的 usage 为准。
CHARS_PER_TOKEN = 2.0


def rough_tokens(text: str) -> int:
    return int(len(text or "") / CHARS_PER_TOKEN)


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
                 timeout: int = 600, proxy: str = "", max_tokens: int = 16000):
        if not api_key:
            raise LLMError("缺少 llm_api_key")
        self.api_key = api_key
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.timeout = timeout
        self.proxy = proxy
        # 不显式给上限的话走服务商默认值（常见 4k）。环节1 要为整部剧输出
        # 人物/场景/道具/伏笔 + 每一集的边界锚点，4k 根本不够 —— 输出被截断
        # 表现为 JSON 不完整，会白重试两次再报错。给足了就是一次成功。
        self.max_tokens = int(max_tokens or 0)

    def chat(self, system: str, user: str, retries: int = 3, log=None,
             cancel=None, on_usage=None) -> str:
        """一次对话。默认走流式。

        为什么必须流式：非流式时，整个生成要在「timeout 秒内一个字节都没来」
        之前完成 —— 环节1 那种 12 万 token 输入、上万 token 输出的请求，
        模型思考几分钟不吐字就会被判读超时，然后（更糟）被当成网络抖动重试，
        900 秒 × 3 次 = 白等 45 分钟还可能让上游重复计费。
        流式下 token 持续到达，读超时的语义变成「块与块之间的间隔」，
        长生成不会误判；顺便还能报进度，不用干等着猜死没死。
        """
        url = self.base_url + "/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        base = {
            "model": self.model,
            "messages": ([{"role": "system", "content": system}] if system else [])
                        + [{"role": "user", "content": user}],
        }
        if self.max_tokens > 0:
            base["max_tokens"] = self.max_tokens
        # (连接超时, 读超时)。流式下读超时是「两个数据块之间」的间隔，
        # 不是整次生成的总时长 —— 这正是我们要的语义。
        tmo = (30, max(60, int(self.timeout)))

        use_stream, want_usage = True, True
        last = None
        if log:
            log(f"发出 {len(user)} 字（约 {rough_tokens(user)} token），"
                f"上限 {self.max_tokens} token，流式")
        for attempt in range(retries):
            try:
                body = dict(base, stream=use_stream)
                if use_stream and want_usage:
                    # 让服务商在最后一个块里回传 usage —— 不然只能靠估算，
                    # 对不上后台账单时没法查
                    body["stream_options"] = {"include_usage": True}
                if use_stream:
                    return self._stream_once(url, headers, body, proxies, tmo,
                                             log, cancel, on_usage)
                return self._plain_once(url, headers, body, proxies, tmo, log,
                                        on_usage)
            except _Retryable as exc:                 # 429/5xx，值得重试
                last = exc
                if attempt < retries - 1:
                    time.sleep(2 ** attempt * 2)
                    continue
                raise LLMError(str(exc)) from exc
            except _NoStreamOptions as exc:           # 这家不认 stream_options，去掉再试
                if want_usage:
                    (log or (lambda m: None))(
                        f"这家不认 stream_options（{str(exc)[:60]}），"
                        f"去掉它重试；token 数只能靠估算了")
                    want_usage = False
                    continue
                raise LLMError(str(exc)) from exc
            except _NoStream as exc:                  # 这家不支持流式，退回普通请求
                if use_stream:
                    (log or (lambda m: None))(f"这家不支持流式（{exc}），改用普通请求")
                    use_stream = False
                    continue
                raise LLMError(str(exc)) from exc
            except requests.Timeout as exc:
                # 超时不重试：这不是抖动，是这次请求太重。重试只会再白等一轮，
                # 而且上游可能已经算完并计了费。直接说清怎么办。
                raise LLMFatal(
                    f"等了 {tmo[1]} 秒还没收到新内容，判定超时（已放弃，没有重试——"
                    f"重试只会再白等一轮，还可能让上游重复计费）。"
                    f"这一步的输入是 {len(user)} 字。要么去「设置 → 分析引擎」"
                    f"把超时调大，要么换一个出得快的模型，"
                    f"要么把剧本拆小再跑。原始错误：{exc}") from exc
            except requests.RequestException as exc:
                last = exc
                if attempt < retries - 1:
                    time.sleep(2 ** attempt * 2)
                    continue
                raise LLMError(f"网络错误: {exc}") from exc
        raise LLMError(f"重试耗尽: {last}")

    # ---------------------------------------------------------------- 两种收法
    def _check_status(self, r) -> None:
        if r.status_code in (429, 502, 503, 504):
            raise _Retryable(f"HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            if not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1"):
                r.encoding = "utf-8"
            txt = r.text[:400]
            low = txt.lower()
            # 先判 stream_options（更具体），再判 stream —— 顺序反了会误退化成非流式
            if "stream_options" in low or "include_usage" in low:
                raise _NoStreamOptions(f"HTTP {r.status_code}: {txt}")
            if "stream" in low:
                raise _NoStream(f"HTTP {r.status_code}: {txt}")
            raise LLMError(f"HTTP {r.status_code}: {txt}")

    def _finish(self, content: str, reason: str, usage: Optional[dict] = None) -> str:
        if not content.strip():
            # 空回复要重试，不能当场判死。多集并发时网关被打满，很多家不回 429
            # 而是回一个 200 加空 content —— 那是限流，退一步再来就好。
            # 直接失败会把这一集后面所有环节连带跳过，代价大得离谱：
            # 空回复没产生输出 token，重试几乎不花钱，而丢一集要重跑二十几次调用。
            # 真是内容被拒的话，重试完还是空，最后照样报 LLM_EMPTY 并提示查剧本。
            #
            # 把服务商给的线索一起带上：线上碰到 EP01-SEG07 连空三次，事后只有
            # 一句「回复内容为空」，查不出是被拒、被截断、还是上游吐了个空。
            # 这些线索本来就在响应里，不记下来纯属浪费。
            u = usage or {}
            det = u.get("completion_tokens_details") or {}
            cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            hint = "；".join(x for x in (
                f"结束原因={reason or '（服务商没给）'}",
                f"输出 token={u.get('completion_tokens')}" if u else "",
                f"其中思考 token={det.get('reasoning_tokens')}"
                if det.get("reasoning_tokens") else "",
                f"输入 token={u.get('prompt_tokens')}（缓存命中 {cached}）" if u else "",
            ) if x)
            # 文案里别写「限流」「发得太快」这类词：诊断是按关键词归类的，
            # 带上就会被归成 RATE_LIMITED，盖掉 LLM_EMPTY 那张卡里
            # 「先降并发、再查内容」的完整说明。原因交给那张卡讲。
            raise _Retryable(f"回复内容为空（{hint}）" if hint else "回复内容为空")
        if reason == "length":
            raise LLMFatal(
                f"模型输出被长度上限截断了（max_tokens={self.max_tokens}），"
                f"所以 JSON 不完整。去「设置 → 分析引擎」把「单次输出上限」调大，"
                f"或者把剧本拆成更少的集数分批处理。")
        return content

    def _stream_once(self, url, headers, body, proxies, tmo, log, cancel=None,
                     on_usage=None) -> str:
        started = time.time()
        parts, reason, ticks = [], "", 0
        closed = False              # 有没有正常收到 [DONE] / finish_reason
        usage = None                # 服务商回传的真实 token 数（可能没有）
        with requests.post(url, headers=headers, json=body, timeout=tmo,
                           proxies=proxies, stream=True) as r:
            self._check_status(r)
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "text/event-stream" not in ctype and "stream" not in ctype:
                # 说了 stream 却回了整包 JSON —— 按普通响应解析，不算错
                data = r.json()
                ch = (data.get("choices") or [{}])[0]
                return self._finish((ch.get("message") or {}).get("content") or "",
                                    ch.get("finish_reason") or "", data.get("usage"))
            for raw in r.iter_lines(decode_unicode=False):
                # 每收到一块就看一眼有没有被取消。不看的话「取消」只是设了个
                # 标志位，这边照样把整次生成读完 —— 钱照花、人照等。
                # 退出 with 会关掉连接，上游那边也就停了。
                if cancel and cancel():
                    n = sum(len(p) for p in parts)
                    raise LLMCancelled(
                        f"已按取消停止接收（收到 {n} 字就断开了，"
                        f"用了 {int(time.time()-started)} 秒）")
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    closed = True
                    break
                try:
                    d = json.loads(chunk)
                except ValueError:
                    continue                       # 心跳/注释行，跳过
                ch = (d.get("choices") or [{}])[0]
                piece = ((ch.get("delta") or {}).get("content")
                         or (ch.get("message") or {}).get("content") or "")
                if piece:
                    parts.append(piece)
                if ch.get("finish_reason"):
                    reason = ch["finish_reason"]
                    closed = True
                if d.get("usage"):
                    usage = d["usage"]
                # 每 15 秒报一次，让人知道它在吐字而不是卡死
                if log and time.time() - started > (ticks + 1) * 15:
                    ticks += 1
                    n = sum(len(p) for p in parts)
                    log(f"正在生成… 已等 {int(time.time()-started)} 秒，收到 {n} 字")
        n = sum(len(p) for p in parts)
        if on_usage:
            # 没回传 usage 的家用估算兜底，标明 estimated 让记账那边知道
            u = dict(usage) if usage else {
                "prompt_tokens": rough_tokens(
                    "".join(m.get("content", "") for m in body.get("messages", []))),
                "completion_tokens": rough_tokens("".join(parts)),
                "estimated": True}
            on_usage(dict(u, model=self.model, seconds=round(time.time() - started, 1)))
        if not closed:
            # 流没有正常收尾就断了（既没 [DONE] 也没 finish_reason）。
            # 这跟「模型答得不合格」是两回事：不该拿去反馈重试，该当网络问题重试。
            # 尤其别在这时候报「回复内容为空」—— 那会让人以为是模型拒答。
            raise _Retryable(
                f"连接在流传输中途断开：只收到 {n} 字就没了"
                f"（等了 {int(time.time()-started)} 秒，没有收到正常的结束标记）。"
                f"这是传输中断，不是模型答错，会自动重试。")
        if log:
            secs = int(time.time() - started)
            if usage:
                log(f"生成完成：输出 {n} 字，用了 {secs} 秒。"
                    f"服务商记账 输入 {usage.get('prompt_tokens', '?')} token"
                    f"（其中缓存 {(usage.get('prompt_tokens_details') or {}).get('cached_tokens', 0)}）"
                    f"／输出 {usage.get('completion_tokens', '?')} token")
            else:
                log(f"生成完成：输出 {n} 字（约 {rough_tokens(''.join(parts))} token），"
                    f"用了 {secs} 秒。这家没回传 usage，以上是估算")
        return self._finish("".join(parts), reason, usage)

    def _plain_once(self, url, headers, body, proxies, tmo, log=None,
                    on_usage=None) -> str:
        r = requests.post(url, headers=headers, json=body, timeout=tmo, proxies=proxies)
        self._check_status(r)
        data = r.json()
        u = data.get("usage") or {}
        if on_usage:
            on_usage(dict(u, model=self.model, seconds=0))
        if log and u:
            log(f"服务商记账 输入 {u.get('prompt_tokens', '?')} token"
                f"／输出 {u.get('completion_tokens', '?')} token")
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"无 choices: {json.dumps(data, ensure_ascii=False)[:300]}")
        return self._finish((choices[0].get("message") or {}).get("content") or "",
                            choices[0].get("finish_reason") or "", u)

    def json_call(self, system: str, user: str, required: Optional[list] = None,
                  json_retries: int = 2, log=print, cancel=None,
                  on_usage=None,
                  validator: Optional[Callable[[Any], list]] = None) -> Any:
        """要求 JSON 输出；解析失败或缺键时把错误反馈给模型重试（≤json_retries）。

        只有「模型这次答得不合格」才重试 —— 反馈具体哪里不对，让它再答一次。
        超时和输出被截断**不重试**：同一个提示词重试必然同样超时、同样截断，
        白花三倍的钱。这类直接往上抛，由诊断告诉用户去调什么。
        """
        attempt_user = user
        for attempt in range(1 + json_retries):
            if cancel and cancel():
                raise LLMCancelled("已按取消停止")
            text = self.chat(system, attempt_user, log=log, cancel=cancel,
                             on_usage=on_usage)
            try:
                data = extract_json(text)
                missing = check_keys(data, required or [])
                if missing:
                    raise LLMError(f"输出缺少必需字段: {missing}")
                problems = validator(data) if validator else []
                if problems:
                    shown = list(problems)[:24]
                    tail = f"；另有{len(problems) - len(shown)}处" \
                        if len(problems) > len(shown) else ""
                    raise LLMError("输出结构不符合要求: " + "；".join(shown) + tail)
                return data
            except (LLMFatal, LLMCancelled):
                raise                                  # 超时/截断/取消，重试无意义
            except (json.JSONDecodeError, LLMError) as exc:
                if attempt >= json_retries:
                    raise LLMError(f"JSON 输出校验失败（已重试{json_retries}次）: {exc}") from exc
                log(f"    JSON 校验失败，反馈重试 {attempt + 1}/{json_retries}: {str(exc)[:150]}")
                attempt_user = (user + "\n\n【上次输出的问题】" + str(exc)[:400]
                                + "\n请严格只输出一个符合要求的 JSON（用 ```json 围栏包裹），不要输出其他内容。")
        raise LLMError("不可达")
