"""In-memory account assignments. Never merge cloud secrets into saved local config."""
import copy
import hashlib
import threading
import functools

_lock = threading.RLock()
_manager = None
_owner = ''
_configs = {}
_catalogs = {}
KEY_FIELDS = ('api_key', 'image_1k_api_key', 'image_4k_api_key', 'video_api_key', 'llm_api_key')
LLM_PROVIDER = 'prompt_rewrite_llm'


def llm_managed():
    with _lock:
        assigned = LLM_PROVIDER in _configs
    return assigned or client_managed()


def llm(local):
    """Resolve the assigned rewrite route locally; never fetch while building an LLM."""
    with _lock:
        cloud = copy.deepcopy(_configs.get(LLM_PROVIDER))
    if cloud is None and not client_managed():
        return dict(local or {})
    rows = cloud.get('keys', [cloud]) if cloud is not None else []
    if not rows:
        raise ValueError('后台尚未分配可用的提示词改写 LLM，请联系管理员在 Key 库分配后刷新后台配置')
    row = rows[0]
    from urllib.parse import urlsplit
    url = urlsplit(row.get('base_url', ''))
    models = row.get('models', [])
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or len(models) != 1 or not models[0].strip()
            or not row.get('api_key', '').strip()):
        raise ValueError('后台提示词改写 LLM 配置不完整，需要 HTTPS 地址、API Key 和一个模型 ID')
    # Keep runtime controls, but never mix a cloud secret with a personal endpoint/model.
    out = {k: v for k, v in (local or {}).items() if k in ('timeout', 'max_tokens', 'stream')}
    out.update(base_url=row['base_url'], api_key=row['api_key'], model=models[0], proxy='direct',
               source='managed', key_name=row.get('key_name', ''))
    return out


def consistent(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        with _lock:
            return fn(*args, **kwargs)
    return wrapped


def install(manager, owner, configs):
    global _manager, _owner, _configs
    if not isinstance(configs, dict) or len(configs) > 48:
        raise ValueError('invalid provider configuration')
    clean = {}
    for pid, value in configs.items():
        if not isinstance(pid, str) or not isinstance(value, dict):
            raise ValueError('invalid provider configuration')
        def clean_row(v):
            if not isinstance(v, dict):
                raise ValueError('invalid provider configuration')
            row = {k: v[k] for k in (*KEY_FIELDS, 'base_url', 'assignment_id', 'key_id', 'key_name') if k in v}
            if any(not isinstance(item, str) for item in row.values()):
                raise ValueError('invalid provider configuration')
            if 'models' in v:
                if not isinstance(v['models'], list) or any(not isinstance(m, str) for m in v['models']):
                    raise ValueError('invalid model bindings')
                row['models'] = list(v['models'])
            return row
        row = clean_row(value)
        if 'keys' in value:
            if not isinstance(value['keys'], list) or len(value['keys']) > 32:
                raise ValueError('invalid provider keys')
            row['keys'] = [clean_row(v) for v in value['keys']]
        clean[pid] = row
    with _lock:
        for pid in list(_catalogs):
            if owner != _owner or clean.get(pid) != _configs.get(pid):
                _catalogs.pop(pid, None)
        _manager, _owner, _configs = manager, owner, clean


def clear(manager):
    from . import cloud_storage
    cloud_storage.clear(manager)
    global _manager, _owner, _configs
    with _lock:
        if _manager is manager:
            _manager, _owner, _configs = None, '', {}
            _catalogs.clear()


def owner():
    with _lock:
        return _owner


def client_managed():
    from . import cloud_adapters, providers
    return cloud_adapters.required() or providers.status()['managed']


def route_urls():
    """Internal presentation redaction only; never expose these to the page."""
    with _lock:
        return [row.get('base_url', '') for cfg in _configs.values()
                for row in [cfg, *cfg.get('keys', [])]]


def provider(pid, local, field=None):
    from .providers import resolve_id, REGISTRY
    pid = resolve_id(pid)
    out = dict(local or {})
    with _lock:
        cloud = copy.deepcopy(_configs.get(pid))
    if cloud is None and client_managed():
        raise ValueError('该服务商未获后台授权，请刷新后台配置或联系管理员分配')
    if cloud is not None:
        candidates = cloud.get('keys', [cloud])
        eligible = [v for v in candidates if not field or v.get(field) or v.get('api_key')]
        cloud = eligible[0] if eligible else {}
        for k in (*KEY_FIELDS, 'assignment_id', 'key_id', 'key_name', '_cloud_keys', '_key_candidates'):
            out.pop(k, None)
        out.update(cloud)
        out['_cloud_keys'] = copy.deepcopy(candidates)
        # A personal/custom URL must never receive an administrator's credential.
        out['base_url'] = cloud.get('base_url') or getattr(REGISTRY.get(pid), 'default_base_url', '')
        out['proxy'] = 'direct'
        if not out['base_url'].startswith('https://'):
            raise ValueError('后台分配的服务商缺少 HTTPS 地址，请联系管理员填写 Base URL')
    return out


def managed(pid):
    from .providers import resolve_id
    with _lock:
        return resolve_id(pid) in _configs


def display_name(pid, default=''):
    """Use assigned Key names for display only; never change the provider ID or route."""
    from .providers import resolve_id
    with _lock:
        cloud = _configs.get(resolve_id(pid))
        if cloud is None:
            return default or pid
        names = [row.get('key_name', '').strip() for row in cloud.get('keys', [cloud])]
        return ' / '.join(dict.fromkeys(name for name in names if name)) or default or pid


def display_providers(rows):
    with _lock:
        return [dict(row, name=display_name(row['id'], row.get('name', ''))) for row in rows]


def catalog_config(cfg):
    result = dict(cfg)
    remote = client_managed()
    with _lock:
        ids = set(_configs) if remote else set(_configs) | set(cfg.get('providers', {}))
    ids.discard(LLM_PROVIDER)
    result['providers'] = {pid: provider(pid, cfg.get('providers', {}).get(pid, {})) for pid in ids}
    return result


def attribution(pid, cfg):
    with _lock:
        uid = _owner
    if not uid:
        return {}
    return {'owner': uid, 'source': 'managed' if managed(pid) else 'personal',
            'assignment_id': cfg.get('assignment_id', ''),
            'key_id': cfg.get('key_id', ''), 'key_name': cfg.get('key_name', ''),
            'key_hash': hashlib.sha256(str(cfg.get('api_key', '')).encode()).hexdigest()}



def _variants(pid, cloud):
    from .providers import REGISTRY
    result = []
    for row in cloud.get('keys', [cloud]):
        for field in KEY_FIELDS:
            if field == 'llm_api_key' or not row.get(field):
                continue
            item = {k: copy.deepcopy(v) for k, v in row.items() if k not in KEY_FIELDS}
            item['api_key'] = row[field]
            item['base_url'] = row.get('base_url') or getattr(REGISTRY.get(pid), 'default_base_url', '')
            item['proxy'] = 'direct'
            if not item['base_url'].startswith('https://'):
                raise ValueError('后台 Key 缺少 HTTPS 地址，请联系管理员')
            result.append(item)
    return result


def refresh_models(pid):
    """Read-only per-Key discovery. Keep secrets/routes only in account-scoped memory."""
    from concurrent.futures import ThreadPoolExecutor
    from . import model_catalog
    with _lock:
        snapshot, uid = copy.deepcopy(_configs.get(pid)), _owner
    if snapshot is None:
        return {'ok': False, 'msg': '该服务商未分配后台 Key'}
    choices = _variants(pid, snapshot)
    def fetch_one(item):
        # Explicit bindings override provider catalogs that do not filter by Key.
        if item.get('models'):
            result = {'ok': True, 'models': item['models'], 'rows': [{'id': m} for m in item['models']]}
        else:
            try:
                result = model_catalog.fetch(pid, {'api_key': item['api_key'], 'base_url': item['base_url'], 'proxy': 'direct'})
            except Exception:
                result = {'ok': False, 'msg': '模型清单拉取失败，请检查服务商或在 Key 库绑定模型'}
        item['_models'] = result.get('models', []) if result.get('ok') else []
        return item, result
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(fetch_one, choices))
    rows, issues = {}, []
    for item, result in results:
        for row in result.get('rows', []) if result.get('ok') else []:
            rows.setdefault(row['id'], row)
        if not result.get('ok') or result.get('partial'):
            issues.append((item.get('key_name') or '未命名 Key') + '：' + result.get('msg', '模型清单不完整'))
    catalog = {'ok': bool(rows), 'strict': True, 'models': list(rows), 'rows': list(rows.values()),
               'partial': bool(issues), 'msg': '；'.join(issues) or ('后台 Key 对应模型已更新' if rows else '没有启用的后台 Key')}
    with _lock:
        if uid != _owner or snapshot != _configs.get(pid):
            raise ValueError('账号配置已变化，请重新刷新')
        _catalogs[pid] = (catalog, [item for item, result in results])
    return copy.deepcopy(catalog)


def catalog(pid):
    with _lock:
        value = _catalogs.get(pid)
        return copy.deepcopy(value[0]) if value else None


def assigned_ids():
    with _lock:
        return [pid for pid in _configs if pid != LLM_PROVIDER]


def resolve(pid, resolved, model):
    """Resolve a model to its Key and freeze all choices for original-task recovery."""
    with _lock:
        cloud = copy.deepcopy(_configs.get(pid))
    resolved.pop('_cloud_keys', None)
    if cloud is None:
        return resolved
    if 'keys' not in cloud:  # Compatibility with 1.2 server and existing local snapshots.
        return resolved
    with _lock:
        ready = pid in _catalogs
    if resolved.get('_resume_only'):
        variants = _variants(pid, cloud)
    else:
        if not ready:
            refresh_models(pid)
        with _lock:
            variants = copy.deepcopy(_catalogs[pid][1])
    choices = []
    for row in variants:
        item = {k: copy.deepcopy(v) for k, v in resolved.items()
                if k not in (*KEY_FIELDS, '_usage', 'assignment_id', 'key_id', 'key_name', 'models')}
        item.update({k: v for k, v in row.items() if k not in ('models', '_models')})
        item['_usage'] = attribution(pid, item)
        choices.append(item)
    selected = next((c for c, row in zip(choices, variants) if model in row.get('_models', [])), None)
    if resolved.get('_resume_only') and choices:
        selected = choices[0]
    if selected is None:
        raise ValueError('当前模型没有对应的授权 Key，请点击“刷新后台配置”；若服务商清单不区分权限，请管理员在 Key 库绑定模型')
    result = copy.deepcopy(selected)
    result['_key_candidates'] = choices
    return result
