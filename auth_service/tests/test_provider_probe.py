import json
import socket
import ssl

import pytest

from studio_auth import provider_probe as P


SPEC = dict(url='https://provider.example/v1/models', auth='bearer', kind='models', result_path='data')


@pytest.mark.parametrize('url', ['http://provider.example', 'https://localhost/health', 'https://127.0.0.1/',
    'https://[::1]/', 'https://192.168.1.2/', 'https://user:secret@provider.example/',
    'https://provider.example:8443/', 'https://provider.example/?api_key=secret', 'https://provider.example/#secret'])
def test_unsafe_or_credential_urls_rejected(url):
    with pytest.raises(ValueError):
        P.clean_url(url)


def test_origin_normalization_and_query_capabilities():
    assert P.same_origin('https://PROVIDER.example:443/v1', SPEC['url'])
    assert P.clean_url('https://provider.example/v1/models?include_capabilities=1').endswith('?include_capabilities=1')


@pytest.mark.parametrize('status,raw,kind,path,category,count', [
    (200, b'{"data": [{"id":"m"}]}', 'models', 'data', 'ok', 1),
    (200, b'{"data": []}', 'models', 'data', 'ok', 0),
    (200, b'{"data": {"models":["m"]}}', 'models', 'data.models', 'ok', 1),
    (200, b'{"ok":true}', 'health', 'ok', 'ok', None),
    (200, b'{"ok":false}', 'http', '', 'ok', None),  # Explicitly connectivity only.
    (200, b'{"ok":false}', 'health', 'ok', 'invalid_response', None),
    (200, b'{"error":"secret","data":[]}', 'models', 'data', 'invalid_response', None),
    (200, b'<html>login</html>', 'models', 'data', 'invalid_response', None),
    (200, b'{"data":[true]}', 'models', 'data', 'invalid_response', None),
    (302, b'', 'models', 'data', 'redirect', None),
    (401, b'secret', 'models', 'data', 'authentication', None),
    (403, b'secret', 'models', 'data', 'authentication', None),
    (429, b'secret', 'models', 'data', 'rate_limited', None),
    (503, b'secret', 'models', 'data', 'server_error', None),
    (404, b'secret', 'models', 'data', 'http_error', None),
])
def test_response_contract(status, raw, kind, path, category, count):
    assert P.inspect_response(status, raw, kind, path) == (category, count)


def resolver(*args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]


class Connection:
    status = 200
    body = b'{"data":["model"]}'
    instances = []

    def __init__(self, host, address, timeout):
        self.host, self.address, self.timeout = host, address, timeout
        self.sock = None
        self.raw = self.body
        self.closed = False
        self.instances.append(self)

    def request(self, method, path, headers):
        self.sent = (method, path, headers)

    def connect(self):
        pass

    def getresponse(self):
        return self

    def read1(self, length):
        data, self.raw = self.raw[:length], self.raw[length:]
        return data

    def close(self):
        self.closed = True


def test_get_pins_checked_address_no_secrets_in_result():
    result = P.probe(SPEC, 'private-secret', resolver=resolver, connection=Connection)
    conn = Connection.instances[-1]
    assert conn.address == '8.8.8.8' and conn.host == 'provider.example'
    assert conn.sent[0] == 'GET' and conn.sent[2]['Authorization'] == 'Bearer private-secret'
    assert conn.closed and result['category'] == 'ok'
    assert 'secret' not in json.dumps(result) and 'url' not in result


def test_dns_private_answer_and_mixed_addresses_never_connect():
    def mixed(*args, **kwargs):
        return resolver() + [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
    def forbidden(*args):
        pytest.fail('connected to rejected DNS answer')
    assert P.probe(SPEC, 'secret', resolver=mixed, connection=forbidden)['category'] == 'unsafe_target'


@pytest.mark.parametrize('error,category', [(socket.gaierror('private'), 'dns'),
    (ssl.SSLError('private'), 'tls'), (TimeoutError('private'), 'timeout'), (ConnectionError('private'), 'connection')])
def test_errors_classified_without_strings(error, category):
    def fail(*args, **kwargs):
        raise error
    result = P.probe(SPEC, 'secret', resolver=fail)
    assert result['category'] == category and 'private' not in json.dumps(result)


def test_redirect_and_error_body_not_read(monkeypatch):
    monkeypatch.setattr(Connection, 'status', 302)
    monkeypatch.setattr(Connection, 'read1', lambda *args: pytest.fail('redirect body read'))
    result = P.probe(SPEC, 'secret', resolver=resolver, connection=Connection)
    assert result['category'] == 'redirect' and Connection.instances[-1].closed


def test_response_size_limit(monkeypatch):
    monkeypatch.setattr(P, 'MAX_BYTES', 8)
    assert P.probe(SPEC, 'secret', resolver=resolver, connection=Connection)['category'] == 'invalid_response'


def test_public_probe_never_sends_key_and_empty_secret_is_not_network_test():
    public = dict(SPEC, auth='none')
    assert P.probe(public, 'private-secret', resolver=resolver, connection=Connection)['category'] == 'ok'
    assert 'Authorization' not in Connection.instances[-1].sent[2]
    assert P.probe(SPEC, resolver=lambda *a, **k: pytest.fail('no credentials'))['category'] == 'not_configured'


def test_unreachable_first_address_can_fall_back_without_dns_rebinding():
    attempted = []
    class Alternate(Connection):
        def connect(self):
            attempted.append(self.address)
            if self.address == '8.8.8.8':
                raise ConnectionError('unreachable')
    def addresses(*args, **kwargs):
        return resolver() + [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('1.1.1.1', 443))]
    assert P.probe(SPEC, 'secret', resolver=addresses, connection=Alternate)['category'] == 'ok'
    assert attempted == ['8.8.8.8', '1.1.1.1']
