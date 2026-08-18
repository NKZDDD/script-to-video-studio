# -*- coding: utf-8 -*-
"""LLM 分析引擎客户端（OpenAI 兼容 /v1/chat/completions，任意中转站）。

用于环节1-8的剧情分析与提示词编译：固定提示词模板 → LLM → JSON 校验 → 失败带错误反馈重试。
这是"调用API分析剧本"，编排本身是确定性代码。
"""

from __future__ import annotations

import json
import re
import threading
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


class _TokenCap(RuntimeError):
    """这家的输出上限比我们发的小，而且它把数字告诉我们了 —— 按它说的重发。

    不自愈的代价很实在：整个请求被 400 挡回来，几十万 token 的输入白发一遍、
    白等两分钟，然后报一句「参数不合法」。而正确的值就写在报错里。
    """

    def __init__(self, limit: int, msg: str):
        super().__init__(msg)
        self.limit = limit


# 约 2 字/token。实测（paisio + gpt-5.6-sol，中文+印尼语混排的剧本）：
#   10022 字 → prompt_tokens 8440，减去网关固定注入的 3840 缓存前缀 = 4600 → 2.18
#    5033 字 → prompt_tokens 6454，同样减 3840 = 2614 → 1.93
# 注意那个 3840：**每次调用都有**，是网关自己的前缀，不是我们发的内容。
# 直接用 prompt_tokens 除字数会把比值算低近一倍（我第一版就错在这）。
# 这个常数只用来在发请求前给个量级提示，真实数字一律以回传的 usage 为准。
CHARS_PER_TOKEN = 2.0

# 「你发的 max_tokens 太大了，上限是 N」——各家措辞不一样，数字倒都会给。
# 实收到过：Field 'max_output_tokens' must be at most 128000
_CAP_RE = re.compile(
    r"(?:max_output_tokens|max_tokens|max_completion_tokens)[^0-9]{0,60}?"
    r"(\d{3,7})", re.I)


def token_cap(text: str) -> int:
    """报错里说的输出上限。没说返回 0。"""
    m = _CAP_RE.search(text or "")
    return int(m.group(1)) if m else 0


# 这些状态码值得原样重发一次。
#
# **524 以前不在这里，代价很实在。** 它落到了「>= 400 一律 LLMError」那一支：
# 既不重试、也不换服务商，一次 524 就把整个环节判死，下游一串跟着停。
# 实跑一晚上撞了三次（n4b、n5、环节8 的一段），三次都是**一次都没重**。
#
# 52x 是 Cloudflare 边缘自己报的，跟模型答得对不对没有半点关系：
#   520 源站回了它看不懂的东西    521 源站拒绝连接
#   522 连不上源站                523 源站不可达
#   524 源站太久没回应 ← 撞到的就是它
#   525/526 TLS 握手/证书         527/529 边缘自己的毛病
# 全都是「这一下没走通」，重发一次经常就过了。
RETRY_STATUS = frozenset({429, 502, 503, 504,
                          520, 521, 522, 523, 524, 525, 526, 527, 529})


def stop_note(reason: str, usage: Optional[dict] = None) -> str:
    """模型为什么停下来，以及有多少输出 token 花在了思考上。

    以前这两个都只在「回复内容为空」和 finish_reason=length 两种情况下才用，
    其余一律丢掉。而排「写到一半就断」时，需要的恰恰就是它们：

      · reason=length  → 真的撞上限了，调大上限或者把活拆小
      · reason=stop 但 JSON 没闭合 → 模型「以为」自己写完了，
        或者中转站截断了却没设这个字段。**调上限没有任何用**，
        往那个方向排查是白费时间 —— 实跑在这上面耗过一整轮。
      · 思考 token 占掉一大半 → 输出预算其实没花在正文上

    这些字段本来就在响应里，不记下来纯属浪费。
    """
    u = usage or {}
    det = u.get("completion_tokens_details") or {}
    bits = [f"结束原因={reason or '（服务商没给）'}"]
    if det.get("reasoning_tokens"):
        bits.append(f"其中思考 {det['reasoning_tokens']} token")
    return "　" + "，".join(bits)


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


def _truncated(body: str, n: int, reason: str = "") -> "LLMError":
    """JSON 没闭合。**为什么没闭合，决定了该跟模型说什么。**

    这段话是给模型的反馈（json_call 会附在下一次的提示词后面），
    所以说错话是有代价的 —— 而以前不管三七二十一都说同一句
    「太长了没写完，请重新输出更紧凑的 JSON」。

    **一次都不许让它压缩。** 这是想清楚之后的结论，不是保守起见：

    输出长度是**由内容决定的** —— 这一集有几场戏、这部剧有几个资产。
    要它把十几万 token 压成几万，压掉的不是形容词，是场次和条目：
    压完的东西和原来那份**不是同一个产物**，只是恰好能装进一次调用。
    这个项目在这上面栽过一次（V6.0 太精简，把故事板吃了）。

    装不下的正确解法是**分段处理**，不是让模型少写：n3 改成按集跑、
    n4b 改成按资产分批之后，原来怎么都过不去的两步一次就过了。

    而且「真撞上限」这一支在实跑中**根本走不到**：`_finish()` 遇到
    `finish_reason == "length"` 会先抛 LLMFatal，压根到不了这里。
    也就是说每一次走到这个函数的截断，原因都是下面两种 ——
    那句「更紧凑」从来没有一次是对的，纯粹在损害产出。

        结束原因=stop     模型自己认为写完了 —— 不是长度问题，是它把
                          JSON 写坏了（少括号、多逗号）。要它「更紧凑」，
                          它就真的删内容，而括号还是少的。
        结束原因=（没给）  多半是中转站在传输中途切的。模型压根没做错什么，
                          原样重发经常就过了。要它压缩，等于**为了绕开
                          一次网络抖动，永久牺牲这一步的产出**。

    **可重试，不是 Fatal。** 实跑数据：同一个模型、同一步、同一份剧本，
    一次断在 8570 token，另一次 16251 token 就成功了。是随机的 ——
    但重试的意义在于「再传一次」，不在于「让它写少点」。
    """
    tail = re.sub(r"\s+", " ", body[-100:])
    head = (f"上一次的输出没有形成完整的 JSON（收到 {n} 字，括号始终没有闭合，"
            f"停在「…{tail}」）。")
    if (reason or "").lower() == "stop":
        return LLMError(
            head + "而结束原因是 stop —— 你**认为自己已经写完了**，"
            "所以这不是长度问题，是 JSON 本身写坏了（多半少了收尾的括号，"
            "或者多了个逗号）。\n"
            "请把**同样的内容**重新输出一遍，重点检查括号配对和结尾。"
            "**不要删减任何字段或条目** —— 内容是对的，坏的只是格式。")
    return LLMError(
        head + "服务商没给结束原因，多半是传输在中途被切断了 —— "
        "不是你写错了什么。\n"
        "请把**同样的内容**原样再输出一遍。"
        "**不要压缩、不要删减字段或条目**：内容没有问题，"
        "为了绕开一次传输抖动而把产出改少，代价比重发一次大得多。")


def _first_json(inner: str, log: Optional[Callable] = None,
                reason: str = "") -> Any:
    """取开头那一份完整的 JSON，后面多出来的东西丢掉。

    只有**前面这一份是完整的**才算数 —— 不完整时照旧当截断处理，
    别把「写到一半」当成「多写了一点」，那两件事的修法完全相反。
    """
    try:
        obj, end = json.JSONDecoder().raw_decode(inner)
    except json.JSONDecodeError:
        raise _truncated(inner, len(inner), reason)   # 前面就没写完，是截断
    rest = inner[end:].strip()
    if rest and log:
        # 说一声。不说的话，模型每次都多吐一截也没人知道 ——
        # 而这里是「我们替它擦了屁股」，属于该看得见的那一类。
        log(f"⚠️ JSON 之后还跟了 {len(rest)} 个字符，已丢弃："
            f"{rest[:80]!r}（前面那份 JSON 是完整的，按它用）")
    return obj


def extract_json(text: str, log: Optional[Callable] = None,
                 reason: str = "") -> Any:
    """从 LLM 回复中提取 JSON：```json 围栏优先，其次首个 {..} / [..] 平衡块。

    **截断要单独认出来。** 以前的写法在外层对象没配平时会「退而求其次」
    去找第一个 `[`，于是把 JSON 里某个恰好完整的内层数组（比如 entities）
    当成整个文档返回 —— 校验随后报「输出缺少必需字段」。
    实跑就这么骗了一整轮：三次全是输出被截断，程序却报了两种不同的错，
    指向「模型没按 schema 输出」这个完全错误的方向。

    **JSON 后面跟了别的东西时取前面那一份。** 实跑撞到 n11 的一段：
    模型写完了一份完整的 JSON（四个顶层键齐全、finish_reason=stop），
    然后把结尾的 `]` `}` 又多写了一遍。整段 `json.loads` 报
    `Extra data: line 110 column 3`，于是判成校验失败、重试三次、这一段废掉 ——
    而那份 JSON 本身是好的，一个字都不缺。
    """
    body = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", body)
    if fenced:
        inner = fenced.group(1).strip()
        try:
            return json.loads(inner)
        except json.JSONDecodeError:
            return _first_json(inner, log, reason)
    # 开了围栏却没闭合 = 写到一半断了
    if re.search(r"```(?:json)?\s", body):
        raise _truncated(body, len(body), reason)
    if "{" in body:
        blk = _balanced(body, "{", "}")
        if blk is not None:
            return json.loads(blk)
        # 有 { 但配不平 —— 就是没写完。**不许**再去找 []，
        # 那样会捞出一个内层数组冒充整个文档。
        raise _truncated(body, len(body), reason)
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
    """轻量结构校验。返回缺失项列表。

    三种写法：

        "a"        这个键要在
        "b[]"      必须是**非空**数组 —— 空了等于这一步什么都没产出
        "b[]?"     必须是数组，**可以为空**

    第三种是后加的，因为「非空」在很多地方是一个**关于剧本的假设**，
    而剧本没法穷举：

      · 一部平铺直叙的剧没有 `reality_threads`（没有回忆/平行线）
      · 一部戏里可能一件道具都没有（`prop_specs` / `prop_instances`）
      · 一个段落可能就是一个长镜头，没有任何转场（`vt` / `transitions`）
      · **审计一个问题都没发现时 `findings` 就是空的** —— 而这本来是好事

    最后那条最能说明问题：全都要求非空的话，**一集拍得完美反而会让
    审计这一步失败**，然后重试三次、报「模型没按格式回答」。
    """
    missing = []
    for spec in required:
        parts = spec.split(".")
        nodes = [data]
        ok = True
        for part in parts:
            may_empty = part.endswith("[]?")
            if may_empty:
                part = part[:-1]                # 去掉 ?，剩下 xxx[]
            is_list = part.endswith("[]")
            key = part[:-2] if is_list else part
            nxt = []
            for n in nodes:
                if not isinstance(n, dict) or key not in n:
                    ok = False
                    break
                v = n[key]
                if is_list:
                    if not isinstance(v, list) or (not v and not may_empty):
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
        # 上一次调用是怎么结束的（finish_reason + usage）。按线程存 ——
        # 逐段跑的环节多线程共用同一个实例，存在实例上会把别的段的结果串过来。
        self._last = threading.local()
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
        # 每次调用先清掉，免得这一次根本没走到记录点时，
        # 「失败原文」的文件头印上一次的结束原因。
        self._last.stop = None
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
            except _TokenCap as exc:                  # 上限比我们发的小，按它说的降
                if self.max_tokens and exc.limit < self.max_tokens:
                    (log or (lambda m: None))(
                        f"这条线路的输出上限是 {exc.limit:,}，"
                        f"而我们发的是 {self.max_tokens:,} —— 降到它说的值重发一次。"
                        f"（去「设置 → 分析引擎」把「单次输出上限」改成 "
                        f"{exc.limit:,} 以内，就不用每次都白发一遍）")
                    self.max_tokens = exc.limit
                    base["max_tokens"] = exc.limit
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

    def _check_status(self, r, started: float = 0.0) -> None:
        if r.status_code in RETRY_STATUS:
            waited = f"（等了 {int(time.time() - started)} 秒）" if started else ""
            raise _Retryable(
                f"HTTP {r.status_code}{waited}: {self._err_body(r.text)}")
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
            cap = token_cap(r.text or "")
            if cap and r.status_code in (400, 422):
                raise _TokenCap(cap, f"HTTP {r.status_code}: {txt}")
            raise LLMError(f"HTTP {r.status_code}: {txt}")

    def _finish(self, content: str, reason: str, usage: Optional[dict] = None) -> str:
        # 记下这一次是怎么结束的，好让「失败原文」的文件头能写上。
        # **这是判断截断属于哪一类唯一有用的字段**（见 stop_note），
        # 而它以前只进运行日志 —— 排错包里恰恰没有运行日志，
        # 于是发过来的那份文件看不出是撞上限、模型自以为写完、还是中转站切断。
        # 用 thread-local：一个 LLM 实例会被多段并发共用，存在实例上会串。
        self._last.stop = {"reason": reason, "usage": dict(usage or {})}
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
                f"模型输出撞到了长度上限（max_tokens={self.max_tokens}），"
                f"所以 JSON 不完整。\n"
                f"**这一步要产出的东西，本来就装不进一次调用** —— "
                f"该做的是把它拆开分批跑，不是让模型写少一点。"
                f"输出长度是由内容决定的（有几场戏、有几个资产），"
                f"压成一半就是内容少了一半，那不是同一份产物。\n"
                f"调大上限也基本没用：网关自己有硬上限（实测 128000），"
                f"再往上整个请求会被 400 挡回来。")
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
        parts = []                  # 收到的正文块；心跳线程也读它

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

        # **心跳必须独立于数据到达。**
        #
        # 原来这一句写在下面 `for raw in stream_lines(r):` 的循环体里 ——
        # 没有数据到达，循环体就不执行，于是一条日志都不出。
        # 实跑：第9环节 10:27:27 发出请求，到 10:37 整整十分钟**一行都没有**，
        # 看上去就是死了；实际它一直在等模型吐第一个字节。
        #
        # 而这段静默恰恰是最该看见的：模型的思考期不产生任何数据，
        # 中转站看不到数据就会在 125 秒左右切断（那些 524 就是这么来的）。
        # 分不清「还在想」和「已经挂了」，人只能干等或者瞎重启。
        stop_beat = threading.Event()
        # 从请求体算，不引用外层的 user —— 这个函数的签名里根本没有 user，
        # 写了只会在心跳第一次响的时候抛 NameError，而那是在后台线程里，
        # 主流程看不见、只是从此不再有心跳。
        sent = sum(len(m.get("content") or "") for m in body.get("messages") or [])

        def beat():
            while not stop_beat.wait(15):
                waited = int(time.time() - started)
                got = sum(len(p) for p in parts)
                if got:
                    log(f"正在生成… 已等 {waited} 秒，收到 {got} 字")
                else:
                    log(f"正在等模型开口… 已等 {waited} 秒，**还没收到第一个字**。"
                        f"这是思考期，线上没有任何数据 —— "
                        f"中转站看不到数据可能会在 125 秒左右切断（HTTP 524）。"
                        f"输入越大想得越久，这一次发了 {sent} 字。")

        if log:
            threading.Thread(target=beat, daemon=True).start()
        try:
            return self._stream_body(sess, url, headers, body, proxies, tmo, log,
                                     cancel, on_usage, on_partial, started, parts,
                                     keep, stream_lines)
        finally:
            stop_beat.set()

    def _stream_body(self, sess, url, headers, body, proxies, tmo, log, cancel,
                     on_usage, on_partial, started, parts, keep, stream_lines) -> str:
        reason, skipped = "", []
        closed = False
        usage = None
        with sess.post(url, headers=headers, json=body, timeout=tmo,
                           proxies=proxies, stream=True) as r:
            self._check_status(r, started)
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
                    # 心跳/注释行，跳过 —— 但**要数**。
                    # 这里丢的可能是正文块，而丢了之后一点痕迹都没有：
                    # 收到的字数少一截，JSON 于是配不平，看上去和
                    # 「模型没写完」一模一样，两者的修法却完全相反。
                    # 排到「是不是我们没收全」的时候，没有这个数就只能猜。
                    skipped.append(chunk[:120])
                    continue
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
            # 这条路走不到 _finish，得自己记 —— 不记的话文件头上会印着
            # **上一次调用**留下的结束原因，比没有还糟。
            self._last.stop = {"reason": "（没有收到结束标记，连接中途断了）",
                               "usage": dict(usage or {})}
            keep("流没有正常收尾就断了")
            # 流没有正常收尾就断了（既没 [DONE] 也没 finish_reason）。
            # 这跟「模型答得不合格」是两回事：不该拿去反馈重试，该当网络问题重试。
            # 尤其别在这时候报「回复内容为空」—— 那会让人以为是模型拒答。
            secs = int(time.time() - started)
            # 一个字都没收到，和「写到一半断了」是两回事，别混成一句话：
            # 前者是模型还在思考、线上一直没有字节，被空闲超时切掉 ——
            # 流式救不了它（思考期本来就不吐字），要减小的是**输入**。
            # 后者才是传输中途出问题。指错方向的代价很实在：
            # 前一种去调输出上限、开关流式，全是白费。
            raise _Retryable(
                (f"连接断开时一个字都还没收到（等了 {secs} 秒）。"
                 f"这多半是模型的思考期超过了中转站的空闲上限 —— "
                 f"它在吐第一个 token 之前不发任何数据，流式也盖不住这段静默。"
                 f"会自动重试。" if n == 0 else
                 f"连接在流传输中途断开：只收到 {n} 字就没了"
                 f"（等了 {secs} 秒，没有收到正常的结束标记）。"
                 f"这是传输中断，不是模型答错，会自动重试。"))
        if log:
            secs = int(time.time() - started)
            if usage:
                log(f"生成完成：输出 {n} 字，用了 {secs} 秒。"
                    f"服务商记账 输入 {usage.get('prompt_tokens', '?')} token"
                    f"（其中缓存 {(usage.get('prompt_tokens_details') or {}).get('cached_tokens', 0)}）"
                    f"／输出 {usage.get('completion_tokens', '?')} token"
                    + stop_note(reason, usage))
            else:
                log(f"生成完成：输出 {n} 字（约 {rough_tokens(''.join(parts))} token），"
                    f"用了 {secs} 秒。这家没回传 usage，以上是估算"
                    + stop_note(reason, usage))
            if skipped:
                # 正常情况下这是 0。不是 0 就得看一眼跳过的到底是心跳还是正文 ——
                # 如果是正文，那 JSON 配不平的原因是**我们没收全**，
                # 不是模型没写完，而这两件事的修法完全相反。
                log(f"⚠️ 有 {len(skipped)} 个 data: 块解析不了被跳过了，"
                    f"头一个是：{skipped[0]!r}　"
                    f"—— 如果那不是心跳行，说明收到的正文少了一截")
        return self._finish("".join(parts), reason, usage)

    def _plain_once(self, sess, url, headers, body, proxies, tmo, log=None,
                    on_usage=None, on_partial=None) -> str:
        started = time.time()
        r = sess.post(url, headers=headers, json=body, timeout=tmo, proxies=proxies)
        self._check_status(r, started)
        data = r.json()
        u = data.get("usage") or {}
        choices = data.get("choices") or []
        reason = (choices[0].get("finish_reason") or "") if choices else ""
        if on_usage:
            on_usage(dict(u, model=self.model, seconds=0))
        if log and u:
            log(f"服务商记账 输入 {u.get('prompt_tokens', '?')} token"
                f"／输出 {u.get('completion_tokens', '?')} token"
                + stop_note(reason, u))
        if not choices:
            raise LLMError(f"无 choices: {json.dumps(data, ensure_ascii=False)[:300]}")
        return self._finish((choices[0].get("message") or {}).get("content") or "",
                            reason, u)

    def json_call(self, system: str, user: str, required: Optional[list] = None,
                  json_retries: int = 2, log=print, cancel=None,
                  on_usage=None, on_partial=None,
                  validator: Optional[Callable[[Any], list]] = None,
                  on_soft: Optional[Callable[[list], None]] = None) -> Any:
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
                # 结束原因决定了该跟模型说什么（见 _truncated）——
                # 记了不用等于没记，而说错话会让它白白删内容。
                # 双层 getattr：测试里的假 LLM 不走 __init__，没有 _last。
                # 拿不到结束原因不该炸，退回「没给」那一支就好。
                stop = (getattr(getattr(self, "_last", None), "stop", None)
                        or {}).get("reason", "")
                data = extract_json(text, log, stop)
                missing = check_keys(data, required or [])
                if missing:
                    raise LLMError(f"输出缺少必需字段: {missing}")
                problems = validator(data) if validator else []
                if problems and on_soft:
                    # **业务规则不拦，只记。**
                    #
                    # 这些规则是我们对「剧本长什么样」的假设，而剧本结构
                    # 没法穷举：这次是穿越回忆，下次可能是梦境、平行时空、
                    # 双主角双线。任何写死的规则迟早撞上一个它没想到的剧本。
                    #
                    # 实跑撞过：一部穿越剧，同一段里主角现实在医院、回忆在操场，
                    # 模型规规矩矩写了两条，被判成「人物空间记录重复」——
                    # 模型是对的，规则是错的。而规则错时重试**必然**三次都失败：
                    # 同一份 2.8 万字的输出白跑三遍，最后整步卡死。
                    #
                    # 真正「下游读不了」的东西上面两道已经拦住了：
                    # JSON 解析不了 = 截断；缺必需字段 = 数组是空的。
                    # 再往后还有出图前的参考图解析、身份映射检查和四道闸门。
                    try:
                        on_soft(list(problems))
                    except Exception:                       # noqa: BLE001
                        pass                                # 记录失败不能盖掉正事
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
