"""Reusable named credentials; administrator responses never contain secrets."""
import json
import uuid
from collections import Counter

from fastapi import APIRouter, Request
from pydantic import Field

from .admin import Body, current, record
from .provisioning import ProviderConfig, KEY_FIELDS, user_id
from .service import AuthError
from .provider_names import PROVIDER_NAMES

router = APIRouter()


def migrate(db):
    # Run inside a write transaction. Removing legacy rows makes this idempotent.
    for row in db.execute('SELECT p.*,u.username FROM provider_assignments p JOIN users u ON u.id=p.user_id').fetchall():
        kid = str(uuid.uuid4())
        name = row['username'] + ' · ' + PROVIDER_NAMES.get(row['provider'], row['provider'])
        db.execute('INSERT INTO key_library VALUES(?,?,?,?,?,?,?)',
                   (kid, row['provider'], name[:100], row['config'], row['revision'], 1, row['updated_at']))
        db.execute('INSERT INTO user_keys VALUES(?,?,?)', (row['user_id'], kid, 0))
    db.execute('DELETE FROM provider_assignments')


def configuration(db, uid):
    result = {}
    rows = db.execute('SELECT k.* FROM user_keys a JOIN key_library k ON k.id=a.key_id '
                      'WHERE a.user_id=? ORDER BY a.position,k.id', (uid,)).fetchall()
    for row in rows:
        group = result.setdefault(row['provider'], {'keys': []})
        if row['enabled']:
            group['keys'].append(dict(json.loads(row['config']), assignment_id=row['revision'],
                                      key_id=row['id'], key_name=row['name']))
    for group in result.values():
        # Older clients still receive the first configuration; new clients consume keys.
        if group['keys']:
            group.update(group['keys'][0])
    return result


def public(row):
    cfg = json.loads(row['config'])
    return dict(id=row['id'], provider=row['provider'], name=row['name'], enabled=bool(row['enabled']),
                base_url=cfg.get('base_url', ''), models=cfg.get('models', []), configured=[k for k in KEY_FIELDS if cfg.get(k)])


@router.get('/admin/api/keys')
def keys(request: Request):
    current(request)
    with request.app.state.auth.store.connect() as db:
        rows = db.execute('SELECT * FROM key_library ORDER BY updated_at DESC,id').fetchall()
        from .adapters import _active
        bundle = _active(db)
    names = dict(PROVIDER_NAMES)
    if bundle:
        names.update({p['id']: p['name'] for p in bundle['providers']})
    return {'keys': [public(r) for r in rows], 'providers': names}


class KeyEdit(ProviderConfig):
    id: str = Field(default='', max_length=64)
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    models: list[str] = Field(default_factory=list, max_length=100)


@router.post('/admin/api/keys')
def save_key(body: KeyEdit, request: Request):
    actor, _ = current(request, csrf=True)
    auth = request.app.state.auth
    if not body.name.strip() or body.remove:
        raise AuthError(422, 'invalid_key', '请填写 Key 名称；停用请使用状态开关')
    with auth.store.connect(write=True) as db:
        old = db.execute('SELECT * FROM key_library WHERE id=?', (body.id,)).fetchone() if body.id else None
        if body.id and not old:
            raise AuthError(404, 'key_missing', 'Key 不存在')
        if old and old['provider'] != body.provider:
            raise AuthError(422, 'provider_changed', '已有 Key 的服务商不能更改，请新建一条')
        if not old and db.execute('SELECT COUNT(*) FROM key_library').fetchone()[0] >= 2000:
            raise AuthError(422, 'key_limit', 'Key 库最多 2000 条，请复用已有记录')
        cfg = json.loads(old['config']) if old else {}
        cfg['base_url'] = body.base_url
        if any(not m.strip() or len(m) > 200 for m in body.models):
            raise AuthError(422, 'invalid_models', '模型 ID 不能为空且最多 200 字符')
        cfg['models'] = list(dict.fromkeys(m.strip() for m in body.models))
        for field in KEY_FIELDS:
            if getattr(body, field).strip():
                cfg[field] = getattr(body, field).strip()
        if not any(cfg.get(k) for k in KEY_FIELDS):
            raise AuthError(422, 'key_required', '新建时请填写 Key')
        kid = body.id or str(uuid.uuid4())
        revision = old['revision'] if old and cfg == json.loads(old['config']) else str(uuid.uuid4())
        db.execute('INSERT INTO key_library VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                   'name=excluded.name,config=excluded.config,revision=excluded.revision,'
                   'enabled=excluded.enabled,updated_at=excluded.updated_at',
                   (kid, body.provider, body.name.strip(), json.dumps(cfg), revision, int(body.enabled), auth.now()))
    record(request, actor, 'save_key', kid)
    return {'ok': True, 'id': kid}


class Assignment(Body):
    key_ids: list[str] = Field(max_length=256)


@router.get('/admin/api/users/{name}/keys')
def assigned(name: str, request: Request):
    current(request)
    auth = request.app.state.auth
    uid = user_id(auth, name)
    with auth.store.connect() as db:
        rows = db.execute('SELECT key_id FROM user_keys WHERE user_id=? ORDER BY position,key_id', (uid,)).fetchall()
    return {'key_ids': [r['key_id'] for r in rows]}


@router.post('/admin/api/users/{name}/keys')
def assign_keys(name: str, body: Assignment, request: Request):
    actor, _ = current(request, csrf=True)
    auth = request.app.state.auth
    uid = user_id(auth, name)
    ids = body.key_ids
    if len(ids) != len(set(ids)) or any(len(k) > 64 for k in ids):
        raise AuthError(422, 'invalid_keys', 'Key 列表重复或无效')
    with auth.store.connect(write=True) as db:
        providers = []
        for kid in ids:
            row = db.execute('SELECT provider FROM key_library WHERE id=?', (kid,)).fetchone()
            if not row:
                raise AuthError(422, 'key_missing', '选中的 Key 已不存在，请刷新后再分配')
            providers.append(row['provider'])
        counts = Counter(providers)
        if len(counts) > 48 or any(n > 32 for n in counts.values()):
            raise AuthError(422, 'assignment_limit', '每个账号最多 48 家服务商，每家最多 32 把 Key')
        db.execute('DELETE FROM user_keys WHERE user_id=?', (uid,))
        db.executemany('INSERT INTO user_keys VALUES(?,?,?)', [(uid, k, i) for i, k in enumerate(ids)])
    record(request, actor, 'assign_keys', name)
    return {'ok': True}
