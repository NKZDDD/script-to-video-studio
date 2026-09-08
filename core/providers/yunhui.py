# -*- coding: utf-8 -*-
"""云绘 AI（ai.yunhuiart.cn）Seedance 视频网关。只做视频。

依据《云绘AI-客户API接入文档》（2026-09-07 用户提供的那一份）：

  · GET  /v1/models                     模型列表（**以它实时返回的为准**）
  · POST /v1/videos                     创建异步任务
  · GET  /v1/videos/{task_id}           查询状态；completed 时带 `url`
  · GET  /v1/videos/{task_id}/content   下载，**可能 302 到临时签名地址**

请求体是扁平的：`model` / `prompt` / `seconds` / `aspect_ratio`（四个必填）
加 `images` / `audios` / `videos` 三个字符串数组（HTTPS URL 或 Base64 Data URL）。

⚠ **`seconds` 必须按字符串发。** 文档的参数表写的是 `string/number`，
但实跑发数字回：

    HTTP 400 {"code":"invalid_request",
              "message":"json: cannot unmarshal number into Go struct field
                         .Alias.seconds of type string"}

那不是「值不合适」，是请求体整个没被解析、任务根本没建。文档两处示例
（curl 和 Python）用的都是 `"seconds": "15"`，**照示例走，别照参数表走**。
本项目里阿珂、超模、好漫剧、M86 也都是这条约定。

⚠ **取成品那一步不能用 JSON 那份请求头。** `/content` 带
`Accept/Content-Type: application/json` 去请求，回：

    HTTP 502 {"code":"artifact_request_rejected",
              "message":"Artifact redirect was rejected"}

文档的官方示例只带一个 `Authorization`，那样能用。所以这里走
`session.save_item()`（下载通道，用 `_download_headers()`），
**绝不给 `poll()` 传 `content_path_tpl`** —— 那条支路是拿 JSON 通道去请求的。
（阿珂/小霸龙/一手传那个参数是对的，它们的 `/content` 回 JSON。
  同一个参数名对两种端点行为，必须分开。）

计费（文档第 6 节，直接决定失败怎么处理）：
  · 按次计费，`seconds` **不影响单价**（所以短片不省钱）
  · 创建时预扣一次，**查询状态和下载不会重复扣费**
  · 创建失败不扣费；生成失败或取消自动退回
  → 所以「已经 completed、只是取不回来」时**绝不能重投**：重投是重新生成、
    再扣一次，而下载本身免费、随便重试。

⚠ 时长和比例**文档没有给可校验的上限**：`seconds` 写「通常 4～60 秒，
具体以模型能力为准」，`aspect_ratio` 写「例如 16:9、9:16、1:1」。
所以这里只按 4–60 兜底，**不自造逐模型的档位** —— 自造约束会把合法请求
拦在本地，报错说「不支持」而其实支持，比服务商回 400 更难查。
"""

from __future__ import annotations

import time
from typing import Callable, Optional

try:                                  # 内置加载：在 core.providers 包内，相对导入可用
    from ..apiutil import ApiError, extract_task_id, extract_video_url
    from .base import Provider, VideoTask
except ImportError:                   # 插件加载：独立顶层模块，没有父包，用绝对导入
    from core.apiutil import ApiError, extract_task_id, extract_video_url
    from core.providers.base import Provider, VideoTask

# 文档第 2 节列出的 14 个，**一个不多一个不少**。
# 只当界面首次打开时的兜底；文档原话「请优先以此接口实时返回的模型列表为准」，
# 所以真正的清单由 list_models() 实拉。
#
# ⚠ 上一版这里有 19 个，多出来的 5 个（jd-seedance-2.5-720p、jd-seedance-2.0-720p、
# jd-seedance-2.0-720p-903、Seedance MINI 933、Quality V4）**文档里都没有**，
# 是从本项目别家的清单里串过来的（`Quality V4` 是巨轮的，`933` 后缀是小裴/好漫剧的）。
# 而 default_model 恰好是那 5 个里的一个 —— 不手改模型就直接 404
# （文档第 7 节：「404：模型名或任务 ID 不存在，请检查空格、大小写和完整 ID」）。
#
# 名字里的空格、大小写（`720P` 和 `720p` 两种都有）、`wd-Seedance` 的大写 S
# **照抄，别手打**。
VIDEO_MODELS = [
    "cd-seedance 2.0 720p",
    "ed-seedance 2.0 720p",
    "ed-seedance 2.0 1080p",
    "ed-seedance 2.0 fast 720p",
    "md-seedance-2.0-480p",
    "md-seedance-2.0-720p",
    "nd-seedance-2.0 480p",
    "nd-seedance-2.0 720p",
    "pd-seedance-2.0 9tu",
    "pd-seedance-2.0 480P 933",
    "pd-seedance-2.0 720P 933",
    "ud-seedance 2.0-480p",
    "ud-seedance 2.0-720p",
    "wd-Seedance 2.0",
]
DEFAULT_MODEL = "nd-seedance-2.0 720p"      # 文档两处示例用的就是这个

# 文档只说「例如」，不是封闭清单 → 只当界面上的候选，不用来拦请求。
RATIO_HINTS = ["16:9", "9:16", "1:1"]
# 文档「通常为 4～60 秒，具体以模型能力为准」。真实上限只有服务商知道。
SEC_MIN, SEC_MAX = 4, 60

# 文档「素材上传限制」
MAX_IMAGES, MAX_VIDEOS, MAX_AUDIOS = 9, 3, 3
MAX_MEDIA_TOTAL = 12                  # 图 + 音频 + 视频**合计**最多 12 个
IMAGE_MB_LIMIT = 10                   # 上游单张 10MB；文档建议压到 9MB 以内


class YunhuiProvider(Provider):
    id = "yunhui"
    name = "云绘 AI ai.yunhuiart.cn（Seedance）"
    aliases = ("yunhuiart", "云绘", "云绘AI", "yh")
    default_base_url = "https://ai.yunhuiart.cn"
    supports = ("video",)
    # images/audios/videos 三个数组「HTTPS URL 或 Base64 Data URL」都收 → data_uri。
    # 文档还建议：有公网地址时优先传 URL，Base64 会多出约 33% 体积
    # （而整包上限是 100MB，6 张图走 base64 很容易顶到）。
    ref_mode = "data_uri"

    def __init__(self, api_key: str = "", base_url: str = "", proxy: str = "",
                 timeout: int = 900):
        # 和 Gate 同理：Windows 系统代理对这类网关的大包上传容易假死，默认直连。
        super().__init__(api_key, base_url, proxy or "direct", timeout)

    def capabilities(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "default_base_url": self.default_base_url,
            "supports": list(self.supports),
            "video": {
                "models": VIDEO_MODELS,
                "default_model": DEFAULT_MODEL,
                "ratios": RATIO_HINTS,
                "durations": list(range(SEC_MIN, SEC_MAX + 1)),
                "default_duration": 15,
                # 分辨率**写在模型名里**（480p / 720p / 1080p），没有单独的字段。
                # 所以这里不给 resolutions —— 给了页面上会多一个填了不生效的框。
                "max_refs": MAX_IMAGES,
                "max_video_refs": MAX_VIDEOS,
                "max_audio_refs": MAX_AUDIOS,
                "ref_mode": "data_uri",
                "notes": "四个必填：model / prompt / seconds / aspect_ratio。"
                         "⚠ seconds 按**字符串**发（文档参数表写 string/number，"
                         "实测发数字整个请求体不会被解析）。"
                         "素材是 images/audios/videos 三个字符串数组，"
                         "HTTPS URL 或 Base64 Data URL 都收，有公网地址优先给 URL"
                         "（base64 多约 33% 体积，整包上限 100MB）。"
                         f"图≤{MAX_IMAGES} 张（单张上游 {IMAGE_MB_LIMIT}MB，建议压到 9MB）、"
                         f"音频≤{MAX_AUDIOS}（单个 30MB）、视频≤{MAX_VIDEOS}（单个 60MB），"
                         f"**三类合计≤{MAX_MEDIA_TOTAL} 个**。"
                         f"时长文档写「通常 {SEC_MIN}～{SEC_MAX} 秒，具体以模型能力为准」，"
                         "这里不自造逐模型档位，超出范围由服务商回 400。"
                         "分辨率在模型名里，没有单独字段。"
                         "按次计费，seconds 不影响单价；创建预扣一次，"
                         "查询和下载不重复扣费；生成失败或取消自动退回。",
            },
            "notes": "模型清单以 list_models()（GET /v1/models）实拉为准 —— 文档原话。"
                     "模型名带空格且大小写不一（720P/720p、wd-Seedance），照抄别手打，"
                     "打错回 404。默认直连，不继承 Windows 系统代理。"
                     "轮询别太密：文档要求前 1 分钟 5 秒一次、之后 10～15 秒一次，"
                     "且「请勿高频并发查询同一个任务」。",
        }

    def list_models(self) -> list:
        try:
            data = self.session.request("GET", "/v1/models", retries=2, timeout=30)
        except ApiError:
            return []
        return sorted({str(m.get("id")) for m in (data.get("data") or [])
                       if m.get("id")})

    # ------------------------------------------------------------ 请求体
    def build_body(self, task: VideoTask, log: Callable = print) -> dict:
        """把一条任务变成请求体。**单独拎出来是为了能不联网就测字段类型** ——
        `seconds` 发错类型的代价是整个请求体不被解析，而那只有真发一次才看得见。
        """
        model = (task.model or DEFAULT_MODEL).strip()
        images = list(task.refs or [])
        videos = list(task.extra.get("video_refs") or task.extra.get("videos") or [])
        audios = list(task.extra.get("audio_refs") or task.extra.get("audios") or [])
        sec = int(task.duration or 15)
        ratio = task.ratio or "9:16"

        problems = []
        if not SEC_MIN <= sec <= SEC_MAX:
            problems.append(f"时长文档写 {SEC_MIN}–{SEC_MAX} 秒，收到 {sec} 秒")
        for got, cap, what in ((images, MAX_IMAGES, "图片"),
                               (videos, MAX_VIDEOS, "视频"),
                               (audios, MAX_AUDIOS, "音频")):
            if len(got) > cap:
                problems.append(f"{what}素材最多 {cap} 个，收到 {len(got)} 个")
        total = len(images) + len(videos) + len(audios)
        if total > MAX_MEDIA_TOTAL:
            # 文档单列的一条，容易漏：三类各自都没超，合计也可能超。
            problems.append(f"图片+音频+视频合计最多 {MAX_MEDIA_TOTAL} 个，"
                            f"收到 {total} 个（图{len(images)}/音频{len(audios)}/"
                            f"视频{len(videos)}）")
        # 素材只收 HTTPS URL 或 data: 开头的 Base64 数据地址，裸路径不行。
        bad = [r for r in images + videos + audios
               if not str(r).startswith(("https://", "http://", "data:"))]
        if bad:
            problems.append(f"参考素材必须是 HTTPS URL 或 Base64 Data URL，"
                            f"有 {len(bad)} 条不是（本机文件要先转 data URI 或上传图床）")
        if problems:
            raise ApiError(f"云绘 {model} 的参数不符合要求：" + "；".join(problems),
                           status=0, kind="task_fatal")

        # **比例不在本地拦。** 文档只给了「例如 16:9、9:16、1:1」，
        # 不是封闭清单；自造清单会把合法比例拦下来，报错说「不支持」而其实支持。
        if ratio not in RATIO_HINTS:
            log(f"比例 {ratio} 不在文档举例的 {'、'.join(RATIO_HINTS)} 里 —— "
                f"照发，不合法的话服务商会回 400（文档写的是「例如」，不是全集）。")

        # ⚠ seconds 必须是**字符串**，见文件头。发 int 会 400 且任务不会创建。
        # 网关是 Go 写的，json.Unmarshal **碰到第一个类型不符就停**，
        # 所以这条改对之后如果还报 unmarshal，那是下一个字段，不是这条没生效。
        body: dict = {"model": model, "prompt": task.prompt or "",
                      "seconds": str(sec), "aspect_ratio": ratio}
        if images:
            body["images"] = images
        if audios:
            body["audios"] = audios
        if videos:
            body["videos"] = videos
        return body

    # ------------------------------------------------------------ 取成品
    def _content_url(self, task_id: str) -> str:
        return f"{self.session.base_url}/v1/videos/{task_id}/content"

    def _probe_content(self, task_id: str) -> str:
        """下载失败了，探一次 `/content` 的真实形状，把事实写进报错。

        探的是**不跟随重定向**那一次 —— 跟随完就看不见 Location 了，
        而 Location 正是要看的东西（文档只说「可能 302」，没说跳去哪）。

        ⚠ 只记主机和路径，**绝不记查询串**：临时签名地址的 query 里就是
        下载凭证，打进日志等于把它散出去（这个项目的日志是会被截图发出来的）。
        """
        import requests                                        # noqa: PLC0415
        try:
            r = requests.get(
                self._content_url(task_id),
                headers={"Authorization": f"Bearer {self.session.api_key}",
                         "User-Agent": "ScriptToVideoRunner/2.0",
                         "Accept": "*/*"},
                timeout=60, allow_redirects=False, stream=True)
            if 300 <= r.status_code < 400:
                loc = (r.headers.get("Location") or "").split("?", 1)[0]
                out = (f"探到的形状：/content 回 {r.status_code} 跳转到 "
                       f"{loc or '（没给 Location）'}")
            else:
                out = (f"探到的形状：/content 直接回 {r.status_code}，"
                       f"Content-Type={r.headers.get('Content-Type') or '未给'}")
            r.close()
            return out
        except Exception as exc:                               # noqa: BLE001
            return f"想探一下 /content 的形状，连探都失败了：{exc}"

    def generate_video(self, task: VideoTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 5, poll_timeout: int = 2400) -> dict:
        body = self.build_body(task, log)
        model = body["model"]
        log(f"云绘 {model}: {body['seconds']}s {body['aspect_ratio']} "
            f"图{len(body.get('images') or [])}/"
            f"音频{len(body.get('audios') or [])}/"
            f"视频{len(body.get('videos') or [])}")
        try:
            data = self.session.request("POST", "/v1/videos", json_body=body,
                                        retries=2, timeout=300)
        except ApiError as exc:
            # 文档第 7 节的两条，通用错误码给不出来，只有这家自己知道：
            if "fail_to_fetch_task" in str(exc):
                exc.extra_fix.append(
                    "文档：`400 fail_to_fetch_task` + 提示素材过大 = 参考素材超过上游限制。"
                    "把参考图压到 9MB 以内再试（上游单张上限 10MB），"
                    "或者改成传公网 HTTPS URL —— Base64 会多出约 33% 体积。")
            if getattr(exc, "status", 0) == 404:
                exc.extra_fix.append(
                    f"文档：404 = 模型名或任务 ID 不存在，**检查空格和大小写**。"
                    f"这家的名字带空格且大小写不一（720P/720p、wd-Seedance），"
                    f"照抄别手打。当前发的是 {model!r}。"
                    f"可用清单点「实拉模型」（GET /v1/models）看，文档那份只是兜底。")
            if getattr(exc, "status", 0) == 403:
                exc.extra_fix.append(
                    "文档：403 = 账户余额不足，或者当前 API Key 无权使用该模型。"
                    "换个模型试一下就能分清是余额还是权限。")
            raise

        task_id = extract_task_id(data)
        url = extract_video_url(data)

        if not url:
            if not task_id:
                raise ApiError(f"提交没返回任务 ID: {str(data)[:300]}")
            # 文档第 4 节的 completed 示例里**是有 `url` 的**，但实跑那次没有
            # （否则 poll 早就返回了，不会走到 /content）。所以两条路都要留：
            # 有 url 就用它，没有就走 /content 下载。
            # 顺手留一份最后那次轮询的响应体 —— 「到底有哪些字段」是排错的线索，
            # 而文档和实际对不上时，只能靠这个。
            seen: dict = {}

            def pick(d):
                seen["last"] = d
                return extract_video_url(d)

            try:
                url = self.session.poll("/v1/videos/{id}", task_id, picker=pick,
                                        interval=poll_interval, timeout=poll_timeout,
                                        log=log, cancel=cancel)
                # ⚠ 不给 poll 传 content_path_tpl，理由见文件头。
            except ApiError as exc:
                if "未取到结果" not in str(exc):
                    raise
                last = seen.get("last")
                keys = (sorted(last.keys()) if isinstance(last, dict)
                        else type(last).__name__)
                log(f"完成了，但响应体里没有直链（顶层字段：{keys}；文档示例里本该有 "
                    f"`url`）—— 转去 /v1/videos/{{id}}/content 下载。")
                url = self._content_url(task_id)

        # 下载**可以放心重试**：文档明写「查询状态和下载不会重复扣费」。
        # 生成不行 —— 那是重新扣一次。所以只在这一层转圈。
        last_exc: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                self.session.save_item(url, dest)
                return {"task_id": task_id, "source": url,
                        "provider": self.id, "model": model}
            except Exception as exc:                           # noqa: BLE001
                last_exc = exc
                if attempt < 3:
                    log(f"取成品失败（第 {attempt}/3 次，重试不花钱）：{exc}")
                    time.sleep(3 * attempt)

        # 走到这儿：**片子已经生成好、已经计费了**，只是取不回来。
        # 所以标 task_fatal —— 让执行器**别重投**。502 默认被 classify 判成
        # retryable，而重投在这一层是「从头再生成一次」：一次取不回来最多付
        # 三次生成费，而且什么都换不到（实跑就是这么烧的，2026-09-07）。
        raise ApiError(
            f"片子生成好了（任务 {task_id}，状态 completed，费用已扣），但取不回来："
            f"{last_exc}\n"
            f"{self._probe_content(task_id)}\n"
            f"⚠ 这一步**不重投**：下载免费、生成要钱，重投买不到任何东西。\n"
            f"手工取：curl -L -H 'Authorization: Bearer <KEY>' "
            f"{self._content_url(task_id)} -o out.mp4\n"
            f"（文档第 5 节：临时签名地址可能过期，尽快下。第 7 节：5xx 属上游或"
            f"网关临时异常，保留这个任务 ID 找服务方。）",
            status=0, kind="task_fatal") from last_exc
