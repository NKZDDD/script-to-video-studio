"""超模生图：取消本地参考图张数拦截和前九张截断。放入数据目录/providers。"""
from copy import deepcopy
from functools import wraps
import sys
from types import FunctionType

from core import model_catalog
from core.providers.chaomo import ChaomoProvider

PATCH_VERSION = "20260921.1"
_MARKER = "chaomo_image_refs_hotfix"


def _release_image_limit(cap):
    # 旧版预检把 None 当成“能力未知”而阻断。用 Python 容器最大长度作为
    # 兼容值，不冒充服务商声明；实际上传使用 [:None]，没有截断。
    block = cap["image"]
    block["max_refs"] = sys.maxsize
    for options in block.get("model_options", {}).values():
        options["max_refs"] = sys.maxsize
    return cap


# 快照和在线目录会在 provider.capabilities() 之后覆盖逐模型参数。
# 仅处理本插件标记的超模能力；移除插件并重扫后不再有标记，恢复原行为。
# 重扫时不重复套包装器。
if not getattr(model_catalog.apply_catalog, "_chaomo_refs_hotfix", False):
    _original_apply_catalog = model_catalog.apply_catalog

    @wraps(_original_apply_catalog)
    def _apply_catalog(cap, result, source):
        merged = _original_apply_catalog(cap, result, source)
        if cap.get("id") == "chaomo" and cap.get(_MARKER):
            _release_image_limit(merged)
        return merged

    _apply_catalog._chaomo_refs_hotfix = True
    model_catalog.apply_catalog = _apply_catalog


def _without_slice_limit(fn):
    # 复用宿主已有的格式校验、上传、轮询及下载修复；兼容冻结 EXE，无需源码。
    # 私有 globals 避免改动内置模块常量或并发中的视频请求。
    namespace = dict(fn.__globals__, MAX_REFS=None)
    cloned = FunctionType(fn.__code__, namespace, fn.__name__, fn.__defaults__, fn.__closure__)
    cloned.__kwdefaults__ = fn.__kwdefaults__
    return cloned


class ChaomoImageRefsHotfix(ChaomoProvider):
    id = "chaomo"

    def capabilities(self):
        cap = deepcopy(super().capabilities())
        cap[_MARKER] = PATCH_VERSION
        _release_image_limit(cap)
        cap["image"]["notes"] += " 本地参考图张数限制已解除，完整提交，由服务商判定。"
        return cap

    generate_image = _without_slice_limit(ChaomoProvider.generate_image)
