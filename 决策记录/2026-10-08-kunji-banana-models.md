# 坤鸡香蕉模型更新与独立外挂

日期：2026-10-08。用户要求：“那需要给我一个外挂”。

## 原因和范围

香蕉分组 Key 的实时 `/v1/models` 返回五个名称，旧 `BANANA_MODELS` 只列两个 preview 名称。动态模型目录负责显示，不负责协议分派，因此新名字可以被选中，却在 `generate_image` 中落入 GPT 图片分支；参考图接受 URL/需要字节的判断同样依赖该旧名单。以前只测试原来的两个名称，未暴露新增名称的路由缺口。

范围限定为 Studio 坤鸡适配器及其同代码外挂，不修改批量工具、其他服务商或本机 Key 配置。

## 修改和效果

五个已查到的香蕉名称共用一份常量，供目录、请求分派、参考图准备使用；使用绝对导入支持旧 EXE 外挂加载；旧缓存保留已适配的香蕉名称。原 GPT/Veo 路由不变。

新型号选择后会进入 Gemini generateContent 并使用香蕉参考图格式，避免只显示了新名称却按 GPT 接口提交。外挂不包含用户 Key。

## 验证与交付

此前用户授权的单次实时测试：`gemini-nano-banana-2.1`，Gemini 原生协议，1K/1:1，HTTP 200，28.6 秒，1024×1024 JPEG，图片解码及目视检查正常。此次制作外挂不增加收费请求。

专项离线测试 67 项通过（1.13 秒），包含主程序与外挂的五个名称、文生图/参考图输入、真实图片保存、旧目录保留规则，以及 GPT/Veo 既有行为。

使用 `Respect短剧制作平台-20261007.exe` 在隔离数据目录实际加载新外挂，启动与 HTTP 能力检查通过，五个香蕉名称均出现。用户配置未改动。

交付 `dist/坤鸡香蕉模型外挂-20261008.zip`（8,425 字节），包含 `providers/zz_kunji_latest.py`、安装说明与 manifest，不含 Key；另提供 `dist/zz_kunji_latest.py`。内置和外挂源文件逐字节一致，ZIP 完整性校验通过。

ZIP SHA256：`624d25a16a24eb50a8a20f6dca94a3cca5b26060a3d75e1067febe04e7085943`。

全量回归：3117 项通过、1 项失败（388.01 秒）。唯一失败是 `test_agent_handoff.py::test_revision_not_newer_than_active_is_refused_before_extracting`，临时测试目录的 `staging.replace(target)` 报 Windows `PermissionError [WinError 5]`；不涉及坤鸡调用。该材料导入测试文件在独立临时目录复跑 14 项全部通过（10.08 秒）。未修改无关导入代码，未将首轮全量结果表述为全部通过。
