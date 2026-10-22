"""Verified per-account code delivery. Published adapters execute on this computer."""
from __future__ import annotations
import hashlib
import importlib
import importlib.abc
import importlib.util
import json
from pathlib import Path
import sys
import threading
import types

from auth_service.studio_auth.adapter_format import PROTOCOL, MAX_BYTES, digest, encode, validate
from . import paths

_lock = threading.RLock()
_owner = None
_active = ''
_packages = {}


def required():
    import os
    return os.environ.get('STV_MANAGED_ADAPTERS') == '1' or Path(paths.res('managed-adapters.json')).is_file()


class _Modules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        package, _, name = fullname.rpartition('.')
        if package in _packages and name in _packages[package]:
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        package, _, name = module.__name__.rpartition('.')
        module.__file__ = '<server-adapter:'+module.__name__+'>'
        exec(compile(_packages[package][name], module.__file__, 'exec'), module.__dict__)


sys.meta_path.append(_Modules())


def load(bundle):
    """A namespace per content hash keeps existing instances on their original code."""
    from . import providers
    validate(bundle)
    name = 'core._adapters_'+digest(bundle)
    if name not in _packages:
        pkg = types.ModuleType(name)
        pkg.__path__ = []
        pkg.__package__ = name
        sys.modules[name] = pkg
        sys.modules[name+'.base'] = sys.modules['core.providers.base']
        _packages[name] = bundle['modules']
    classes = {}
    try:
        for row in bundle['providers']:
            mod = importlib.import_module(name+'.'+row['module'])
            cls = getattr(mod, row['class'])
            if not isinstance(cls, type) or not issubclass(cls, providers.Provider) or cls.id != row['id']:
                raise ValueError('服务商入口声明不匹配：'+row['id'])
            if cls.__module__ != mod.__name__:
                raise ValueError('服务商入口必须在声明的模块中定义')
            cap = cls().capabilities()
            if cap.get('id') != row['id'] or not cap.get('supports'):
                raise ValueError('服务商能力声明不正确：'+row['id'])
            classes[row['id']] = cls
    except Exception:
        _packages.pop(name, None)
        for key in list(sys.modules):
            if key == name or key.startswith(name+'.'):
                sys.modules.pop(key, None)
        raise
    return classes


def install(owner, uid, meta, fetch):
    global _owner, _active
    from . import providers
    if meta is not None and not isinstance(meta, dict):
        raise ValueError('适配包配置格式不正确')
    if not meta or meta.get('enabled') is not True:
        if required() or _owner is not None:
            raise ValueError('后台尚未发布服务商适配包，请管理员在“服务商适配包”中导入并发布')
        return
    checksum = meta.get('sha256', '')
    if meta.get('protocol') != PROTOCOL or not isinstance(checksum, str) or len(checksum) != 64 or any(c not in '0123456789abcdef' for c in checksum):
        raise ValueError('适配包协议或校验码不正确，请更新客户端')
    if meta.get('missing'):
        raise ValueError('后台缺少账号所需适配器：'+', '.join(str(x) for x in meta['missing']))
    identity = (owner, uid)
    with _lock:
        if _owner == identity and _active == checksum:
            return
        cache = Path(paths.data_dir()) / 'adapter-cache' / (hashlib.sha256(uid.encode()).hexdigest()+'.json')
        bundle = None
        try:
            if cache.stat().st_size <= MAX_BYTES:
                candidate = json.loads(cache.read_bytes())
                if digest(candidate) == checksum:
                    bundle = candidate
        except (OSError, ValueError, TypeError):
            pass
        if bundle is None:
            bundle = fetch(checksum).get('bundle')
        if digest(bundle) != checksum:
            raise ValueError('服务商适配包校验失败，请刷新后台配置重试')
        validate(bundle)
        if sorted(p['id'] for p in bundle['providers']) != sorted(meta.get('providers', [])):
            raise ValueError('适配包与账号授权不一致')
        try:
            classes = load(bundle)
        except Exception as exc:
            raise ValueError('适配包加载失败，请管理员回退版本或更新客户端（'+type(exc).__name__+'）') from None
        providers.activate_remote(classes, bundle['version'])
        _owner, _active = identity, checksum
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            temp = cache.with_suffix('.tmp')
            temp.write_bytes(encode(bundle))
            temp.replace(cache)
        except OSError:
            pass  # The already verified in-memory bundle remains usable.


def clear(owner):
    global _owner, _active
    from . import providers
    with _lock:
        if _owner is not None and _owner[0] is owner:
            providers.activate_remote({}, '')
            _owner, _active = None, ''


def module(provider_id):
    from .providers import REGISTRY, resolve_id
    cls = REGISTRY.get(resolve_id(provider_id))
    return sys.modules[cls.__module__] if cls else None
