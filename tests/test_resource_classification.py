"""Material imports keep asset categories and show handoff images before video."""
from collections import Counter
import json
from pathlib import Path
import re

import pytest

from core import agent_projects as A, agent_runtime as R, matimport as M
from core.store import Project
from test_agent_workspace import CAPS


def project(tmp_path, plan):
    pj = Project(str(tmp_path / 'project'))
    pj.init_dirs()
    pj.save_meta({'system': 'v34', 'title': '分类回归'})
    pj.save_tasks(plan)
    return pj


def image_task(key, output):
    return {'key': key, 'output': output, 'prompt_ref': '03_提示词/' + key + '.txt',
            'params': {'size': '16:9'}, 'reference_images': []}


def material_plan():
    rows = []
    for family, count in [('CHAR', 10), ('LOC', 6), ('PROP', 4)]:
        for i in range(count):
            key = f'{family}_DEMO{i}_R1'
            rows.append({'kind': 'image', 'key': key, 'filename': key + '.png',
                         'prompt': '分类测试，无生成请求', 'size': '16:9'})
    for i in range(6):
        c = f'DEMO_ABC_SPACE{i}_R1'
        for view in ['C', 'A', 'B']:
            key = c if view == 'C' else f'DEMO_ABC_CUT{i}_{view}_R1'
            row = {'kind': 'image', 'key': key, 'filename': key + '.png',
                   'prompt': '独立交接图测试', 'size': '16:9',
                   'handoff_mode': 'c_then_a_b', 'view': view}
            if view != 'C':
                row.update(boundary=f'CUT{i}', c_key=c, reference_images=[{'image_n': 1, 'key': c}])
            rows.append(row)
    for i in range(1, 11):
        rows.append({'kind': 'video', 'key': f'EP01-SEG{i:02}', 'filename': f'video{i}.mp4',
                     'episode': 'EP01', 'seg': f'SEG{i:02}', 'duration': 15,
                     'prompt': '视频分类测试', 'ratio': '16:9',
                     'handoff_refs': [{'image_n': 1, 'key': 'DEMO_ABC_CUT0_A_R1', 'role': 'OUT_A'}]})
    return M.build(M.parse('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows)), system='v34')['tasks']


def test_48_material_tasks_recover_categories_without_rewriting_the_plan(tmp_path):
    plan = material_plan()
    pj = project(tmp_path, plan)
    task_file = Path(pj.p('03_提示词', 'tasks.json'))
    before = task_file.read_bytes()
    state = A.view(pj)
    assert Counter(r['semantic'] for r in state['resources']) == {
        'CHAR': 10, 'LOC': 6, 'PROP': 4, 'HANDOFF': 18, 'VIDEO': 10}
    assert len(state['tasks']) == 48
    bykey = {t['key']: t for t in state['tasks']}
    assert bykey['DEMO_ABC_CUT0_A_R1']['dependencies'] == ['DEMO_ABC_SPACE0_R1']
    assert bykey['EP01-SEG01']['dependencies'] == ['DEMO_ABC_CUT0_A_R1']
    A.view(pj)
    assert task_file.read_bytes() == before
    assert pj.tasks() == plan


@pytest.mark.parametrize('key,expected', [
    ('LOC_ROOM_R1', 'LOC'), ('PROP_WATCH_R1', 'PROP'),
    ('PRJ_X_PROP_WATCH_R1', 'PROP'), ('PROP001_R1', 'PROP'),
    ('PRJ_X_LOC001_R1', 'LOC'), ('prop001_r1', 'PROP'),
    ('PH001_R1', 'CHAR'), ('LK01_R1', 'LOOK'),
    ('COST001_R1', 'COST'), ('VEH001_R1', 'VEH'),
    ('CLOCK_R1', 'CHAR'), ('ASSET0', 'CHAR'),
])
def test_old_ids_without_category_directories(key, expected):
    assert A._inferred_semantic(dict(image_task(key, '02_固定资产/' + key + '.png'), kind='asset')) == expected


@pytest.mark.parametrize('kind,expected', [
    ('scstate', 'SCSTATE'), ('board', 'ABC'), ('region', 'REGION'),
    ('handoff', 'HANDOFF'), ('video', 'VIDEO'),
])
def test_task_type_wins_over_misleading_key(kind, expected):
    assert A._inferred_semantic({'key': 'PRJ_X_ABC_CHAR_R1', 'kind': kind}) == expected


@pytest.mark.parametrize('folder,expected', [
    ('人物身份资产', 'CHAR'), ('人物造型资产', 'LOOK'), ('服饰资产', 'COST'),
    ('连续状态资产', 'CT'), ('群体资产', 'GRP'), ('生物资产', 'CRE'),
    ('场景资产', 'LOC'), ('道具资产', 'PROP'), ('载具资产', 'VEH'), ('特效资产', 'VFX'),
])
def test_existing_asset_directory_wins_over_id_guess(folder, expected):
    assert A._inferred_semantic({'key': 'PRJ_X_CHAR_LOC_PROP_R1', 'kind': 'asset',
                                 'output': f'02_固定资产/{folder}/image.png'}) == expected


def test_explicit_resource_classification_and_parentage_stay_unchanged(tmp_path):
    plan = {'asset_tasks': [image_task('PROP_DEMO', '02_固定资产/道具资产/a.png'),
                            image_task('LOOK_DEMO', '02_固定资产/人物造型资产/b.png')],
            'resources': [{'id': 'parent', 'task_key': 'PROP_DEMO', 'name': '已声明资源',
                           'semantic': 'CHAR', 'parent_id': None, 'episodes': []},
                          {'id': 'child', 'task_key': 'LOOK_DEMO', 'name': '造型',
                           'semantic': 'LOOK', 'parent_id': 'parent', 'episodes': []}]}
    pj = project(tmp_path, plan)
    assert A.view(pj)['resources'] == plan['resources']
    assert pj.tasks() == plan


def test_browser_orders_resources_and_image_settings_before_video(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    root = Path(__file__).resolve().parents[1]
    source = (root / 'web/agent.js').read_text(encoding='utf-8')
    startup = 'safe(async()=>{await window.StudioLogin.ready;await reloadBoot();render();tick();await refreshCloudConfiguration();})();'
    assert startup in source
    source = source.replace(startup, 'window.testUI={S,production,renderTree};')
    html = re.sub(r'<script.*?</script>', '', (root / 'web/agent.html').read_text(encoding='utf-8'))
    pj = project(tmp_path, material_plan())
    data = A.view(pj)
    data.update(jobs=[], failures=[], running={}, production=R.settings(pj, CAPS, {}))
    # Published resources can arrive in any order; their stored order is not a UI order.
    data['resources'].reverse()
    boot = {'features': {}, 'semantics': A.SEMANTICS, 'kinds': A.KINDS,
            'capabilities': CAPS, 'providers_public': {}, 'retry': 0}
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1400, 'height': 950})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/*', lambda r: r.fulfill(status=200, body=html, content_type='text/html')
                   if r.request.url == 'http://studio.test/' else r.abort())
        page.goto('http://studio.test/')
        page.add_script_tag(content=source)
        page.evaluate('''({data,boot}) => {
            const s=testUI.S;s.root=data.root;s.page='production';s.boot=boot;s.data=data;
            testUI.production();
        }''', {'data': data, 'boot': boot})
        expected = ['人物身份', '场景', '道具', '独立交接图', '分段视频']
        assert page.locator('#resource-tree > details > summary > strong').all_text_contents() == expected
        assert len(page.locator('#resource-tree input[data-key]').all()) == 48
        service_types = page.locator('#production-services .rv-service-row').evaluate_all('(els)=>els.map(e=>e.dataset.kind)')
        assert service_types.index('handoff') < service_types.index('video')
        assert page.locator('[aria-label="独立交接图 C/A/B秒数"]').count() == 0
        assert page.locator('[aria-label="分段视频秒数"]').count() == 1
        for mode in ['type-episode', 'episode']:
            page.evaluate('(mode)=>{testUI.S.treeMode=mode;testUI.renderTree();}', mode)
            if mode == 'type-episode':
                assert page.locator('#resource-tree > details > summary > strong').all_text_contents() == expected
            assert len(page.locator('#resource-tree input[data-key]').all()) == 48
        assert not errors
        browser.close()
