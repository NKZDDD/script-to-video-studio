# -*- coding: utf-8 -*-
"""超模香蕉独立 Key 入口，合并用户提供的 2026-10-08 接入。
图片格式校验和转码由 Studio 共用保存层完成，不修改全局 session。
"""
import os

from core.apiutil import ApiError, extract_task_id
from .chaomo import ChaomoProvider, _to_ratio

_BANANA_MODELS = [
    "gemini-3-pro-image",        # Nano Banana Pro（1K/2K/4K，¥0.09/0.10）
    "gemini-3.1-flash-image",    # Nano Banana 2（512/1K/2K/4K，¥0.07/0.08）
    "gemini-nano-banana-2.1",    # Nano Banana 2.1（512/1K/2K/4K，¥0.07/0.08）
]

# 清晰度档位（2026-10-08 香蕉分组 key 实拉 /v1/models 的 supported_image_sizes）：
# pro 没有 512；界面按模型声明显示档位。
_DEFAULT_SIZES = ["1K", "2K", "4K"]
_SIZES_BY_MODEL = {
    "gemini-3-pro-image": ["1K", "2K", "4K"],
    "gemini-3.1-flash-image": ["512", *_DEFAULT_SIZES],
    "gemini-nano-banana-2.1": ["512", *_DEFAULT_SIZES],
}

class ChaomoBananaProvider(ChaomoProvider):
    id = "chaomo_banana"
    name = "超模·香蕉（独立Key）"
    aliases = ("chaomobanana", "超模香蕉", "cmbanana")
    supports = ("image",)
    key_fields = ()  # 独立 api_key，不共用超模 TH / Native 的两个槽位。

    # 只暴露香蕉模型；PINNED 保证不依赖 list_models 也能出现在模型下拉里
    IMAGE_MODELS = list(_BANANA_MODELS)
    PINNED_IMAGE_MODELS = list(_BANANA_MODELS)

    def capabilities(self) -> dict:
        """在父类能力上换掉 model_options：香蕉模型各报各的清晰度档位。

        界面「清晰度」下拉读 model_options[模型].resolutions（没拉过模型
        列表时回落 image.resolutions），两处都补上 —— 不点「拉取模型」也能选。
        不覆盖的话父类 IMAGE_MODEL_OPTIONS 里没有香蕉条目，
        agent_runtime 的「模型不支持所选清晰度」会把任何清晰度都拦下来。
        """
        cap = super().capabilities()
        img = cap.get("image", {})
        # 父类 capabilities 里 models/default_model 用的是 chaomo 模块级常量，
        # 类属性覆盖不到 —— 这里必须整个换掉，否则模型下拉还是 gpt-image
        # 全家桶，agent_runtime 的「模型不在当前能力清单」会把香蕉模型拒掉。
        img["models"] = list(_BANANA_MODELS)
        img["default_model"] = "gemini-nano-banana-2.1"
        img["model_options"] = {
            m: {"resolutions": list(_SIZES_BY_MODEL.get(m, _DEFAULT_SIZES)),
                "default_resolution": "1K"}
            for m in _BANANA_MODELS
        }
        img["catalog_keep_models"] = list(_BANANA_MODELS)
        img["size_mode"] = "ratio"
        img["resolution_mode"] = "parameter"
        img["resolutions"] = list(_DEFAULT_SIZES)   # 兜底：供未声明模型的回落
        img['notes'] = '香蕉独立 Key；比例和 image_size 清晰度分开传递。参考图完整上传，不按 9 张截断。'
        cap.pop('video', None)
        return cap

    def generate_image(self, task, dest, **kw):
        """香蕉线出图本体：请求带 image_size 清晰度。

        不能直接 super()：父类只在 TH_SIZED_MODELS 才发清晰度且参数名叫
        size，香蕉线的参数名是 image_size（2026-10-08 实拉
        generation_parameters：model/prompt/image_size/ratio/n/response_format）；
        且父类的档位校验表（IMAGE_MODEL_OPTIONS）里没有香蕉条目，
        用户一选清晰度就会在本地被拦死。请求结构照抄父类（async / URL
        轮询 / 参考图 multipart image[] 那套在香蕉线上实测是通的），
        只把校验表换成香蕉自己的、补上 image_size 字段。
        """
        log = kw.get("log", print)
        model = task.model or _BANANA_MODELS[0]
        ratio = _to_ratio(task.size)
        resolution = str(task.extra.get("resolution") or "").strip().upper()
        allowed = _SIZES_BY_MODEL.get(model, _DEFAULT_SIZES)
        if resolution and resolution not in allowed:
            raise ApiError(f"{model} 支持的图片清晰度：{'/'.join(allowed)}，"
                           f"不支持 {resolution}。", kind="task_fatal")
        size_kv = [("image_size", (None, resolution))] if resolution else []
        refs = list(task.refs or [])

        if refs:
            files = [("model", (None, model)), ("prompt", (None, task.prompt or "")),
                     ("ratio", (None, ratio)), ("n", (None, "1")),
                     ("response_format", (None, "url")),
                     ("async", (None, "true")),
                     ("include_metadata", (None, "true")),
                     *size_kv]
            attached = 0
            for i, ref in enumerate(refs, start=1):
                got = self._ref_bytes(ref, i, log=log)
                if got:
                    files.append(("image[]", (got[1], got[0], got[2])))
                    attached += 1
            if attached != len(refs):
                raise ApiError(f"超模·香蕉图生图：{len(refs)} 张参考图只有 {attached} 张转成文件，"
                               "不出这张图 —— 少了参考图出来的就不是同一个人。")
            log(f"超模·香蕉 图生图 {model}: ratio={ratio} "
                f"image_size={resolution or '默认'} 参考图{attached}张")
            data = self.session.request("POST", "/v1/images/edits", files=files,
                                        retries=2, timeout=600)
        else:
            body = {"model": model, "prompt": task.prompt, "ratio": ratio,
                    "n": 1, "response_format": "url", "async": True,
                    "include_metadata": True}
            if resolution:
                body["image_size"] = resolution
            log(f"超模·香蕉 文生图 {model}: ratio={ratio} "
                f"image_size={resolution or '默认'}")
            data = self.session.request("POST", "/v1/images/generations",
                                        json_body=body, retries=2, timeout=600)

        items, final = self._pick_images(
            data, "", log=log,
            poll_interval=kw.get("poll_interval", 5),
            poll_timeout=kw.get("poll_timeout", 900))
        if not items:
            raise ApiError(f"出图没返回可用结果: {str(data)[:300]}")
        self.session.save_item(items[0], dest, log=log)
        log("超模·香蕉 图片已下载，正在核验图片信息")
        self.check_meta(self.meta_of(final), items, dest=dest, log=log,
                        source_bytes=getattr(self.session, "result_source_bytes", {}).get(os.path.abspath(dest)))
        log("超模·香蕉 图片结果已就绪")
        return {"task_id": extract_task_id(data), "source": items[0][:200],
                "provider": self.id, "model": model}
