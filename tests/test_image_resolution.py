import base64
import io
from PIL import Image
import pytest
from core import model_catalog, agent_projects as A, agent_runtime as R
from core.providers.chaomo import ChaomoProvider
from core.providers.base import ImageTask
from core.store import write_json
from test_agent_workspace import plan_for,publish,resolve


def test_resolution_catalog_prefers_separate_supported_sizes():
    assert model_catalog._from_toplevel({'resolution_mode':'1K/2K/4K',
        'supported_image_sizes':['1K','2K','4K']})['resolutions']==['1K','2K','4K']


def test_image_resolution_applies_without_advanced_feature(tmp_path):
    cap=ChaomoProvider().capabilities()
    pj=A.create_project(str(tmp_path),{'title':'清晰度','source':'fixture',
        'options':{'image_provider':'chaomo','image_model':'gpt-image-2-th'}},[cap])
    publish(pj,plan_for(pj,count=1));A.scan(pj)
    settings=R.validate_settings({'asset':{'provider':'chaomo','model':'gpt-image-2-th',
        'concurrency':1,'image_resolution':'4K'}},[cap])
    write_json(pj.p('07_检查与记录','production-settings.json'),settings)
    preview=R.preview(pj,{'agent_features':{'advanced':False}},[cap],resolve)
    assert not preview['blocked']
    assert preview['_tasks'][0]['params']['resolution']=='4K'


@pytest.mark.parametrize('refs',[False,True])
def test_th_image_size_is_resolution_ratio_is_separate(monkeypatch,refs):
    p=ChaomoProvider();seen={}
    buf=io.BytesIO();Image.new('RGB',(24,24),'blue').save(buf,'PNG')
    ref='data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()
    def request(method,path,**kwargs):
        seen.update(kwargs);return {'data':[{'url':'https://fixture.invalid/result.png'}]}
    monkeypatch.setattr(p.session,'request',request)
    monkeypatch.setattr(p.session,'save_item',lambda *a,**kw:None)
    monkeypatch.setattr(p,'check_meta',lambda *a,**kw:None)
    p.generate_image(ImageTask('test',refs=[ref] if refs else [],size='16:9',
        model='gpt-image-2-th',extra={'resolution':'4K'}),'unused.png',log=lambda _:None)
    if refs:
        body=dict(seen['files']);assert body['size']==(None,'4K') and body['ratio']==(None,'16:9')
    else:assert seen['json_body']['size']=='4K' and seen['json_body']['ratio']=='16:9'
