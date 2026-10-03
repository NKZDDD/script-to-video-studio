# HZYF 4K 通过服务商外挂更新

日期：2026-10-04
用户原话：「那就需要加入4K选项，这个可以从外挂包引入吗下次就不用直接修改studio」。

## 原因与范围

HZYF 先前只声明三种已验证的像素尺寸，并拒绝独立 resolution 参数。因此服务商即使能返回 4K，Studio 页面也不会显示 4K。2026-10-04 经用户授权实测 gpt-image-2：size=4096x4096，HTTP 200，28.22 秒返回，PNG 完整解码为 4096x4096（2,923,426 字节），证据 outputs/hzyfai-4k-20261004/result.json。

现有 Studio 根据服务商 capabilities 的 model_options.sizes / resolutions 展示选项并预检；providers 外挂可替换同 ID 的内置实现。因此无需改页面或重打 EXE，更新适配器即可。

## 实现

- 外挂版本 20261004.1，内置 core/providers/hzyfai.py 与 provider_patches/zz_hzyfai_image.py 字节一致。
- 仅为已实测的 gpt-image-2 声明 4096x4096 与清晰度 4K。清晰度 4K 将方图尺寸转为 size=4096x4096，不向服务商发送未经验证的 resolution 字段。
- 直接选择 4096x4096 同样可用。保留原三种尺寸，不默认为 4K；其他模型和非方图的 4K 尚未验证，不能静默套用或改变比例。
- 文生图 JSON 与图生图 multipart 共用转换；4K 图生图已验证请求透传，未额外执行付费编辑测试。
- 原项目、Key 和全局生产流程不变；交付可导入的 .py 和带安装说明的 ZIP，不重打完整 EXE。

## 避免什么

页面可根据外挂自动显示已验证的 4K 选项，原 EXE 的能力预检也能放行；不再因旧适配器固定的尺寸清单挡住服务商支持的方图 4K。同类服务商能力或接口参数变化可继续通过更新外挂交付；页面结构与全局流程变更仍需要主程序更新。

## 验证与交付

针对性测试 39 项通过，包含新加 11 项参数透传、禁止未验证模型/比例转换与现有生产预检测试。全量回归 3036 passed，203.42 秒，日志 outputs/hzyfai-4k-full-20261004.log。

20261003 EXE 在隔离数据目录通过 /api/agent/providers/import 导入外挂，不重启即可获取 gpt-image-2 的 4096x4096 / 4K 能力，日志 outputs/hzyfai-4k-frozen-20261004.log。该验证不调用收费生成接口。

交付 dist/Respect-Studio-HZYF-4K-20261004.zip（插件、说明、版本与 SHA-256 清单）及 dist/zz_hzyfai_image.py。包完整性、插件与源码一致性均校验通过；不包含 API Key。
