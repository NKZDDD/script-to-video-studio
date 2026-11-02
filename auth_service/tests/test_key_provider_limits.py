import json

import pytest

from test_admin import env, login, post
from test_provisioning import client_token, verify


@pytest.mark.parametrize('providers,target', [(['julun'] * 32 + ['chaomo'], 'julun'),
                                            ([f'p{i}' for i in range(48)] + ['p0'], 'new-provider')])
def test_editing_shared_key_provider_checks_every_account_atomically(env, providers, target):
    c, auth, _ = env; login(c)
    auth.create_user('another', 'another-test-password-only')
    ids = [f'key-{i}' for i in range(len(providers))]
    with auth.store.connect(write=True) as db:
        db.executemany('INSERT INTO key_library VALUES(?,?,?,?,?,?,?)', [
            (kid, provider, kid, json.dumps({'api_key': 'fake-secret', 'models': []}), kid+'-rev', 1, auth.now())
            for kid, provider in zip(ids, providers)])
    assert post(c, 'users/customer/keys', {'key_ids': ids}).status_code == 200
    assert post(c, 'users/another/keys', {'key_ids': [ids[-1]]}).status_code == 200
    token = client_token(c)
    before = verify(c, token).json()['provider_config']
    with auth.store.connect() as db:
        original = tuple(db.execute('SELECT * FROM key_library WHERE id=?', (ids[-1],)).fetchone())
        relations = [tuple(r) for r in db.execute('SELECT * FROM user_keys ORDER BY user_id,position')]
    result = post(c, 'keys', {'id': ids[-1], 'provider': target, 'name': 'changed', 'api_key': 'replacement'})
    assert result.status_code == 422
    assert result.json()['error']['code'] == 'assignment_limit'
    assert 'customer' in result.json()['error']['message']
    with auth.store.connect() as db:
        assert tuple(db.execute('SELECT * FROM key_library WHERE id=?', (ids[-1],)).fetchone()) == original
        assert [tuple(r) for r in db.execute('SELECT * FROM user_keys ORDER BY user_id,position')] == relations
    assert verify(c, token).json()['provider_config'] == before
    # Free exactly one slot, then moving the shared Key is valid for both users.
    assert post(c, 'users/customer/keys', {'key_ids': ids[1:]}).status_code == 200
    assert post(c, 'keys', {'id': ids[-1], 'provider': target, 'name': 'moved'}).status_code == 200
    after = verify(c, token).json()['provider_config']
    assert len(after) <= 48 and max(len(v['keys']) for v in after.values()) <= 32
    moved = next(k for k in after[target]['keys'] if k['key_id'] == ids[-1])
    assert moved['api_key'] == 'fake-secret' and moved['assignment_id'] != original[4]


def test_disabled_assigned_keys_also_count_when_editing_provider(env):
    c, auth, _ = env; login(c)
    with auth.store.connect(write=True) as db:
        db.executemany('INSERT INTO key_library VALUES(?,?,?,?,?,?,?)', [
            (f'k{i}', 'julun' if i < 32 else 'chaomo', f'k{i}', '{"api_key":"fake"}', str(i), 0, auth.now())
            for i in range(33)])
    assert post(c, 'users/customer/keys', {'key_ids': [f'k{i}' for i in range(33)]}).status_code == 200
    assert post(c, 'keys', {'id': 'k32', 'provider': 'julun', 'name': 'moved', 'enabled': False}).status_code == 422
