from pathlib import Path

import pytest

from core import agent_projects as A, produce, video_inputs
from core.providers.cangyuan_video import CangyuanVideoProvider
from core.store import read_json, write_json
from test_audio_references import project


def test_links_survive_reload_and_removal_does_not_change_running_snapshot(project):
    pj, row = project
    a, b = 'https://example.test/one.mp4', 'https://example.test/two.mp4'
    before = Path(pj.p('03_提示词', 'tasks.json')).read_bytes()
    video_inputs.update(pj, row['key'], a)
    video_inputs.update(pj, row['key'], b)
    video_inputs.update(pj, row['key'], a)
    snapshot = video_inputs.overlay(pj, row)
    assert video_inputs.urls(snapshot) == [a, b]
    video_inputs.update(pj, row['key'], a, remove=True)
    assert video_inputs.urls(video_inputs.overlay(pj, row)) == [b]
    assert video_inputs.urls(video_inputs.overlay(pj, snapshot)) == [a, b]
    assert Path(pj.p('03_提示词', 'tasks.json')).read_bytes() == before


@pytest.mark.parametrize('url', ['', 'file:///C:/video.mp4', 'http://example.test/a.mp4',
                                  'https://', 'https://user:password@example.test/a.mp4'])
def test_invalid_urls_not_persisted(project, url):
    pj, row = project
    with pytest.raises(ValueError):
        video_inputs.update(pj, row['key'], url)
    assert video_inputs.urls(video_inputs.overlay(pj, row)) == []


def test_limits_required_video_and_unsupported_provider():
    row = {'reference_videos': ['https://example.test/a.mp4']}
    with pytest.raises(ValueError):
        video_inputs.validate(row, {})
    with pytest.raises(ValueError):
        video_inputs.validate(row, {'max_video_refs': 0, 'reference_video_urls_supported': True})
    with pytest.raises(ValueError):
        video_inputs.validate({}, {'min_video_refs': 1, 'reference_video_urls_supported': True})
    video_inputs.validate(row, {'min_video_refs': 1, 'max_video_refs': 2, 'reference_video_urls_supported': True})


def test_video_links_are_frozen_in_job_snapshot(project, monkeypatch):
    from core import agent_runtime as R
    from core.executor import JobManager
    from test_agent_workspace import resolve
    pj, row = project
    url = 'https://example.test/guide.mp4'
    video_inputs.update(pj, row['key'], url)
    cap = CangyuanVideoProvider().capabilities()
    write_json(pj.p('07_检查与记录', 'production-settings.json'),
               {'video': {'provider': 'cangyuan_video', 'model': 'sd11-seedance-2.5', 'concurrency': 1}})
    preview = R.preview(pj, {}, [cap], resolve)
    assert not preview['blocked'], preview['blocked']
    monkeypatch.setattr(R.threading.Thread, 'start', lambda self: None)
    result = R.start(pj, {}, [cap], resolve, JobManager())
    snapshot = read_json(pj.p('07_检查与记录', 'jobs', result['job_id'], 'snapshot.json'))
    frozen = snapshot['tasks'][0]
    video_inputs.update(pj, row['key'], url, remove=True)
    assert video_inputs.urls(video_inputs.overlay(pj, frozen)) == [url]
    assert frozen['_video_capability']['max_video_refs'] == 10


def test_worker_transmits_selected_video_links_in_order(project, monkeypatch):
    pj, row = project
    p = CangyuanVideoProvider()
    bodies = []
    def request(method, path, **kw):
        if method == 'POST':
            bodies.append(kw['json_body'])
            return {'id': 'task-existing', 'status': 'queued'}
        return {'status': 'completed', 'video_url': 'https://example.test/result.mp4'}
    def save(url, dest, **kw):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b'video')
    monkeypatch.setattr(p.session, 'request', request)
    monkeypatch.setattr(p.session, 'save_item', save)
    monkeypatch.setattr(produce, 'build_provider', lambda *a: p)
    monkeypatch.setattr(produce, '_fit_size', lambda *a: '16:9')
    monkeypatch.setattr(produce, '_ratio_warn', lambda *a, **k: '')
    monkeypatch.setattr(produce.ledger, 'record', lambda *a, **k: None)
    monkeypatch.setattr(produce.probe, 'have_output', lambda path: Path(path).is_file())
    urls = ['https://example.test/a.mp4', 'https://example.test/b.mp4']
    for url in urls:
        video_inputs.update(pj, row['key'], url)
    worker = produce.make_video_worker(pj, {'provider':p.id, 'api_key':'test', 'model':'sd11-seedance-2.5',
                                          'agent_mode':True, 'soften_rounds':0})
    worker(row, lambda _: None, lambda: False)
    assert len(bodies) == 1 and bodies[0]['reference_videos'] == urls
