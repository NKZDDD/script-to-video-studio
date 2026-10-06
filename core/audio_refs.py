"""Per-video reference audio, separate from Agent releases and frozen per job."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import uuid

from . import probe, uploader
from .store import LOCK, read_json, write_json

STATE = '07_检查与记录/reference-audios.json'
FOLDER = '03_提示词/参考音频'
EXTENSIONS = {'.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac'}
MAX_BYTES = 25 * 1024 * 1024


def refs(task):
    value = task.get('reference_audios')
    if value is None:
        value = task.get('audio_refs') or (task.get('extra') or {}).get('audio_refs') or (task.get('extra') or {}).get('audios') or []
    if not isinstance(value, list):
        raise ValueError('参考音频必须是列表')
    out = []
    for row in value:
        src = (row.get('file_ref') or row.get('url')) if isinstance(row, dict) else row
        if not isinstance(src, str) or not src.strip():
            raise ValueError('参考音频缺少文件路径或 URL')
        out.append({'file_ref': src.strip(), 'name': (row.get('name') if isinstance(row, dict) else '') or src.rsplit('/', 1)[-1]})
    return out


def overlay(pj, task):
    """Snapshot tasks already have the field; never reload mutable settings for them."""
    if task.get('_audio_refs_frozen'):
        return task
    state = read_json(pj.p(*STATE.split('/')), {}) or {}
    out = dict(task)
    out['reference_audios'] = state.get(task['output'], refs(task))
    out['_audio_refs_frozen'] = True
    return out


def _task(pj, key):
    from . import agent_projects as A
    row = next((t for t in A.task_rows(pj.tasks(), legacy=True) if t['key'] == key and t['kind'] == 'video'), None)
    if row is None:
        raise ValueError('视频任务不存在，请刷新')
    return overlay(pj, row)


def save(pj, key, filename, raw):
    from . import agent_projects as A
    suffix = Path(filename or '').suffix.lower()
    if suffix not in EXTENSIONS:
        raise ValueError('请选择 MP3、WAV、M4A、AAC、OGG 或 FLAC 音频')
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError('参考音频不能为空，单个文件最多 25MB')
    _task(pj, key)
    exe = probe.find_ffmpeg()
    if not exe:
        raise ValueError('未找到 FFmpeg，无法检查音频；请使用包含 FFmpeg 的完整安装包，或安装 FFmpeg 并加入系统 PATH 后重试')
    folder = A.inside(pj.root, FOLDER)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / (uuid.uuid4().hex + suffix)
    temporary.write_bytes(raw)
    try:
        fmt = {'.m4a': 'mov'}.get(suffix, suffix[1:])
        checked = subprocess.run([exe, '-v', 'error', '-xerror', '-nostdin', '-protocol_whitelist', 'file,pipe',
                                  '-f', fmt, '-i', str(temporary), '-map', '0:a:0', '-f', 'null', '-'],
                                 capture_output=True, timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if checked.returncode:
            raise ValueError('文件无法解码为完整音频，请重新导出为 MP3 或 WAV 后添加')
        rel = f'{FOLDER}/{hashlib.sha256(raw).hexdigest()}{suffix}'
        target = A.inside(pj.root, rel)
        with LOCK:
            task = _task(pj, key)
            if not target.exists():
                temporary.replace(target)
            rows = refs(task)
            if not any(r['file_ref'] == rel for r in rows):
                rows.append({'file_ref': rel, 'name': str(filename).replace('\\', '/').rsplit('/', 1)[-1]})
            state = read_json(pj.p(*STATE.split('/')), {}) or {}
            state[task['output']] = rows
            write_json(pj.p(*STATE.split('/')), state)
        return {'ok': True, 'reference_audios': rows}
    except subprocess.TimeoutExpired:
        raise ValueError('音频校验超时，请缩短音频后重新添加')
    finally:
        temporary.unlink(missing_ok=True)


def remove(pj, key, src):
    with LOCK:
        task = _task(pj, key)
        rows = [r for r in refs(task) if r['file_ref'] != src]
        state = read_json(pj.p(*STATE.split('/')), {}) or {}
        state[task['output']] = rows
        write_json(pj.p(*STATE.split('/')), state)
    # Keep immutable audio bytes: a running job may still reference this file.
    return {'ok': True, 'reference_audios': rows}


def validate(pj, task, capability, upload):
    from . import agent_projects as A
    rows = refs(task)
    if not rows:
        return
    cap = capability.get('max_audio_refs')
    if cap is None and not capability.get('supports_audio_refs'):
        raise ValueError('当前视频模型未声明支持参考音频，请移除任务中的音频或换用支持参考音频的模型')
    if cap is not None and len(rows) > int(cap):
        raise ValueError(f'当前视频模型最多支持 {cap} 个参考音频，本任务有 {len(rows)} 个；请移除多余音频或更换模型')
    for row in rows:
        src = row['file_ref']
        if src.startswith(('https://', 'http://')):
            continue
        path = A.inside(pj.root, src)
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f'参考音频不存在或为空：{row["name"]}；请移除后重新添加')
        if not uploader.configured(upload):
            raise ValueError('本地参考音频需要公网链接，请先在设置中配置对象存储上传，或移除该音频')


def resolve(pj, task, upload, log):
    from . import agent_projects as A
    out = []
    for row in refs(task):
        src = row['file_ref']
        if src.startswith(('https://', 'http://')):
            out.append(src)
            continue
        path = A.inside(pj.root, src)
        # Separate from image encoder/cache: audio must never be re-encoded as an image.
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        destination = json.dumps({k: upload.get(k, '') for k in ('endpoint', 'bucket', 'prefix', 'public_base_url')}, sort_keys=True)
        key = 'audio-' + hashlib.sha256(destination.encode()).hexdigest()[:16] + '-' + digest + path.suffix.lower()
        url = uploader._cache_get(pj.root, key)
        if not url:
            prefix = str(upload.get('prefix') or 'respect').strip('/')
            url = uploader.put(upload, data, f'{prefix}/audio/{digest}{path.suffix.lower()}')
            uploader._cache_put(pj.root, key, url)
            log(f'参考音频已上传：{row["name"]}')
        out.append(url)
    return out
