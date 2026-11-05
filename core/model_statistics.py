"""Account-safe projection of public supplier statistics for model selectors."""
from auth_service.studio_auth.model_statistics import CACHE, matches
from . import cloud_config, providers


def snapshot(provider, cfg):
    pid = providers.resolve_id(provider)
    cls = providers.REGISTRY.get(pid)
    if cls is None or (cloud_config.client_managed() and not cloud_config.managed(pid)):
        return dict(provider=provider, state='unsupported', models=[], refreshing=False, message='暂无服务商统计')
    config = cloud_config.provider(pid, cfg.get('providers', {}).get(pid, {}))
    candidates = config.get('_cloud_keys') or [config]
    if any(not matches(pid, c.get('base_url') or cls.default_base_url) for c in candidates):
        return dict(provider=provider, state='unsupported', models=[], refreshing=False,
                    message='当前地址与公开统计不匹配，暂无数据')
    value = CACHE.snapshot(pid)
    # In managed builds, statistics must not disclose models the account cannot use.
    from . import model_catalog
    caps = model_catalog.capabilities(cloud_config.catalog_config(cfg))
    cap = next((c for c in caps if c['id'] == pid), {})
    allowed = {m for kind in ('image', 'video') for m in cap.get(kind, {}).get('models', [])}
    value['models'] = [r for r in value['models'] if r['model'] in allowed]
    value['provider'] = provider
    return value
