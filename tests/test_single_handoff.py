import copy
import json
from pathlib import Path
import threading
import time
import zipfile

import pytest

from core import agent_projects as A, agent_runtime as R, agent_handoff as H
from core import matimport as M, matspec, single_handoff as S, explorer, pipeline, pipeline_v34
from core.executor import JobManager
from core.store import write_json, write_text
from test_agent_workspace import pj, publish, image, resolve, CAPS
from test_handoff_production import setup, put, run

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples/独立C-独立AB-生产材料.jsonl'


def rows():
    return [json.loads(line) for line in EXAMPLE.read_text(encoding='utf8').splitlines()]


def parsed(data):
    return M.parse('\n'.join(json.dumps(r, ensure_ascii=False) for r in data))


def plan(pj, data=None):
    built = M.build(parsed(data or rows()), system='v34')
    p = {'schema': A.SCHEMA, 'project_id': pj.meta()['project_code'], 'revision': 'r0001', 'resources': [],
         **{kind + '_tasks': built['tasks'][kind + '_tasks'] for kind in A.KINDS}}
    texts = {}
    for kind in A.KINDS:
        for t in p[kind + '_tasks']:
            texts[t['key']] = built['prompts'][t['prompt_ref']]
            t['prompt_ref'] = '03_提示词/releases/r0001/images/' + t['key'] + '.txt'
            for h in t.get('handoff_refs', []):
                h['asset_id'], h['image_n'] = h.pop('sheet_id'), h.pop('order')
            for old in ('storyboard_ref', 'storyboard_refs', 'no_image_refs', 'from_material'):
                t.pop(old, None)
            p['resources'].append(dict(id=t['key'], name=t['key'], semantic={'handoff':'HANDOFF', 'asset':'CHAR', 'video':'VIDEO'}[kind],
                                       parent_id=None, episodes=[t['episode']] if t.get('episode') else [], task_key=t['key']))
    folder, ready = publish(pj, p)
    for t in A.task_rows(p):
        rel = t['prompt_ref'].split('r0001/', 1)[1]
        (folder / rel).write_text(texts[t['key']], encoding='utf8')
        ready['files'][rel] = A.digest((folder / rel).read_bytes())
    write_json(str(folder / 'READY.json'), ready)
    return p


@pytest.mark.parametrize('system', ['v34', 'v61'])
def test_jsonl_new_group_and_both_production_paths(setup, system):
    pj, _ = setup
    units = parsed(rows())
    assert M.audit(units) == []
    built = M.build(units, system=system)['tasks']
    assert len(built['handoff_tasks']) == 3
    assert built['scstate_tasks'] == built['board_tasks'] == built['region_tasks'] == []
    assert [t['view'] for t in built['handoff_tasks']] == ['C', 'A', 'B']
    pj.save_meta({'system': system}); pj.save_tasks(built)
    assert any(k[0] == 'handoff_tasks' for k in explorer._task_kinds(pj))
    runner = pipeline_v34 if system == 'v34' else pipeline
    steps = runner.plan(pj, include_llm=False, include_deliver=False)
    assert any(s.get('task_key') == 'handoff_tasks' for s in steps)


def test_release_contract_and_dependencies(pj):
    p = plan(pj)
    state = A.view(pj)
    assert not state['publication']['errors']
    c, a, b = p['handoff_tasks']
    bykey = {t['key']: t for t in state['tasks']}
    assert bykey[a['key']]['dependencies'] == bykey[b['key']]['dependencies'] == [c['key']]
    assert bykey['EP01-SEG02']['dependencies'] == [b['key'], c['key']]
    assert S.RULES in matspec.render(system='v34')
    with zipfile.ZipFile(H.export_package(pj)['file']) as archive:
        for name in ['生产材料契约.md', '00_项目说明/production-contract.md']:
            assert S.RULES in archive.read(name).decode('utf8')
    assert 'handoff_tasks' not in A.schema_document()['required']


@pytest.mark.parametrize('case,code', [
    ('board', 'SINGLE_HANDOFF_SHAPE'), ('view', 'SINGLE_HANDOFF_SHAPE'),
    ('c_key', 'SINGLE_C_SOURCE'), ('no_source', 'SINGLE_C_SOURCE'),
    ('pair', 'SINGLE_PAIR'), ('boundary', 'SINGLE_BOUNDARY'),
    ('b_from_a', 'SINGLE_HANDOFF_DOWNSTREAM'), ('c_from_b', 'SINGLE_HANDOFF_DOWNSTREAM'),
    ('wrong_role', 'SINGLE_VIDEO_ROLE'), ('duplicate', 'SINGLE_VIDEO_ROLE'),
    ('container', 'SINGLE_ASPECT'),
])
def test_jsonl_invalid_graph_is_rejected(case, code):
    data = rows(); c, a, b = data[1:4]
    if case == 'board': a['board'] = c['key']
    if case == 'view': a['view'] = 'AB'
    if case == 'c_key': b['c_key'] = 'not-the-same-c'
    if case == 'no_source': b['reference_images'] = []
    if case == 'pair': b['view'] = 'A'
    if case == 'boundary': b.pop('boundary')
    if case == 'b_from_a': b['reference_images'].append({'key': a['key'], 'image_n': 2})
    if case == 'c_from_b': c['reference_images'] = [{'key': b['key'], 'image_n': 1}]
    if case == 'wrong_role': data[-1]['handoff_refs'][1]['role'] = 'OUT_A'
    if case == 'duplicate': data[-1]['reference_images'] = [{'key': c['key'], 'image_n': 3}]
    if case == 'container': c['size'] = '1:1'
    assert code in {i['code'] for i in M.audit(parsed(data))}


@pytest.mark.parametrize('case', ['board', 'wrong_group', 'wrong_mode', 'source', 'role', 'pair', 'container'])
def test_release_rejects_invalid_single_handoff(pj, case):
    p = plan(pj); c, a, b = p['handoff_tasks']
    if case == 'board': a['parent'] = c['key']
    if case == 'wrong_group': p['asset_tasks'].append(p['handoff_tasks'].pop(0))
    if case == 'wrong_mode': c.pop('handoff_mode')
    if case == 'source': b['c_key'] = 'other-version'
    if case == 'role': p['video_tasks'][1]['handoff_refs'][1]['role'] = 'OUT_A'
    if case == 'pair': b['boundary'] = 'other-cut'
    if case == 'container': c['params']['size'] = '1:1'
    publish(pj, p)
    with pytest.raises(ValueError): A.validate_release(pj, 'r0001')


def test_indirect_a_b_dependency_and_competing_c():
    data = rows(); c, a, b = data[1:4]
    middle = {'kind':'image', 'key':'PROP_MIDDLE_R01', 'filename':'middle.png', 'prompt':'参考',
              'reference_images':[{'key':a['key'], 'image_n':1}]}
    b['reference_images'].append({'key':middle['key'], 'image_n':2})
    assert 'SINGLE_HANDOFF_DOWNSTREAM' in {i['code'] for i in M.audit(parsed(data + [middle]))}
    other = copy.deepcopy(c); other.update(key='OTHER_C_R01', filename='other-c.png')
    middle['reference_images'][0]['key'] = other['key']
    assert 'SINGLE_C_SOURCE' in {i['code'] for i in M.audit(parsed(data + [middle, other]))}


def test_c_only_and_existing_release_without_optional_array(pj):
    c = rows()[1]
    assert not [i for i in M.audit(parsed([c])) if i['level'] == 'error']
    from test_agent_workspace import plan_for
    p = plan_for(pj); p.pop('handoff_tasks')
    publish(pj, p)
    accepted, _, _ = A.validate_release(pj, 'r0001')
    assert accepted['handoff_tasks'] == []


def test_scheduler_runs_a_b_independently_after_c(pj, monkeypatch):
    p = plan(pj); A.scan(pj)
    barrier = threading.Barrier(2, timeout=8)
    calls = []
    def maker(*args):
        def worker(t, *_):
            for ref in A.references(t): assert Path(pj.p(*ref['file_ref'].split('/'))).is_file()
            if t.get('view') in ('A', 'B'): barrier.wait()
            target = Path(pj.p(*t['output'].split('/'))); target.parent.mkdir(parents=True, exist_ok=True)
            if t['output'].endswith('.png'): image(str(target))
            else: target.write_bytes(b'\x00\x00\x00\x18ftypisom' + bytes(4096))
            calls.append(t['key'])
            return {'output': t['output']}
        return worker
    monkeypatch.setattr(R.produce, 'make_image_worker', maker)
    monkeypatch.setattr(R.produce, 'make_video_worker', maker)
    caps = copy.deepcopy(CAPS); caps[0]['video']['durations'] = [15]
    jobs = JobManager()
    job = jobs.get(R.start(pj, {'defaults':{'max_retry':0, 'soften_rounds':0}}, caps, resolve, jobs)['job_id'])
    until = time.monotonic() + 15
    while job.status == 'running' and time.monotonic() < until: time.sleep(.03)
    assert job.status == 'done', job.snapshot()
    c, a, b = p['handoff_tasks']
    assert calls.index(c['key']) < min(calls.index(a['key']), calls.index(b['key']))
    assert calls.index(b['key']) < calls.index('EP01-SEG02')


def test_video_worker_uses_single_b_c_in_order(setup):
    pj, provider = setup
    built = M.build(parsed(rows()), system='v34'); pj.save_meta({'system':'v34'}); pj.save_tasks(built['tasks'])
    for rel, text in built['prompts'].items(): write_text(pj.p(*rel.split('/')), text)
    for t in built['tasks']['handoff_tasks']: put(pj, t['output'])
    task = built['tasks']['video_tasks'][1]; run(pj, task)
    assert [Path(f) for f in provider.sent[0].refs] == [Path(pj.p(*r['file_ref'].split('/'))) for r in task['handoff_refs']]
    assert [Path(f).parent.name for f in provider.sent[0].refs] == ['B', 'C']


def test_actual_c_is_snapshotted_and_reroll_warns_without_blocking(setup):
    pj, _ = setup
    c, a, b = M.build(parsed(rows()))['tasks']['handoff_tasks']
    put(pj, c['output'])
    srcs = [(r, r['file_ref']) for r in a['reference_images']]
    actual, meta = S.freeze_source(pj, a, srcs)
    assert actual[0][1] != c['output']
    assert meta['handoff_source']['sha256'] == A.digest(Path(pj.p(c['output'])).read_bytes())
    assert S.freeze_source(pj, b, srcs) == (actual, meta)
    put(pj, c['output'], b'new-version')
    logs = []
    fresh, fresh_meta = S.freeze_source(pj, b, srcs, logs.append)
    assert fresh[0][1] != actual[0][1]
    assert Path(pj.p(fresh[0][1])).read_bytes() == b'new-version'
    assert fresh_meta['handoff_source']['sha256'] == A.digest(b'new-version')
    assert len(logs) == 1 and '本次按最新版出图' in logs[0] and '旧版 C' in logs[0]
    assert S.freeze_source(pj, a, srcs, logs.append) == (fresh, fresh_meta)
    assert len(logs) == 1  # unchanged bytes do not warn again for the other view
    record = next(Path(pj.p('07_检查与记录/handoff-sources')).glob('*.json'))
    assert json.loads(record.read_text(encoding='utf-8')) == fresh_meta['handoff_source']
    assert Path(pj.p(actual[0][1])).read_bytes() != b'new-version'


@pytest.mark.parametrize('count', [0, 2])
def test_unlock_still_requires_one_c_source(setup, count):
    pj, _ = setup
    c, a, _ = M.build(parsed(rows()))['tasks']['handoff_tasks']
    put(pj, c['output'])
    srcs = [(r, r['file_ref']) for r in a['reference_images']] * count
    with pytest.raises(ValueError, match='缺少唯一'):
        S.freeze_source(pj, a, srcs)


def test_old_malformed_source_record_does_not_lock_rerolls(setup):
    pj, _ = setup
    c, a, _ = M.build(parsed(rows()))['tasks']['handoff_tasks']
    put(pj, c['output'])
    srcs = [(r, r['file_ref']) for r in a['reference_images']]
    actual, meta = S.freeze_source(pj, a, srcs)
    record = next(Path(pj.p('07_检查与记录/handoff-sources')).glob('*.json'))
    write_json(str(record), ['old-invalid-record'])
    assert S.freeze_source(pj, a, srcs) == (actual, meta)
    assert json.loads(record.read_text(encoding='utf-8')) == meta['handoff_source']


def test_image_worker_uses_snapshot_and_records_source_for_resource_alias(setup, monkeypatch):
    from core import produce
    pj, provider = setup
    built = M.build(parsed(rows()), system='v34')
    c, a, b = built['tasks']['handoff_tasks']
    # Agent resources can have IDs distinct from their mapped task keys.
    a['reference_images'][0]['asset_id'] = 'resource-c'
    text = built['prompts'][a['prompt_ref']].replace(c['key'], 'resource-c')
    pj.save_tasks(built['tasks'])
    put(pj, c['output']); write_text(pj.p(a['prompt_ref']), text)
    sent = []
    def generate(task, dest, **kw):
        sent.append(task)
        image(dest)
        return {'task_id':'mock-single-image'}
    monkeypatch.setattr(provider, 'generate_image', generate)
    worker = produce.make_image_worker(pj, {'provider':'mock', 'api_key':'x', 'model':'m', 'agent_mode':True, 'soften_rounds':0}, 'storyboard')
    worker(a, lambda _: None, lambda: False)
    assert len(sent) == 1 and len(sent[0].refs) == 1
    record = next(r for r in pj.registry('storyboard') if r['id'] == a['key'])
    assert record['handoff_source']['c_key'] == c['key']
    assert record['handoff_source']['sha256'] == A.digest(Path(pj.p(c['output'])).read_bytes())


@pytest.mark.parametrize('system', ['v34', 'v61'])
def test_rerolled_c_reaches_image_provider_and_keeps_previous_output_evidence(setup, monkeypatch, system):
    from core import produce
    pj, provider = setup
    built = M.build(parsed(rows()), system=system)
    pj.save_meta({'system': system}); pj.save_tasks(built['tasks'])
    c, a, b = built['tasks']['handoff_tasks']
    for rel, text in built['prompts'].items(): write_text(pj.p(rel), text)
    put(pj, c['output'])
    original = Path(pj.p(c['output'])).read_bytes()
    sent = []
    def generate(task, dest, **kw):
        sent.append(Path(task.refs[0]).read_bytes())
        image(dest)
        return {'task_id': 'test-reroll-' + str(len(sent))}
    monkeypatch.setattr(provider, 'generate_image', generate)
    cfg = {'provider':'mock', 'api_key':'x', 'model':'m', 'agent_mode':True, 'soften_rounds':0}
    worker = produce.make_image_worker(pj, cfg, 'storyboard')
    worker(a, lambda _: None, lambda: False)
    image(pj.p(c['output']))
    latest = Path(pj.p(c['output'])).read_bytes()
    logs = []
    worker(b, logs.append, lambda: False)
    records = {r['id']: r for r in pj.registry('storyboard')}
    assert sent == [original, latest]
    assert records[a['key']]['handoff_source']['sha256'] == A.digest(original)
    assert records[b['key']]['handoff_source']['sha256'] == A.digest(latest)
    assert any('本次按最新版出图' in line for line in logs)
    assert Path(pj.p(records[a['key']]['handoff_source']['snapshot'])).read_bytes() == original


def test_pending_a_resumes_old_job_and_keeps_old_c_evidence_after_reroll(setup, monkeypatch):
    from core import produce, submitted
    from core.apiutil import ApiError
    pj, provider = setup
    built = M.build(parsed(rows()), system='v34'); pj.save_tasks(built['tasks'])
    c, a, _ = built['tasks']['handoff_tasks']
    for rel, text in built['prompts'].items(): write_text(pj.p(rel), text)
    put(pj, c['output'])
    original = Path(pj.p(c['output'])).read_bytes()
    calls = []
    def generate(task, dest, **kw):
        calls.append(('create', Path(task.refs[0]).read_bytes()))
        submitted.remember('pending-a', custom='fixture')
        raise ApiError('模拟查询超时')
    def resume(record, dest, **kw):
        calls.append(('resume', record['task_id']))
        image(dest)
        return {'task_id': record['task_id'], 'resumed': True}
    monkeypatch.setattr(provider, 'generate_image', generate)
    monkeypatch.setattr(provider, 'resume', resume)
    worker = produce.make_image_worker(pj, {'provider':'mock', 'api_key':'x', 'model':'m', 'agent_mode':True, 'soften_rounds':0}, 'storyboard')
    with pytest.raises(ApiError, match='原任务 ID 已保留'):
        worker(a, lambda _: None, lambda: False)
    image(pj.p(c['output']))
    result = worker(a, lambda _: None, lambda: False)
    assert result['resumed'] is True
    assert calls == [('create', original), ('resume', 'pending-a')]
    record = next(r for r in pj.registry('storyboard') if r['id'] == a['key'])
    assert record['handoff_source']['sha256'] == A.digest(original)


def test_existing_project_schema_updated_with_contract(pj):
    schema_path = pj.p('00_项目说明', 'production.schema.json')
    schema = A.schema_document(); schema['properties'].pop('handoff_tasks')
    write_json(schema_path, schema)
    A.update_reference_contract(pj)
    assert 'handoff_tasks' in json.loads(Path(schema_path).read_text(encoding='utf8'))['properties']
    assert S.RULES in Path(pj.p('00_项目说明', 'production-contract.md')).read_text(encoding='utf8')
