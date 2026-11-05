import json
import threading
import time

from studio_auth import key_model_catalog as K
from studio_auth import provider_probe as P
from test_provider_probe import Connection, SPEC, resolver

RULE = dict(base_url='https://provider.example', path='/v1/models', auth='bearer', kind='models', result_path='data')


def test_probe_extracts_only_exact_model_ids_and_validates_response(monkeypatch):
    monkeypatch.setattr(Connection, 'body', b'{"data":[{"id":"wan-exact","secret":"private"},{"id":"wan-exact"}]}')
    value = P.probe(SPEC, 'private', resolver=resolver, connection=Connection, model_id_field='id')
    assert value['models'] == ['wan-exact'] and 'private' not in json.dumps(value)
    monkeypatch.setattr(Connection, 'body', b'{"data":[{"name":"not-an-id"}]}')
    assert P.probe(SPEC, 'private', resolver=resolver, connection=Connection, model_id_field='id')['category'] == 'invalid_response'


def test_key_fetch_uses_each_capability_credential_and_no_partial_catalog():
    calls = []
    def reader(spec, secret, **kwargs):
        calls.append((spec, secret))
        return {'category': 'ok', 'models': [secret+'-model']}
    rule = dict(RULE, key_fields=['image_1k_api_key', 'video_api_key'])
    cfg = {'image_1k_api_key': 'one', 'video_api_key': 'two'}
    data = K.fetch(rule, cfg, reader)
    assert data['models'] == ['one-model', 'two-model']
    assert [s for _, s in calls] == ['one', 'two']
    assert all(s['url'] == 'https://provider.example/v1/models' for s, _ in calls)
    data = K.fetch(rule, cfg, lambda spec, secret, **kw: {'category': 'authentication'} if secret == 'two' else {'category': 'ok', 'models': ['one-model']})
    assert data['state'] == 'error' and data['models'] == []
    assert '鉴权失败' in data['message']


def test_public_list_cannot_prove_key_access():
    assert K.fetch(dict(RULE, auth='none'), {})['state'] == 'unsupported'
    assert K.fetch(dict(RULE, kind='health'), {})['state'] == 'unsupported'


def test_cache_is_nonblocking_coalesces_and_does_not_reuse_other_key_or_revision():
    entered, release = threading.Event(), threading.Event()
    calls = []
    def fetch(rule, cfg):
        calls.append(cfg['api_key']); entered.set(); release.wait(3)
        return {'state': 'ready', 'models': [cfg['api_key']]}
    cache = K.KeyModelCatalog(fetch)
    row = dict(id='A', revision='1', enabled=True)
    try:
        assert cache.snapshot(row, {'api_key': 'first'}, RULE)['state'] == 'loading'
        assert entered.wait(1)
        start = time.monotonic()
        for _ in range(20):
            assert cache.snapshot(row, {'api_key': 'first'}, RULE)['models'] == []
        assert time.monotonic() - start < .5 and calls == ['first']
        release.set();cache.queue.join()
        assert cache.snapshot(row, {'api_key': 'first'}, RULE)['models'] == ['first']
        assert cache.snapshot(dict(row, revision='2'), {'api_key': 'changed'}, RULE)['models'] == []
        assert cache.snapshot(dict(row, id='B'), {'api_key': 'second'}, RULE)['models'] == []
        cache.queue.join()
        assert cache.snapshot(dict(row, revision='2'), {}, RULE)['models'] == ['changed']
        assert cache.snapshot(dict(row, id='B'), {}, RULE)['models'] == ['second']
        assert cache.snapshot(dict(row, enabled=False), {}, RULE)['models'] == []
    finally:
        release.set();cache.close()


def test_expired_or_failed_key_catalog_does_not_keep_stale_permissions():
    clock = [1000]
    def fetch(rule, cfg):
        if clock[0] > 1000:
            raise RuntimeError('private-secret')
        return {'state': 'ready', 'models': ['allowed']}
    cache = K.KeyModelCatalog(fetch, lambda: clock[0])
    row = dict(id='A', revision='1', enabled=True)
    try:
        cache.snapshot(row, {}, RULE);cache.queue.join()
        clock[0] += 301
        assert cache.snapshot(row, {}, RULE)['models'] == []
        cache.queue.join()
        value = cache.snapshot(row, {}, RULE)
        assert value['models'] == [] and value['state'] == 'error'
        assert 'private-secret' not in json.dumps(value)
        assert cache.snapshot(row, {'models': ['bound']}, RULE)['source'] == 'binding'
    finally:
        cache.close()
