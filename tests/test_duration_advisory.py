"""Stale 30-second catalogs must not shorten a requested 60-second video."""
import copy
from pathlib import Path
import shutil
import subprocess

import pytest

from core import model_catalog
from core.providers.base import VideoTask
from core.providers.paisio import PaisioProvider
from core.video_duration import positive_seconds
from test_agent_workspace import A, R, CAPS, pj, publish, board_plan, resolve, write_json
from test_lensforge import _provider


@pytest.mark.parametrize('seconds', [30, 60, 120, 601])
def test_requested_seconds_survive_old_hints_preview_and_request(pj, seconds):
    publish(pj, board_plan(pj)); A.scan(pj)
    saved = R.settings(pj, CAPS)
    saved.update(R.validate_settings({'video': dict(saved['video'], duration=str(seconds))}, CAPS))
    write_json(pj.p('07_检查与记录', 'production-settings.json'), saved)
    plan = R.preview(pj, {}, CAPS, resolve)
    assert not plan['blocked']
    video = next(t for t in plan['_tasks'] if t['kind'] == 'video')
    body = PaisioProvider._video_body(VideoTask('test', duration=video['params']['duration']), 'paisio-seedance-2.5-720p')
    assert body['duration'] == seconds


def test_catalog_refresh_changes_hints_but_does_not_change_saved_choice(pj):
    publish(pj, board_plan(pj)); A.scan(pj)
    cap = model_catalog.apply_catalog(copy.deepcopy(CAPS[0]), {'rows': [
        {'id': 'test-video', 'durations_seconds': [60]}]}, 'test refresh')
    assert cap['video']['model_options']['test-video']['durations'] == [60]
    saved = R.settings(pj, [cap])
    saved['video']['duration'] = 30
    write_json(pj.p('07_检查与记录', 'production-settings.json'), saved)
    plan = R.preview(pj, {}, [cap], resolve)
    assert next(t for t in plan['_tasks'] if t['kind'] == 'video')['params']['duration'] == 30
    saved['video']['duration'] = ''
    write_json(pj.p('07_检查与记录', 'production-settings.json'), saved)
    plan = R.preview(pj, {}, [cap], resolve)
    expected = board_plan(pj)['video_tasks'][0]['params']['duration']
    assert next(t for t in plan['_tasks'] if t['kind'] == 'video')['params']['duration'] == expected


@pytest.mark.parametrize('schema,expected', [
    ({'duration': {'min': 30, 'max': 60}}, list(range(30, 61))),
    ({'duration': {'values': [30, 60]}}, [30, 60]),
    ({'durations': [60, 120]}, [60, 120]),
])
def test_live_duration_declarations_remain_available(schema, expected):
    assert model_catalog._from_schema(schema)['durations'] == expected


@pytest.mark.parametrize('bad', [0, -1, True, 1.5, '1.5', 'nan', float('inf')])
def test_only_positive_integers_are_accepted_even_in_advanced_override(bad):
    with pytest.raises(ValueError, match='正整数'):
        positive_seconds(bad)
    with pytest.raises(ValueError, match='正整数'):
        R.validate_settings({'video': {'provider': 'chaomo', 'model': 'test-video', 'override': {'duration': bad}}}, CAPS)


@pytest.mark.parametrize('mode', ['fixed', 'model'])
def test_non_adjustable_protocols_have_honest_plans_and_reject_stale_override(pj, mode):
    caps = copy.deepcopy(CAPS)
    caps[0]['video'].update(duration_mode=mode, fixed_duration=15)
    publish(pj, board_plan(pj)); A.scan(pj)
    plan = R.preview(pj, {}, caps, resolve)
    row = next(r for r in plan['rows'] if r['kind'] == 'video')
    assert ('固定 15 秒' if mode == 'fixed' else '由模型 / 服务商决定') in row['duration_note']
    with pytest.raises(ValueError, match='清空旧的秒数覆盖'):
        R.validate_settings({'video': dict(row, duration=60)}, caps)
    saved = R.settings(pj, caps)
    saved['video']['duration'] = 60  # old disk settings must also be checked
    write_json(pj.p('07_检查与记录', 'production-settings.json'), saved)
    assert any('清空旧的秒数覆盖' in b['reason'] for b in R.preview(pj, {}, caps, resolve)['blocked'])


@pytest.mark.parametrize('seconds', [1.25, 45.75])
@pytest.mark.parametrize('supplied', [True, False])
def test_reference_media_duration_matches_unchanged_url(monkeypatch, seconds, supplied):
    monkeypatch.setattr('core.providers.lensforge._measured_seconds', lambda url: seconds)
    calls = []
    p = _provider(calls)
    extra = {'video_refs': ['https://cdn.example/ref.mp4'], 'audio_refs': ['https://cdn.example/ref.wav']}
    if supplied:
        extra.update(video_durations=[seconds], audio_durations=[seconds])
    p.generate_video(VideoTask('test', duration=60, model='渠道3-seedance2.0（支持视频参考）', extra=extra), 'out.mp4')
    body = next(kw['json_body'] for _, path, kw in calls if path == '/api/v1/client/tasks')
    assert body['duration'] == 60
    assert [m['durationSeconds'] for m in body['materials']] == [seconds, seconds]
    assert [m['url'] for m in body['materials']] == extra['video_refs'] + extra['audio_refs']


@pytest.mark.parametrize('bad', [0, -1, True, 'bad', float('nan'), float('inf')])
def test_invalid_reference_duration_never_creates_task(bad):
    calls = []
    p = _provider(calls)
    with pytest.raises(Exception, match='真实时长无效'):
        p.generate_video(VideoTask('test', model='渠道3-seedance2.0（支持视频参考）', extra={
            'video_refs': ['https://cdn.example/ref.mp4'], 'video_durations': [bad]}), 'out.mp4')
    assert not any(method == 'POST' for method, _, _ in calls)


def test_duration_controls_allow_values_beyond_hints_and_identify_fixed_protocols():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for actual UI function test')
    root = Path(__file__).resolve().parents[1]
    source = (root/'web/agent.js').read_text(encoding='utf-8')
    fn = source[source.index('function serviceRow('):source.index('function editChain(')]
    legacy = (root/'web/index.html').read_text(encoding='utf-8')
    legacy_fn = legacy[legacy.index('function paramField('):legacy.index('// ---------- tabs')]
    script = r'''
const assert=require('node:assert/strict');
const esc=v=>String(v),option=(v,label,cur)=>`<option value="${v}"${String(v)===String(cur)?' selected':''}>${label}</option>`;
const cap={id:'test',supports:['video'],video:{models:['m'],durations:[30]}};
const row={provider:'test',model:'m',duration:120,concurrency:1};
const S={boot:{capabilities:[cap],features:{}},data:{production:{video:row}}};
const durationsOf=()=>cap.video.durations;
''' + fn + r'''
let html=serviceRow('video','video');
assert(html.includes('value="120"'));assert(html.includes('参考 30'));assert(!html.includes('max="'));
cap.video.durations=[60];html=serviceRow('video','video');
assert(html.includes('参考 60'));assert(html.includes('value="120"'));
for(const mode of ['model','fixed']){
 cap.video.duration_mode=mode;cap.video.fixed_duration=15;
 html=serviceRow('video','video');
 assert(html.includes('旧覆盖 120 秒，请清空'));assert(!html.includes('type="number" min="1" step="1" inputmode'));
 assert(html.includes(mode==='fixed'?'接口固定 15 秒':'由模型 / 服务商决定'));
}
''' + legacy_fn + r'''
const field=paramField('duration','秒数','number',120,'data-param');
assert(field.includes('value="120"'));assert(!field.includes('<select'));assert(!field.includes('max="'));
'''
    result = subprocess.run([node], input=script, encoding='utf-8', capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
