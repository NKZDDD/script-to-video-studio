"""Page-facing provider metadata. Routing and diagnostics retain their originals."""
import copy
import re
from urllib.parse import urlsplit

from core import cloud_config, providers


def capabilities(rows):
    if not cloud_config.client_managed():
        return rows
    result = []
    for row in rows:
        if not cloud_config.managed(row['id']):
            continue
        # No defaults, endpoints, credential schemas, aliases or adapter source.
        item = {key: copy.deepcopy(row[key]) for key in
                ('id', 'supports', 'image', 'video', 'model_catalog') if key in row}
        item['name'] = cloud_config.display_name(row['id'])
        result.append(item)
    return _metadata(result)


def _metadata(value):
    if isinstance(value, dict):
        return {k: _metadata(v) for k, v in value.items()
                if k not in ('base_url', 'default_base_url', 'endpoint', 'url', 'docs',
                             'api_key', 'key_fields', 'account_form')}
    if isinstance(value, list):
        return [_metadata(v) for v in value]
    if isinstance(value, str):
        # Descriptions sometimes include documentation links, even for a brand-new install.
        return re.sub(r'https?://[^\s<>\u3000-\u9fff]+', '（地址由后台管理）', value)
    return value


def status(data):
    if not cloud_config.client_managed():
        return data
    return dict(managed=True, adapter_version=data['adapter_version'],
                errors=diagnostics(data.get('errors', [])), warnings=diagnostics(data.get('warnings', [])),
                providers=[dict(id=row['id'], name=cloud_config.display_name(row['id']),
                                supports=row['supports'], source='后台分配')
                           for row in data['providers'] if cloud_config.managed(row['id'])])


_DIAGNOSTICS = {'error', 'msg', 'message', 'reason', 'detail', 'notes',
                'logs', 'raw', 'hint', 'abort_reason', 'warning'}


def diagnostics(data):
    """Hide known upstream hosts in UI errors/logs, without rewriting user prompts or media URLs."""
    if not cloud_config.client_managed():
        return data
    urls = cloud_config.route_urls() + [getattr(cls, 'default_base_url', '')
                                      for cls in list(providers.REGISTRY.values())]
    hosts = set()
    for url in urls:
        try:
            if urlsplit(url).hostname:
                hosts.add(urlsplit(url).hostname)
        except ValueError:
            continue
    pattern = re.compile('|'.join(re.escape(h) for h in sorted(hosts, key=len, reverse=True)), re.I) if hosts else None

    def walk(value, diagnostic=False):
        if isinstance(value, dict):
            return {k: walk(v, k in _DIAGNOSTICS) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, diagnostic) for v in value]
        if diagnostic and isinstance(value, str):
            value = re.sub(r'https?://[^\s<>\u3000-\u9fff]+', '（地址由后台管理）', value)
            value = re.sub(r'(host\s*=\s*[\'"])[^\'"]+', r'\1服务端配置的通道', value, flags=re.I)
            return pattern.sub('服务端配置的通道', value) if pattern else value
        return value
    return walk(data)
