from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from .admin import WEB, current
from .model_statistics import CACHE

router = APIRouter()


@router.get('/admin/model-statistics.js')
def javascript():
    return FileResponse(WEB / 'model-statistics.js', media_type='text/javascript')


@router.get('/admin/model-statistics.css')
def stylesheet():
    return FileResponse(WEB / 'model-statistics.css', media_type='text/css')


@router.get('/admin/api/provider-model-stats')
def statistics(request: Request, provider: str = '', base_url: str = ''):
    current(request)
    # base_url is only compared with an allowlist; it is never requested.
    return CACHE.snapshot(provider, base_url)
