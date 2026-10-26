"""纵横科技视频协议，依据 /api-docs 与 2026-10-26 按 Key 读取的能力。

仅接入已授权的视频能力；对话使用 Studio 现有的 OpenAI LLM 配置。
本文件使用绝对公共导入，也可由服务端适配包独立加载。
"""
from __future__ import annotations

import base64
import copy
import mimetypes
from pathlib import Path
import time
from urllib.parse import quote, urlsplit
import uuid

from core import submitted
from core.apiutil import ApiError, RETRYABLE, TASK_FATAL, classify, submit_uncertain, task_failed
from core.providers.base import Provider


# Public capability snapshot; generated below from the provider catalog, no credentials.
MODEL_SPECS = {'XXseadanco2.0M': {'capability_type': 'video',
                    'duration_options': [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
                    'resolution_options': ['720p'],
                    'aspect_ratio_options': [],
                    'quality_options': [],
                    'max_image_refs': 9,
                    'max_audio_refs': 3,
                    'max_video_refs': 3,
                    'reference_materials': ['image', 'video', 'audio'],
                    'defaults': {'duration': 5, 'resolution': '720p', 'aspect_ratio': ''}},
 'XXseedacn2.5': {'capability_type': 'video',
                  'duration_options': [5,
                                       6,
                                       7,
                                       8,
                                       9,
                                       10,
                                       11,
                                       12,
                                       13,
                                       14,
                                       15,
                                       16,
                                       17,
                                       18,
                                       19,
                                       20,
                                       21,
                                       22,
                                       23,
                                       24,
                                       25,
                                       26,
                                       27,
                                       28,
                                       29,
                                       30],
                  'resolution_options': ['720p'],
                  'aspect_ratio_options': [],
                  'quality_options': [],
                  'max_image_refs': 30,
                  'max_audio_refs': 10,
                  'max_video_refs': 10,
                  'reference_materials': ['image', 'video', 'audio'],
                  'defaults': {'duration': 5, 'resolution': '720p', 'aspect_ratio': ''}},
 'wan3/1080': {'capability_type': 'video',
               'duration_options': [5,
                                    6,
                                    7,
                                    8,
                                    9,
                                    10,
                                    11,
                                    12,
                                    13,
                                    14,
                                    15,
                                    16,
                                    17,
                                    18,
                                    19,
                                    20,
                                    21,
                                    22,
                                    23,
                                    24,
                                    25,
                                    26,
                                    27,
                                    29,
                                    30],
               'resolution_options': ['1080p', '720p'],
               'aspect_ratio_options': [],
               'quality_options': [],
               'max_image_refs': 10,
               'max_audio_refs': 5,
               'max_video_refs': 5,
               'reference_materials': ['image', 'video', 'audio'],
               'defaults': {'duration': 5, 'resolution': '1080p', 'aspect_ratio': ''}},
 'Aseedance2.0': {'capability_type': 'video',
                  'duration_options': [5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
                  'resolution_options': ['720p'],
                  'aspect_ratio_options': [],
                  'quality_options': [],
                  'max_image_refs': 9,
                  'max_audio_refs': 3,
                  'max_video_refs': 3,
                  'reference_materials': ['image', 'video', 'audio'],
                  'defaults': {'duration': 5, 'resolution': '720p', 'aspect_ratio': ''}},
 'A-SD2.0': {'capability_type': 'video',
             'duration_options': [5, 10, 15],
             'resolution_options': ['720p'],
             'aspect_ratio_options': [],
             'quality_options': [],
             'max_image_refs': 9,
             'max_audio_refs': 3,
             'max_video_refs': 3,
             'reference_materials': ['image', 'video', 'audio'],
             'defaults': {'duration': 5, 'resolution': '720p', 'aspect_ratio': ''}},
 'MinimaxH3-768': {'capability_type': 'video',
                   'duration_options': [5, 6, 7, 9, 10, 11, 12, 13, 14, 15],
                   'resolution_options': ['768p'],
                   'aspect_ratio_options': [],
                   'quality_options': [],
                   'max_image_refs': 9,
                   'max_audio_refs': 3,
                   'max_video_refs': 3,
                   'reference_materials': ['image', 'video', 'audio'],
                   'defaults': {'duration': 5, 'resolution': '768p', 'aspect_ratio': ''}},
 'Xminimex-h3': {'capability_type': 'video',
                 'duration_options': [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
                 'resolution_options': ['2k'],
                 'aspect_ratio_options': [],
                 'quality_options': [],
                 'max_image_refs': 9,
                 'max_audio_refs': 3,
                 'max_video_refs': 3,
                 'reference_materials': ['image', 'video', 'audio'],
                 'defaults': {'duration': 5, 'resolution': '2k', 'aspect_ratio': ''}},
 'TTsedanco2.0-480': {'capability_type': 'video',
                      'duration_options': [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
                      'resolution_options': ['480p'],
                      'aspect_ratio_options': [],
                      'quality_options': [],
                      'max_image_refs': 9,
                      'max_audio_refs': 3,
                      'max_video_refs': 3,
                      'reference_materials': ['image', 'video', 'audio'],
                      'defaults': {'duration': 5, 'resolution': '480p', 'aspect_ratio': ''}},
 'TTsedanco2.0-720': {'capability_type': 'video',
                      'duration_options': [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
                      'resolution_options': ['720p'],
                      'aspect_ratio_options': [],
                      'quality_options': [],
                      'max_image_refs': 9,
                      'max_audio_refs': 3,
                      'max_video_refs': 3,
                      'reference_materials': ['image', 'video', 'audio'],
                      'defaults': {'duration': 5, 'resolution': '720p', 'aspect_ratio': ''}},
 'Fseadanco2.0mini': {'capability_type': 'video',
                      'duration_options': [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16],
                      'resolution_options': ['720p', '480p'],
                      'aspect_ratio_options': [],
                      'quality_options': [],
                      'max_image_refs': 9,
                      'max_audio_refs': 3,
                      'max_video_refs': 3,
                      'reference_materials': ['image', 'video', 'audio'],
                      'defaults': {'duration': 5, 'resolution': '720p', 'aspect_ratio': ''}},
 'TTseadanco2.5-1080': {'capability_type': 'video',
                        'duration_options': [5,
                                             6,
                                             7,
                                             8,
                                             9,
                                             10,
                                             11,
                                             12,
                                             13,
                                             14,
                                             15,
                                             16,
                                             17,
                                             18,
                                             19,
                                             20,
                                             21,
                                             22,
                                             23,
                                             24,
                                             25,
                                             26,
                                             27,
                                             28,
                                             29,
                                             30],
                        'resolution_options': ['1080p'],
                        'aspect_ratio_options': [],
                        'quality_options': [],
                        'max_image_refs': 30,
                        'max_audio_refs': 10,
                        'max_video_refs': 10,
                        'reference_materials': ['image', 'video', 'audio'],
                        'defaults': {'duration': 5, 'resolution': '1080p', 'aspect_ratio': ''}},
 'TTsedanco2.5-480': {'capability_type': 'video',
                      'duration_options': [5,
                                           6,
                                           7,
                                           8,
                                           9,
                                           10,
                                           11,
                                           12,
                                           13,
                                           14,
                                           15,
                                           16,
                                           17,
                                           18,
                                           19,
                                           20,
                                           21,
                                           22,
                                           23,
                                           24,
                                           25,
                                           26,
                                           27,
                                           28,
                                           29,
                                           30],
                      'resolution_options': ['480p'],
                      'aspect_ratio_options': [],
                      'quality_options': [],
                      'max_image_refs': 30,
                      'max_audio_refs': 10,
                      'max_video_refs': 10,
                      'reference_materials': ['image', 'video', 'audio'],
                      'defaults': {'duration': 5, 'resolution': '480p', 'aspect_ratio': ''}},
 'TTseadanco2.5-720': {'capability_type': 'video',
                       'duration_options': [5,
                                            6,
                                            7,
                                            8,
                                            9,
                                            10,
                                            11,
                                            12,
                                            13,
                                            14,
                                            15,
                                            16,
                                            17,
                                            18,
                                            19,
                                            20,
                                            21,
                                            22,
                                            23,
                                            24,
                                            25,
                                            26,
                                            27,
                                            28,
                                            29,
                                            30],
                       'resolution_options': ['720p'],
                       'aspect_ratio_options': [],
                       'quality_options': [],
                       'max_image_refs': 30,
                       'max_audio_refs': 10,
                       'max_video_refs': 10,
                       'reference_materials': ['image', 'video', 'audio'],
                       'defaults': {'duration': 5, 'resolution': '720p', 'aspect_ratio': ''}},
 'OOsd2.5': {'capability_type': 'video',
             'duration_options': [15],
             'resolution_options': ['720p'],
             'aspect_ratio_options': [],
             'quality_options': [],
             'max_image_refs': 9,
             'max_audio_refs': 0,
             'max_video_refs': 0,
             'reference_materials': ['image'],
             'defaults': {'duration': 15, 'resolution': '720p', 'aspect_ratio': ''}},
 '00sd2.0': {'capability_type': 'video',
             'duration_options': [5, 10, 15],
             'resolution_options': ['720p'],
             'aspect_ratio_options': [],
             'quality_options': [],
             'max_image_refs': 9,
             'max_audio_refs': 0,
             'max_video_refs': 0,
             'reference_materials': ['image'],
             'defaults': {'duration': 5, 'resolution': '720p', 'aspect_ratio': ''}},
 'BBseadanco-1080': {'capability_type': 'video',
                     'duration_options': [5,
                                          6,
                                          7,
                                          8,
                                          9,
                                          10,
                                          11,
                                          12,
                                          13,
                                          14,
                                          15,
                                          16,
                                          17,
                                          18,
                                          19,
                                          20,
                                          21,
                                          22,
                                          23,
                                          24,
                                          25,
                                          26,
                                          27,
                                          28,
                                          29],
                     'resolution_options': ['1080p'],
                     'aspect_ratio_options': [],
                     'quality_options': [],
                     'max_image_refs': 30,
                     'max_audio_refs': 10,
                     'max_video_refs': 0,
                     'reference_materials': ['image', 'audio'],
                     'defaults': {'duration': 5, 'resolution': '1080p', 'aspect_ratio': ''}},
 'BBseadanco-720': {'capability_type': 'video',
                    'duration_options': [5,
                                         6,
                                         7,
                                         8,
                                         9,
                                         10,
                                         11,
                                         12,
                                         13,
                                         14,
                                         15,
                                         16,
                                         17,
                                         18,
                                         19,
                                         20,
                                         21,
                                         22,
                                         23,
                                         24,
                                         25,
                                         26,
                                         28,
                                         29,
                                         30],
                    'resolution_options': ['720p'],
                    'aspect_ratio_options': [],
                    'quality_options': [],
                    'max_image_refs': 30,
                    'max_audio_refs': 10,
                    'max_video_refs': 0,
                    'reference_materials': ['image', 'audio'],
                    'defaults': {'duration': 5, 'resolution': '720p', 'aspect_ratio': ''}},
 'BBseadanco-480': {'capability_type': 'video',
                    'duration_options': [5,
                                         6,
                                         7,
                                         8,
                                         9,
                                         11,
                                         12,
                                         13,
                                         14,
                                         15,
                                         16,
                                         17,
                                         18,
                                         20,
                                         21,
                                         22,
                                         23,
                                         24,
                                         25,
                                         26,
                                         27,
                                         28,
                                         29,
                                         30],
                    'resolution_options': ['480p'],
                    'aspect_ratio_options': [],
                    'quality_options': [],
                    'max_image_refs': 30,
                    'max_audio_refs': 10,
                    'max_video_refs': 0,
                    'reference_materials': ['image', 'audio'],
                    'defaults': {'duration': 5, 'resolution': '480p', 'aspect_ratio': ''}},
 'wan3-480/720': {'capability_type': 'video',
                  'duration_options': [5, 10, 15, 30],
                  'resolution_options': ['720p', '480p'],
                  'aspect_ratio_options': [],
                  'quality_options': [],
                  'max_image_refs': 10,
                  'max_audio_refs': 5,
                  'max_video_refs': 0,
                  'reference_materials': ['image', 'audio'],
                  'defaults': {'duration': 5, 'resolution': '720p', 'aspect_ratio': ''}},
 'TTP-grok': {'capability_type': 'video',
              'duration_options': [6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
              'resolution_options': ['720p', '480p'],
              'aspect_ratio_options': [],
              'quality_options': [],
              'max_image_refs': 5,
              'max_audio_refs': 0,
              'max_video_refs': 0,
              'reference_materials': ['image'],
              'defaults': {'duration': 6, 'resolution': '720p', 'aspect_ratio': ''}},
 'sedanco2.0-300秒必出': {'capability_type': 'video',
                       'duration_options': [15],
                       'resolution_options': ['480p'],
                       'aspect_ratio_options': [],
                       'quality_options': [],
                       'max_image_refs': 9,
                       'max_audio_refs': 3,
                       'max_video_refs': 0,
                       'reference_materials': ['image', 'audio'],
                       'defaults': {'duration': 15, 'resolution': '480p', 'aspect_ratio': ''}},
 'Tseadanco2.5-1080YT': {'capability_type': 'video',
                         'duration_options': [5,
                                              6,
                                              7,
                                              8,
                                              9,
                                              10,
                                              11,
                                              12,
                                              13,
                                              14,
                                              15,
                                              16,
                                              17,
                                              18,
                                              19,
                                              20,
                                              21,
                                              22,
                                              23,
                                              24,
                                              25,
                                              26,
                                              27,
                                              28,
                                              29,
                                              30],
                         'resolution_options': ['1080p'],
                         'aspect_ratio_options': [],
                         'quality_options': [],
                         'max_image_refs': 30,
                         'max_audio_refs': 10,
                         'max_video_refs': 10,
                         'reference_materials': ['image', 'video', 'audio'],
                         'defaults': {'duration': 5, 'resolution': '1080p', 'aspect_ratio': ''}},
 'Tseadanco2.5-720YT': {'capability_type': 'video',
                        'duration_options': [5,
                                             6,
                                             7,
                                             8,
                                             9,
                                             11,
                                             13,
                                             14,
                                             16,
                                             17,
                                             18,
                                             19,
                                             20,
                                             21,
                                             22,
                                             23,
                                             24,
                                             25,
                                             26,
                                             27,
                                             28,
                                             30],
                        'resolution_options': ['720p'],
                        'aspect_ratio_options': [],
                        'quality_options': [],
                        'max_image_refs': 30,
                        'max_audio_refs': 10,
                        'max_video_refs': 10,
                        'reference_materials': ['image', 'video', 'audio'],
                        'defaults': {'duration': 5, 'resolution': '720p', 'aspect_ratio': ''}},
 'Tseadanco2.5-480YT': {'capability_type': 'video',
                        'duration_options': [5,
                                             6,
                                             7,
                                             8,
                                             9,
                                             10,
                                             11,
                                             12,
                                             13,
                                             14,
                                             15,
                                             16,
                                             18,
                                             19,
                                             20,
                                             21,
                                             22,
                                             23,
                                             24,
                                             25,
                                             26,
                                             27,
                                             28,
                                             29,
                                             30],
                        'resolution_options': ['480p'],
                        'aspect_ratio_options': [],
                        'quality_options': [],
                        'max_image_refs': 30,
                        'max_audio_refs': 10,
                        'max_video_refs': 10,
                        'reference_materials': ['image', 'video', 'audio'],
                        'defaults': {'duration': 5, 'resolution': '480p', 'aspect_ratio': ''}},
 'KKseadanco2.5': {'capability_type': 'video',
                   'duration_options': [30],
                   'resolution_options': ['720p', '480p'],
                   'aspect_ratio_options': [],
                   'quality_options': [],
                   'max_image_refs': 30,
                   'max_audio_refs': 10,
                   'max_video_refs': 10,
                   'reference_materials': ['image', 'video', 'audio'],
                   'defaults': {'duration': 30, 'resolution': '720p', 'aspect_ratio': ''}},
 'KKseadanco2.5M': {'capability_type': 'video',
                    'duration_options': [5,
                                         6,
                                         7,
                                         8,
                                         9,
                                         10,
                                         11,
                                         12,
                                         13,
                                         14,
                                         15,
                                         16,
                                         17,
                                         18,
                                         19,
                                         20,
                                         21,
                                         22,
                                         23,
                                         24,
                                         25,
                                         26,
                                         27,
                                         28,
                                         29,
                                         30],
                    'resolution_options': ['720p'],
                    'aspect_ratio_options': [],
                    'quality_options': [],
                    'max_image_refs': 30,
                    'max_audio_refs': 10,
                    'max_video_refs': 10,
                    'reference_materials': ['image', 'video', 'audio'],
                    'defaults': {'duration': 5, 'resolution': '720p', 'aspect_ratio': ''}}}


def model_options(spec):
    """Empty optional lists mean omit that parameter, not arbitrary values."""
    return dict(durations=list(spec.get('duration_options') or []),
                ratios=list(spec.get('aspect_ratio_options') or ['auto']),
                resolutions=list(spec.get('resolution_options') or []),
                default_duration=spec.get('defaults', {}).get('duration', 5),
                default_resolution=spec.get('defaults', {}).get('resolution', ''),
                max_refs=spec.get('max_image_refs'),
                refs_unlimited=spec.get('max_image_refs') is None,
                max_video_refs=spec.get('max_video_refs'),
                max_audio_refs=spec.get('max_audio_refs'),
                supports_audio_refs=spec.get('max_audio_refs') != 0,
                reference_video_urls_supported=True,
                ref_mode='bytes', ref_max_bytes=25 * 1024 * 1024)


def catalog_row(model, spec):
    options = model_options(spec)
    schema = dict(durations=options['durations'], aspect_ratios=options['ratios'],
                  resolutions=options['resolutions'])
    for field, target in [('max_image_refs', 'max_reference_images'),
                          ('max_video_refs', 'max_reference_videos'),
                          ('max_audio_refs', 'max_reference_audios')]:
        if isinstance(spec.get(field), int):
            schema[target] = spec[field]
    return dict(id=model, type='video', capability_schema=schema)


def _cancel(cancel):
    if cancel and cancel():
        raise ApiError('用户已取消；已有任务保留原 ID 供续查', kind=TASK_FATAL)


def _checked(data):
    if not isinstance(data, dict):
        raise ApiError('纵横科技返回的不是 JSON 对象', kind=TASK_FATAL)
    terminal = data.get('status') in ('failed', 'cancelled', 'canceled', 'expired')
    if not terminal and (data.get('success') is False or data.get('error')):
        error = data.get('error')
        message = (error.get('message') if isinstance(error, dict) else error) or data.get('message') or '请求失败'
        raise ApiError('纵横科技：' + str(message)[:400], kind=classify(200, str(message)))
    return data


def _https(value):
    parts = urlsplit(str(value))
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise ApiError('纵横科技参考素材须使用公网 HTTPS 直链或本地文件', kind=TASK_FATAL)
    return str(value)


class ZhonghengProvider(Provider):
    id = 'zhongheng'
    name = '纵横科技 cnd-coo-new.pages.dev'
    default_base_url = 'https://cnd-coo-new.pages.dev'
    supports = ('video',)
    ref_mode = 'bytes'

    def __init__(self, api_key='', base_url='', proxy='', timeout=900):
        base = (base_url or self.default_base_url).strip().rstrip('/')
        if base.endswith('/v1'):
            base = base[:-3]
        super().__init__(api_key, base, proxy, timeout)
        self._specs = None

    def accepts_url(self, model='', media='image'):
        return True

    def capabilities(self):
        options = {model: model_options(spec) for model, spec in MODEL_SPECS.items()}
        return dict(id=self.id, name=self.name, default_base_url=self.default_base_url,
                    supports=list(self.supports), video=dict(
                        models=list(options), default_model=next(iter(options), ''),
                        default_duration=5, ratios=['auto'], resolutions=[], durations=[],
                        max_refs=None, max_video_refs=None, max_audio_refs=None,
                        supports_audio_refs=True, reference_video_urls_supported=True,
                        ref_mode='bytes', ref_max_bytes=25 * 1024 * 1024,
                        model_options=options,
                        notes='模型按 Key 授权刷新，秒数、清晰度和参考数量以模型能力为准。'
                              '当前目录未声明比例时请选择“自动比例”，不发送 ratio；'
                              '有固定画幅要求时先向服务商确认。参考图可直接上传，无需对象存储。'))

    def _read_specs(self):
        if self._specs is None:
            data = _checked(self.session.request('GET', '/v1/models?include_capabilities=1',
                                                 retries=1, timeout=30))
            if not isinstance(data.get('data'), list):
                raise ApiError('纵横科技没有返回模型能力数组，请检查接口地址', kind=TASK_FATAL)
            self._specs = {row['id']: copy.deepcopy(row['capabilities']) for row in data['data']
                           if isinstance(row, dict) and isinstance(row.get('id'), str)
                           and isinstance(row.get('capabilities'), dict)
                           and row['capabilities'].get('capability_type') == 'video'}
        return self._specs

    def list_models(self):
        return list(self._read_specs())

    def filter_catalog(self, rows):
        # Existing clients call this hook after /v1/models; return only video models
        # and normalize metadata so opaque public IDs are not misclassified by name.
        return [catalog_row(model, spec) for model, spec in self._read_specs().items()]

    def _upload(self, ref):
        if str(ref).startswith('https://'):
            return _https(ref)
        if str(ref).startswith('http://'):
            return _https(ref)
        try:
            if str(ref).startswith('data:'):
                head, encoded = ref.split(',', 1)
                if ';base64' not in head:
                    raise ValueError('非 Base64 素材')
                mime = head[5:].split(';')[0]
                raw = base64.b64decode(encoded, validate=True)
                name = 'reference' + (mimetypes.guess_extension(mime) or '.bin')
            else:
                path = Path(ref)
                if not 0 < path.stat().st_size <= 25 * 1024 * 1024:
                    raise ValueError('文件须为 1 字节到 25 MB')
                raw, name = path.read_bytes(), path.name
                mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
            if not 0 < len(raw) <= 25 * 1024 * 1024:
                raise ValueError('文件须为 1 字节到 25 MB')
        except (OSError, ValueError) as exc:
            raise ApiError('纵横科技参考素材读取失败：' + str(exc), kind=TASK_FATAL) from exc
        data = _checked(self.session.request('POST', '/v1/media',
                         files=[('file', (name, raw, mime))], submit=False, retries=1, timeout=120))
        rows = data.get('data')
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or not rows[0].get('url'):
            raise ApiError('纵横科技上传未返回素材 URL，未提交生成', kind=TASK_FATAL)
        return _https(rows[0]['url'])

    def build_video_body(self, task, spec):
        model = task.model
        if not model or not (task.prompt or '').strip():
            raise ApiError('请选择纵横科技模型并填写提示词', kind=TASK_FATAL)
        if isinstance(task.duration, bool) or task.duration not in spec.get('duration_options', []):
            raise ApiError(f'{model} 不支持 {task.duration} 秒，请刷新模型后选择已声明的时长；未提交生成', kind=TASK_FATAL)
        body = dict(model=model, prompt=task.prompt, duration=int(task.duration))
        for field, value, options in [('ratio', task.ratio, 'aspect_ratio_options'),
                                      ('resolution', task.resolution, 'resolution_options'),
                                      ('quality', task.extra.get('quality'), 'quality_options')]:
            value = str(value or '').strip()
            if field == 'ratio' and value == 'auto':
                continue
            if not value:
                continue
            choices = spec.get(options) or []
            match = next((str(v) for v in choices if str(v).lower() == value.lower()), None)
            if match is None:
                hint = '；请在生产页将比例选为“自动比例”' if field == 'ratio' and not choices else ''
                raise ApiError(f'{model} 未声明支持 {field}={value}{hint}，未提交生成', kind=TASK_FATAL)
            body[field] = match
        extra = task.extra
        groups = [('images', task.refs, 'max_image_refs', 'image'),
                  ('reference_videos', extra.get('video_refs') or extra.get('reference_videos') or [], 'max_video_refs', 'video'),
                  ('reference_audios', extra.get('audio_refs') or extra.get('reference_audios') or [], 'max_audio_refs', 'audio')]
        # Validate every group before uploading anything; never silently truncate refs.
        for field, values, limit, kind in groups:
            maximum = spec.get(limit)
            if not isinstance(values, (list, tuple)):
                raise ApiError(field + ' 必须是素材数组', kind=TASK_FATAL)
            if values and (kind not in spec.get('reference_materials', []) or
                           maximum is not None and len(values) > maximum):
                raise ApiError(f'{model} 的 {field} 当前 {len(values)} 个，支持上限 {maximum}；未裁剪或提交', kind=TASK_FATAL)
            if values:
                body[field] = list(values)
        first, last = extra.get('start_frame'), extra.get('end_frame')
        if first or last:
            materials = spec.get('reference_materials') or []
            if (last and not first or any(values for _, values, _, _ in groups)
                    or first and 'start_frame' not in materials or last and 'end_frame' not in materials):
                raise ApiError('当前模型未声明该首尾帧模式，或与普通参考混用；未提交生成', kind=TASK_FATAL)
            body['start_frame'] = first
            if last:
                body['end_frame'] = last
        if extra.get('negative_prompt'):
            body['negative_prompt'] = str(extra['negative_prompt'])
        if 'generate_audio' in extra:
            if not isinstance(extra['generate_audio'], bool) or spec.get('supports_generate_audio') is not True:
                raise ApiError('当前目录未明确声明 generate_audio 支持，请移除此参数或向服务商确认', kind=TASK_FATAL)
            body['generate_audio'] = extra['generate_audio']
        return body

    def _retrieve(self, task_id, dest, *, log, cancel, poll_interval, poll_timeout, initial=None):
        path = '/v1/tasks/' + quote(str(task_id), safe='')
        submitted.remember(task_id, path=path)
        deadline = time.monotonic() + poll_timeout
        data, failures = initial, 0
        while time.monotonic() < deadline:
            _cancel(cancel)
            if data is None:
                try:
                    data = _checked(self.session.request('GET', path, retries=1,
                                                        timeout=max(1, min(60, deadline - time.monotonic()))))
                    failures = 0
                except ApiError as exc:
                    failures += 1
                    if exc.kind != RETRYABLE or failures >= 5:
                        raise
                    log(f'纵横科技查询暂时失败，保留任务 {task_id}，第 {failures} 次：{exc}')
                    data = {'status': 'processing', 'next_poll_seconds': max(poll_interval, min(60, 2 ** failures))}
            status = str(data.get('status') or '').lower()
            if status in ('failed', 'cancelled', 'canceled', 'expired'):
                raise task_failed(dict(error_code=data.get('error_code', ''),
                                       message=data.get('error_detail') or data.get('message') or status))
            if status in ('succeeded', 'success', 'completed'):
                url = next((data.get(k) for k in ('download_url', 'video_url', 'result_url', 'url') if data.get(k)), '')
                if not url:
                    raise ApiError(f'纵横科技任务 {task_id} 已完成但缺少成品 URL；请续查原任务', kind=TASK_FATAL)
                self.session.save_item(_https(url), dest, log=log)
                return dict(task_id=task_id, source=url, provider=self.id)
            if status not in ('processing', 'pending', 'queued', 'running', 'in_progress'):
                raise ApiError(f'纵横科技任务 {task_id} 返回未知状态 {status!r}；原 ID 已保留', kind=TASK_FATAL)
            log(f'纵横科技任务 {task_id}：{status}')
            try:
                suggested = float(data.get('next_poll_seconds') or poll_interval)
            except (TypeError, ValueError):
                suggested = poll_interval
            delay = min(300, max(5, poll_interval, suggested))
            until = min(deadline, time.monotonic() + delay)
            while time.monotonic() < until:
                _cancel(cancel)
                time.sleep(min(0.25, max(0, until - time.monotonic())))
            data = None
        raise ApiError(f'纵横科技任务 {task_id} 查询超时，原 ID 已保留；下次仅续查，不重新生成', kind=RETRYABLE)

    def generate_video(self, task, dest, *, log=print, cancel=None, poll_interval=10, poll_timeout=2400):
        _cancel(cancel)
        spec = self._read_specs().get(task.model)
        if spec is None:
            raise ApiError(f'当前 Key 未授权视频模型 {task.model}，请刷新模型或检查后台分配', kind=TASK_FATAL)
        body = self.build_video_body(task, spec)
        for field in ('images', 'reference_videos', 'reference_audios'):
            if field in body:
                uploaded = []
                for ref in body[field]:
                    _cancel(cancel)
                    uploaded.append(self._upload(ref))
                body[field] = uploaded
        for field in ('start_frame', 'end_frame'):
            if field in body:
                _cancel(cancel)
                body[field] = self._upload(body[field])
        _cancel(cancel)
        log(f'纵横科技 {task.model}：{task.duration} 秒，比例 {body.get("ratio", "自动（不发送 ratio）")}')
        # This gateway documents idempotency. HTTP retries keep precisely the same
        # body/key; exhausted uncertain submissions must not become a fresh order.
        idem = uuid.uuid4().hex
        try:
            data = _checked(self.session.request('POST', '/v1/videos', json_body=body,
                             headers={'Idempotency-Key': idem}, submit=False, retries=2, timeout=300))
        except ApiError as exc:
            if exc.kind == RETRYABLE:
                raise submit_uncertain('纵横科技幂等提交仍未确认，订单号 ' + idem, exc.status) from exc
            raise
        except ValueError as exc:
            raise submit_uncertain('纵横科技返回无法解析的 JSON，订单号 ' + idem) from exc
        task_id = data.get('task_id')
        if not task_id:
            raise submit_uncertain('纵横科技未返回 task_id，订单号 ' + idem)
        result = self._retrieve(task_id, dest, log=log, cancel=cancel, poll_interval=poll_interval,
                                poll_timeout=poll_timeout, initial=data)
        return dict(result, model=task.model)

    def resume(self, record, dest, *, log=print, cancel=None, poll_interval=10, poll_timeout=2400):
        if not record.get('task_id'):
            raise ApiError('纵横科技待取回记录缺少 task_id', kind=TASK_FATAL)
        result = self._retrieve(record['task_id'], dest, log=log, cancel=cancel,
                                poll_interval=poll_interval, poll_timeout=poll_timeout)
        return dict(result, model=record.get('model', ''), resumed=True)
