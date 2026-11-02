import json
import threading
import time
from types import SimpleNamespace

import pytest

from core import provider_monitor as M, studio_login as L


def spec():
    return dict(provider='gate', revision='a'*64, url='https://provider.example/v1/models',
                kind='models', result_path='data', auth='bearer', secret='private-key', key_name='probe-key')


@pytest.fixture
def monitor(tmp_path):
    manager = L.LoginManager()
    manager.user = {'id': 'alice', 'username': 'alice'}
    manager.tokens = {'access_token': 'a'*43}
    manager._diagnostics_dir = str(tmp_path)
    manager._device = 'monitor-device-123456'
    worker = M.Worker(manager)
    worker.fetch_at = 0
    return manager, worker, tmp_path


def result():
    return dict(at=int(time.time()), category='timeout', status=0, duration_ms=10000, model_count=None)


def pending(directory):
    db = M.connect(directory)
    try:
        return db.execute('SELECT owner,payload FROM provider_probe_outbox ORDER BY id').fetchall()
    finally:
        db.close()


def test_offline_probe_is_durable_and_retried_without_secrets(monitor, monkeypatch):
    manager, worker, directory = monitor
    monkeypatch.setattr(M, 'probe', lambda *args: result())
    calls = []
    def post(action, body, access):
        calls.append((action, body))
        if action.endswith('/config'):
            return {'protocol': 1, 'monitors': [spec()], 'monitor_token': 'm'*64}
        raise L.LoginRequired('auth_unavailable', 'offline', 503)
    manager._post = post
    worker.tick(manager)
    rows = pending(directory)
    assert len(rows) == 1 and rows[0][0] == 'alice'
    assert 'private-key' not in rows[0][1] and 'provider.example' not in rows[0][1]
    assert worker.rows['gate']['category'] == 'timeout'
    # Do not hammer authentication or submit generation while waiting for retry.
    worker.tick(manager)
    assert len(calls) == 2
    manager._post = lambda action, body, access: {'accepted': [e['event_id'] for e in body['events']]}
    M.send(manager, M.identity(manager))
    assert pending(directory) == []


def test_account_switch_during_probe_never_uploads_under_new_account(monitor, monkeypatch):
    manager, worker, directory = monitor
    calls = []
    def post(action, body, access):
        calls.append(action)
        return {'protocol': 1, 'monitors': [spec()], 'monitor_token': 'm'*64}
    manager._post = post
    def probe(*args):
        with manager.lock:
            manager.user = {'id': 'bob', 'username': 'bob'}
            manager.tokens = {'access_token': 'b'*43}
            manager.generation += 1
        return result()
    monkeypatch.setattr(M, 'probe', probe)
    worker.tick(manager)
    assert [r[0] for r in pending(directory)] == ['alice']
    assert worker.rows == {} and calls == ['provider-monitor/config']
    M.send(manager, M.identity(manager))
    assert calls == ['provider-monitor/config']


def test_expired_configuration_and_auth_rejection_stop_probing(monitor, monkeypatch):
    manager, worker, directory = monitor
    worker.owner = M.identity(manager)[:2]
    worker.specs = [spec()]
    worker.config_until = time.monotonic() - 1
    manager._post = lambda *args: (_ for _ in ()).throw(L.LoginRequired('auth_unavailable', 'offline', 503))
    monkeypatch.setattr(M, 'probe', lambda *args: pytest.fail('expired config probed'))
    worker.tick(manager)
    assert worker.specs == [] and pending(directory) == []
    worker.specs = [spec()]
    worker.config_until = time.monotonic() + 300
    worker.fetch_at = 0
    manager._post = lambda *args: (_ for _ in ()).throw(L.LoginRequired('invalid_session', 'revoked', 401))
    worker.tick(manager)
    assert worker.specs == []


def test_network_wait_does_not_hold_login_or_config_locks(monitor):
    manager, worker, _ = monitor
    entered, finish = threading.Event(), threading.Event()
    def post(*args):
        entered.set()
        assert finish.wait(5)
        return {'protocol': 1, 'monitors': [], 'monitor_token': 'm'*64}
    manager._post = post
    thread = threading.Thread(target=worker.tick, args=(manager,))
    thread.start()
    try:
        assert entered.wait(2)
        from core import cloud_config
        for lock in [manager.lock, manager.network_lock, cloud_config._lock]:
            assert lock.acquire(timeout=.2)
            lock.release()
    finally:
        finish.set()
        thread.join(5)
    assert not thread.is_alive()


def test_outbox_bounds_ack_and_data_directory_redirect(monitor, tmp_path, monkeypatch):
    manager, worker, original = monitor
    target = tmp_path / 'moved'
    target.mkdir()
    from core import paths
    (target / 'config.json').write_text('{}')
    (original / paths.DATA_REDIRECT).write_text(json.dumps({'data_dir': str(target)}))
    monkeypatch.setattr(M, 'MAX_ROWS', 2)
    for i in range(5):
        M.store(original, 'alice', dict(result(), event_id=str(i)))
    assert len(pending(original)) == 2 and not (original/'usage-outbox.sqlite3').exists()
    manager._post = lambda *args: {'accepted': []}
    with pytest.raises(ValueError):
        M.send(manager, M.identity(manager))
    assert len(pending(original)) == 2


def test_invalid_remote_spec_and_manual_debounce(monitor):
    manager, worker, _ = monitor
    for change in [{'url': 'https://127.0.0.1/'}, {'kind': 'generate'}, {'auth': 'cookie'}, {'revision': 'bad'}]:
        with pytest.raises(ValueError):
            M.validate_config({'protocol': 1, 'monitors': [dict(spec(), **change)]})
    assert worker.trigger() and not worker.trigger()
    assert worker.fetch_at == worker.probe_at == 0
