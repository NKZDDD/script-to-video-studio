"""In-memory account assignments. Never merge cloud secrets into saved local config."""
import copy
import hashlib
import threading
import functools

_lock = threading.RLock()
_manager = None
_owner = ''
_configs = {}
KEY_FIELDS = ('api_key', 'image_1k_api_key', 'image_4k_api_key', 'video_api_key', 'llm_api_key')


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
        row = {k: value[k] for k in (*KEY_FIELDS, 'base_url', 'assignment_id') if k in value}
        if any(not isinstance(v, str) for v in row.values()):
            raise ValueError('invalid provider configuration')
        clean[pid] = row
    with _lock:
        _manager, _owner, _configs = manager, owner, clean


def clear(manager):
    global _manager, _owner, _configs
    with _lock:
        if _manager is manager:
            _manager, _owner, _configs = None, '', {}


def owner():
    with _lock:
        return _owner


def provider(pid, local):
    from .providers import resolve_id, REGISTRY
    pid = resolve_id(pid)
    out = dict(local or {})
    with _lock:
        cloud = copy.deepcopy(_configs.get(pid))
    if cloud is not None:
        for k in KEY_FIELDS:
            out.pop(k, None)
        out.update(cloud)
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


def catalog_config(cfg):
    result = dict(cfg)
    with _lock:
        ids = set(_configs) | set(cfg.get('providers', {}))
    result['providers'] = {pid: provider(pid, cfg.get('providers', {}).get(pid, {})) for pid in ids}
    return result


def attribution(pid, cfg):
    with _lock:
        uid = _owner
    if not uid:
        return {}
    return {'owner': uid, 'source': 'managed' if managed(pid) else 'personal',
            'assignment_id': cfg.get('assignment_id', ''),
            'key_hash': hashlib.sha256(str(cfg.get('api_key', '')).encode()).hexdigest()}
