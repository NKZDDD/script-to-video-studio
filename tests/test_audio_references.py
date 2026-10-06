import io
from pathlib import Path
import wave
from types import SimpleNamespace

import pytest

from core import agent_projects as A, audio_refs, produce, recycle, uploader
from core.store import Project, write_json, write_text
from core.providers import VideoTask


@pytest.fixture
def project(tmp_path):
    pj = Project(str(tmp_path))
    row = {'key': 'EP01-SEG01', 'kind': 'video', 'output': '05_分段视频/test.mp4',
           'prompt_ref': '03_提示词/test.txt', 'params': {'duration': 10, 'ratio': '16:9'},
           'handoff_refs': [], 'reference_images': []}
    write_json(pj.p('03_提示词', 'tasks.json'), {'video_tasks': [row]})
    write_text(pj.p('03_提示词', 'test.txt'), 'video')
    return pj, row


def wav_bytes():
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(8000); f.writeframes(b'\0\0' * 800)
    return stream.getvalue()


def fake_decode(monkeypatch):
    monkeypatch.setattr(audio_refs.probe, 'find_ffmpeg', lambda: 'ffmpeg')
    monkeypatch.setattr(audio_refs.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0))


def test_audio_binding_survives_reload_and_removal_preserves_snapshot(project, monkeypatch):
    pj, row = project
    fake_decode(monkeypatch)
    before = Path(pj.p('03_提示词', 'tasks.json')).read_bytes()
    result = audio_refs.save(pj, row['key'], '人物声音.wav', wav_bytes())
    snap = audio_refs.overlay(pj, row)
    ref = result['reference_audios'][0]['file_ref']
    assert len(audio_refs.overlay(Project(pj.root), row)['reference_audios']) == 1
    audio_refs.save(pj, row['key'], '重复名字.wav', wav_bytes())
    assert len(audio_refs.overlay(pj, row)['reference_audios']) == 1
    audio_refs.remove(pj, row['key'], ref)
    assert audio_refs.overlay(pj, row)['reference_audios'] == []
    assert audio_refs.overlay(pj, snap)['reference_audios'][0]['file_ref'] == ref
    assert A.inside(pj.root, ref).read_bytes() == wav_bytes()
    assert Path(pj.p('03_提示词', 'tasks.json')).read_bytes() == before


def test_decode_failure_never_binds_or_keeps_invalid_audio(project, monkeypatch):
    pj, row = project
    fake_decode(monkeypatch)
    monkeypatch.setattr(audio_refs.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1))
    with pytest.raises(ValueError, match='无法解码'):
        audio_refs.save(pj, row['key'], 'fake.wav', b'<html>error</html>')
    assert audio_refs.refs(audio_refs.overlay(pj, row)) == []
    assert list(A.inside(pj.root, audio_refs.FOLDER).iterdir()) == []


def test_real_audio_decoder_accepts_wav_and_rejects_html(project):
    if not audio_refs.probe.find_ffmpeg():
        pytest.skip('FFmpeg unavailable')
    pj, row = project
    assert audio_refs.save(pj, row['key'], 'voice.wav', wav_bytes())['ok']
    with pytest.raises(ValueError, match='无法解码'):
        audio_refs.save(pj, row['key'], 'fake.mp3', b'<html>error</html>')


def test_agent_start_freezes_audio_in_persisted_snapshot(project, monkeypatch):
    from core import agent_runtime as R
    from core.executor import JobManager
    from core.store import read_json
    from test_agent_workspace import CAPS, resolve
    import copy
    pj, row = project
    fake_decode(monkeypatch)
    audio_refs.save(pj, row['key'], 'voice.wav', wav_bytes())
    caps = copy.deepcopy(CAPS)
    caps[0]['video']['max_audio_refs'] = 3
    write_json(pj.p('07_检查与记录', 'production-settings.json'),
               {'video': {'provider':'chaomo','model':'test-video','concurrency':1}})
    cfg = {'upload': {'bucket':'test','access_key':'test','secret_key':'test'}}
    preview = R.preview(pj, cfg, caps, resolve)
    assert not preview['blocked'], preview['blocked']
    monkeypatch.setattr(R.threading.Thread, 'start', lambda self: None)
    result = R.start(pj, cfg, caps, resolve, JobManager())
    snapshot = read_json(pj.p('07_检查与记录', 'jobs', result['job_id'], 'snapshot.json'))
    frozen = snapshot['tasks'][0]
    ref = frozen['reference_audios'][0]['file_ref']
    audio_refs.remove(pj, row['key'], ref)
    assert audio_refs.overlay(pj, frozen)['reference_audios'][0]['file_ref'] == ref
    assert frozen['_audio_capability']['max_audio_refs'] == 3


def test_audio_capability_storage_and_path_guards(project):
    pj, row = project
    row = dict(row, reference_audios=[{'file_ref': 'https://example.com/a.mp3'}])
    with pytest.raises(ValueError, match='未声明'):
        audio_refs.validate(pj, row, {}, {})
    with pytest.raises(ValueError, match='最多支持 0'):
        audio_refs.validate(pj, row, {'max_audio_refs': 0}, {})
    audio_refs.validate(pj, row, {'max_audio_refs': 1}, {})
    row['reference_audios'] *= 2
    with pytest.raises(ValueError, match='本任务有 2'):
        audio_refs.validate(pj, row, {'max_audio_refs': 1}, {})
    row['reference_audios'] = ['../outside.wav']
    with pytest.raises(ValueError):
        audio_refs.validate(pj, row, {'max_audio_refs': 1}, {})
    A.inside(pj.root, 'audio.wav').write_bytes(wav_bytes())
    row['reference_audios'] = ['audio.wav']
    with pytest.raises(ValueError, match='对象存储'):
        audio_refs.validate(pj, row, {'max_audio_refs': 1}, {})


def test_audio_upload_preserves_bytes_and_cache_is_storage_scoped(project, monkeypatch):
    pj, row = project
    A.inside(pj.root, 'voice.wav').write_bytes(wav_bytes())
    row = dict(row, reference_audios=['voice.wav'])
    calls = []
    def put(cfg, data, key):
        calls.append((cfg, data, key))
        return 'https://' + cfg['bucket'] + '/' + key
    monkeypatch.setattr(uploader, 'put', put)
    a = audio_refs.resolve(pj, row, {'bucket':'one'}, lambda _: None)
    assert audio_refs.resolve(pj, row, {'bucket':'one'}, lambda _: None) == a
    b = audio_refs.resolve(pj, row, {'bucket':'two'}, lambda _: None)
    assert len(calls) == 2 and a != b and calls[0][1] == wav_bytes()


def test_video_worker_passes_ordered_audio_urls_to_provider(project, monkeypatch):
    pj, row = project
    seen = []
    class Provider:
        def needs_url(self, *a): return False
        def needs_bytes(self, *a): return False
        def accepts_url(self, *a): return True
        def capabilities(self): return {'video': {'max_audio_refs': 3}}
        def generate_video(self, task, dest, **kwargs):
            seen.append(task)
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(b'video')
            return {'provider': 'fake'}
    monkeypatch.setattr(produce, 'build_provider', lambda *a: Provider())
    monkeypatch.setattr(produce, '_fit_size', lambda *a: '16:9')
    monkeypatch.setattr(produce, '_ratio_warn', lambda *a, **k: '')
    monkeypatch.setattr(produce.ledger, 'record', lambda *a, **k: None)
    monkeypatch.setattr(produce.probe, 'have_output', lambda path: Path(path).is_file())
    urls = ['https://cdn.example/one.wav', 'https://cdn.example/two.mp3']
    row = dict(row, reference_audios=urls)
    worker = produce.make_video_worker(pj, {'provider':'fake','api_key':'test','model':'fake','agent_mode':True,'soften_rounds':0})
    worker(row, lambda _: None, lambda: False)
    assert seen[0].extra['audio_refs'] == urls


def test_supported_adapters_declare_audio_caps_and_do_not_clip_selected_audio():
    from core.providers.aicopy import AicopyProvider
    provider = AicopyProvider()
    assert provider.capabilities()['video']['model_options']['sd2.5-720均衡版']['max_audio_refs'] == 10
    _, body, _ = provider.build_video_body(VideoTask('p', model='sd2.5-720均衡版', extra={'audio_refs':['https://cdn.example/a.wav']}))
    assert body['extra']['reference_audios'] == [{'url':'https://cdn.example/a.wav'}]


def test_recycle_only_moves_chosen_file_not_its_unselected_children(project):
    pj, _ = project
    rows = [{'key':'a','output':'a.png','reference_images':[]},
            {'key':'b','output':'b.png','reference_images':[{'file_ref':'a.png'}]}]
    write_json(pj.p('03_提示词','tasks.json'), {'asset_tasks':rows})
    for t in rows: A.inside(pj.root,t['output']).write_bytes(b'keep me')
    result = recycle.delete(pj, ['a'], running={'b':object()})
    assert [r['key'] for r in result['moved']] == ['a']
    assert A.inside(pj.root, 'b.png').read_bytes() == b'keep me'
