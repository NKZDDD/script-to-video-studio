from types import SimpleNamespace

from studio_auth import model_statistics_api as api
from test_admin import env, login, post


def test_statistics_require_admin_and_never_fetch_browser_supplied_urls(env, monkeypatch):
    c, _, _ = env
    calls = []
    monkeypatch.setattr(api, 'CACHE', SimpleNamespace(snapshot=lambda p,b: calls.append((p,b)) or {'models': [], 'state': 'loading'}))
    assert c.get('/admin/api/provider-model-stats?provider=ake').status_code == 401
    assert calls == []
    login(c)
    response = c.get('/admin/api/provider-model-stats?provider=ake&base_url=https://custom.example')
    assert response.status_code == 200 and response.json()['models'] == []
    assert calls == []
    kid = post(c, 'keys', {'provider': 'ake', 'name': 'A', 'api_key': 'saved-secret',
                          'base_url': 'https://snumom.com', 'models': ['wan']}).json()['id']
    response = c.get('/admin/api/provider-model-stats', params={'key_id': kid, 'provider': 'other', 'base_url': 'https://custom.example'})
    assert response.json()['catalog']['models'] == ['wan']
    assert calls == [('ake', 'https://snumom.com')]
    assert 'saved-secret' not in response.text
    assert c.get('/admin/api/provider-model-stats?key_id=missing').status_code == 404
    assert c.get('/admin/model-statistics.js').status_code == 200
    assert c.get('/admin/model-statistics.css').status_code == 200


def test_two_keys_filter_public_statistics_and_changed_bindings_invalidate_immediately(env, monkeypatch):
    c, _, _ = env
    login(c)
    monkeypatch.setattr(api, 'CACHE', SimpleNamespace(snapshot=lambda *a: {'models': [
        {'model': 'A', 'rate': 99}, {'model': 'B', 'rate': 60}, {'model': 'public-only', 'rate': 100}]}))
    ids = [post(c, 'keys', {'provider': 'ake', 'name': name, 'api_key': 'secret-'+name, 'models': [name]}).json()['id']
           for name in ('A', 'B')]
    for kid, model in zip(ids, ('A', 'B')):
        data = c.get('/admin/api/provider-model-stats', params={'key_id': kid}).json()
        assert [r['model'] for r in data['models']] == [model]
    post(c, 'keys', {'id': ids[0], 'provider': 'ake', 'name': 'A', 'models': ['B']})
    assert c.get('/admin/api/provider-model-stats', params={'key_id': ids[0]}).json()['models'][0]['model'] == 'B'
    post(c, 'keys', {'id': ids[0], 'provider': 'ake', 'name': 'A', 'models': ['B'], 'enabled': False})
    assert c.get('/admin/api/provider-model-stats', params={'key_id': ids[0]}).json()['models'] == []
