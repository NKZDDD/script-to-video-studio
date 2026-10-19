"""Import Studio provider credentials without importing machine/project settings."""
import json
import uuid

from fastapi import APIRouter, Request
from pydantic import Field, ValidationError

from .admin import Body, current, record
from .key_library import KeyEdit
from .provider_names import PROVIDER_NAMES
from .provisioning import KEY_FIELDS
from .service import AuthError

router = APIRouter()
FIELD_NAMES = {'api_key': '通用 Key', 'image_1k_api_key': 'TH 图片 Key',
               'image_4k_api_key': 'Native 图片 Key', 'video_api_key': '视频 Key'}


class ImportBody(Body):
    content: str = Field(max_length=524288)
    selected: list[int] = Field(default_factory=list, max_length=256)


def parse(content):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result

    try:
        data = json.loads(content.lstrip('\ufeff'), object_pairs_hook=unique)
    except (ValueError, RecursionError):
        raise AuthError(422, 'invalid_config', '文件不是有效 JSON，或含重复字段；请检查文件格式') from None
    if not isinstance(data, dict) or not isinstance(data.get('providers'), dict):
        raise AuthError(422, 'invalid_config', '需要 Studio config.json：顶层应包含 providers 对象')
    candidates, warnings = [], []
    for position, (provider, cfg) in enumerate(data['providers'].items(), 1):
        if not isinstance(cfg, dict):
            warnings.append(f'第 {position} 个服务商配置不是对象，已跳过')
            continue
        fields = [k for k in KEY_FIELDS if cfg.get(k)]
        if any(k.endswith('_api_key') and k not in KEY_FIELDS and cfg.get(k) for k in cfg):
            warnings.append(f'第 {position} 个服务商有不支持的密钥字段，未导入；请在原文件核对')
        if not fields:
            warnings.append(f'第 {position} 个服务商没有可导入的 Key，已跳过')
        for field in fields:
            try:
                # Keep credential types (including legacy TH/Native) distinct.
                item = KeyEdit(provider=provider, name=f'{PROVIDER_NAMES.get(provider, provider)} · {FIELD_NAMES[field]}'[:100],
                               base_url=cfg.get('base_url', ''), models=cfg.get('models', []), **{field: cfg[field]})
                if not getattr(item, field).strip() or any(not m.strip() or len(m) > 200 for m in item.models):
                    raise ValueError('empty credential or invalid model')
            except (ValidationError, ValueError):
                # Validation exceptions can contain the raw credential; never echo them.
                warnings.append(f'第 {position} 个服务商的 {FIELD_NAMES[field]}、HTTPS 地址或模型列表格式不正确，已跳过；请修正后重导')
                continue
            config = {'base_url': item.base_url, 'models': list(dict.fromkeys(m.strip() for m in item.models)),
                      field: getattr(item, field).strip()}
            candidates.append({'provider': item.provider, 'name': item.name, 'config': config, 'field': field})
    if len(candidates) > 256:
        raise AuthError(422, 'import_limit', '一次最多导入 256 条 Key，请拆分文件')
    if not candidates:
        raise AuthError(422, 'no_keys', '没有可导入的 Key；支持 api_key、image_1k_api_key、image_4k_api_key、video_api_key')
    return candidates, warnings


def signature(provider, cfg):
    # Exclude record identity/name/enabled: reimport must not reactivate a stopped Key.
    normalized = {'base_url': cfg.get('base_url', ''), 'models': sorted(set(cfg.get('models', [])))}
    normalized.update({k: cfg[k] for k in KEY_FIELDS if cfg.get(k)})
    return provider, json.dumps(normalized, sort_keys=True)


def existing(db):
    return {signature(r['provider'], json.loads(r['config']))
            for r in db.execute('SELECT provider,config FROM key_library')}


@router.post('/admin/api/keys/import/preview')
def preview(body: ImportBody, request: Request):
    current(request, csrf=True)
    candidates, warnings = parse(body.content)
    with request.app.state.auth.store.connect() as db:
        seen = existing(db)
    rows = []
    for index, row in enumerate(candidates):
        sig = signature(row['provider'], row['config'])
        rows.append(dict(index=index, name=row['name'], provider=row['provider'], field=row['field'],
                         base_url=row['config']['base_url'], models=row['config']['models'], duplicate=sig in seen))
        seen.add(sig)
    return {'rows': rows, 'warnings': warnings}


@router.post('/admin/api/keys/import/commit')
def commit(body: ImportBody, request: Request):
    actor, _ = current(request, csrf=True)
    candidates, _ = parse(body.content)
    if not body.selected or len(set(body.selected)) != len(body.selected) or any(i < 0 or i >= len(candidates) for i in body.selected):
        raise AuthError(422, 'invalid_selection', '请选择有效的导入条目')
    auth = request.app.state.auth
    added, skipped = [], 0
    with auth.store.connect(write=True) as db:
        seen = existing(db)
        names = {r['name'] for r in db.execute('SELECT name FROM key_library')}
        count = db.execute('SELECT COUNT(*) FROM key_library').fetchone()[0]
        for index in body.selected:
            row = candidates[index]
            sig = signature(row['provider'], row['config'])
            if sig in seen:
                skipped += 1
                continue
            if count + len(added) >= 2000:
                raise AuthError(422, 'key_limit', '导入后超过 Key 库 2000 条上限，本次未写入')
            name, suffix = row['name'], 2
            while name in names:
                name = row['name'][:85] + f'（导入 {suffix}）'
                suffix += 1
            kid = str(uuid.uuid4())
            db.execute('INSERT INTO key_library VALUES(?,?,?,?,?,?,?)',
                       (kid, row['provider'], name, json.dumps(row['config']), str(uuid.uuid4()), 1, auth.now()))
            added.append(kid)
            names.add(name)
            seen.add(sig)
    record(request, actor, 'import_keys', f'added={len(added)},skipped={skipped}')
    return {'ok': True, 'added': len(added), 'skipped': skipped, 'key_ids': added}
