from types import SimpleNamespace

from studio_auth import model_statistics_api as api
from test_admin import env, login


def test_statistics_require_admin_and_never_fetch_browser_supplied_urls(env, monkeypatch):
    c, _, _ = env
    calls = []
    monkeypatch.setattr(api, 'CACHE', SimpleNamespace(snapshot=lambda p,b: calls.append((p,b)) or {'models': [], 'state': 'loading'}))
    assert c.get('/admin/api/provider-model-stats?provider=ake').status_code == 401
    assert calls == []
    login(c)
    response = c.get('/admin/api/provider-model-stats?provider=ake&base_url=https://custom.example')
    assert response.status_code == 200 and response.json()['state'] == 'loading'
    assert calls == [('ake', 'https://custom.example')]
    assert c.get('/admin/model-statistics.js').status_code == 200
    assert c.get('/admin/model-statistics.css').status_code == 200
