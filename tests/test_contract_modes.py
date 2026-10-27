"""Exported guidance must match real import and preserve old project snapshots."""
import base64
import copy
import json
from pathlib import Path
import runpy
import zipfile

import pytest

from core import agent_handoff as H, agent_projects as A, matimport as M, matspec, produce
from core.reference_contract import REVISION, SCOPE_MARKER
from core.store import write_json
from test_agent_workspace import pj, plan_for, publish, CAPS  # noqa: F401
from test_single_handoff import plan

ROOT = Path(__file__).resolve().parents[1]


def material():
    return [json.loads(line) for line in matspec.single_jsonl_schema().splitlines()]


@pytest.mark.parametrize('system', ['v34', 'v61'])
def test_current_example_has_valid_mappings_routes_and_declared_duration(system):
    rows = material()
    parsed = M.parse(matspec.single_jsonl_schema())
    assert M.audit(parsed) == []
    built = M.build(parsed, system=system)
    tasks = built['tasks']
    assert [t['view'] for t in tasks['handoff_tasks']] == ['C', 'A', 'B']
    assert tasks['scstate_tasks'] == tasks['board_tasks'] == tasks['region_tasks'] == []
    for task in tasks['handoff_tasks']:
        assert task['output'].startswith('04_交接图/' + task['view'] + '/')
    for row in rows[1:]:
        refs = [dict(ref, asset_id=ref['key'])
                for ref in row.get('handoff_refs', []) + row['reference_images']]
        assert produce.check_image_map(row['prompt'], refs)[0] == ''
        assert produce.check_identity_map(row['prompt'], refs)[0] == ''
        assert '完整可投喂提示词正文' not in row['prompt']
        if row['kind'] == 'video':
            for heading in ['画幅风格', '参考图', '站位声明', '时间轴分镜', '接续状态', '音效', '强制禁止项']:
                assert '【' + heading + '】' in row['prompt']
    assert rows[0]['params']['episode_seconds'] == sum(r.get('duration', 0) for r in rows[1:])
    example = ROOT / 'examples/独立C-独立AB-生产材料.jsonl'
    assert example.read_text(encoding='utf8').strip() == matspec.single_jsonl_schema()


def test_current_example_imports_through_http_and_creates_handoff_dependencies(pj, monkeypatch):
    from server import agent_api, app
    cfg = {'projects_dir': str(Path(pj.root).parent), 'providers': {},
           'limits': {'global': 8, 'per_provider': {}}}
    monkeypatch.setattr(app, 'load_config', lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(app, 'caps_of', lambda cfg=None: copy.deepcopy(CAPS))
    monkeypatch.setattr(app, '_material_limits', lambda *a: {})
    result = agent_api.post(app, '/api/agent/handoff/import', {
        'root': pj.root, 'filename': 'current.jsonl',
        'content_b64': base64.b64encode(matspec.single_jsonl_schema().encode()).decode()})
    assert result['ok'] and result['revision']
    state = A.view(pj)
    assert state['publication']['source'] == 'jsonl'
    bykey = {t['key']: t for t in state['tasks']}
    c, a, b = material()[1:4]
    assert len(bykey) == 5
    assert bykey[a['key']]['dependencies'] == bykey[b['key']]['dependencies'] == [c['key']]
    assert bykey['EP01-SEG02']['dependencies'] == [b['key'], c['key']]


def test_current_example_also_passes_ready_release_validation(pj):
    plan(pj, material())
    result, _, _ = A.validate_release(pj, 'r0001')
    assert len(result['handoff_tasks']) == 3
    assert len(result['video_tasks']) == 2


def test_old_project_refresh_and_export_preserve_locked_and_published_files(pj):
    publish(pj, plan_for(pj))
    A.scan(pj)
    folder = Path(pj.p('00_项目说明'))
    old_docs = {}
    for name in ['production-contract.md', 'AGENT_TASK.md']:
        path = folder / name
        old_docs[path] = ('# 旧文档\r\n项目原路径：' + pj.root + '\r\n旧工艺不迁移。').encode()
        path.write_bytes(old_docs[path])
    schema_path = folder / 'production.schema.json'
    schema = A.schema_document()
    schema['properties'].pop('handoff_tasks')
    write_json(str(schema_path), schema)
    protected = [folder / 'project-lock.json']
    protected += [p for d in [folder / 'skill', Path(pj.p('03_提示词'))]
                  for p in d.rglob('*') if p.is_file()]
    before = {p: p.read_bytes() for p in protected}
    tasks_before = pj.tasks()
    A.view(pj)
    updated = {p: p.read_bytes() for p in old_docs}
    A.view(pj)
    assert updated == {p: p.read_bytes() for p in old_docs}
    for path, original in old_docs.items():
        assert original in updated[path]
        assert updated[path].count(SCOPE_MARKER.encode()) == 1
        assert updated[path].index(SCOPE_MARKER.encode()) < updated[path].index(original)
    with zipfile.ZipFile(H.export_package(pj)['file']) as archive:
        assert 'handoff_tasks' in json.loads(archive.read('00_项目说明/production.schema.json'))['properties']
        exported = json.loads(archive.read('production-material.schema.json'))
        assert exported == json.loads(matspec.json_schema())
        assert json.loads(archive.read('交接包说明.json'))['contract_revision'] == REVISION
        assert archive.read('00_项目说明/project-lock.json') == before[folder / 'project-lock.json']
        for p in protected:
            if (folder / 'skill') in p.parents:
                assert archive.read(p.relative_to(Path(pj.root)).as_posix()) == before[p]
    assert {p: p.read_bytes() for p in protected} == before
    assert pj.tasks() == tasks_before


def test_bundled_audit_studio_profile_checks_handoff_refs_without_invented_fields():
    audit = runpy.run_path(str(ROOT / 'skills/production-skill/scripts/audit_material_refs.py'))['audit']
    rows = material()
    assert audit(rows, profile='studio')['errors'] == []
    assert any('storyboard_refs' in e for e in audit(rows)['errors'])
    # Studio has a manifest but only declared counts are checked; no internal-only fields.
    rows[0] = {'kind': 'manifest'}
    assert audit(rows, profile='studio')['errors'] == []
    rows[0]['total'] = 99
    assert any('manifest total' in e for e in audit(rows, profile='studio')['errors'])
    rows = material()
    rows[-1]['handoff_refs'][0]['key'] = 'MISSING_IMAGE'
    result = audit(rows, profile='studio')
    assert any('missing image task' in e for e in result['errors'])
    assert result['layers']['material'] == 'failed'
    assert result['layers']['files'] == 'not_checked'


def test_bundled_audit_compares_actual_handoff_upload_order():
    audit = runpy.run_path(str(ROOT / 'skills/production-skill/scripts/audit_material_refs.py'))['audit']
    rows = material()
    video = rows[-1]
    records = [dict(ref, source='test-only-request-source') for ref in video['handoff_refs']]
    okay = audit(rows, uploads={video['key']: records}, profile='studio')
    assert not okay['errors']
    assert okay['layers']['submission'] == 'partial_manifest_compared'
    wrong = audit(rows, uploads={video['key']: list(reversed(records))}, profile='studio')
    assert any('images/order differ' in e for e in wrong['errors'])
