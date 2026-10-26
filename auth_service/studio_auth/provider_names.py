"""Display names copied from Studio provider adapters (no credentials)."""
LLM_PROVIDER = 'prompt_rewrite_llm'


def validate_llm_config(provider, cfg):
    """The rewrite route is an explicit endpoint + model + credential binding."""
    if provider != LLM_PROVIDER:
        return
    from .service import AuthError
    from urllib.parse import urlsplit
    url = urlsplit(cfg.get('base_url', ''))
    models = cfg.get('models', [])
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or len(models) != 1
            or not isinstance(models[0], str) or not models[0].strip() or len(models[0]) > 200
            or not str(cfg.get('api_key', '')).strip()):
        raise AuthError(422, 'invalid_llm_config', '提示词改写 LLM 必须填写 HTTPS 地址、API Key 和一个完整模型 ID')
PROVIDER_NAMES = {'aicopy': '小裴 api.aicopy.top', 'ake': '阿珂 snumom.com', 'cangyuan': '沧元算力（图像）', 'cangyuan_video': '沧元算力（视频）', 'chaomo': '超模 chaomoapi.com', 'chaomo_banana': '超模·香蕉（独立Key）', 'gate': 'Gate api-gate.astralmindai.com', 'haomanju': '好漫剧 75api.com', 'hongniao': '红鸟', 'hvtald': 'HVTALD 空间（即梦国际版·15秒）', 'hzyfai': 'HZYF hzyfai.com', 'julun': '巨轮 julun.cc', 'kunji': '坤鸡 img.yunfei.best（图片 + veo视频）', 'lingganya': '灵感鸭 lingganyaapi.com', 'm86': 'M86 / New API yiyun.xiaoge.uk', 'octopus': '章鱼哥', 'paisio': '鹤 api.paisio.online（SD2/SD3/Seedance）', 'wuxianhuabu': '无限画布 videogogo.top（Seedance 2.5）', 'xiaobalong': '小霸龙 api.keik.cc', 'xmd': 'XMD 视频（ai.xmdomain.dpdns.org）', 'yishou': '一手 ONE API www.weijinapi.top', 'yunhui': '云绘 AI ai.yunhuiart.cn（Seedance）', 'zeroapi': '零视工坊 zeroapi.ai-ren.cn', 'zhi': '智 zhi168.it.com（图 + 视频）'}
