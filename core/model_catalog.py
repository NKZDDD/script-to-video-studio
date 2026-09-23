"""模型目录：按服务商实拉刷新、按凭据隔离缓存，打包时快照兜底。

**从 respect_batch 移植过来的**（那边先做的，两边保持同一套口径）。
用户原话（2026-09-02）：「批量工具都已经尝试使用拉取最新模型来更新服务商了，
现在 studio 也需要这样」。

为什么必须是运行时拉、不是我手改清单：模型名换得比什么都快。
这几天实拉的账：鹤一次下线 9 个（其中 `sd2-720p` 还是它的默认模型和代码
兜底 —— 也就是「没改过模型就点开始」必然失败）、灵感鸭多出 24 个我们没列的、
章鱼哥 4 个已下线。手改一次，下周又对不上，而**对不上的表现是跑到那一步
才报「找不到模型」**，那可能是几百步之后的事。

几条从批量工具那边继承的设计，都别改：
  · **认不出类型的照样进清单**（`unknown`）—— 拉取结果不是白名单。
    判不出是图还是片就都留着，让人自己选；漏掉一个能用的模型比多列一个贵。
  · **按凭据缓存**：换了 Key 或 base_url 就重拉 —— 不同 Key 的可见清单不一样
    （坤鸡按令牌分组过滤，实拉只回 1 个）。
  · **打包时快照兜底**：目标机器还没配 Key 时也有一份能用的清单。
  · hvtald 没有清单接口，跳过 —— 它是固定账号模型。
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from datetime import datetime, timezone

import requests

from . import paths, providers
from .store import read_json, write_json


def _provider_cfg(cfg: dict, pid: str) -> dict:
    """这一家保存下来的配置。studio 没有 core.config —— 形状和批量工具一样，
    都是 `cfg["providers"][pid]`，所以就地取，别为一个函数拖一个模块过来。"""
    return ((cfg or {}).get("providers") or {}).get(pid) or {}

_LOCK = threading.RLock()


def identity(pid, pc):
    """缓存的凭据指纹。**所有 Key 字段都要进**，不只 `api_key`。

    和 `fetch` 是同一个毛病的两处：超模按能力分四把 Key、根本没有 `api_key`
    这个字段，所以原来这个指纹对超模**永远是同一个值** —— 换了任何一把 Key
    都认不出变化，会一直拿旧清单用，而旧清单里的模型这把新 Key 可能看不见。
    不报错，只是选中了跑起来才「找不到模型」。

    排序后再算：字典顺序不该影响指纹，否则改一次配置就白拉一次。
    """
    keys = sorted(f"{k}={v}" for k, v in (pc or {}).items()
                  if 'key' in k.lower() and str(v).strip())
    values = [pid, pc.get('base_url') or providers.REGISTRY[pid].default_base_url,
              *keys]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def fetch(pid, pc):
    """仅 GET 模型端点；不跟随跨站跳转、不返回含凭据的原始错误。"""
    if pid in ('hvtald', 'xmd'):
        return {'ok': False, 'msg': '该服务商使用固定模型，没有模型清单接口'}
    prov = providers.build(pid, pc.get('api_key', ''), pc.get('base_url', ''),
                           pc.get('proxy') or 'direct', 20)
    endpoint = {'gate': '/public/model_group/info',
                'zhi': '/api/v1/available-models'}.get(pid, '/v1/models')
    if not prov.session.base_url:
        return {'ok': False, 'msg': '请先填写该服务商的接口地址'}
    # **把这一家配置里所有的 Key 都试一遍，不只认 `api_key`。**
    #
    # 原来只取 `pc['api_key']`（加一个 kunji 的特例）。而超模**根本没有那个
    # 字段** —— 它按能力分四把：`llm_api_key` / `image_1k_api_key` /
    # `image_4k_api_key` / `video_api_key`（server 层的 _CHAOMO_KEY_FIELDS，
    # 那儿的注释写着「超模没有可跨能力复用的通用 key」）。
    # 于是「拉取最新模型清单」对超模**必然拉不到**，哪怕四把 Key 都填好了，
    # 而它只报一句鉴权/网络失败 —— 人会以为是 Key 填错了，
    # 去改一个本来就对的东西（2026-09-14 查出来）。
    #
    # 遍历所有 Key 还有第二个理由：**不同 Key 的可见清单不一样**。
    # 坤鸡按令牌分组过滤（实拉只回 1 个），超模按 1K/4K 分组 ——
    # 只用一把等于只看见那一把能看见的那部分。合并起来才是这个账号的全集。
    keys = [str(v).strip() for k, v in (pc or {}).items()
            if 'key' in k.lower() and str(v).strip()]
    if getattr(prov, 'keys', None):                 # 坤鸡把多把 Key 挂在类上
        keys += [str(v).strip() for v in prov.keys.values() if str(v).strip()]
    keys = list(dict.fromkeys(keys)) or [prov.session.api_key]
    rows, failures = {}, []
    for key in keys:
        prov.session.api_key = key
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(prov.session.base_url + endpoint,
                    headers=prov.session._headers(), proxies=prov.session._proxies(),
                    timeout=(8, 20), allow_redirects=False)
            if response.status_code != 200:
                failures.append(f'HTTP {response.status_code}'); continue
            data = response.json()
            data = data if isinstance(data, list) else data.get('data')
            if pid == 'hongniao' and isinstance(data, dict):
                data = data.get('models')
            if not isinstance(data, list):
                failures.append('接口未返回模型数组'); continue
            for row in data:
                if not isinstance(row, dict):
                    continue
                mid = row.get('model_group') if pid == 'gate' else row.get('id')
                if mid is None or not str(mid).strip():
                    continue
                mid = str(mid).strip()
                # 只保留模型字段，不把响应中的账号、调试信息打包或传给页面。
                item = {'id': mid}
                if pid == 'hongniao':
                    from .providers.hongniao import catalog_schema
                    item['capability_schema'] = catalog_schema(row)
                # `capability_schema` 是无限画布那家给的**逐模型约束**
                # （duration.min/max、aspect_ratio(s)、resolutions、
                # max_reference_images 这些）—— 比任何文档都准，而字段名和
                # 上面那几个都不一样。不收的话它整份被丢掉，等于白拉一趟。
                # 后面这一串是**超模那套**的字段名（2026-09-14 实拉）：
                # 它把逐模型的比例、参考图上限、一次出几张、quality 档位
                # 全摆在顶层，名字和别家都不一样。不收的话整份被丢掉 ——
                # 和无限画布的 capability_schema 是同一个毛病。
                for field in ('type', 'model_type', 'category', 'output_modalities',
                              'display_name', 'durations_seconds', 'ratios',
                              'max_images', 'resolution', 'capability_schema',
                              'resolution_mode', 'supported_aspect_ratios',
                              'supported_image_sizes', 'max_reference_images',
                              'n_max', 'supported_quality_values'):
                    if field in row:
                        item[field] = row[field]
                rows[mid] = item
        except requests.RequestException:
            failures.append('网络连接失败或超时')
        except (ValueError, AttributeError):
            failures.append('接口返回的不是模型 JSON')
    if not rows:
        return {'ok': False, 'msg': '；'.join(dict.fromkeys(failures)) or '模型清单为空'}
    return {'ok': True, 'models': sorted(rows), 'rows': [rows[k] for k in sorted(rows)],
            'updated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'partial': bool(failures),
            'msg': '部分分组拉取失败，已合并成功分组' if failures else '模型清单已更新'}


def model_kind(row, cap):
    mid = row['id']
    known = [k for k in cap.get('supports', []) if mid in cap.get(k, {}).get('models', [])]
    if known:
        return known
    metadata = ' '.join(str(row.get(k, '')) for k in
                        ('type', 'model_type', 'category', 'output_modalities')).lower()
    kinds = [k for k in ('image', 'video') if k in metadata]
    if kinds:
        return kinds
    name = mid.lower()
    if re.search(r'image|banana|seedream|flux|dall|gpt本地|香蕉|绘图|生图|图片', name):
        return ['image']
    if re.search(r'video|seedance|sora|veo|kling|h3|wan|happy.?horse|omni_flash|快乐马|可灵|视频|sd[\d.-]|官方稳定版.*2\.5', name):
        return ['video']
    if re.search(r'^gpt-|^o[134]-|claude|deepseek|qwen|embedding|rerank|whisper|tts|gemini|^glm-|^kimi-|doubao-seed|推理', name):
        return ['other']
    return []


def _from_schema(schema):
    """`capability_schema` → 我们的 model_options 形状。

    无限画布那家逐模型回一份这个，比文档准 —— 而**同一个平台里字段名都不统一**：
    `seedance-2.5-hf-720p` 用 `aspect_ratio`（单数），别的用 `aspect_ratios`
    （复数）。只认一种的话有的模型的比例会整份丢掉，然后前端给的候选是整家的
    并集 —— 人选到一个这个模型不收的值，要到付费请求发出去才收到拒绝。

    **只取声明了的**，没声明的键不放进来 —— 放一个「看起来合理」的数
    和照文档抄没有区别：它会显示在页面上、会拿去拦人，而没有任何东西背书。
    """
    if not isinstance(schema, dict):
        return {}
    out = {}
    dur = schema.get('duration')
    if isinstance(dur, dict):
        if isinstance(dur.get('values'), list) and dur['values']:
            out['durations'] = [int(x) for x in dur['values']]
        elif isinstance(dur.get('min'), int) and isinstance(dur.get('max'), int):
            out['durations'] = list(range(dur['min'], dur['max'] + 1))
    # 2026-09-17 实拉：同一家还有第三种写法 —— `durations` 直接是个数组
    # （无限画布的 `seedance2.5` 回的是 `"durations": [30]`）。
    # 只认前两种的话，它**唯一合法的那个时长整份丢掉**：页面上不给候选、
    # 校验也不拦，人填 15 秒发出去被平台拒，而拒绝来得比出图还晚。
    if 'durations' not in out and isinstance(schema.get('durations'), list)             and schema['durations']:
        out['durations'] = [int(x) for x in schema['durations']]
    for key in ('aspect_ratios', 'aspect_ratio'):        # 单复数都认
        v = schema.get(key)
        if isinstance(v, list) and v:
            out['ratios'] = [str(x) for x in v]
            break
    v = schema.get('resolutions')
    if isinstance(v, list) and v:
        out['resolutions'] = [str(x) for x in v]
    for key, target in (('max_reference_images', 'max_refs'),
                        ('max_reference_videos', 'max_video_refs'),
                        ('max_reference_audios', 'max_audio_refs'),
                        ('max_reference_assets', 'max_asset_refs'),
                        ('min_reference_images', 'min_refs'),
                        ('max_prompt_chars', 'max_prompt_chars')):
        if isinstance(schema.get(key), int):
            out[target] = schema[key]
    # 「这个模型收不收视频参考」也是它自己声明的。**只在明确写了 False
    # 时记 0** —— 没写就是没声明，不是不允许（见 `_UNKNOWN` 那一课）。
    if schema.get('videoReference') is False:
        out['max_video_refs'] = 0
    kinds = schema.get('reference_types')
    if isinstance(kinds, list) and kinds:
        up = {str(x).upper() for x in kinds}
        for word, target in (('VIDEO', 'max_video_refs'),
                             ('AUDIO', 'max_audio_refs')):
            if word not in up:
                out[target] = 0
    return out


def _from_toplevel(row):
    """超模那套：逐模型的约束摆在**顶层**，不在 capability_schema 里。

    字段名和别家都不一样（`supported_aspect_ratios` / `max_reference_images` /
    `n_max` / `supported_quality_values` / `resolution_mode`），所以单独一支。

    **只取声明了的。** 超模的 `gpt-image2-*-Native` 把 `max_reference_images`
    写成 null（明确表示没声明），那就不给它填数 —— 填一个「看起来合理」的
    和照文档抄没有区别：它会显示在页面上、会拿去拦人，而没有任何东西背书。
    """
    if not isinstance(row, dict):
        return {}
    out = {}
    v = row.get('supported_aspect_ratios')
    if isinstance(v, list) and v:
        out['ratios'] = [str(x) for x in v]
    v = row.get('supported_image_sizes') or row.get('resolution_mode')
    if isinstance(v, str) and v:
        out['resolutions'] = [s.strip() for s in v.split('/') if s.strip()]
    elif isinstance(v, list) and v:
        out['resolutions'] = [str(x) for x in v]
    for key, target in (('max_reference_images', 'max_refs'),
                        ('n_max', 'n_max')):
        if isinstance(row.get(key), int):          # null / None 不算声明
            out[target] = row[key]
    v = row.get('supported_quality_values')
    if isinstance(v, list) and v:
        out['quality'] = [str(x) for x in v]
    return out


def apply_catalog(cap, result, source):
    cap = copy.deepcopy(cap)
    rows = result.get('rows') or [{'id': m} for m in result.get('models', [])]
    grouped = {k: [] for k in cap.get('supports', [])}
    unknown, other = [], []
    for row in rows:
        kinds = model_kind(row, cap)
        if not kinds:
            unknown.append(row['id'])
        elif not any(k in grouped for k in kinds):
            other.append(row['id'])
        for kind in kinds:
            if kind in grouped:
                grouped[kind].append(row['id'])
    # **一类都拉不到时保留原来声明的那份。** 这是 studio 侧加的一道护栏，
    # 批量工具那边没有 —— 因为有的家的 `/v1/models` 不是完整目录：
    # 坤鸡按令牌分组过滤，实拉只回 `gpt-image-2` 一个（它自己代码注释里
    # 就写着这件事）。照原样套上去，它的 video 会从 3 个变 **0 个**，
    # 页面上那一类直接消失 —— 而那几个模型其实是能用的。
    #
    # 「多列一个已下线的」和「少列一个能用的」代价差很多：前者选中了会得到
    # 一句响的「找不到模型」，后者是**页面上根本没有**，人只会以为不支持。
    kept = []
    for kind, names in grouped.items():
        block = cap[kind]
        got = names + unknown
        if not got and (block.get('models') or []):
            kept.append(kind)
            continue                    # 原样留着，别把这一类清空
        block['models'] = got
        if block.get('default_model') not in block['models']:
            block['default_model'] = next(iter(block['models']), '')
        for row in rows:
            if row['id'] not in names:
                continue
            options = {}
            for field, target in [('durations_seconds', 'durations'), ('ratios', 'ratios')]:
                if isinstance(row.get(field), list) and row[field]:
                    options[target] = row[field]
            if isinstance(row.get('max_images'), int):
                options['max_refs'] = row['max_images']
            options.update(_from_schema(row.get('capability_schema')))
            options.update(_from_toplevel(row))
            if options:
                block.setdefault('model_options', {}).setdefault(row['id'], {}).update(options)
    cap['model_catalog'] = {'source': source, 'updated_at': result.get('updated_at', ''),
        'total': len(rows), 'unknown': unknown, 'other': other,
        # 哪几类没动 —— 页面要能说清「这一类的清单不是实拉的」，
        # 否则人以为看到的是最新的。
        'kept_declared': kept,
        'all_models': [r['id'] for r in rows], 'partial': result.get('partial', False)}
    return cap


def capabilities(cfg):
    caps = providers.list_capabilities()
    saved = read_json(paths.res('model-snapshot.json'), {}) or {}
    cache = read_json(paths.data_dir() + '/model-catalog.json', {}) or {}
    for i, cap in enumerate(caps):
        pid = cap['id']
        pc = _provider_cfg(cfg, pid)
        current = cache.get(pid, {})
        if current.get('identity') == identity(pid, pc) and current.get('ok'):
            caps[i] = apply_catalog(cap, current, '当前 Key 拉取')
        elif saved.get(pid, {}).get('ok'):
            caps[i] = apply_catalog(cap, saved[pid], '打包时快照，请按当前 Key 刷新')
    return caps


def refresh(pid, cfg):
    pid = providers.resolve_id(pid)
    pc = _provider_cfg(cfg, pid)
    result = fetch(pid, pc)
    if result['ok']:
        with _LOCK:
            path = paths.data_dir() + '/model-catalog.json'
            cached = read_json(path, {}) or {}
            cached[pid] = {**result, 'identity': identity(pid, pc)}
            write_json(path, cached)
        result['capability'] = next(c for c in capabilities(cfg) if c['id'] == pid)
    return result
