import json

import pytest

from test_admin import env, login, post, PASSWORD
from test_provisioning import client_token, verify
from test_adapters import bundle, download
from studio_auth.provider_names import LLM_PROVIDER


def entry(**changes):
    return dict(dict(provider=LLM_PROVIDER, name='改写专用', api_key='test-rewrite-secret',
                     base_url='https://rewrite.example.test', models=['rewrite-model']), **changes)


def test_assign_refresh_disable_remove_and_adapter_independence(env):
    c, a, _ = env
    login(c)
    first = post(c, 'keys', entry()).json()['id']
    second = post(c, 'keys', entry(name='第二条', api_key='second-secret')).json()['id']
    post(c, 'users/customer/keys', {'key_ids': [second, first]})
    token = client_token(c)
    checksum = post(c, 'adapters/import', {'bundle': bundle()}).json()['sha256']
    post(c, 'adapters/publish', {'sha256': checksum})
    data = verify(c, token).json()
    assert data['adapter_config']['missing'] == []
    assert data['adapter_config']['providers'] == []
    assert download(c, token, data['adapter_config']).status_code == 200
    rows = data['provider_config'][LLM_PROVIDER]['keys']
    assert [x['key_id'] for x in rows] == [second, first]
    assert rows[0]['api_key'] == 'second-secret'
    assert rows[0]['models'] == ['rewrite-model']
    public = c.get('/admin/api/keys')
    assert 'second-secret' not in public.text and 'test-rewrite-secret' not in public.text
    assert '提示词改写' in public.json()['providers'][LLM_PROVIDER]
    a.create_user('another', PASSWORD)
    assert verify(c, client_token(c, 'another')).json()['provider_config'] == {}
    assert post(c, 'keys', entry(id=second, api_key='', models=['updated-model'])).status_code == 200
    row = verify(c, token).json()['provider_config'][LLM_PROVIDER]['keys'][0]
    assert row['api_key'] == 'second-secret' and row['models'] == ['updated-model']
    post(c, 'keys', entry(id=second, api_key='', enabled=False))
    assert verify(c, token).json()['provider_config'][LLM_PROVIDER]['keys'][0]['key_id'] == first
    post(c, 'users/customer/keys', {'key_ids': []})
    assert verify(c, token).json()['provider_config'] == {}


@pytest.mark.parametrize('change', [dict(base_url=''), dict(base_url='http://unsafe.test'),
                                   dict(api_key=''), dict(models=[]), dict(models=['one', 'two'])])
def test_incomplete_rewrite_config_is_rejected_without_secret_echo(env, change):
    c, _, _ = env
    login(c)
    r = post(c, 'keys', entry(**change))
    assert r.status_code == 422 and 'test-rewrite-secret' not in r.text
    assert c.get('/admin/api/keys').json()['keys'] == []


def test_import_llm_config_preview_commit_deduplicate_and_assignment(env):
    c, _, _ = env
    login(c)
    data = dict(llm=dict(base_url='https://rewrite.example.test/', model='rewrite-model',
                         api_key='import-rewrite-secret', proxy='do-not-import'), projects_dir='ignored')
    body = {'content': json.dumps(data)}
    preview = post(c, 'keys/import/preview', body)
    assert preview.status_code == 200 and 'import-rewrite-secret' not in preview.text
    assert preview.json()['rows'][0]['provider'] == LLM_PROVIDER
    result = post(c, 'keys/import/commit', dict(body, selected=[0])).json()
    assert result['added'] == 1
    assert post(c, 'keys/import/commit', dict(body, selected=[0])).json()['skipped'] == 1
    post(c, 'users/customer/keys', {'key_ids': result['key_ids']})
    row = verify(c, client_token(c)).json()['provider_config'][LLM_PROVIDER]['keys'][0]
    assert row['api_key'] == 'import-rewrite-secret' and row['models'] == ['rewrite-model']
    assert row['base_url'] == 'https://rewrite.example.test' and 'proxy' not in row
    data['llm'].pop('model')
    data['providers'] = {'julun': {'api_key': 'test-media-secret'}}
    preview = post(c, 'keys/import/preview', {'content': json.dumps(data)}).json()
    assert len(preview['rows']) == 1 and preview['rows'][0]['provider'] == 'julun'
    assert any('LLM' in w for w in preview['warnings'])
    assert 'import-rewrite-secret' not in json.dumps(preview)


def test_legacy_assignment_cannot_create_unconfigured_rewrite_entry(env):
    c, _, _ = env
    login(c)
    assert post(c, 'users/customer/providers', {'provider': LLM_PROVIDER, 'api_key': 'secret'}).status_code == 422
