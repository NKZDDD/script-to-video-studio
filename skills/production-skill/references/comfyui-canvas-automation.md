# 工作流参数装配与依赖调度
本文件是接口无关的装配示意。真实字段由平台适配器按已核实文档转换；不固定服务商endpoint，不在示例中自动发送请求。

## 一、禁止静默默认
model、aspect_ratio、duration、audio_mode来自项目锁。缺值报告缺项；不默认seedance-2.0、9:16、15秒、dynamic_follow或high。参考图和剧情不因缺字段被替换。

## 二、纯装配示例
输入uploaded_refs必须来自实际上传/请求装配记录，顺序与expected_refs、正文Image一致。该函数只比较显式清单，不解析正文、不验证服务商收到或文件图像内容。
```python
import math

def build_video_manifest(project_lock, prompt, expected_refs, uploaded_refs):
    required = ("model", "aspect_ratio", "duration", "audio_mode")
    missing = [name for name in required if project_lock.get(name) in (None, "")]
    if missing:
        raise ValueError("缺少项目锁: " + ", ".join(missing))
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("视频正文为空")
    duration = project_lock["duration"]
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration必须为项目锁定的正数秒数")
    if project_lock["audio_mode"] not in ("native", "post", "silent"):
        raise ValueError("音频模式须明确")
    if len(expected_refs) != len(uploaded_refs):
        raise ValueError("参考数量与实际上传不一致")
    seen = set()
    for index, (expected, uploaded) in enumerate(zip(expected_refs, uploaded_refs), 1):
        if (type(expected["image_n"]) is not int or type(uploaded["image_n"]) is not int
                or expected["image_n"] != index or uploaded["image_n"] != index):
            raise ValueError("Image编号或实际上传顺序错误")
        if any(not isinstance(ref.get(field), str) or not ref[field].strip()
               for ref in (expected, uploaded) for field in ("key", "revision")):
            raise ValueError("资源key与版本必须为非空字符串")
        if expected["key"] != uploaded["key"] or expected["revision"] != uploaded["revision"]:
            raise ValueError("资源或版本不匹配")
        identity = (expected["key"], expected["revision"])
        if identity in seen:
            raise ValueError("同一资源版本重复上传")
        seen.add(identity)
        if not isinstance(uploaded.get("source"), str) or not uploaded["source"].strip():
            raise ValueError("素材未取得实际上传源")
    return {
        "model": project_lock["model"],
        "aspect_ratio": project_lock["aspect_ratio"],
        "duration": duration,
        "audio_mode": project_lock["audio_mode"],
        "prompt": prompt,
        "references": [dict(ref) for ref in uploaded_refs],
    }
```
上述返回值为内部manifest，不是任意平台的可发送API请求。适配后再对照最终payload，避免转换时丢图。零参考仅用于明确规划为不需要参考的任务，不能靠清空expected_refs绕过依赖检查。

## 三、实际执行顺序
材料模式可先交完整planned依赖图。执行模式按就绪任务调度：
基础资产→P3调度对应共同母图→每边界一次生成整张ABC并验收三区→以同版整板为唯一图片参考分别生成独立A/B/C并对原区验收→视频。禁止先分区后合成；区域派生调用图像模型，不用人工或程序裁切。执行日志记录整板、区域任务、region及确切父图版本。
后段有入板时先核对前段实际尾帧；无关联边界不强制该依赖。母图与ABC不能因家族固定批次顺序倒置；需依赖调度或明确分阶段执行。
每项状态区分planned、生产中、失败、文件就绪、视觉通过、提交完成。失败报告具体素材和受影响下游，不静默删引用。

## 四、范围与容量
按需入板B独立图、入板C独立图、出板A独立图，不上传整板、各场景参考按镜头范围、人物道具按身份用途；同图去重，多场景不跨域删图，无SBSHEET。
参考数量、时长、模式混用及原生音频按实际接口核实，超限报告方案冲突。不得自动切模型、删参考或把一个30秒SEG拆成多个任务。
组数按生产范围及锁定SEG预算；90秒/30秒可规划3组，不套固定14组。平台分块仅改变传输，不截断材料。
