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


def _env_proxy() -> str:
    """这台机器上会被 requests 自动采用的 HTTPS 代理，没有就返回空。

    urllib 的 getproxies() 读的正是 requests 会读的那几处：
    环境变量 HTTPS_PROXY/ALL_PROXY，以及 Windows 的系统代理设置。
    """
    import urllib.request
    p = urllib.request.getproxies()
    return p.get("https") or p.get("http") or p.get("all") or ""


def mask_url(u: str) -> str:
    """代理地址里可能带账号密码，打日志前抹掉。"""
    return re.sub(r"://[^/@]+@", "://***:***@", u or "")


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


def _balanced(text: str, opener: str, closer: str):
    """从第一个 opener 开始找配平的那一段。找不到返回 None（说明没写完）。"""
    start = text.find(opener)
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
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
                return text[start:i + 1]
    return None


def _truncated(body: str, n: int) -> "LLMFatal":
    """输出写到一半断了。这和「格式不对」是两回事，报错必须分开。"""
    tail = re.sub(r"\s+", " ", body[-120:])
    return LLMFatal(
        f"模型的输出**没写完**就断了：收到 {n} 字，JSON 一直没有闭合，"
        f"最后停在「…{tail}」。\n"
        f"这不是格式不对，也不是模型不听话 —— 是这一步的输出太大，"
        f"在写到一半时被截断了。重试同一个提示词多半还是同样的结果。\n"
        f"能试的几条：把「分析引擎」的流式关掉（部分中转站的长响应会被切断）；"
        f"换一个输出上限更高的模型；或者先只跑一集（「只测第一集」），"
        f"让这一步要产出的东西少一些。完整原文见项目里的 07_检查与记录/失败原文/。")


def extract_json(text: str) -> Any:
    """从 LLM 回复中提取 JSON：```json 围栏优先，其次首个 {..} / [..] 平衡块。

    **截断要单独认出来。** 以前的写法在外层对象没配平时会「退而求其次」
    去找第一个 `[`，于是把 JSON 里某个恰好完整的内层数组（比如 entities）
    当成整个文档返回 —— 校验随后报「输出缺少必需字段」。
    实跑就这么骗了一整轮：三次全是输出被截断，程序却报了两种不同的错，
    指向「模型没按 schema 输出」这个完全错误的方向。
    """
    body = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", body)
    if fenced:
        return json.loads(fenced.group(1).strip())
    # 开了围栏却没闭合 = 写到一半断了
    if re.search(r"```(?:json)?\s", body):
        raise _truncated(body, len(body))
    if "{" in body:
        blk = _balanced(body, "{", "}")
        if blk is not None:
            return json.loads(blk)
        # 有 { 但配不平 —— 就是没写完。**不许**再去找 []，
        # 那样会捞出一个内层数组冒充整个文档。
        raise _truncated(body, len(body))
    blk = _balanced(body, "[", "]")
    if blk is not None:
        return json.loads(blk)
    # 把模型实际回了什么带上。只说「没找到 JSON」等于什么都没说 ——
    # 它到底是写了一段散文、拒答了、还是用了别的围栏，改法完全不同：
    #   散文/解说   → 模板或 _common 里的「只输出 JSON」被改掉了
    #   拒答/安全语 → 内容触发了审核，要改剧本措辞
    #   围栏不对    → 模型习惯问题，提示词里加一句示例
    # 不带原文的话，这三种在日志里长得一模一样。
    head = re.sub(r"\s+", " ", body[:200])
    tail = re.sub(r"\s+", " ", body[-120:]) if len(body) > 320 else ""
    raise LLMError(
        f"回复中未找到可解析的 JSON。模型实际回了 {len(body)} 字，"
        f"开头是：{head or '（空）'}"
        + (f" …… 结尾是：{tail}" if tail else "")
        + "。完整原文见项目里的 07_检查与记录/失败原文/")


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
                 timeout: int = 600, proxy: str = "", max_tokens: int = 16000,
                 stream: bool = False):
        if not api_key:
            raise LLMError("缺少 llm_api_key")
        self.api_key = api_key
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.timeout = timeout
        # 三态，不是「填了/没填」两态：
        #   ""        跟随系统与环境代理（requests 默认行为，向后兼容）
        #   "direct"  强制直连，忽略系统与环境代理
        #   其它      指定这个代理
        # 为什么要有「强制直连」这一档：requests 在 proxies=None 时会**自动**
        # 读 HTTPS_PROXY 和 Windows 系统代理。机器上挂着 Clash 之类的东西时，
        # 请求会悄悄绕一圈，而那些工具常在 100 秒左右掐掉长连接 ——
        # 表现是「流式中途断」或「Unable to connect to proxy」，
        # 看起来像服务商挂了。程序这边连「我在走代理」都不说，根本查不到。
        self.proxy = (proxy or "").strip()
        # 所有 OpenAI 兼容 LLM 共用。默认非流式：部分中转站的 SSE 长连接会在
        # 约 100 秒被网关提前切断；需要实时字数时再由设置页显式开启。
        self.stream = bool(stream)
        # 不显式给上限的话走服务商默认值（常见 4k）。环节1 要为整部剧输出
        # 人物/场景/道具/伏笔 + 每一集的边界锚点，4k 根本不够 —— 输出被截断
        # 表现为 JSON 不完整，会白重试两次再报错。给足了就是一次成功。
        self.max_tokens = int(max_tokens or 0)

    # ------------------------------------------------------------ 代理
    DIRECT = ("direct", "直连", "none", "off")

    def _session(self, trust_env: bool):
        """整个 LLM 对象共用一个 Session。

        必须用 Session 而不是 requests.post：`trust_env` 只能在 Session 上设，
        裸 requests.post 每次自己建一个 trust_env=True 的临时 session，
        「强制直连」就成了一句空话。
        一个对象一个 Session 顺带拿到 keep-alive —— 一集五十次调用，
        每次重新握手 TLS 是白等。
        """
        s = getattr(self, "_sess", None)
        if s is None:
            s = self._sess = requests.Session()
        s.trust_env = trust_env
        return s

    def close(self):
        s = getattr(self, "_sess", None)
        if s is not None:
            s.close()
            self._sess = None

    def resolve_proxy(self) -> tuple:
        """(给 requests 的 proxies, trust_env, 给人看的一句话)。

        这句话必须打进日志。之前排一个「n2 老是断」排了半天，
        就是因为谁也不知道请求实际走了代理 —— 报错只说「连不上代理」，
        不说是哪个代理、从配置来还是从环境来。
        """
        if self.proxy.lower() in self.DIRECT:
            return {"http": None, "https": None}, False, "强制直连（忽略系统与环境代理）"
        if self.proxy:
            return ({"http": self.proxy, "https": self.proxy}, False,
                    f"走配置指定的代理 {mask_url(self.proxy)}")
        env = _env_proxy()
        if env:
            return (None, True,
                    f"跟随系统/环境代理 {mask_url(env)}（程序没配代理，"
                    f"是这台机器上的设置）—— 长请求被它掐断过的话，"
                    f"把「网络」改成强制直连再试")
        return None, True, "直连（系统与环境都没有代理）"

    def chat(self, system: str, user: str, retries: int = 3, log=None,
             cancel=None, on_usage=None, on_partial=None) -> str:
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
        proxies, trust_env, proxy_note = self.resolve_proxy()
        sess = self._session(trust_env)
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

        use_stream, want_usage = self.stream, self.stream
        last = None
        if log:
            mode = ("流式（实时显示接收字数）" if use_stream else
                    "非流式（完成后一次返回，期间不会显示接收字数）")
            log(f"发出 {len(user)} 字（约 {rough_tokens(user)} token），"
                f"上限 {self.max_tokens} token，{mode}")
            # 网络路径必须报出来。不报的话，代理掐连接会被当成服务商挂了。
            log(f"网络：{proxy_note}")
            # 配置被夹住过就说一句。只在设置页提示的话，看日志的人看不到 ——
            # 而看日志的时候才是他在纳闷「我填的明明是别的数」。
            for n in getattr(self, "config_notes", ()) or ():
                log(f"注意：{n}")
        for attempt in range(retries):
            try:
                body = dict(base, stream=use_stream)
                if use_stream and want_usage:
                    # 让服务商在最后一个块里回传 usage —— 不然只能靠估算，
                    # 对不上后台账单时没法查
                    body["stream_options"] = {"include_usage": True}
                if use_stream:
                    return self._stream_once(sess, url, headers, body, proxies,
                                             tmo, log, cancel, on_usage,
                                             on_partial)
                return self._plain_once(sess, url, headers, body, proxies, tmo,
                                        log, on_usage, on_partial)
            except _Retryable as exc:                 # 429/5xx，值得重试
                last = exc
                if attempt < retries - 1:
                    delay = 2 ** attempt * 2
                    (log or (lambda m: None))(
                        f"[传输中断] 第 {attempt + 1}/{retries} 次请求未完整结束：{exc}。"
                        f"本次内容不会进入 JSON 校验；{delay} 秒后重新发送同一任务"
                        f"（传输重试 {attempt + 1}/{retries - 1}）")
                    time.sleep(delay)
                    continue
                (log or (lambda m: None))(
                    f"[传输失败] 第 {attempt + 1}/{retries} 次请求仍未完整结束：{exc}。"
                    "传输重试已耗尽，本任务没有形成可保存结果")
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
                    delay = 2 ** attempt * 2
                    (log or (lambda m: None))(
                        f"[网络中断] 第 {attempt + 1}/{retries} 次请求失败：{exc}。"
                        f"{delay} 秒后重新发送同一任务"
                        f"（传输重试 {attempt + 1}/{retries - 1}）")
                    time.sleep(delay)
                    continue
                (log or (lambda m: None))(
                    f"[网络失败] 第 {attempt + 1}/{retries} 次请求仍失败：{exc}。"
                    "传输重试已耗尽，本任务没有形成可保存结果")
                raise LLMError(f"网络错误: {exc}") from exc
        raise LLMError(f"重试耗尽: {last}")

    # ---------------------------------------------------------------- 两种收法
    @staticmethod
    def _err_body(text: str) -> str:
        """错误响应里真正有用的那一句。

        中转站挂在 CDN 后面时，出错返回的是**整页 HTML** —— 直接截 200 字
        会得到一堆 `<!--[if lt IE 7]>`，而真正的那一句
        （比如「524: A timeout occurred」）在几百字之后，被埋掉了。
        实跑就这么埋过一次：日志里全是 IE 条件注释，
        看不出这是 Cloudflare 入口超时而不是模型慢。
        """
        s = (text or "").strip()
        head = s[:400].lower()
        if "<html" not in head and "<!doctype" not in head:
            return s[:300]
        m = re.search(r"<title[^>]*>(.*?)</title>", s, re.S | re.I)
        title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        m2 = re.search(r"(\d{3}:\s*[A-Z][^<\r\n]{0,80})", s)
        line = m2.group(1).strip() if m2 else ""
        got = "；".join(x for x in (title, line) if x)
        if not got:
            got = re.sub(r"<[^>]+>", " ", s)
            got = re.sub(r"\s+", " ", got).strip()[:200]
        return got + "（原文是一整页 HTML 错误页，这里只摘了要点）"

    def _check_status(self, r) -> None:
        if r.status_code in (429, 502, 503, 504):
            raise _Retryable(f"HTTP {r.status_code}: {self._err_body(r.text)}")
        if r.status_code >= 400:
            if not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1"):
                r.encoding = "utf-8"
            txt = self._err_body(r.text)
            low = (r.text or "")[:800].lower()
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

    def _stream_once(self, sess, url, headers, body, proxies, tmo, log, cancel=None,
                     on_usage=None, on_partial=None) -> str:
        # 丢弃之前先交给调用方存盘。不存的话，连接断在第几个字段、
        # 模型是不是正在写某个超长数组，全都看不到 —— 而那是排
        # 「老是断在中途」唯一有用的证据。之前三次断流丢了两万多字。
        def keep(why):
            if on_partial and parts:
                try:
                    on_partial("".join(parts), why)
                except Exception:                       # noqa: BLE001
                    pass                                # 存盘失败不能盖掉真错误
        started = time.time()
        parts, reason, ticks = [], "", 0
        closed = False              # 有没有正常收到 [DONE] / finish_reason
        usage = None                # 服务商回传的真实 token 数（可能没有）

        def stream_lines(response):
            try:
                yield from response.iter_lines(decode_unicode=False)
            except requests.Timeout:
                raise
            except requests.RequestException as exc:
                n = sum(len(p) for p in parts)
                keep(f"流式连接中断：{exc}")
                raise _Retryable(
                    f"流式连接异常中断：本次已收到 {n} 字，但没有形成完整回复，"
                    f"已接收内容将被丢弃（耗时 {int(time.time()-started)} 秒；"
                    f"原始错误：{exc}）") from exc

        with sess.post(url, headers=headers, json=body, timeout=tmo,
                           proxies=proxies, stream=True) as r:
            self._check_status(r)
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "text/event-stream" not in ctype and "stream" not in ctype:
                # 说了 stream 却回了整包 JSON —— 按普通响应解析，不算错
                data = r.json()
                ch = (data.get("choices") or [{}])[0]
                return self._finish((ch.get("message") or {}).get("content") or "",
                                    ch.get("finish_reason") or "", data.get("usage"))
            for raw in stream_lines(r):
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
            keep("流没有正常收尾就断了")
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

    def _plain_once(self, sess, url, headers, body, proxies, tmo, log=None,
                    on_usage=None, on_partial=None) -> str:
        r = sess.post(url, headers=headers, json=body, timeout=tmo, proxies=proxies)
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
                  on_usage=None, on_partial=None,
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
                             on_usage=on_usage, on_partial=on_partial)
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
                if on_partial and text:
                    try:
                        on_partial(text, f"JSON 校验不过（第 {attempt + 1} 次）："
                                         f"{str(exc)[:200]}")
                    except Exception:                   # noqa: BLE001
                        pass
                if attempt >= json_retries:
                    raise LLMError(f"JSON 输出校验失败（已重试{json_retries}次）: {exc}") from exc
                log(f"[JSON 校验重试] 第 {attempt + 1}/{json_retries} 次："
                    f"{str(exc)[:150]}。这次是内容结构不合格，不是传输中断")
                attempt_user = (user + "\n\n【上次输出的问题】" + str(exc)[:400]
                                + "\n请严格只输出一个符合要求的 JSON（用 ```json 围栏包裹），不要输出其他内容。")
        raise LLMError("不可达")
