"""Small, credential-safe GET probe shared by the auth service and Studio.

Never execute downloaded adapters, follow redirects, inherit proxies, or use a
cached model catalog. DNS answers are checked and the TLS socket is pinned to
the checked public address, retaining the original hostname for SNI/validation.
"""
import http.client
import ipaddress
import json
import socket
import ssl
import time
from urllib.parse import parse_qsl, urlsplit, urlunsplit

TIMEOUT = 10
MAX_BYTES = 1048576
CATEGORIES = ('ok', 'authentication', 'rate_limited', 'server_error', 'http_error',
              'redirect', 'dns', 'tls', 'timeout', 'connection', 'invalid_response',
              'unsafe_target', 'not_configured')
AUTH_HEADERS = {'none': None, 'bearer': 'Authorization', 'x-api-key': 'x-api-key', 'api-key': 'api-key'}


def clean_url(value):
    if not isinstance(value, str) or len(value) > 1000 or any(ord(c) < 33 for c in value):
        raise ValueError('请填写不含空格的 HTTPS 检测地址')
    u = urlsplit(value)
    if (u.scheme != 'https' or not u.hostname or u.username or u.password or u.fragment
            or u.port not in (None, 443)):
        raise ValueError('检测地址必须为 HTTPS 443，不能带账号、密码或片段')
    if any(k.lower().replace('-', '').replace('_', '') in ('key', 'apikey', 'token', 'accesstoken', 'authorization', 'password', 'secret')
           for k, _ in parse_qsl(u.query)):
        raise ValueError('检测地址不能携带密钥参数，请选择 Key 库中的凭据')
    host = u.hostname.encode('idna').decode('ascii')
    if host == 'localhost' or host.endswith(('.localhost', '.local')):
        raise ValueError('检测地址必须是公网服务商地址')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not public_ip(str(address)):
        raise ValueError('检测地址必须是公网服务商地址')
    return urlunsplit(('https', f'[{host}]' if ':' in host else host, u.path or '/', u.query, ''))


def public_ip(value):
    ip = ipaddress.ip_address(value)
    if getattr(ip, 'ipv4_mapped', None):
        ip = ip.ipv4_mapped
    return ip.is_global and not (ip.is_multicast or ip.is_unspecified or ip.is_reserved)


def same_origin(first, second):
    def origin(value):
        url = urlsplit(value)
        return url.scheme, (url.hostname or '').encode('idna').decode('ascii').lower(), url.port or 443
    return origin(first) == origin(second)


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        deadline = time.monotonic() + self.timeout
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            raw.settimeout(remaining)
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def select(data, path):
    for part in path.split('.') if path else []:
        if not isinstance(data, dict) or part not in data:
            raise ValueError('missing response field')
        data = data[part]
    return data


def inspect_response(status, raw, kind, result_path):
    if status in (401, 403):
        return 'authentication', None
    if status == 429:
        return 'rate_limited', None
    if 500 <= status <= 599:
        return 'server_error', None
    if 300 <= status <= 399:
        return 'redirect', None
    if not 200 <= status <= 299:
        return 'http_error', None
    if kind == 'http':
        return 'ok', None
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and (data.get('error') or data.get('ok') is False or data.get('success') is False):
            return 'invalid_response', None
        value = select(data, result_path)
        if kind == 'models' and isinstance(value, list) and len(value) <= 100000:
            if all(isinstance(row, (dict, str)) for row in value):
                return 'ok', len(value)
        if kind == 'health' and (value is True or isinstance(value, str) and value.lower() in ('ok', 'healthy', 'success', 'up')):
            return 'ok', None
    except (ValueError, TypeError, RecursionError):
        pass
    return 'invalid_response', None


def probe(spec, secret='', *, resolver=None, connection=None, model_id_field=None):
    started = time.monotonic()
    result = {'at': int(time.time()), 'status': 0, 'category': 'connection', 'duration_ms': 0,
              'model_count': None}
    conn = None
    try:
        url = clean_url(spec['url'])
        auth = spec['auth']
        if auth not in AUTH_HEADERS or spec['kind'] not in ('models', 'health', 'http'):
            raise ValueError('invalid probe configuration')
        if auth != 'none' and (not secret or any(ord(c) < 32 for c in secret)):
            result['category'] = 'not_configured'
            return result
        u = urlsplit(url)
        answers = (resolver or socket.getaddrinfo)(u.hostname, 443, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(row[4][0] for row in answers))
        if not addresses:
            raise socket.gaierror('no addresses')
        if any(not public_ip(a) for a in addresses):
            result['category'] = 'unsafe_target'
            return result
        last_error = None
        for address in addresses[:8]:
            remaining = TIMEOUT - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError()
            candidate = (connection or PinnedHTTPS)(u.hostname, address, min(3, remaining))
            try:
                candidate.connect()
                conn = candidate
                break
            except OSError as exc:
                candidate.close()
                last_error = exc
        if conn is None:
            raise last_error or ConnectionError()
        remaining = TIMEOUT - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError()
        if conn.sock:
            conn.sock.settimeout(remaining)
        headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity',
                   'Cache-Control': 'no-cache', 'User-Agent': 'Respect-Studio-Monitor/1'}
        if auth != 'none':
            headers[AUTH_HEADERS[auth]] = ('Bearer ' if auth == 'bearer' else '') + secret
        conn.request('GET', urlunsplit(('', '', u.path or '/', u.query, '')), headers=headers)
        response = conn.getresponse()
        result['status'] = response.status if 100 <= response.status <= 599 else 0
        raw = b''
        # Error/redirect responses never need their potentially sensitive bodies.
        if 200 <= response.status <= 299 and spec['kind'] != 'http':
            while len(raw) <= MAX_BYTES:
                remaining = TIMEOUT - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError()
                if conn.sock:
                    conn.sock.settimeout(remaining)
                chunk = response.read1(min(65536, MAX_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw += chunk
            if len(raw) > MAX_BYTES:
                result['category'] = 'invalid_response'
                return result
        result['category'], result['model_count'] = inspect_response(response.status, raw, spec['kind'], spec['result_path'])
        if model_id_field and result['category'] == 'ok' and spec['kind'] == 'models':
            rows = select(json.loads(raw), spec['result_path'])
            ids = [row if isinstance(row, str) else row.get(model_id_field) for row in rows]
            if len(ids) > 5000 or any(not isinstance(mid, str) or not mid.strip() or len(mid) > 200
                                      or any(ord(c) < 32 for c in mid) for mid in ids):
                result['category'] = 'invalid_response'
            else:
                # Only exact model IDs leave the probe, never arbitrary response fields.
                result['models'] = list(dict.fromkeys(mid.strip() for mid in ids))
    except socket.gaierror:
        result['category'] = 'dns'
    except ssl.SSLError:
        result['category'] = 'tls'
    except TimeoutError:
        result['category'] = 'timeout'
    except (ValueError, KeyError, UnicodeError):
        result['category'] = 'unsafe_target'
    except (OSError, http.client.HTTPException):
        result['category'] = 'connection'
    finally:
        if conn:
            conn.close()
        result['duration_ms'] = min(604800000, max(0, round((time.monotonic() - started) * 1000)))
    return result
