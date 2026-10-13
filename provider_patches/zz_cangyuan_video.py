"""沧元统一视频接口；2026-10-13 官方逐模型文档与 Key 只读目录。

独立 id 让视频 Key 与图像 Key 分开保存。此文件可独立用作旧 EXE 外挂。
来源：https://ai.cangyuansuanli.cn/docs/api/video
"""
from urllib.parse import urlsplit

from core.apiutil import ApiError, extract_task_id
from core.providers.base import Provider

PATCH_VERSION = '20261013.1'
DEFAULT_MODEL = 'sd11-seedance-2.5'
# 官方文档快照；suggestions 是候选，不当作服务商完整允许集合。
MODELS = {'grok-imagine-video': {'params': {'model': {},
                                   'prompt': {},
                                   'duration': {'minimum': 1, 'maximum': 15},
                                   'aspect_ratio': {'suggestions': ['16:9', '9:16', '1:1']},
                                   'resolution': {'suggestions': ['480p', '720p']},
                                   'reference_image_urls': {'maxItems': 1}},
                        'notes': [],
                        'paired_frames': False,
                        'exclusive_frames': False},
 'grok-imagine-video-1.5': {'params': {'model': {},
                                       'prompt': {},
                                       'duration': {'minimum': 1, 'maximum': 15},
                                       'aspect_ratio': {'suggestions': ['16:9', '9:16', '1:1']},
                                       'resolution': {'suggestions': ['480p', '720p', '1080p']},
                                       'reference_image_urls': {'maxItems': 7}},
                            'notes': [],
                            'paired_frames': False,
                            'exclusive_frames': False},
 'gv3-grok-video-1.5': {'params': {'model': {},
                                   'prompt': {},
                                   'duration': {'minimum': 3, 'maximum': 15, 'suggestedDefault': 5},
                                   'aspect_ratio': {'suggestions': ['1:1', '16:9', '9:16']},
                                   'resolution': {'suggestions': ['720p'], 'suggestedDefault': '720p'},
                                   'reference_image_urls': {'maxItems': 7}},
                        'notes': [],
                        'paired_frames': False,
                        'exclusive_frames': False},
 'happyhorse-1.1': {'params': {'model': {},
                               'prompt': {},
                               'duration': {'minimum': 3, 'maximum': 15},
                               'aspect_ratio': {'suggestions': ['16:9',
                                                                '9:16',
                                                                '1:1',
                                                                '4:3',
                                                                '3:4',
                                                                '21:9',
                                                                '9:21',
                                                                '5:4',
                                                                '4:5']},
                               'resolution': {'suggestions': ['1080p', '720p']},
                               'reference_image_urls': {'maxItems': 9}},
                    'notes': [],
                    'paired_frames': False,
                    'exclusive_frames': False},
 'kl1-kling-3.0': {'params': {'model': {},
                              'prompt': {},
                              'duration': {'minimum': 3, 'maximum': 15},
                              'aspect_ratio': {'suggestions': ['16:9', '9:16', '1:1']},
                              'resolution': {'suggestions': ['720p', '1080p'], 'suggestedDefault': '720p'},
                              'generate_audio': {},
                              'seed': {},
                              'first_image_url': {},
                              'last_image_url': {}},
                   'notes': ['可以只提供首帧；提供尾帧时必须同时提供首帧。', '首尾帧不能与参考图、参考视频或参考音频同时使用。'],
                   'paired_frames': False,
                   'exclusive_frames': True},
 'kling-3.0': {'params': {'model': {},
                          'prompt': {},
                          'duration': {'minimum': 3, 'maximum': 15},
                          'aspect_ratio': {'suggestions': ['16:9', '1:1', '9:16']},
                          'generate_audio': {},
                          'reference_image_urls': {'maxItems': 1}},
               'notes': [],
               'paired_frames': False,
               'exclusive_frames': False},
 'kling-3.0-pro': {'params': {'model': {},
                              'prompt': {},
                              'duration': {'minimum': 3, 'maximum': 15},
                              'aspect_ratio': {'suggestions': ['16:9', '1:1', '9:16']},
                              'generate_audio': {},
                              'reference_image_urls': {'maxItems': 4}},
                   'notes': [],
                   'paired_frames': False,
                   'exclusive_frames': False},
 'mm2-minimax-h3': {'params': {'model': {},
                               'prompt': {},
                               'duration': {'minimum': 4, 'maximum': 15},
                               'aspect_ratio': {'suggestions': ['16:9',
                                                                '4:3',
                                                                '1:1',
                                                                '3:4',
                                                                '9:16',
                                                                '21:9',
                                                                'adaptive']},
                               'resolution': {'suggestions': ['768P', '2K'], 'suggestedDefault': '768P'},
                               'seed': {},
                               'reference_image_urls': {'maxItems': 9},
                               'reference_videos': {'maxItems': 3},
                               'reference_audios': {'maxItems': 3},
                               'first_image_url': {},
                               'last_image_url': {}},
                    'notes': ['可以只提供首帧；提供尾帧时必须同时提供首帧。'],
                    'paired_frames': False,
                    'exclusive_frames': False},
 'mm2-minimax-h3-max': {'params': {'model': {},
                                   'prompt': {},
                                   'duration': {'minimum': 5, 'maximum': 15},
                                   'aspect_ratio': {'suggestions': ['16:9',
                                                                    '4:3',
                                                                    '1:1',
                                                                    '3:4',
                                                                    '9:16',
                                                                    '21:9',
                                                                    'adaptive']},
                                   'resolution': {'suggestions': ['480P', '768P'],
                                                  'suggestedDefault': '480P'},
                                   'seed': {},
                                   'reference_image_urls': {'maxItems': 9},
                                   'reference_videos': {'maxItems': 3},
                                   'reference_audios': {'maxItems': 3},
                                   'first_image_url': {},
                                   'last_image_url': {}},
                        'notes': ['可以只提供首帧；提供尾帧时必须同时提供首帧。'],
                        'paired_frames': False,
                        'exclusive_frames': False},
 'mm3-minimax-h3-2k': {'params': {'model': {},
                                  'prompt': {},
                                  'duration': {'minimum': 15, 'maximum': 15, 'suggestedDefault': 15},
                                  'aspect_ratio': {'suggestions': ['1:1', '16:9', '9:16']},
                                  'resolution': {'suggestions': ['2k'], 'suggestedDefault': '2k'},
                                  'reference_image_urls': {'maxItems': 9},
                                  'reference_videos': {'maxItems': 3},
                                  'reference_audios': {'maxItems': 3}},
                       'notes': [],
                       'paired_frames': False,
                       'exclusive_frames': False},
 'niulai-pro': {'params': {'model': {},
                           'prompt': {},
                           'duration': {'minimum': 4, 'maximum': 15},
                           'aspect_ratio': {'suggestions': ['16:9',
                                                            '9:16',
                                                            '1:1',
                                                            '4:3',
                                                            '3:4',
                                                            '21:9',
                                                            'adaptive'],
                                            'suggestedDefault': '16:9'},
                           'resolution': {'suggestions': ['768p', '2k'], 'suggestedDefault': '768p'},
                           'reference_image_urls': {'maxItems': 9},
                           'reference_videos': {'maxItems': 3},
                           'reference_audios': {'maxItems': 3},
                           'first_image_url': {},
                           'last_image_url': {}},
                'notes': ['可以只提供首帧；提供尾帧时必须同时提供首帧。', '首尾帧不能与参考图、参考视频或参考音频同时使用。'],
                'paired_frames': False,
                'exclusive_frames': True},
 'omni-fast': {'params': {'model': {},
                          'prompt': {},
                          'duration': {},
                          'aspect_ratio': {'suggestions': ['16:9', '9:16']},
                          'reference_image_urls': {'maxItems': 5},
                          'first_image_url': {},
                          'last_image_url': {}},
               'notes': ['首尾帧须成对提供。', '首尾帧不能与参考图、参考视频或参考音频同时使用。'],
               'paired_frames': True,
               'exclusive_frames': True},
 'omni-fast-no-water': {'params': {'model': {},
                                   'prompt': {},
                                   'duration': {},
                                   'aspect_ratio': {'suggestions': ['16:9', '9:16']},
                                   'reference_image_urls': {'maxItems': 5},
                                   'first_image_url': {},
                                   'last_image_url': {}},
                        'notes': ['首尾帧须成对提供。', '首尾帧不能与参考图、参考视频或参考音频同时使用。'],
                        'paired_frames': True,
                        'exclusive_frames': True},
 'omni-v2v': {'params': {'model': {},
                         'prompt': {},
                         'duration': {},
                         'aspect_ratio': {'suggestions': ['16:9', '9:16']},
                         'reference_image_urls': {'maxItems': 5},
                         'reference_videos': {'maxItems': 2}},
              'notes': ['需要提供参考素材；编辑请求请同时提供参考图。'],
              'paired_frames': False,
              'exclusive_frames': False},
 'omni-v2v-no-water': {'params': {'model': {},
                                  'prompt': {},
                                  'duration': {},
                                  'aspect_ratio': {'suggestions': ['16:9', '9:16']},
                                  'reference_image_urls': {'maxItems': 5},
                                  'reference_videos': {'maxItems': 2}},
                       'notes': ['需要提供参考素材；编辑请求请同时提供参考图。'],
                       'paired_frames': False,
                       'exclusive_frames': False},
 'sd10-seedance-2.0': {'params': {'model': {},
                                  'prompt': {},
                                  'duration': {'minimum': 5, 'maximum': 15, 'enum': [5, 10, 15]},
                                  'aspect_ratio': {'suggestions': ['16:9', '9:16', '1:1']},
                                  'resolution': {'suggestions': ['720p']},
                                  'reference_image_urls': {'maxItems': 9}},
                       'notes': [],
                       'paired_frames': False,
                       'exclusive_frames': False,
                       'fixed_resolution': '720p'},
 'sd10-seedance-2.0-fast': {'params': {'model': {},
                                       'prompt': {},
                                       'duration': {'minimum': 5, 'maximum': 15, 'enum': [5, 10, 15]},
                                       'aspect_ratio': {'suggestions': ['16:9', '9:16', '1:1']},
                                       'resolution': {'suggestions': ['720p']},
                                       'reference_image_urls': {'maxItems': 9}},
                            'notes': [],
                            'paired_frames': False,
                            'exclusive_frames': False,
                            'fixed_resolution': '720p'},
 'sd10-seedance-2.0-mini': {'params': {'model': {},
                                       'prompt': {},
                                       'duration': {'minimum': 5, 'maximum': 10, 'enum': [5, 10]},
                                       'aspect_ratio': {'suggestions': ['16:9', '9:16', '1:1']},
                                       'resolution': {'suggestions': ['720p']},
                                       'reference_image_urls': {'maxItems': 9}},
                            'notes': [],
                            'paired_frames': False,
                            'exclusive_frames': False,
                            'fixed_resolution': '720p'},
 'sd10-seedance-2.5': {'params': {'model': {},
                                  'prompt': {},
                                  'duration': {'minimum': 30, 'maximum': 30, 'enum': [30]},
                                  'aspect_ratio': {'suggestions': ['16:9', '9:16', '1:1']},
                                  'resolution': {'suggestions': ['720p']},
                                  'reference_image_urls': {'maxItems': 30}},
                       'notes': [],
                       'paired_frames': False,
                       'exclusive_frames': False,
                       'fixed_resolution': '720p'},
 'sd11-seedance-2.0': {'params': {'model': {},
                                  'prompt': {},
                                  'duration': {'minimum': 4, 'maximum': 15},
                                  'aspect_ratio': {'suggestions': ['16:9',
                                                                   '4:3',
                                                                   '1:1',
                                                                   '3:4',
                                                                   '9:16',
                                                                   '21:9',
                                                                   'adaptive']},
                                  'resolution': {'suggestions': ['480p', '720p', '1080p'],
                                                 'suggestedDefault': '480p'},
                                  'generate_audio': {},
                                  'seed': {},
                                  'reference_image_urls': {'maxItems': 9},
                                  'reference_videos': {'maxItems': 3},
                                  'reference_audios': {'maxItems': 3},
                                  'first_image_url': {},
                                  'last_image_url': {}},
                       'notes': ['可以只提供首帧；提供尾帧时必须同时提供首帧。', 'resolution 不支持 4k / 4K。'],
                       'paired_frames': False,
                       'exclusive_frames': False},
 'sd11-seedance-2.0-fast': {'params': {'model': {},
                                       'prompt': {},
                                       'duration': {'minimum': 4, 'maximum': 15},
                                       'aspect_ratio': {'suggestions': ['16:9',
                                                                        '4:3',
                                                                        '1:1',
                                                                        '3:4',
                                                                        '9:16',
                                                                        '21:9',
                                                                        'adaptive']},
                                       'resolution': {'suggestions': ['480p', '720p'],
                                                      'suggestedDefault': '480p'},
                                       'generate_audio': {},
                                       'seed': {},
                                       'reference_image_urls': {'maxItems': 9},
                                       'reference_videos': {'maxItems': 3},
                                       'reference_audios': {'maxItems': 3},
                                       'first_image_url': {},
                                       'last_image_url': {}},
                            'notes': ['可以只提供首帧；提供尾帧时必须同时提供首帧。'],
                            'paired_frames': False,
                            'exclusive_frames': False},
 'sd11-seedance-2.0-mini': {'params': {'model': {},
                                       'prompt': {},
                                       'duration': {'minimum': 4, 'maximum': 15},
                                       'aspect_ratio': {'suggestions': ['16:9',
                                                                        '4:3',
                                                                        '1:1',
                                                                        '3:4',
                                                                        '9:16',
                                                                        '21:9',
                                                                        'adaptive']},
                                       'resolution': {'suggestions': ['480p', '720p'],
                                                      'suggestedDefault': '480p'},
                                       'generate_audio': {},
                                       'seed': {},
                                       'reference_image_urls': {'maxItems': 9},
                                       'reference_videos': {'maxItems': 3},
                                       'reference_audios': {'maxItems': 3},
                                       'first_image_url': {},
                                       'last_image_url': {}},
                            'notes': ['可以只提供首帧；提供尾帧时必须同时提供首帧。'],
                            'paired_frames': False,
                            'exclusive_frames': False},
 'sd11-seedance-2.5': {'params': {'model': {},
                                  'prompt': {},
                                  'duration': {'minimum': 4, 'maximum': 30},
                                  'aspect_ratio': {'suggestions': ['16:9',
                                                                   '4:3',
                                                                   '1:1',
                                                                   '3:4',
                                                                   '9:16',
                                                                   '21:9',
                                                                   'adaptive']},
                                  'resolution': {'suggestions': ['480p', '720p', '1080p'],
                                                 'suggestedDefault': '480p'},
                                  'generate_audio': {},
                                  'seed': {},
                                  'reference_image_urls': {'maxItems': 30},
                                  'reference_videos': {'maxItems': 10},
                                  'reference_audios': {'maxItems': 10},
                                  'first_image_url': {},
                                  'last_image_url': {}},
                       'notes': ['可以只提供首帧；提供尾帧时必须同时提供首帧。'],
                       'paired_frames': False,
                       'exclusive_frames': False},
 'sd13-seedance-2.0': {'params': {'model': {},
                                  'prompt': {},
                                  'duration': {'minimum': 4, 'maximum': 15, 'suggestedDefault': 8},
                                  'aspect_ratio': {'suggestions': ['auto',
                                                                   '21:9',
                                                                   '16:9',
                                                                   '4:3',
                                                                   '1:1',
                                                                   '3:4',
                                                                   '9:16'],
                                                   'suggestedDefault': 'auto'},
                                  'resolution': {'suggestions': ['480p', '720p', '1080p', '4k'],
                                                 'suggestedDefault': '480p'},
                                  'generate_audio': {},
                                  'reference_image_urls': {'maxItems': 9},
                                  'reference_videos': {'maxItems': 3}},
                       'notes': [],
                       'paired_frames': False,
                       'exclusive_frames': False},
 'sd13-seedance-2.0-fast': {'params': {'model': {},
                                       'prompt': {},
                                       'duration': {'minimum': 4, 'maximum': 15, 'suggestedDefault': 8},
                                       'aspect_ratio': {'suggestions': ['auto',
                                                                        '21:9',
                                                                        '16:9',
                                                                        '4:3',
                                                                        '1:1',
                                                                        '3:4',
                                                                        '9:16'],
                                                        'suggestedDefault': 'auto'},
                                       'resolution': {'suggestions': ['480p', '720p'],
                                                      'suggestedDefault': '480p'},
                                       'generate_audio': {},
                                       'reference_image_urls': {'maxItems': 9},
                                       'reference_videos': {'maxItems': 3}},
                            'notes': [],
                            'paired_frames': False,
                            'exclusive_frames': False},
 'sd13-seedance-2.0-mini': {'params': {'model': {},
                                       'prompt': {},
                                       'duration': {'minimum': 4, 'maximum': 15, 'suggestedDefault': 8},
                                       'aspect_ratio': {'suggestions': ['auto',
                                                                        '21:9',
                                                                        '16:9',
                                                                        '4:3',
                                                                        '1:1',
                                                                        '3:4',
                                                                        '9:16'],
                                                        'suggestedDefault': 'auto'},
                                       'resolution': {'suggestions': ['480p', '720p'],
                                                      'suggestedDefault': '480p'},
                                       'generate_audio': {},
                                       'reference_image_urls': {'maxItems': 9},
                                       'reference_videos': {'maxItems': 3}},
                            'notes': [],
                            'paired_frames': False,
                            'exclusive_frames': False},
 'sd13-seedance-2.5': {'params': {'model': {},
                                  'prompt': {},
                                  'duration': {'minimum': 4, 'maximum': 30, 'suggestedDefault': 5},
                                  'aspect_ratio': {'suggestions': ['auto',
                                                                   '21:9',
                                                                   '16:9',
                                                                   '4:3',
                                                                   '1:1',
                                                                   '3:4',
                                                                   '9:16'],
                                                   'suggestedDefault': 'auto'},
                                  'resolution': {'suggestions': ['480p', '720p'], 'suggestedDefault': '480p'},
                                  'generate_audio': {},
                                  'reference_image_urls': {'maxItems': 30},
                                  'reference_videos': {'maxItems': 10}},
                       'notes': [],
                       'paired_frames': False,
                       'exclusive_frames': False},
 'sd14-seedance-2.0': {'params': {'model': {},
                                  'prompt': {},
                                  'duration': {'minimum': 4, 'maximum': 15},
                                  'aspect_ratio': {'suggestions': ['1:1', '16:9', '9:16']},
                                  'resolution': {'suggestions': ['720p'], 'suggestedDefault': '720p'},
                                  'reference_image_urls': {'maxItems': 9},
                                  'reference_videos': {'maxItems': 3},
                                  'reference_audios': {'maxItems': 3},
                                  'first_image_url': {},
                                  'last_image_url': {}},
                       'notes': ['可以只提供首帧；提供尾帧时必须同时提供首帧。', '首尾帧不能与参考图、参考视频或参考音频同时使用。'],
                       'paired_frames': False,
                       'exclusive_frames': True,
                       'fixed_resolution': '720p'},
 'sd7-seedance-2.0-1080p': {'params': {'model': {},
                                       'prompt': {},
                                       'duration': {'minimum': 4, 'maximum': 15},
                                       'aspect_ratio': {'suggestions': ['16:9',
                                                                        '9:16',
                                                                        '1:1',
                                                                        '4:3',
                                                                        '3:4',
                                                                        '21:9']},
                                       'resolution': {'suggestions': ['1080p']},
                                       'generate_audio': {},
                                       'reference_image_urls': {'maxItems': 5},
                                       'reference_videos': {'maxItems': 3},
                                       'reference_audios': {'maxItems': 3}},
                            'notes': [],
                            'paired_frames': False,
                            'exclusive_frames': False,
                            'fixed_resolution': '1080p'},
 'sd7-seedance-2.0-720p': {'params': {'model': {},
                                      'prompt': {},
                                      'duration': {'minimum': 4, 'maximum': 15},
                                      'aspect_ratio': {'suggestions': ['16:9',
                                                                       '9:16',
                                                                       '1:1',
                                                                       '4:3',
                                                                       '3:4',
                                                                       '21:9']},
                                      'resolution': {'suggestions': ['720p']},
                                      'generate_audio': {},
                                      'reference_image_urls': {'maxItems': 5},
                                      'reference_videos': {'maxItems': 3},
                                      'reference_audios': {'maxItems': 3}},
                           'notes': [],
                           'paired_frames': False,
                           'exclusive_frames': False,
                           'fixed_resolution': '720p'},
 'sd8-seedance-2.5': {'params': {'model': {},
                                 'prompt': {},
                                 'duration': {'minimum': 30, 'maximum': 30},
                                 'aspect_ratio': {'suggestions': ['21:9',
                                                                  '16:9',
                                                                  '4:3',
                                                                  '1:1',
                                                                  '3:4',
                                                                  '9:16']},
                                 'reference_image_urls': {'maxItems': 9}},
                      'notes': [],
                      'paired_frames': False,
                      'exclusive_frames': False,
                      'fixed_resolution': '720p'},
 'sd8-seedance-2.5-s': {'params': {'model': {},
                                   'prompt': {},
                                   'duration': {'minimum': 30, 'maximum': 30},
                                   'aspect_ratio': {'suggestions': ['21:9',
                                                                    '16:9',
                                                                    '4:3',
                                                                    '1:1',
                                                                    '3:4',
                                                                    '9:16']},
                                   'reference_image_urls': {'maxItems': 30},
                                   'reference_videos': {'maxItems': 10},
                                   'reference_audios': {'maxItems': 10}},
                        'notes': [],
                        'paired_frames': False,
                        'exclusive_frames': False,
                        'fixed_resolution': '720p'},
 'wan3.0-15s': {'params': {'model': {},
                           'prompt': {},
                           'duration': {'minimum': 4, 'maximum': 15},
                           'aspect_ratio': {'suggestions': ['16:9', '1:1', '9:16', '4:3', '3:4']},
                           'resolution': {'suggestions': ['480p', '720p', '1080p']},
                           'reference_image_urls': {'maxItems': 10},
                           'reference_videos': {'maxItems': 5},
                           'reference_audios': {'maxItems': 5}},
                'notes': [],
                'paired_frames': False,
                'exclusive_frames': False},
 'wan4-wan3.0': {'params': {'model': {},
                            'prompt': {},
                            'duration': {'minimum': 2, 'maximum': 30},
                            'aspect_ratio': {'suggestions': ['adaptive',
                                                             '16:9',
                                                             '9:16',
                                                             '1:1',
                                                             '4:3',
                                                             '3:4']},
                            'resolution': {'suggestions': ['480P', '720P', '1080P'],
                                           'suggestedDefault': '480P'},
                            'generate_audio': {},
                            'seed': {},
                            'reference_image_urls': {'maxItems': 10},
                            'reference_videos': {'maxItems': 5},
                            'reference_audios': {'maxItems': 5},
                            'first_image_url': {},
                            'last_image_url': {}},
                 'notes': ['可以只提供首帧；提供尾帧时必须同时提供首帧。'],
                 'paired_frames': False,
                 'exclusive_frames': False}}


def _video_result(data):
    """只取结果字段，避免把回显的参考视频当成片下载。"""
    if not isinstance(data, dict):
        return ''
    if data.get('status') in ('queued', 'in_progress', 'failed', 'running', 'pending'):
        return ''
    url = data.get('video_url')
    if not url:
        rows = data.get('data')
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            url = rows[0].get('url')
        elif isinstance(rows, dict):
            return _video_result(rows)
    return url if isinstance(url, str) and url.startswith(('https://', 'http://')) else ''


def _url(value):
    if not isinstance(value, str) or urlsplit(value).scheme != 'https' or not urlsplit(value).netloc:
        raise ApiError('沧元视频参考素材必须是 HTTPS 链接；请配置对象存储上传', kind='task_fatal')
    return value


def model_options(model):
    spec = MODELS[model]
    params = spec['params']
    duration = params['duration']
    durations = duration.get('enum', [])
    if not durations and 'minimum' in duration and 'maximum' in duration:
        durations = list(range(duration['minimum'], duration['maximum'] + 1))
    image = params.get('reference_image_urls', {})
    # kl1 只有首尾帧：Studio 的一/两张图片按上传顺序明确映射到首/尾帧。
    frames_only = 'reference_image_urls' not in params and 'first_image_url' in params
    opts = {
        'ratios': params.get('aspect_ratio', {}).get('suggestions', []),
        'resolutions': params.get('resolution', {}).get('suggestions', []),
        'durations': durations,
        'max_refs': 2 if frames_only else image.get('maxItems', 0),
        'refs_unlimited': 'reference_image_urls' in params and 'maxItems' not in image,
        'max_video_refs': params.get('reference_videos', {}).get('maxItems', 0),
        'max_audio_refs': params.get('reference_audios', {}).get('maxItems', 0),
        'supports_audio_refs': 'reference_audios' in params,
        'ref_max_bytes': 9_500_000,
        'ref_mode': 'url',
        'notes': '；'.join(spec.get('notes', [])),
    }
    if spec.get('fixed_resolution'):
        opts['resolutions'] = [spec['fixed_resolution']]
    return opts


class CangyuanVideoProvider(Provider):
    id = 'cangyuan_video'
    name = '沧元算力（视频）'
    aliases = ('苍元视频', 'cangyuan-video')
    default_base_url = 'https://ai.cangyuansuanli.cn'
    supports = ('video',)
    ref_mode = 'url'

    def __init__(self, api_key='', base_url='', proxy='', timeout=900):
        base = (base_url or self.default_base_url).rstrip('/')
        if base.endswith('/v1'):
            base = base[:-3]
        super().__init__(api_key=api_key, base_url=base, proxy=proxy, timeout=timeout)

    def capabilities(self):
        options = {m: model_options(m) for m in MODELS}
        def union(key):
            return list(dict.fromkeys(v for op in options.values() for v in op[key]))
        return {'id': self.id, 'name': self.name, 'supports': list(self.supports),
                'default_base_url': self.default_base_url, 'video': {
                    'models': list(MODELS), 'catalog_keep_models': list(MODELS),
                    'default_model': DEFAULT_MODEL, 'default_duration': 15,
                    'ratios': union('ratios'), 'resolutions': union('resolutions'),
                    'durations': sorted(union('durations')), 'max_refs': 30,
                    'max_video_refs': 10, 'max_audio_refs': 10,
                    'ref_mode': 'url', 'ref_max_bytes': 9_500_000,
                    'model_options': options,
                    'notes': '2026-10-13 文档快照：34 个视频型号；按模型展示参数。'
                             '参考素材使用 HTTPS 链接，视频 Key 与图片 Key 分开填写。'
                             'kl1-kling-3.0 的第1/2张图分别作为首/尾帧。'}}

    def build_video_body(self, task):
        model = task.model or DEFAULT_MODEL
        if model not in MODELS:
            raise ApiError(f'沧元视频尚未适配 {model} 的字段，请更新外挂或选择目录内型号', kind='task_fatal')
        spec = MODELS[model]
        params = spec['params']
        extra = task.extra or {}
        if not (task.prompt or '').strip():
            raise ApiError('视频提示词不能为空', kind='task_fatal')
        duration = task.duration
        rule = params['duration']
        if (isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0
                or duration < rule.get('minimum', 1) or duration > rule.get('maximum', float('inf'))
                or ('enum' in rule and duration not in rule['enum'])):
            raise ApiError(f'{model} 不支持 {duration} 秒，请按模型候选选择；没有自动改秒数', kind='task_fatal')
        body = {'model': model, 'prompt': task.prompt, 'duration': duration}
        if task.ratio:
            body['aspect_ratio'] = task.ratio
        resolution = (task.resolution or '').strip()
        fixed = spec.get('fixed_resolution')
        if resolution and fixed and resolution.lower() != fixed.lower():
            raise ApiError(f'{model} 的固定清晰度为 {fixed}，当前选了 {resolution}', kind='task_fatal')
        if resolution:
            if 'resolution' not in params and not fixed:
                raise ApiError(f'{model} 未开放清晰度参数，请选“跟随任务”并清除材料中的 resolution', kind='task_fatal')
            if 'resolution' in params:
                choices = params['resolution'].get('suggestions', [])
                body['resolution'] = next((v for v in choices if v.lower() == resolution.lower()), resolution)
        if model == 'sd11-seedance-2.0' and resolution.lower() == '4k':
            raise ApiError(f'{model} 文档明确不支持 4K，请选择 480p/720p/1080p', kind='task_fatal')
        images = list(task.refs or [])
        first = extra.get('first_image_url')
        last = extra.get('last_image_url')
        if 'reference_image_urls' not in params and images:
            if 'first_image_url' not in params or len(images) > 2 or first or last:
                raise ApiError(f'{model} 只接收首尾帧，最多两张图且不能与显式首尾帧重复填写', kind='task_fatal')
            first = images[0]
            last = images[1] if len(images) == 2 else None
            images = []
        media = {'reference_image_urls': images,
                 'reference_videos': extra.get('video_refs') or extra.get('reference_videos') or [],
                 'reference_audios': extra.get('audio_refs') or extra.get('reference_audios') or []}
        for field, values in media.items():
            if not isinstance(values, (list, tuple)):
                raise ApiError(f'{field} 必须是链接数组', kind='task_fatal')
            if not values:
                continue
            if field not in params:
                raise ApiError(f'{model} 不支持 {field}；请移除该类参考或换模型', kind='task_fatal')
            maximum = params[field].get('maxItems')
            if maximum is not None and len(values) > maximum:
                raise ApiError(f'{model} 的 {field} 最多 {maximum} 个，当前 {len(values)} 个；没有裁剪或提交', kind='task_fatal')
            body[field] = [_url(v) for v in values]
        for field, value in [('first_image_url', first), ('last_image_url', last)]:
            if value:
                if field not in params:
                    raise ApiError(f'{model} 不支持 {field}', kind='task_fatal')
                body[field] = _url(value)
        if last and not first or (spec.get('paired_frames') and bool(first) != bool(last)):
            raise ApiError(f'{model} 的首尾帧组合不完整，请补齐或移除', kind='task_fatal')
        if (first or last) and spec.get('exclusive_frames') and any(media.values()):
            raise ApiError(f'{model} 首尾帧不能与其他参考素材混用', kind='task_fatal')
        if model.startswith('omni-v2v') and not (images and media['reference_videos']):
            raise ApiError(f'{model} 是视频编辑型号，请同时提供参考视频和参考图片', kind='task_fatal')
        for field in ('seed', 'generate_audio'):
            if field in extra:
                if field not in params:
                    raise ApiError(f'{model} 不支持 {field}', kind='task_fatal')
                value = extra[field]
                if (field == 'generate_audio' and not isinstance(value, bool)) or (
                        field == 'seed' and (isinstance(value, bool) or not isinstance(value, int))):
                    raise ApiError(f'{field} 类型不正确', kind='task_fatal')
                body[field] = value
        return body

    def _retrieve(self, task_id, dest, *, log, cancel, poll_interval, poll_timeout):
        url = self.session.poll('/v1/videos/{id}', task_id, picker=_video_result,
                                interval=max(5, poll_interval), timeout=max(1800, poll_timeout),
                                log=log, cancel=cancel)
        self.session.save_item(url, dest, log=log)
        return {'task_id': task_id, 'source': url, 'provider': self.id}

    def generate_video(self, task, dest, *, log=print, cancel=None, poll_interval=10, poll_timeout=2400):
        body = self.build_video_body(task)
        log(f'沧元视频 {body["model"]}: {body["duration"]} 秒，比例 {body.get("aspect_ratio", "默认")}，'
            f'参考图 {len(body.get("reference_image_urls", []))} 张'
            + ('（参考图按顺序映射为首尾帧）' if body.get('first_image_url') else ''))
        # 创建只发一次；网络结果不确定交给 Studio 保留，不自动重复付费提交。
        data = self.session.request('POST', '/v1/videos', json_body=body, retries=1, timeout=300)
        tid = extract_task_id(data)
        if not tid:
            raise ApiError('沧元视频提交未返回任务 ID，请先核对后台记录再重试', kind='task_fatal')
        result = self._retrieve(tid, dest, log=log, cancel=cancel,
                                poll_interval=poll_interval, poll_timeout=poll_timeout)
        return dict(result, model=body['model'])

    def resume(self, record, dest, *, log=print, cancel=None, poll_interval=10, poll_timeout=2400):
        tid = record.get('task_id')
        if not tid:
            raise ApiError('待取回记录缺少 task_id', kind='task_fatal')
        result = self._retrieve(tid, dest, log=log, cancel=cancel,
                                poll_interval=poll_interval, poll_timeout=poll_timeout)
        return dict(result, model=record.get('model', ''), resumed=True)
