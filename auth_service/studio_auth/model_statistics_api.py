import json

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from .admin import WEB, current
from .model_statistics import CACHE
from .monitor_catalog import active_catalog
from .service import AuthError

router = APIRouter()


@router.get('/admin/model-statistics.js')
def javascript():
    return FileResponse(WEB / 'model-statistics.js', media_type='text/javascript')


@router.get('/admin/model-statistics.css')
def stylesheet():
    return FileResponse(WEB / 'model-statistics.css', media_type='text/css')


@router.get('/admin/api/provider-model-stats')
def statistics(request: Request, provider: str = '', base_url: str = '', key_id: str = ''):
    current(request)
    # Never use a browser-supplied provider/base URL to choose where to send a Key.
    if not key_id:
        return dict(state='empty', models=[], catalog=dict(state='empty', models=[], message='请先选择或保存一把 Key'))
    with request.app.state.auth.store.connect() as db:
        row = db.execute('SELECT * FROM key_library WHERE id=?', (key_id,)).fetchone()
        if row is None:
            raise AuthError(404, 'key_missing', 'Key 不存在')
        rules, _ = active_catalog(db)
    cfg = json.loads(row['config'])
    rule = rules.get(row['provider'], {})
    catalog = request.app.state.key_model_catalog.snapshot(row, cfg, rule)
    value = CACHE.snapshot(row['provider'], cfg.get('base_url') or rule.get('base_url', ''))
    allowed = set(catalog['models'])
    value['models'] = [r for r in value['models'] if r['model'] in allowed]
    value['catalog'] = catalog
    return value
