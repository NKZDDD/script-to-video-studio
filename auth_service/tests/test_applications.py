from test_admin import env, login, post
from test_releases import build, headers


def test_each_application_has_independent_policy_and_catalog(env):
    c, auth, _ = env
    login(c)
    studio=build('2026.10.29.1')
    batch={**build('2026.10.29.1','b'),'application':'batch','flavor':'batch'}
    assert post(c,'releases',studio).status_code==200
    assert post(c,'releases',batch).status_code==200
    assert post(c,'releases/enforcement',{'application':'batch','enabled':False}).status_code==200
    assert c.get('/admin/api/releases?application=studio').json()['enforcement_enabled']
    assert not c.get('/admin/api/releases?application=batch').json()['enforcement_enabled']
    assert c.get('/api/v1/updates/latest?flavor=batch').json()['release'] is None
    assert c.get('/api/v1/updates/latest').json()['release']['flavor']=='all'
    assert not c.get('/api/v1/updates/latest').json()['client']['allowed']
    assert {r['flavor'] for r in c.get('/admin/api/releases?application=batch').json()['releases']}=={'batch'}
    auth.store.initialize()
    assert not c.get('/admin/api/releases?application=batch').json()['enforcement_enabled']
    assert post(c,'releases/enforcement',{'application':'studio','enabled':False}).status_code==200
    assert post(c,'releases/enforcement',{'application':'batch','enabled':True}).status_code==200
    assert c.get('/api/v1/updates/latest').json()['client']['allowed']
    assert not c.get('/api/v1/updates/latest?flavor=batch').json()['client']['allowed']


def test_wrong_app_registration_publication_and_query_rejected(env):
    c, _, _ = env
    login(c)
    row={**build(),'application':'batch'}
    assert post(c,'releases',row).status_code==422
    assert post(c,'releases/builds',dict(application='studio',flavor='batch',version='2026.10.29.2',build_sha256='a'*64)).status_code==422
    assert c.get('/api/v1/updates/latest?application=studio&flavor=batch').status_code==400
    post(c,'releases/enforcement',{'application':'batch','enabled':False})
    h={**headers('2026.10.29.2','a','batch'),'X-Respect-Application':'studio'}
    assert not c.get('/api/v1/updates/latest?flavor=batch',headers=h).json()['client']['allowed']


def test_old_policy_migration_preserves_pause_but_future_changes_are_separate(env):
    c, auth, _ = env
    with auth.store.connect(write=True) as db:
        db.execute('DROP TABLE application_version_policy')
        db.execute('UPDATE client_version_policy SET enabled=0')
    auth.store.initialize()
    login(c)
    for app in ('studio','batch'):
        assert not c.get('/admin/api/releases?application='+app).json()['enforcement_enabled']
    post(c,'releases/enforcement',{'application':'studio','enabled':True})
    auth.store.initialize()
    assert c.get('/admin/api/releases?application=studio').json()['enforcement_enabled']
    assert not c.get('/admin/api/releases?application=batch').json()['enforcement_enabled']
