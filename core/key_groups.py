"""Shared named credential group parser for legacy configuration fields."""
import re

_KEY_ALIAS = {"1k": "1k", "2k": "2k", "4k": "4k", "high": "high", "default": "default"}


def parse_keys(api_key: str) -> dict:
    """把 api_key 解成 {分组: key}。

    **4K 要单独的 Key**（文档：4K 分组才支持 1K/2K/4K），所以一个字段得能装多把。
    两种写法都认，用户手上是客服发的一段文本，别逼他学格式：

      · 单把：`sk-xxx`                      → 所有档位都用它
      · 多把：`1k=sk-aaa;4k=sk-bbb;high=sk-ccc`

    没配到的档位回落到 `default`（多把写法里没写 default 就用第一把）。
    """
    raw = (api_key or "").strip()
    if not raw:
        return {}
    if "=" not in raw:
        return {"default": raw}
    out = {}
    for part in re.split(r"[;\n]+", raw):
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        key = _KEY_ALIAS.get(k.strip().lower())
        if key and v.strip():
            out[key] = v.strip()
    if out and "default" not in out:
        out["default"] = next(iter(out.values()))
    return out
