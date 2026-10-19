import copy
import json
import time
from pathlib import Path
import zipfile

import pytest

from core import agent_projects as A, agent_runtime as R, agent_handoff as H, matimport as M, matspec
from core.independent_c import MODE, RULES
from core.executor import JobManager
from core.store import write_json, write_text
from test_agent_workspace import pj, publish, image, resolve, CAPS
from test_handoff_production import setup, put, run

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples/独立C-AB双联板-生产材料.jsonl'


def rows():
    return [json.loads(line) for line in EXAMPLE.read_text(encoding='utf8').splitlines()]


def parsed(data):
    return M.parse('\n'.join(json.dumps(r, ensure_ascii=False) for r in data))


def plan(pj):
    built = M.build(parsed(rows()), system='v34')
    p = {'schema': A.SCHEMA, 'project_id': pj.meta()['project_code'], 'revision': 'r0001', 'resources': [],
         **{kind+'_tasks': built['tasks'][kind+'_tasks'] for kind in A.KINDS}}
    texts = {}
    by_key = {t['key']:t for t in A.task_rows(p)}
    for kind in A.KINDS:
        for t in p[kind+'_tasks']:
            texts[t['key']] = built['prompts'][t['prompt_ref']]
            t['prompt_ref'] = '03_提示词/releases/r0001/images/'+t['key']+'.txt'
            for h in t.get('handoff_refs', []):
                h['asset_id'],h['image_n'] = h.pop('sheet_id'),h.pop('order')
                dep = by_key[h['asset_id']]
                if dep['kind']=='region':
                    h['board']=dep['board']
            for old in ('storyboard_ref','storyboard_refs','no_image_refs','from_material'):
                t.pop(old,None)
            p['resources'].append(dict(id=t['key'],name=t['key'],semantic={'scstate':'SCSTATE','board':'ABC','region':'REGION','video':'VIDEO'}[kind],
                                       parent_id=None,episodes=[t['episode']] if t.get('episode') else [],task_key=t['key']))
    folder, ready = publish(pj,p)
    for t in A.task_rows(p):
        rel=t['prompt_ref'].split('r0001/',1)[1]
        (folder/rel).write_text(texts[t['key']],encoding='utf8')
        ready['files'][rel]=A.digest((folder/rel).read_bytes())
    write_json(str(folder/'READY.json'),ready)
    return p


def test_example_import_publish_dependencies_and_contract(pj):
    units=parsed(rows())
    assert M.audit(units)==[]
    built=M.build(units,system='v34')['tasks']
    assert len(built['scstate_tasks'])==1 and len(built['board_tasks'])==1
    c=built['scstate_tasks'][0];assert c['handoff_mode']==MODE and 'board' not in c and 'region' not in c
    p=plan(pj)
    state=A.view(pj)
    assert not state['publication']['errors']
    tasks={t['key']:t for t in state['tasks']}
    ab=p['board_tasks'][0]['key']; b=p['region_tasks'][1]['key']
    assert tasks[ab]['dependencies']==[c['key']]
    assert tasks[b]['dependencies']==[ab]
    assert tasks['EP01-SEG02']['dependencies']==[b,c['key']]
    assert RULES in matspec.render(system='v34')
    assert A.schema_document()['properties']['scstate_tasks']['items']['properties']['handoff_mode']['enum']==[MODE]
    with zipfile.ZipFile(H.export_package(pj)['file']) as archive:
        assert RULES in archive.read('生产材料契约.md').decode('utf8')
        assert RULES in archive.read('00_项目说明/production-contract.md').decode('utf8')


@pytest.mark.parametrize('case,code', [('region','INDEPENDENT_C_SHAPE'),('role','INDEPENDENT_C_ROLE'),
                                    ('source','INDEPENDENT_C_SOURCE'),('parent','AB_REGION_SOURCE'),
                                    ('mode','HANDOFF_MODE_UNKNOWN'),('duplicate','INDEPENDENT_C_DUP')])
def test_jsonl_extension_rejects_invalid_shape_role_and_source(case,code):
    data=rows()
    if case=='region':data[1]['region']='C'
    if case=='mode':data[1]['handoff_mode']='misspelled'
    if case=='role':data[-1]['handoff_refs'][1]['role']='OUT_A'
    if case=='source':data[2]['reference_images']=[]
    if case=='parent':data[4]['board']=data[1]['key']
    if case=='duplicate':data[-1]['reference_images']=[{'image_n':3,'key':data[1]['key']}]
    assert code in {i['code'] for i in M.audit(parsed(data))}


@pytest.mark.parametrize('case', ['region','role','source','parent','mode','board_ref'])
def test_release_extension_rejects_invalid_shape_role_and_source(pj,case):
    p=plan(pj)
    if case=='region':p['scstate_tasks'][0]['region']='C'
    if case=='mode':p['scstate_tasks'][0]['handoff_mode']='misspelled'
    if case=='role':p['video_tasks'][1]['handoff_refs'][1]['role']='OUT_A'
    if case=='source':p['board_tasks'][0]['reference_images']=[]
    if case=='parent':p['region_tasks'][1]['board']=p['scstate_tasks'][0]['key']
    if case=='board_ref':p['video_tasks'][1]['handoff_refs'][1]['board']='wrong-board'
    publish(pj,p)
    with pytest.raises(ValueError):A.validate_release(pj,'r0001')


def test_original_region_c_is_not_created_from_ab(pj):
    p=plan(pj)
    p['region_tasks'][0]['region']='C'
    p['video_tasks'][0]['handoff_refs'][0]['role']='IN_C'
    publish(pj,p)
    with pytest.raises(ValueError,match='AB_REGION_C'):A.validate_release(pj,'r0001')


def test_jsonl_cannot_mix_new_ab_with_an_old_boards_region_c():
    data=rows()
    old_board=copy.deepcopy(data[2]);old_board['key']='PRJ_DEMO__ABC_OLD_R01'
    old_board['filename']=old_board['key']+'.png'
    old_board['reference_images']=[];old_board['prompt']='生成庭院原 ABC 三区整板。'
    old_c=copy.deepcopy(data[4]);old_c['key']=old_board['key']+'_C'
    old_c['filename']=old_c['key']+'.png';old_c['region']='C';old_c['board']=old_board['key']
    old_c['reference_images']=[{'image_n':1,'key':old_board['key']}]
    old_c['prompt']=f"Image 1 = {old_board['key']}｜庭院 ABC 整板，采用其中 C 区空间。"
    data[-1]['handoff_refs'][1]['key']=old_c['key']
    data[-1]['prompt']=data[-1]['prompt'].replace(data[1]['key'],old_c['key'])
    data[0].update(total=8,image=6)
    data[1:1]=[old_board,old_c]
    assert {i['code'] for i in M.audit(parsed(data))}=={'INDEPENDENT_C_SOURCE'}


def test_scheduler_waits_for_c_then_board_then_regions_then_video(pj,monkeypatch):
    p=plan(pj);A.scan(pj)
    calls=[]
    def maker(*args):
        def worker(t,*_):
            for ref in A.references(t):
                assert Path(pj.p(*ref['file_ref'].split('/'))).is_file()
            target=Path(pj.p(*t['output'].split('/')))
            target.parent.mkdir(parents=True,exist_ok=True)
            if t['output'].endswith('.png'):image(str(target))
            else:target.write_bytes(b'\x00\x00\x00\x18ftypisom'+bytes(4096))
            calls.append(t['key'])
            return {'output':t['output']}
        return worker
    monkeypatch.setattr(R.produce,'make_image_worker',maker)
    monkeypatch.setattr(R.produce,'make_video_worker',maker)
    caps=copy.deepcopy(CAPS);caps[0]['video']['durations']=[15]
    jobs=JobManager();job=jobs.get(R.start(pj,{'defaults':{'max_retry':0,'soften_rounds':0}},caps,resolve,jobs)['job_id'])
    # Three cross-kind dependencies each use the scheduler's 2-second polling interval.
    deadline=time.monotonic()+15
    while job.status=='running' and time.monotonic()<deadline:time.sleep(.03)
    assert job.status=='done',job.snapshot()
    c=p['scstate_tasks'][0]['key'];ab=p['board_tasks'][0]['key']
    assert calls.index(c)<calls.index(ab)
    for region in p['region_tasks']:assert calls.index(ab)<calls.index(region['key'])
    assert calls.index(p['region_tasks'][1]['key'])<calls.index('EP01-SEG02')


def test_real_video_worker_uploads_b_then_independent_c_without_board_or_duplicate(setup):
    pj,provider=setup
    built=M.build(parsed(rows()),system='v34');pj.save_meta({'system':'v34'});pj.save_tasks(built['tasks'])
    for rel,text in built['prompts'].items():write_text(pj.p(*rel.split('/')),text)
    for row in A.task_rows(built['tasks']):
        if row['kind']!='video':put(pj,row['output'])
    task=built['tasks']['video_tasks'][1]
    run(pj,task)
    sent=provider.sent[0]
    assert len(sent.refs)==2 and sent.duration==15
    assert [Path(ref) for ref in sent.refs]==[Path(pj.p(*h['file_ref'].split('/'))) for h in task['handoff_refs']]
    assert Path(sent.refs[1]).parent.name=='03b_场景状态图'
