"""Read declarative probe rules from bundles, without executing adapter code."""
import ast
from functools import lru_cache
import hashlib
import json
import re
from urllib.parse import urlsplit

from .provider_probe import AUTH_HEADERS, clean_url, same_origin

MODULE = 'provider_monitor_catalog'
KEY_FIELDS = ('api_key', 'image_1k_api_key', 'image_4k_api_key', 'video_api_key')


def read_catalog(bundle):
    source = bundle.get('modules', {}).get(MODULE)
    if source is None:
        return {}
    try:
        tree = ast.parse(source)
        if (len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign)
                or len(tree.body[0].targets) != 1 or not isinstance(tree.body[0].targets[0], ast.Name)
                or tree.body[0].targets[0].id != 'MONITORS'):
            raise ValueError()
        rules = ast.literal_eval(tree.body[0].value)
        ids = {row['id'] for row in bundle['providers']}
        if not isinstance(rules, dict) or not set(rules) <= ids:
            raise ValueError()
        for rule in rules.values():
            if not isinstance(rule, dict):
                raise ValueError()
            if set(rule) == {'reason'}:
                if not isinstance(rule['reason'], str) or not 1 <= len(rule['reason']) <= 200:
                    raise ValueError()
                continue
            if (not {'base_url', 'path', 'kind', 'result_path', 'auth'} <= set(rule)
                    or set(rule) - {'base_url', 'path', 'kind', 'result_path', 'auth', 'key_fields', 'key_prefixes'}):
                raise ValueError()
            base, path = rule['base_url'], rule['path']
            if not isinstance(base, str) or len(base) > 500:
                raise ValueError()
            if base:
                clean_url(base)
                if urlsplit(base).query:
                    raise ValueError()
            if not isinstance(path, str) or not path.startswith('/') or path.startswith('//'):
                raise ValueError()
            clean_url('https://monitor.example' + path)
            if rule['kind'] not in ('models', 'health', 'http') or rule['auth'] not in AUTH_HEADERS:
                raise ValueError()
            if not isinstance(rule['result_path'], str) or not re.fullmatch(r'(?:[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)*)?', rule['result_path']) or len(rule['result_path']) > 100:
                raise ValueError()
            fields = rule.get('key_fields', ['api_key'])
            prefixes = rule.get('key_prefixes', [])
            if not isinstance(fields, list) or not fields or len(fields) > 4 or any(f not in KEY_FIELDS for f in fields):
                raise ValueError()
            if not isinstance(prefixes, list) or len(prefixes) > 50 or any(not isinstance(p, str) or not 1 <= len(p) <= 30 for p in prefixes):
                raise ValueError()
        return rules
    except (ValueError, TypeError, KeyError, SyntaxError, RecursionError):
        raise ValueError('适配包监测规则无效：必须是合法的纯数据 MONITORS 声明') from None


@lru_cache(maxsize=4)
def _cached(payload):
    bundle = json.loads(payload)
    return read_catalog(bundle), bundle['version']


def active_catalog(db):
    row = db.execute('SELECT b.payload FROM adapter_policy p JOIN adapter_bundles b ON b.sha256=p.sha256 WHERE p.id=1').fetchone()
    return _cached(row[0]) if row else ({}, '')


def rule_digest(rule):
    return hashlib.sha256(json.dumps(rule, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def resolve(rule, cfg):
    if not rule:
        raise ValueError('当前已发布适配包未提供监测规则，请发布新版适配包或使用手动设置')
    if 'reason' in rule:
        raise ValueError(rule['reason'])
    base = (cfg.get('base_url') or rule['base_url']).strip().rstrip('/')
    if not base:
        raise ValueError('此服务商没有默认地址，请先在所选 Key 中填写 Base URL')
    clean_url(base)
    default = rule['base_url']
    # Preserve /api gateways; do not turn https://host/api into https://host/.
    if default and same_origin(base, default) and not urlsplit(base).path:
        base = default.rstrip('/')
    path = rule['path']
    if base.endswith('/v1') and path.startswith('/v1/'):
        base = base[:-3]
    spec = {k: rule[k] for k in ('kind', 'result_path', 'auth')}
    spec.update(url=clean_url(base + path), key_field='api_key')
    if rule['auth'] == 'none':
        return spec, ''
    for field in rule.get('key_fields', ['api_key']):
        value = cfg.get(field, '').strip()
        if not value:
            continue
        if field == 'api_key' and rule.get('key_prefixes') and '=' in value:
            values = [part.partition('=')[2].strip() for part in re.split(r'[;\n]+', value)
                      if part.partition('=')[0].strip().lower() in rule['key_prefixes'] and part.partition('=')[2].strip()]
            value = values[0] if values else ''
        if value:
            spec['key_field'] = field
            return spec, value
    raise ValueError('所选 Key 尚未填写此接口所需的凭据')
