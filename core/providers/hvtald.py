# -*- coding: utf-8 -*-
"""HVTALD 空间（z988.top）。底层是**即梦 AI 国际版**，一手工作室转售。

和本程序接过的所有服务商都不是一类，但对外**做成普通 Provider**，
在前端就是下拉里多一家、照常排进优先级链 —— 它的怪癖全在这个文件里消化掉：

| 它的怪癖 | 这里怎么消化 |
|---|---|
| 没有比例字段，**比例要写在 prompt 最前面** | 自动把 `task.ratio` 拼到提示词开头 |
| 固定 15 秒 / 1080P，不可调 | capabilities 只报 15 秒；给别的值就纠正并说明 |
| 结果靠 **回调** `feedbackurl`，不是查任务 | 没有公网回调时改为轮询 WebDAV 的 `outs/` |
| 成片在 WebDAV 里、要 basic auth | 自己下载，不走 save_item 的匿名取 |
| 认证是 `deviceId`+`token` 写在 body 里 | 从 api_key 里解出来，见下面 `_creds()` |

**凭据怎么传**：`build()` 只给 api_key / base_url / proxy / timeout，
而这家要 5 个值（deviceId、token、webdav 地址、用户名、密码）。
所以 api_key 字段接受三种写法，用户把客服给的东西**原样粘进去**就行：

  1. JSON：`{"deviceId":"…","token":"…","webDavUrl":"…","user":"…","password":"…"}`
  2. 分号键值：`deviceId=…;token=…;webdav=…;user=…;password=…`
  3. 留空 → 读环境变量 `HVTALD_DEVICE_ID` / `HVTALD_TOKEN` /
     `HVTALD_WEBDAV_URL` / `HVTALD_WEBDAV_USER` / `HVTALD_WEBDAV_PASSWORD`

⚠ 成片**只保存 48 小时**，且 API 与 WebDAV 都是明文 HTTP。
"""

from __future__ import annotations

import json
import os
import random
import re
import string
import time
import xml.etree.ElementTree as ET
from typing import Callable, Optional
from urllib.parse import unquote, urlparse

import requests

from ..apiutil import ApiError
from .base import Provider, VideoTask

API_PATH = "/dy/brush/fromApi"
MAX_REFS = 9
FIXED_DURATION = 15                       # 固定 15 秒，文档写死
RATIOS = ["9:16", "16:9", "1:1", "4:3", "3:4"]
_RATIO_HEAD = re.compile(r"^\s*\d{1,2}\s*[:：]\s*\d{1,2}")

_ENV = {
    "device_id": "HVTALD_DEVICE_ID", "token": "HVTALD_TOKEN",
    "webdav_url": "HVTALD_WEBDAV_URL", "user": "HVTALD_WEBDAV_USER",
    "password": "HVTALD_WEBDAV_PASSWORD",
}
_ALIAS = {
    "deviceid": "device_id", "device_id": "device_id",
    "token": "token",
    "webdavurl": "webdav_url", "webdav_url": "webdav_url", "webdav": "webdav_url",
    "user": "user", "username": "user", "webdav_user": "user",
    "password": "password", "pass": "password", "webdav_password": "password",
}


def _action_id() -> str:
    """24 位小写字母。

    文档请求参数表写「32 位」、回调参数表写「24 位」，自相矛盾；
    示例 `jdbamfupzohjmbsnxsombhip` 是 24 位纯小写 —— 以示例为准，
    示例是真跑出来的，表格是人写的。
    """
    return "".join(random.choice(string.ascii_lowercase) for _ in range(24))


def parse_creds(api_key: str) -> dict:
    """把 api_key 里的凭据解出来。JSON / 分号键值 / 环境变量，三种都认。

    宽容解析是刻意的：用户手上是客服发来的一段文本，格式五花八门。
    与其让他学一种格式，不如这里多认几种 —— 认不出来的项再退回环境变量。
    """
    out = {k: "" for k in _ENV}
    raw = (api_key or "").strip()

    if raw.startswith("{"):
        try:
            for k, v in (json.loads(raw) or {}).items():
                key = _ALIAS.get(str(k).strip().lower())
                if key and isinstance(v, (str, int)):
                    out[key] = str(v).strip()
        except Exception:                                   # noqa: BLE001
            pass
    elif "=" in raw:
        for part in re.split(r"[;\n]+", raw):
            if "=" not in part:
                continue
            k, _, v = part.partition("=")
            key = _ALIAS.get(k.strip().lower())
            if key:
                out[key] = v.strip()

    for key, env in _ENV.items():                # 没填的退回环境变量
        if not out[key]:
            out[key] = os.environ.get(env, "").strip()
    return out


class HvtaldProvider(Provider):
    id = "hvtald"
    name = "HVTALD 空间（即梦国际版·15秒）"
    aliases = ("z988", "即梦国际", "hv")
    default_base_url = "http://ha.z988.top"
    supports = ("video",)
    # imgs 要「绝对地址」，本机图必须先传对象存储换链接
    ref_mode = "url"
    # **按账号计费，而且一个账号同时只能生成一条。** 想并发只能配多个账号 ——
    # 并发上限就是账号数。挤在同一个账号上不会报「并发超限」这种明白话，
    # 只会排队超时或者直接拒，而失败记录里只看得到「生成失败」。
    per_account_serial = True

    # 账号表单。键名和上面 `_ALIAS` 认的一致 —— 不一致就是「填了读不到」。
    #
    # 共用/各自的分法按客服实际给凭据的形状来：deviceId + token 一个账号一份，
    # webdav 那三项通常整批共用（写一次）。单个账号要用别的存储时可以覆盖。
    account_form = {
        "shared_label": "共用存储（WebDAV，填一次给所有账号用）",
        "shared": (
            ("webdav", "WebDAV 地址", False,
             "客服给的那个 https 地址，成片会传到这里（**只保存 48 小时**）"),
            ("user", "WebDAV 用户名", False, ""),
            ("password", "WebDAV 密码", True, ""),
        ),
        "per_label": "账号（一个账号同时只能跑一条，配几个就是几路并发）",
        "per": (
            ("deviceId", "deviceId / 设备号", False, "客服给的那一串"),
            ("token", "token", True, ""),
            ("webdav", "这个账号单独用的 WebDAV 地址", False,
             "留空 = 用上面共用的那个"),
            ("user", "单独的用户名", False, "留空 = 用共用的"),
            ("password", "单独的密码", True, "留空 = 用共用的"),
        ),
    }

    def __init__(self, api_key: str = "", base_url: str = "", proxy: str = "",
                 timeout: int = 900):
        super().__init__(api_key=api_key, base_url=base_url, proxy=proxy, timeout=timeout)
        self.creds = parse_creds(api_key)
        self._timeout = timeout

    # ------------------------------------------------------------ 能力
    def capabilities(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "default_base_url": self.default_base_url,
            "supports": list(self.supports),
            "video": {
                "models": ["即梦国际版"],
                "default_model": "即梦国际版",
                "ratios": RATIOS,
                "durations": [FIXED_DURATION],       # 只有 15 秒，别给前端选别的
                "default_duration": FIXED_DURATION,
                "resolutions": ["1080p"],
                "max_refs": MAX_REFS,
                "ref_mode": "url",
                "notes": "固定 **15 秒 / 1080P**，不卡人脸，参考图 ≤9 张、必须是公网绝对地址。"
                         "这家**没有比例字段** —— 比例是从提示词最前面提取的，"
                         "本类会自动把所选比例拼到 prompt 开头，调用方照常设 ratio 即可。"
                         "并发 = 开通的线路数；成片在 WebDAV 里**只保存 48 小时**。",
            },
            "notes": "凭据填在 API Key 里（JSON 或 deviceId=…;token=…;webdav=…;user=…;password=… ），"
                     "也可用 HVTALD_* 环境变量。API 与 WebDAV 均为明文 HTTP。",
        }

    def ready(self) -> tuple:
        """凭据齐不齐。缺哪项直接说，别等发出去才 401。"""
        missing = [k for k, v in self.creds.items() if not v]
        return (not missing, missing)

    # ------------------------------------------------------------ WebDAV
    def _auth(self):
        return (self.creds["user"], self.creds["password"])

    def _dav(self, sub: str = "") -> str:
        base = self.creds["webdav_url"].rstrip("/")
        return f"{base}/{sub.strip('/')}" if sub.strip("/") else base

    def _list(self, sub: str = "outs") -> list:
        """PROPFIND 列目录 → [(文件名, 完整URL)]。WebDAV 就是 HTTP 扩展方法，不用额外依赖。"""
        url = self._dav(sub)
        try:
            r = requests.request("PROPFIND", url, auth=self._auth(),
                                 headers={"Depth": "1", "Content-Type": "application/xml"},
                                 timeout=60)
        except requests.RequestException as exc:
            raise ApiError(f"连不上 WebDAV（{url}）：{exc}")
        if r.status_code == 404:
            return []
        if r.status_code >= 400:
            raise ApiError(f"WebDAV 列目录失败 HTTP {r.status_code}（{url}）"
                           f"—— 401/403 多半是空间账号密码不对")
        out = []
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError as exc:
            raise ApiError(f"WebDAV 返回的不是合法 XML：{exc}")
        self_path = urlparse(url).path.rstrip("/")
        for href in root.iter("{DAV:}href"):
            raw = (href.text or "").strip()
            if not raw or raw.rstrip("/").endswith(self_path):
                continue
            name = unquote(raw.rstrip("/").rsplit("/", 1)[-1])
            if name:
                full = raw if raw.startswith("http") else (
                    f"{urlparse(url).scheme}://{urlparse(url).netloc}{raw}")
                out.append((name, full))
        return out

    def _download(self, url: str, dest: str) -> str:
        """带 basic auth 下载。**先写 .part 再改名** —— 下到一半断了不能留个够大的坏文件，
        那种文件能过大小检查、下次 isfile 为真被跳过，成片里就永远缺一段。"""
        os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
        r = requests.get(url, auth=self._auth(), stream=True, timeout=self._timeout)
        if r.status_code >= 400:
            raise ApiError(f"成片下载失败 HTTP {r.status_code}: {url}")
        part = dest + ".part"
        try:
            with open(part, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
            if os.path.getsize(part) < 1024:
                raise ApiError(f"成片只有 {os.path.getsize(part)} 字节，不是有效视频：{url}")
            os.replace(part, dest)
        finally:
            if os.path.exists(part):
                os.remove(part)
        return dest

    # ------------------------------------------------------------ 出片
    def build_body(self, task: VideoTask, action_id: str = "") -> dict:
        """拼投递 body。**比例自动拼进 prompt** 是这家的关键适配点。"""
        prompt = (task.prompt or "").strip()
        ratio = (task.ratio or "9:16").strip()
        if not _RATIO_HEAD.match(prompt):
            # 这家没有比例字段，系统从提示词最前面提取 —— 调用方照常设 ratio，
            # 这里替他拼上，免得每个上层都得记住这条怪癖。
            prompt = f"{ratio} {prompt}".strip()
        refs = [r for r in (task.refs or []) if str(r).startswith(("http://", "https://"))]
        dropped = len(task.refs or []) - len(refs)
        if dropped:
            raise ApiError(
                f"HVTALD 的 imgs 要「绝对地址」，这一项给的 {dropped} 张不是。"
                f"本该有 {len(task.refs)} 张参考图 —— 少了出来的就不是同一个人，"
                f"所以不出这条。去「设置 → 参考图上传」配对象存储。",
                status=0, kind="task_fatal")
        if not refs:
            raise ApiError("HVTALD 的 imgs 是必填项，至少要 1 张参考图（绝对地址）",
                           status=0, kind="task_fatal")
        return {
            "deviceId": self.creds["device_id"],
            "token": self.creds["token"],
            "actionId": action_id or _action_id(),
            "imgs": refs[:MAX_REFS],
            "prompt": prompt,
            "webDavUrl": self.creds["webdav_url"],
            "user": self.creds["user"],
            "password": self.creds["password"],
        }

    def generate_video(self, task: VideoTask, dest: str, *, log: Callable = print,
                       cancel: Optional[Callable] = None,
                       poll_interval: int = 30, poll_timeout: int = 1800) -> dict:
        ok, missing = self.ready()
        if not ok:
            raise ApiError(
                f"HVTALD 凭据不全，缺：{missing}。"
                f"把客服给的信息粘到 API Key 里（JSON 或 deviceId=…;token=…;webdav=…;"
                f"user=…;password=… ），或设 HVTALD_* 环境变量。",
                status=0, kind="task_fatal")

        if int(task.duration or FIXED_DURATION) != FIXED_DURATION:
            log(f"HVTALD 固定出 {FIXED_DURATION} 秒（不可调），已忽略 duration={task.duration}")

        body = self.build_body(task)
        aid = body["actionId"]
        feedback = task.extra.get("feedbackurl")
        if feedback:
            body["feedbackurl"] = feedback

        log(f"HVTALD 投递 actionId={aid} 参考图{len(body['imgs'])}张 prompt={body['prompt'][:40]}…")
        url = self.session.base_url.rstrip("/") + API_PATH
        try:
            r = requests.post(url, json=body, timeout=self._timeout)
            data = r.json()
        except Exception as exc:                            # noqa: BLE001
            raise ApiError(f"HVTALD 投递失败（{url}）：{exc}")
        if int(data.get("code", 0)) != 200:
            raise ApiError(f"HVTALD 投递被拒：{str(data)[:300]}"
                           f"（检查 deviceId/token，以及线路是否还有余量）")
        aid = str(data.get("actionId") or aid)
        log(f"HVTALD 已插入任务：{data.get('msg', '')} actionId={aid}")

        got = self._wait(aid, poll_interval, poll_timeout, log=log, cancel=cancel)
        self._download(got, dest)
        return {"task_id": aid, "source": got, "provider": self.id, "model": "即梦国际版"}

    def _wait(self, aid: str, interval: int, timeout: int, *, log, cancel=None) -> str:
        """回调用不上时的取片方式：轮询 outs/ 找以 actionId 开头的 mp4。

        文档的回调示例里 videopath 就是 `{actionId}_xxx.mp4` 这个形状。
        """
        start, seen = time.time(), -1
        while time.time() - start < timeout:
            if cancel and cancel():
                raise ApiError("用户已取消")
            files = self._list("outs")
            if len(files) != seen:
                log(f"HVTALD outs/ 现有 {len(files)} 个文件，等 {aid} …")
                seen = len(files)
            for name, full in files:
                if name.startswith(aid) and name.lower().endswith((".mp4", ".mov")):
                    log(f"HVTALD 成片就绪：{name}")
                    return full
            time.sleep(interval)
        raise ApiError(
            f"等了 {timeout} 秒没在 outs/ 看到 {aid} 开头的成片。"
            f"这家按线路排队，满负荷时要等；任务没丢，actionId 记下来可以稍后取。",
            status=0, kind="retryable")
