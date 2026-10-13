"""User-supplied HTTPS reference videos, frozen with each production batch."""
from urllib.parse import urlsplit

from .store import LOCK, read_json, write_json

STATE = '07_检查与记录/reference-videos.json'


def _https(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.netloc or parts.username or parts.password:
        raise ValueError('参考视频请填写可公开访问的 HTTPS 文件链接，不是本机路径或网页地址')
    return url


def refs(task):
    value = task.get('reference_videos')
    if value is None:
        value = task.get('video_refs') or (task.get('extra') or {}).get('video_refs') or []
    if not isinstance(value, list):
        raise ValueError('参考视频必须是列表')
    out = []
    for row in value:
        url = (row.get('url') or row.get('file_ref')) if isinstance(row, dict) else row
        if not isinstance(url, str):
            raise ValueError('参考视频缺少 HTTPS 链接')
        url = url.strip()
        out.append({'url': url, 'name': (row.get('name') if isinstance(row, dict) else '') or f'参考视频 {len(out)+1}'})
    return out


def overlay(pj, task):
    if task.get('_video_refs_frozen'):
        return task
    state = read_json(pj.p(*STATE.split('/')), {}) or {}
    out = dict(task)
    out['reference_videos'] = state.get(task['output'], refs(task))
    out['_video_refs_frozen'] = True
    return out


def update(pj, key, url, *, remove=False):
    from . import agent_projects as A
    with LOCK:
        task = next((t for t in A.task_rows(pj.tasks(), legacy=True)
                     if t['key'] == key and t['kind'] == 'video'), None)
        if task is None:
            raise ValueError('视频任务不存在，请刷新')
        item = refs({'reference_videos': [url]})[0]
        if not remove:
            _https(item['url'])
        rows = refs(overlay(pj, task))
        if remove:
            rows = [r for r in rows if r['url'] != item['url']]
        elif not any(r['url'] == item['url'] for r in rows):
            rows.append(item)
        state = read_json(pj.p(*STATE.split('/')), {}) or {}
        state[task['output']] = rows
        write_json(pj.p(*STATE.split('/')), state)
    return {'ok': True, 'reference_videos': rows}


def validate(task, capability):
    rows = refs(task)
    if not rows:
        if capability.get('min_video_refs', 0):
            raise ValueError('这个模型需要参考视频，请在任务详情添加 HTTPS 视频链接或更换模型')
        return
    if not capability.get('reference_video_urls_supported'):
        raise ValueError('当前服务商尚未接入参考视频链接，请选沧元视频或移除参考视频')
    maximum = capability.get('max_video_refs')
    if maximum is not None and len(rows) > maximum:
        raise ValueError(f'当前模型最多 {maximum} 个参考视频，本任务有 {len(rows)} 个；请移除多余引用或更换模型')
    for row in rows:
        _https(row['url'])


def urls(task):
    return [_https(r['url']) for r in refs(task)]
