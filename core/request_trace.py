"""Bounded, structured local evidence for user-initiated reports. No raw bodies/headers."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps, lru_cache
import hashlib
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

_current = ContextVar('request_trace', default=None)
_response = ContextVar('request_trace_response', default=None)


def safe(value, limit=160):
    value = str(value or '')
    if (len(value) > limit or re.search(r'\bsk-|bearer|secret|password|https?://|[\\\r\n?&=]', value, re.I)
            or not re.fullmatch(r'[\w .:/(){}+@-]*', value)):
        return ''
    return value


def fingerprint(value):
    return hashlib.sha256(('studio-diagnostic:'+str(value)).encode()).hexdigest()[:24] if value else ''


@lru_cache(maxsize=128)
def adapter(cls):
    module = sys.modules.get(cls.__module__)
    name = cls.__module__
    match = re.search(r'core\._adapters_([a-f0-9]{64})\.', name)
    if match:
        return {'adapter_source': 'server', 'adapter_hash': match[1],
                'adapter_version': safe(getattr(cls, '_diagnostic_version', ''))}
    origin = 'builtin' if name.startswith('core.providers.') else 'plugin'
    try:
        path = Path(module.__file__)
        checksum = hashlib.sha256(path.read_bytes()).hexdigest() if path.stat().st_size <= 2097152 else ''
    except (OSError, AttributeError, TypeError):
        checksum = ''
    return {'adapter_source': origin, 'adapter_hash': checksum, 'adapter_version': ''}


def session_info(session, target=''):
    from .cloud_diagnostics import host
    proxy = getattr(session, 'proxy', '') or ''
    direct = proxy.lower() in ('direct', 'none', 'off', '关闭', '直连')
    return {'key_fingerprint': fingerprint(getattr(session, 'api_key', '')),
            'proxy_mode': 'direct' if direct else ('explicit' if proxy else 'system'),
            'proxy_host': host(proxy) if proxy and not direct else '',
            'auth_style': safe(getattr(session, 'auth_style', ''))}


@contextmanager
def production(owner, args, kwargs):
    from . import cloud_diagnostics as D
    if not owner:
        yield
        return
    provider = args[3] if len(args) > 3 else kwargs.get('prov')
    meta = kwargs.get('meta') or {}
    usage = meta.get('_usage') or {}
    record = (args[4] if len(args) > 4 else kwargs.get('rec')) or {}
    detail = dict(adapter(type(provider)), provider=safe(getattr(provider, 'id', '')),
                  model=safe(meta.get('model')), key_source=usage.get('source') if usage.get('source') in ('managed','personal') else 'unknown',
                  key_id=safe(usage.get('key_id')), task_id=safe(record.get('task_id')))
    if len(args) > 1:
        detail['task_fingerprint'] = fingerprint(str(getattr(args[0], 'root', ''))+'|'+str(args[1].get('key', '')))
    detail.update(session_info(getattr(provider, 'session', None)))
    state = {'owner': owner, 'details': detail, 'seen': set()}
    token = _current.set(state)
    started, error = time.monotonic(), None
    try:
        yield
    except Exception as exc:
        error = exc
        raise
    finally:
        try:
            d = dict(state['details'], stage='complete', **error_info(error))
            D.emit(owner, 'provider', 'complete', started, error=error, details=d, local_only=True)
        except Exception:
            pass
        finally:
            _current.reset(token)


def error_info(error):
    if error is None:
        return {}
    # Classify known tokens; never retain free-form exception text (it can echo prompts/Keys).
    text = str(error)[:8192]
    status = re.search(r'\bHTTP\s*[:=]?\s*([45]\d\d)\b', text, re.I)
    request = re.search(r'(?:request[_ -]?id)[\s"\x27:=\\]+([a-zA-Z0-9_-]{8,128})', text, re.I)
    hint = ('key_not_found' if re.search(r"api key.{0,20}(doesn.t exist|not found|invalid)|invalid[_ ]api[_ ]key", text, re.I)
            else 'authentication_rejected' if re.search(r'authentication|unauthorized|鉴权|认证', text, re.I)
            else '')
    frames = []
    tb = error.__traceback__
    while tb:
        code = tb.tb_frame.f_code
        frame = safe(Path(code.co_filename).name+':'+str(tb.tb_lineno)+':'+code.co_name, 160)
        if frame:
            frames.append(frame)
        tb = tb.tb_next
    return {'exception_type': safe(type(error).__name__, 80), 'error_code': safe(getattr(error, 'err_code', ''), 80),
            'upstream_status': int(status[1]) if status else 0,
            'upstream_request_id': safe(request[1], 128) if request else '', 'error_hint': hint, 'frames': frames[-8:]}


def observe_request(headers):
    state = _response.get()
    if state is not None:
        values = {str(k).lower():v for k,v in headers.items()}
        if 'x-api-key' in values:
            state.update(key_fingerprint=fingerprint(values['x-api-key']), auth_style='x-api-key')
        elif 'authorization' in values:
            value = str(values['authorization'])
            kind, _, token = value.partition(' ')
            state.update(key_fingerprint=fingerprint(token), auth_style=safe(kind.lower(), 32))


def observe_response(response):
    state = _response.get()
    if state is not None:
        state['http_status'] = int(response.status_code)
        headers = getattr(response, 'headers', {}) or {}
        state['provider_request_id'] = safe(headers.get('x-request-id') or headers.get('request-id') or headers.get('x-tt-logid'), 128)


def request_call(fn):
    @wraps(fn)
    def wrapped(session, method, path, **kw):
        state = _current.get()
        if not state or not state['owner']:
            return fn(session, method, path, **kw)
        from . import cloud_diagnostics as D
        started, error, result = time.monotonic(), None, None
        observed = {}
        token = _response.set(observed)
        try:
            result = fn(session, method, path, **kw)
            return result
        except Exception as exc:
            error = exc
            raise
        finally:
            try:
                body = kw.get('json_body') or kw.get('raw_body') or {}
                body = body if isinstance(body, dict) else {}
                target = path if path.startswith('http') else session.base_url+path
                endpoint = urlsplit(target).path
                task_id = safe(body.get('taskId') or body.get('task_id') or state['details'].get('task_id'))
                if isinstance(result, dict):
                    inner = result.get('data') if isinstance(result.get('data'), dict) else result
                    task_id = safe(inner.get('task_id') or inner.get('id') or task_id)
                if task_id:
                    state['details']['task_id'] = task_id
                    endpoint = endpoint.replace(task_id, '{task_id}')
                stage = ('query' if task_id and (method.upper() == 'GET' or body.get('taskId') or body.get('task_id'))
                         else 'submit' if method.upper() == 'POST' else 'request')
                detail = dict(state['details'], **session_info(session), stage=stage, method=safe(method, 8),
                              endpoint=safe(endpoint, 192), **error_info(error))
                detail.update(observed)
                model = safe(body.get('model'))
                if model:
                    detail['model'] = model
                if isinstance(result, dict):
                    # A provider can return 200 with an upstream error in the body.
                    upstream = result.get('error')
                    if upstream:
                        e = RuntimeError(str(upstream)[:8192])
                        detail.update(error_info(e))
                        if isinstance(upstream, dict):
                            detail['error_code'] = safe(upstream.get('code') or upstream.get('type'), 80)
                        error = error or e
                    detail['result_state'] = safe(result.get('status') or result.get('state'), 32)
                # Keep each changed state and every failure; omit identical successful polls.
                signature = (detail.get('task_id'), stage, detail.get('result_state'), detail['endpoint'])
                if error is not None or stage != 'query' or signature not in state['seen']:
                    if len(state['seen']) < 256:
                        state['seen'].add(signature)
                    D.emit(state['owner'], 'provider', stage, started, error=error,
                           status=observed.get('http_status', 0), target=target, details=detail, local_only=True)
            except Exception:
                pass  # Diagnostics must never change generation/retry behavior.
            finally:
                _response.reset(token)
    return wrapped


def task_failure(owner, job, key, started, error):
    from . import cloud_diagnostics as D
    try:
        D.emit(owner, 'provider', 'worker', started, error=error, local_only=True,
               details=dict(provider=safe(job.provider), model=safe(job.model), stage='complete',
                            task_fingerprint=fingerprint(str(job.project_root)+'|'+str(key)), **error_info(error)))
    except Exception:
        pass


def download_call(fn):
    @wraps(fn)
    def wrapped(session, item, dest, *args, **kwargs):
        from . import cloud_diagnostics as D
        state, started, error = _current.get(), time.monotonic(), None
        try:
            return fn(session, item, dest, *args, **kwargs)
        except Exception as exc:
            error = exc
            raise
        finally:
            if state:
                try:
                    D.emit(state['owner'], 'provider', 'download', started, error=error,
                           target=item if str(item).startswith('http') else '',
                           details=dict(state['details'], stage='download', **error_info(error)), local_only=True)
                except Exception:
                    pass
    return wrapped
