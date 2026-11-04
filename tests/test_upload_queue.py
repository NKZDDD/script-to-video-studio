"""Upload scheduling uses real worker threads and fake transports, without paid requests."""
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from core import cloud_storage, uploader
from core.apiutil import ApiError
from core.store import LOCK, read_json


LOCAL = dict(endpoint='https://storage.example.test', bucket='refs', prefix='images',
             public_base_url='https://cdn.example.test', access_key='test-access',
             secret_key='test-secret')


@pytest.fixture(autouse=True)
def clean_uploads(monkeypatch):
    manager = object()
    cloud_storage.install(manager, 'test-user', None)
    uploader._MEM.clear()
    monkeypatch.setattr(uploader, '_UPLOAD_SLOTS', uploader.UploadSlots(4))
    monkeypatch.setattr(uploader, 'encode_ref',
                        lambda path, **kw: (open_bytes(path), 'image/png', '.png', 'original'))
    yield
    assert not uploader._FLIGHTS
    cloud_storage.clear(manager)
    uploader._MEM.clear()


def open_bytes(path):
    with open(path, 'rb') as source:
        return source.read()


def image(tmp_path, name='ref.png', data=b'image'):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def upload(path, root='', cfg=None, cancel=None, **kw):
    return uploader.to_url(path, LOCAL if cfg is None else cfg, project_root=str(root),
                           log=lambda *a: None, cancel=cancel, **kw)


def observe_waiters(monkeypatch, count):
    original = uploader.wait
    joined, ready, guard = set(), threading.Event(), threading.Lock()

    def wait(*a, **kw):
        with guard:
            joined.add(threading.get_ident())
            if len(joined) >= count:
                ready.set()
        return original(*a, **kw)

    monkeypatch.setattr(uploader, 'wait', wait)
    return ready


def observe_upload_entry(monkeypatch):
    entered = threading.Event()
    original = uploader._upload_slot

    def slot(cancel):
        entered.set()
        return original(cancel)

    monkeypatch.setattr(uploader, '_upload_slot', slot)
    return entered


def test_many_submissions_refill_upload_window_without_blocking_project_lock(monkeypatch, tmp_path):
    paths = [image(tmp_path, f'{i}.png', str(i).encode()) for i in range(12)]
    release = [threading.Event() for _ in paths]
    first_window, next_started = threading.Event(), threading.Event()
    guard = threading.Lock()
    active = peak = started = 0
    started_indices = []
    encoded = []
    original_encode = uploader.encode_ref

    def encode(*a, **kw):
        encoded.append(a[0])
        return original_encode(*a, **kw)

    monkeypatch.setattr(uploader, 'encode_ref', encode)

    def put_object(**kw):
        nonlocal active, peak, started
        index = int(kw['Body'])
        with guard:
            active += 1
            peak = max(active, peak)
            started += 1
            started_indices.append(index)
            if started == 4:
                first_window.set()
            if started == 5:
                next_started.set()
        try:
            assert release[index].wait(8)
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(uploader, '_client', lambda cfg: SimpleNamespace(put_object=put_object))
    with ThreadPoolExecutor(max_workers=12) as pool:
        # Submit the whole selection at once; no client-visible batch-size rejection.
        futures = [pool.submit(upload, p, tmp_path) for p in paths]
        try:
            assert first_window.wait(5)
            assert started == 4 and active == 4
            assert len(encoded) == 4, 'queued images must not accumulate encoded byte buffers'
            # Other tasks may briefly read the cache. They must release the lock
            # while the four fake network transfers remain blocked for 8 seconds.
            assert LOCK.acquire(timeout=2), 'network I/O must not hold the project lock'
            LOCK.release()
            # A free slot must refill before the rest of the first group finishes.
            release[started_indices[0]].set()
            assert next_started.wait(5)
        finally:
            for event in release:
                event.set()
        assert all(f.result(timeout=5).startswith(LOCAL['public_base_url']) for f in futures)
    assert peak == 4 and started == 12 and active == 0


def test_same_content_across_projects_uploads_once_and_caches_for_every_project(monkeypatch, tmp_path):
    path1, path2 = image(tmp_path, 'a.png'), image(tmp_path, 'b.png')
    joined = observe_waiters(monkeypatch, 11)
    started, release = threading.Event(), threading.Event()
    calls = []

    def put(cfg, data, key):
        calls.append(key)
        started.set()
        assert release.wait(8)
        return 'https://cdn.example.test/shared.png'

    monkeypatch.setattr(uploader, '_put', put)
    roots = [tmp_path / f'project-{i}' for i in range(12)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(upload, path1 if i % 2 else path2, root)
                   for i, root in enumerate(roots)]
        try:
            assert started.wait(5) and joined.wait(5)
            assert len(calls) == 1
        finally:
            release.set()
        urls = [f.result(timeout=5) for f in futures]
    assert len(set(urls)) == 1 and len(calls) == 1
    for root in roots:
        cache = read_json(uploader._cache_path(str(root)))
        assert list(cache.values()) == [urls[0]]
    uploader._MEM.clear()
    assert upload(path1, roots[-1]) == urls[0] and len(calls) == 1


def test_failure_is_shared_releases_slot_and_next_attempt_can_retry(monkeypatch, tmp_path):
    path = image(tmp_path)
    joined = observe_waiters(monkeypatch, 5)
    release = threading.Event()
    calls = []
    monkeypatch.setattr(uploader, '_UPLOAD_SLOTS', uploader.UploadSlots(1))

    def put(cfg, data, key):
        calls.append(key)
        assert release.wait(8)
        if len(calls) == 1:
            raise ApiError('R2 temporary timeout')
        return 'https://cdn.example.test/retry.png'

    monkeypatch.setattr(uploader, '_put', put)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(upload, path, tmp_path) for _ in range(6)]
        try:
            assert joined.wait(5)
        finally:
            release.set()
        for f in futures:
            with pytest.raises(ApiError, match='temporary timeout'):
                f.result(timeout=5)
    assert len(calls) == 1 and not uploader._MEM and not uploader._FLIGHTS
    assert upload(path, tmp_path).endswith('/retry.png')
    assert len(calls) == 2


def test_cancelled_follower_does_not_cancel_shared_upload(monkeypatch, tmp_path):
    path = image(tmp_path)
    joined = observe_waiters(monkeypatch, 1)
    started, release, cancel = threading.Event(), threading.Event(), threading.Event()

    def put(*args):
        started.set()
        assert release.wait(8)
        return 'https://cdn.example.test/ok.png'

    monkeypatch.setattr(uploader, '_put', put)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(upload, path)
        try:
            assert started.wait(5)
            follower = pool.submit(upload, path, cancel=cancel.is_set)
            assert joined.wait(5)
            cancel.set()
            with pytest.raises(ApiError, match='取消'):
                follower.result(timeout=3)
            assert not owner.done()
        finally:
            release.set()
        assert owner.result(timeout=5).endswith('/ok.png')


def test_cancelled_queued_owner_allows_other_task_to_take_over(monkeypatch, tmp_path):
    path = image(tmp_path)
    joined = observe_waiters(monkeypatch, 1)
    slots = uploader.UploadSlots(1)
    slots.acquire()
    monkeypatch.setattr(uploader, '_UPLOAD_SLOTS', slots)
    cancelled = threading.Event()
    calls = []
    monkeypatch.setattr(uploader, '_put', lambda *a: calls.append(a) or 'https://cdn.example.test/ok.png')
    # Ensure the first thread has registered its flight before starting the follower.
    queued = observe_upload_entry(monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(upload, path, cancel=cancelled.is_set)
        try:
            assert queued.wait(5)
            follower = pool.submit(upload, path)
            assert joined.wait(5)
            cancelled.set()
            with pytest.raises(ApiError, match='取消'):
                owner.result(timeout=3)
            assert not calls
        finally:
            slots.release()
        assert follower.result(timeout=5).endswith('/ok.png')
    assert len(calls) == 1


@pytest.mark.parametrize('change', [dict(bucket='other'), dict(endpoint='https://other.test'),
    dict(public_base_url='https://other-cdn.test'), dict(prefix='other'), dict(access_key='other')])
def test_local_destination_and_credentials_do_not_share_cache(monkeypatch, tmp_path, change):
    path = image(tmp_path)
    calls = []
    monkeypatch.setattr(uploader, '_put', lambda *a: calls.append(a) or f'https://cdn.test/{len(calls)}')
    first = upload(path, tmp_path)
    assert upload(path, tmp_path, cfg=dict(LOCAL, **change)) != first
    assert len(calls) == 2
    cache = open_bytes(uploader._cache_path(str(tmp_path)))
    assert b'test-secret' not in cache and b'test-access' not in cache


def test_changed_processing_parameters_do_not_share_upload(monkeypatch, tmp_path):
    path = image(tmp_path)
    calls = []
    monkeypatch.setattr(uploader, '_put', lambda *a: calls.append(a) or f'https://cdn.test/{len(calls)}')
    assert upload(path) != upload(path, fmt='jpeg')
    assert upload(path, max_side=1024) != upload(path)
    assert len(calls) == 3


@pytest.mark.parametrize('change_owner', [True, False])
def test_managed_queue_rejects_account_or_revision_switch(monkeypatch, tmp_path, change_owner):
    manager = SimpleNamespace(storage_ticket=lambda *a: pytest.fail('must not sign'))
    config = dict(assigned=True, available=True, revision='a'*32,
        endpoint='https://test.r2.cloudflarestorage.com', bucket='test',
        public_base_url='https://cdn.test', prefix='account-a')
    cloud_storage.install(manager, 'account-a', config)
    slots = uploader.UploadSlots(1)
    slots.acquire()
    monkeypatch.setattr(uploader, '_UPLOAD_SLOTS', slots)
    queued = observe_upload_entry(monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(upload, image(tmp_path), cfg={})
        try:
            assert queued.wait(5)
            if change_owner:
                cloud_storage.install(manager, 'account-b', dict(config, prefix='account-b'))
            else:
                cloud_storage.install(manager, 'account-a', dict(config, revision='b'*32))
        finally:
            slots.release()
        with pytest.raises(ApiError, match='账号|配置'):
            future.result(timeout=5)
    cloud_storage.clear(manager)


def test_managed_signatures_and_put_requests_share_one_upload_window(monkeypatch, tmp_path):
    import requests

    config = dict(assigned=True, available=True, revision='a'*32,
        endpoint='https://test.r2.cloudflarestorage.com', bucket='test',
        public_base_url='https://cdn.test', prefix='account-a')
    signs, puts = [], []
    guard, started, release = threading.Lock(), threading.Event(), threading.Event()

    def ticket(body, owner):
        with guard:
            signs.append(body['sha256'])
        return dict(upload_url=config['endpoint']+'/test/'+body['sha256']+'?signature=test',
                    public_url=config['public_base_url']+'/account-a/'+body['sha256'],
                    bytes=body['bytes'], revision=config['revision'],
                    headers={'Content-Type': body['content_type']})

    def put(url, **kw):
        with guard:
            puts.append(url)
            if len(puts) == 4:
                started.set()
        assert release.wait(8)
        assert kw['timeout'] == (15, 90)
        return SimpleNamespace(status_code=200, close=lambda: None)

    manager = SimpleNamespace(storage_ticket=ticket)
    cloud_storage.install(manager, 'account-a', config)
    monkeypatch.setattr(requests, 'put', put)
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(upload, image(tmp_path, f'{i}.png', str(i).encode()), cfg={})
                   for i in range(10)]
        try:
            assert started.wait(5)
            assert len(signs) == len(puts) == 4
        finally:
            release.set()
        assert len({f.result(timeout=5) for f in futures}) == 10
    assert len(signs) == len(puts) == 10
    cloud_storage.clear(manager)
