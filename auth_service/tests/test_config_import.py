import json

from test_admin import env, login, post
from test_provisioning import client_token, verify


def payload(providers, **kw):
    return dict(content=json.dumps(dict(providers=providers, projects_dir='D:/not-imported',
                                        llm={'api_key': 'do-not-import-llm'}, **kw)))


def test_preview_commit_split_keys_no_secret_echo_and_no_assignment(env):
    c,a,_=env;login(c)
    body=payload({'chaomo': {'api_key': 'dummy-general', 'image_1k_api_key': 'dummy-th',
                            'image_4k_api_key': 'dummy-native', 'video_api_key': 'dummy-video',
                            'base_url': 'https://example.test/', 'poll_timeout': 123},
                  'gate': {'api_key': 'dummy-gate', 'models': ['model-a', 'model-b', 'model-a']},
                  'unused': {'api_key': ''}})
    r=post(c,'keys/import/preview',body)
    assert r.status_code==200, r.text
    assert 'dummy-' not in r.text and 'do-not-import' not in r.text
    rows=r.json()['rows'];assert len(rows)==5 and r.json()['warnings']
    assert rows[-1]['models']==['model-a','model-b']
    assert c.get('/admin/api/keys').json()['keys']==[]
    r=post(c,'keys/import/commit',dict(body, selected=[1,2,4]))
    assert r.status_code==200 and r.json()['added']==3
    assert verify(c,client_token(c)).json()['provider_config']=={}
    post(c,'users/customer/keys',{'key_ids':r.json()['key_ids']})
    cfg=verify(c,client_token(c)).json()['provider_config']
    assert cfg['gate']['keys'][0]['models']==['model-a','model-b']
    assert cfg['chaomo']['keys'][0]['image_1k_api_key']=='dummy-th'
    with a.store.connect() as db:
        configs=[json.loads(r['config']) for r in db.execute('SELECT config FROM key_library')]
        assert all('poll_timeout' not in x and 'projects_dir' not in x for x in configs)


def test_repeat_import_skips_duplicates_and_never_overwrites_or_enables(env):
    c,a,_=env;login(c)
    body=payload({'julun': {'api_key': 'first-dummy'}})
    r=post(c,'keys/import/commit',dict(body,selected=[0]));kid=r.json()['key_ids'][0]
    post(c,'keys',{'id':kid,'provider':'julun','name':'kept-name','enabled':False})
    assert post(c,'keys/import/preview',body).json()['rows'][0]['duplicate']
    r=post(c,'keys/import/commit',dict(body,selected=[0])).json()
    assert r['added']==0 and r['skipped']==1
    keys=c.get('/admin/api/keys').json()['keys']
    assert len(keys)==1 and keys[0]['name']=='kept-name' and not keys[0]['enabled']
    other=payload({'julun':{'api_key':'different-dummy'}})
    assert post(c,'keys/import/commit',dict(other,selected=[0])).json()['added']==1


def test_auth_csrf_validation_no_partial_write_no_secret_errors(env):
    c,a,_=env
    body=payload({'gate':{'api_key':'private-dummy'}})
    for endpoint in ('preview','commit'):
        assert post(c,'keys/import/'+endpoint,body,csrf='bad').status_code==401
    login(c)
    assert post(c,'keys/import/commit',body,csrf='bad').status_code==403
    invalid=[{'content':'{"providers":{},"providers":{}}'}, {'content':'[]'},
             payload({'gate':{'api_key':123}}),payload({'gate':{'api_key':'private-dummy','base_url':'http://invalid.test'}}),
             payload({'gate':{'api_key':'private-dummy','models':'not-list'}}),
             payload({'gate':{'api_key':'private-dummy','models':['']}})]
    for data in invalid:
        r=post(c,'keys/import/commit',dict(data,selected=[0]))
        assert r.status_code==422 and 'private-dummy' not in r.text
    for selected in ([],[-1],[0,0],[0,1]):
        assert post(c,'keys/import/commit',dict(body,selected=selected)).status_code==422
    assert c.get('/admin/api/keys').json()['keys']==[]


def test_bom_large_config_extra_fields_ignored_and_limit_scoped(env):
    c,a,_=env;login(c)
    body=payload({'gate':{'api_key':'dummy-only'}}, ignored='x'*20000)
    body['content']='\ufeff'+body['content']
    assert post(c,'keys/import/preview',body).status_code==200
    assert post(c,'keys',{'name':'x'*20000}).status_code==413
    assert post(c,'keys/import/preview',{'content':'x'*2097153}).status_code==413
    assert post(c,'keys/import/preview',payload({'gate':{'image_api_key':'legacy-dummy'}})).status_code==422


def test_invalid_provider_is_reported_without_blocking_valid_keys(env):
    c,a,_=env;login(c)
    body=payload({'gate':{'api_key':'dummy-good'}, 'broken':{'api_key':'dummy-bad','base_url':'http://example.test'}})
    d=post(c,'keys/import/preview',body).json()
    assert len(d['rows'])==1 and d['rows'][0]['provider']=='gate'
    assert len(d['warnings'])==1 and 'dummy-' not in json.dumps(d)
    assert post(c,'keys/import/commit',dict(body,selected=[0])).json()['added']==1


def test_library_capacity_import_rollback(env):
    c,a,clock=env;login(c)
    with a.store.connect(write=True) as db:
        db.executemany('INSERT INTO key_library VALUES(?,?,?,?,?,?,?)',
                       [(str(i),'gate',str(i),'{}',str(i),1,clock[0]) for i in range(1999)])
    body=payload({'gate':{'api_key':'dummy-first','video_api_key':'dummy-second'}})
    r=post(c,'keys/import/commit',dict(body,selected=[0,1]))
    assert r.status_code==422
    with a.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM key_library').fetchone()[0]==1999
