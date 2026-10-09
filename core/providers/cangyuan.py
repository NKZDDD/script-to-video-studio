"""沧元算力图像协议，依据 2026-10-09 docs-static 的逐模型文档。

公共模型名决定通道；size 表示画幅或像素，不擅自改名切换计费档。
参考图使用 HTTPS images JSON，异步查询沿用创建端点。
"""
import re

from core.apiutil import ApiError, extract_image_items, extract_task_id
from core.providers.base import Provider

PATCH_VERSION = '20261009.1'
R10 = ['1:1', '16:9', '9:16', '4:3', '3:4', '3:2', '2:3', '5:4', '4:5', '21:9']
R8 = [v for v in R10 if v not in ('5:4', '4:5')]
R7 = [v for v in R8 if v != '21:9']
WIDE = ['8:1', '4:1', '1:4', '1:8']
MJ = ['1:2', '6:11', '9:16', '2:3', '3:4', '4:5', '5:6', '1:1',
      '2:1', '11:6', '16:9', '3:2', '4:3', '5:4', '6:5']
IMAGE_MODELS = [
    'gpt-image-2', 'gpt-image-2-1k', 'gpt-image-2-2k', 'gpt-image-2-4k', 'gpt-image-2-x',
    'gpt-image-2.5', 'gpt-image-2.5-x',
    *[f'gpt-image-2.5-{s}{t}' for s in ('flare', 'sunburst') for t in ('', '-1k', '-2k', '-4k')],
    'gemini-3-pro-image-preview', 'gemini-3.1-flash-image-preview', 'gemini-nano-banana-2.1',
    *[f'{s}-{t}k' for s in ('nano-banana-pro', 'nano-banana2') for t in (1, 2, 4)],
    'doubao-seedream-5-0-pro', 'seedream-5.0-pro-x',
    'grok-imagine-image', 'grok-imagine-image-2.0', 'midjourney-v7', 'midjourney-1k', 'midjourney-2k',
]


def model_options(model):
    fixed = re.search(r'-(1|2|4)k$', model)
    opts = {'sizes': list(R10), 'size_mode': 'ratio', 'resolutions': [], 'max_refs': 9,
            'resolution_mode': 'model', 'default_size': '1:1', 'ref_max_bytes': 9_500_000}
    if fixed:
        opts['fixed_resolution'] = fixed.group(1) + 'K'
    if model.startswith('gpt-image'):
        opts.update(size_mode='mixed', allow_custom_size=True)
        opts['sizes'] = list(R8 if fixed else R10) + ['1024x1024', '1536x1024', '1024x1536']
        if fixed and fixed.group(1) in ('2', '4'):
            side = int(fixed.group(1))*1024
            opts['sizes'] += [f'{side}x{side}']
        if model.startswith(('gpt-image-2.5-flare-', 'gpt-image-2.5-sunburst-')):
            opts['max_refs'] = 16
        if model.endswith('-x'):
            opts.update(resolutions=['WEB', '1K', '2K', '4K'], resolution_mode='parameter')
            if model == 'gpt-image-2.5-x':
                opts.update(series_options=['flare', 'sunburst'], default_series='flare', max_refs=16)
    elif model.startswith('gemini-'):
        opts.update(resolutions=['1K', '2K', '4K'], resolution_mode='parameter', refs_unlimited=True)
        if model == 'gemini-nano-banana-2.1':
            opts.update(max_refs=14, refs_unlimited=False)
        if '3.1-flash' in model:
            opts['sizes'] += WIDE
    elif model.startswith('nano-banana'):
        opts['sizes'] = list(R8)
    elif 'seedream' in model:
        opts.update(sizes=R8+['1024x1024', '2048x1152', '2048x2048'], size_mode='mixed',
                    allow_custom_size=True, resolution_mode='pixels', max_refs=10)
    elif model.startswith('grok-'):
        opts.update(sizes=R7+['1024x1024', '1792x1024', '1024x1792'], size_mode='mixed',
                    resolution_mode='pixels', max_refs=8)
    elif model == 'midjourney-v7':
        opts.update(size_mode='mixed', allow_custom_size=True, max_refs=5)
    elif model.startswith('midjourney-'):
        opts.update(sizes=list(MJ), refs_unlimited=True)
    return opts


class CangyuanProvider(Provider):
    id = 'cangyuan'
    name = '沧元算力（图像）'
    aliases = ('cangyuansuanli', '苍元', '苍原', 'cysl')
    default_base_url = 'https://ai.cangyuansuanli.cn'
    supports = ('image',)
    ref_mode = 'url'

    def __init__(self, api_key='', base_url='', proxy='', timeout=900):
        base = (base_url or self.default_base_url).rstrip('/')
        if base.endswith('/v1'):
            base = base[:-3]
        super().__init__(api_key=api_key, base_url=base, proxy=proxy, timeout=timeout)

    def needs_bytes(self, model=''):
        return False

    def capabilities(self):
        return {'id': self.id, 'name': self.name, 'supports': ['image'],
                'default_base_url': self.default_base_url, 'image': {
                    'models': list(IMAGE_MODELS), 'default_model': 'gpt-image-2.5-flare',
                    'sizes': list(R10), 'default_size': '1:1', 'max_refs': 9,
                    'ref_mode': 'url', 'size_mode': 'ratio',
                    'model_options': {m: model_options(m) for m in IMAGE_MODELS},
                    'notes': '参考图先上传为 HTTPS 链接；尺寸、比例、清晰度按模型文档设置。带 k 后缀的档位由模型确定。'}}

    def build_image_body(self, task):
        model = task.model or 'gpt-image-2.5-flare'
        if model not in IMAGE_MODELS:
            raise ApiError(f'沧元尚未适配这个型号的参数：{model}', kind='task_fatal')
        if not (task.prompt or '').strip():
            raise ApiError('提示词不能为空', kind='task_fatal')
        opts = model_options(model)
        size = str(task.size or opts['default_size'])
        pixel = re.fullmatch(r'([1-9]\d{1,4})x([1-9]\d{1,4})', size)
        if size not in opts['sizes'] and not (pixel and opts.get('allow_custom_size')):
            raise ApiError(f'{model} 不支持 size={size}；可选 {opts["sizes"]}', kind='task_fatal')
        resolution = str(task.extra.get('resolution') or '').upper()
        if resolution and resolution != opts.get('fixed_resolution') and resolution not in opts['resolutions']:
            raise ApiError(f'{model} 的清晰度由模型或像素决定，不能单独指定 {resolution}', kind='task_fatal')
        body = {'model': model, 'prompt': task.prompt, 'size': size, 'n': 1,
                'response_format': 'url', 'async': True}
        maximum = opts['max_refs']
        if model.startswith('gemini-') and resolution:
            body['quality'] = resolution.lower()
        if model.endswith('-x') and model.startswith('gpt-image'):
            body['tier'] = (resolution or 'WEB').lower()
            if model == 'gpt-image-2.5-x':
                series = task.extra.get('series') or 'flare'
                if series not in ('flare', 'sunburst'):
                    raise ApiError('gpt-image-2.5-x 的 series 只能为 flare/sunburst', kind='task_fatal')
                body['series'] = series
                maximum = 9 if body['tier'] == 'web' else 16
        refs = list(task.refs or [])
        if refs and not opts.get('refs_unlimited') and len(refs) > maximum:
            raise ApiError(f'{model} 最多接收 {maximum} 张参考图，当前 {len(refs)} 张；没有截断或提交', kind='task_fatal')
        if any(not str(ref).startswith('https://') for ref in refs):
            raise ApiError('沧元参考图必须为 HTTPS 地址，请配置参考图对象存储上传', kind='task_fatal')
        path = '/v1/images/edits' if refs else '/v1/images/generations'
        if refs:
            body['images'] = refs
            if model in ('midjourney-1k', 'midjourney-2k'):
                path = '/v1/images/generations'
                body['reference'] = 'image'
        return path, body

    def generate_image(self, task, dest, *, log=print, cancel=None, poll_interval=5, poll_timeout=900):
        path, body = self.build_image_body(task)
        log(f'沧元 {body["model"]}: size={body["size"]}，参考图 {len(task.refs or [])} 张')
        data = self.session.request('POST', path, json_body=body, retries=1, timeout=600)
        tid = extract_task_id(data)
        if tid:
            items = self.session.poll(path+'/{id}', tid, picker=extract_image_items,
                                      interval=max(5, poll_interval), timeout=poll_timeout, log=log, cancel=cancel)
        else:
            items = extract_image_items(data)
        if not items:
            raise ApiError('沧元未返回图片或可续查任务 ID', kind='task_fatal')
        self.session.save_item(items[0], dest, log=log)
        if len(items) > 1:
            log(f'服务商返回 {len(items)} 张，本任务按单图输出契约保存第一张')
        return {'task_id': tid, 'source': items[0][:200], 'provider': self.id, 'model': body['model']}
