"""Exercise real selector markup and public statistics widgets in Chromium."""
from pathlib import Path
import json

import pytest

ROOT = Path(__file__).resolve().parents[1]
playwright = pytest.importorskip('playwright.sync_api')


@pytest.fixture
def page():
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        page.set_content('<html lang="zh-CN"><body></body></html>')
        yield page
        browser.close()


MODELS = ['wan-3.0-video', 'no-statistics', 'wan-exact-suffix']
DATA = {'state': 'ready', 'fetched_at': 1793836800, 'models': [
    {'model': MODELS[0], 'rate': 95.16, 'samples': 99, 'seconds': 336.92,
     'window': '近 24 小时', 'basis': '服务商终态统计'},
    {'model': MODELS[2], 'rate': 0, 'samples': 3, 'window': '最近 100 次', 'basis': '服务商统计'},
    {'model': 'not-authorized', 'rate': 100},
]}
HELPERS = r"""
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const option=(v,t,s)=>`<option value="${esc(v)}" ${v===s?'selected':''}>${esc(t)}</option>`;
const input=(n,v,t='text',x='')=>`<input name="${n}" value="${esc(v)}" type="${t}" ${x}>`;
const durationsOf=()=>[5,10,15];
"""


def install(page, data=DATA, admin=False):
    page.add_style_tag(path=str(ROOT / ('auth_service/studio_auth/web/style.css' if admin else 'web/agent.css')))
    page.add_style_tag(path=str(ROOT / 'web/model-statistics.css'))
    page.evaluate('(data)=>{window.statsCalls=[];window.fetch=async url=>{statsCalls.push(url);return {ok:true,json:async()=>data};};}', data)
    page.add_script_tag(path=str(ROOT / 'web/model-statistics.js'))


def production(page):
    source = (ROOT / 'web/agent.js').read_text(encoding='utf-8')
    function = source[source.index('function serviceRow('):source.index('function editChain(')]
    setup = {'boot': {'features': {'advanced': False}, 'capabilities': [
        {'id': 'ake', 'name': '阿珂', 'supports': ['video'], 'video': {'models': MODELS, 'ratios': ['16:9']}}]},
        'data': {'production': {'video': {'provider': 'ake', 'model': MODELS[0], 'concurrency': 4}}}}
    page.add_script_tag(content=HELPERS + 'const S=' + json.dumps(setup) + ';' + function)
    page.evaluate("document.body.innerHTML='<main id=\"studio-agent-prototype\" style=\"padding:24px;box-sizing:border-box\"><h2>生产模型</h2>'+serviceRow('video','分段视频')+'</main>'")
    playwright.expect(page.locator('[data-pms-note]')).to_contain_text('95.16%')


def test_production_choice_preserves_exact_id_and_unknown_is_not_zero(page):
    install(page)
    production(page)
    assert page.locator('datalist option').first.get_attribute('label').endswith('95.16%')
    page.evaluate("window.chosen=[];document.addEventListener('change',e=>{if(e.target.dataset.setting==='model')chosen.push(e.target.value)})")
    page.locator('.pms summary').click()
    assert page.locator('.pms-card').count() == 3
    assert 'not-authorized' not in page.locator('.pms-cards').inner_text()
    row = page.locator('.pms-card').filter(has_text='no-statistics')
    assert '暂无数据' in row.inner_text() and '0.00%' not in row.inner_text()
    assert row.locator('.pms-known').count() == 0
    success = page.locator('.pms-card').filter(has_text='wan-3.0-video')
    assert success.locator('.pms-known .is-lit').count() == 38
    assert '336.92 秒' in success.inner_text()
    page.locator('[data-pms-search]').fill('wan-exact-suffix')
    page.locator('[data-pms-pick]').click()
    assert page.locator('input[data-setting="model"]').input_value() == 'wan-exact-suffix'
    assert page.evaluate('chosen') == ['wan-exact-suffix']
    playwright.expect(page.locator('[data-pms-note]')).to_contain_text('0.00%')
    assert page.locator('.pms-selected .pms-zero').count() == 1
    assert page.locator('.pms-selected .is-lit').count() == 0


@pytest.mark.parametrize('width', [390, 1280])
def test_statistics_panel_stays_inside_viewport(page, width):
    page.set_viewport_size({'width': width, 'height': 900})
    install(page)
    production(page)
    page.locator('.pms summary').click()
    panel = page.locator('.pms-panel').bounding_box()
    assert panel['x'] >= 0 and panel['x'] + panel['width'] <= width
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_admin_key_library_adds_exact_unique_model_without_saving_key(page):
    install(page, {**DATA, 'catalog': {'state': 'ready', 'models': MODELS, 'message': '此 Key 的模型接口返回清单'}}, admin=True)
    page.evaluate("document.body.innerHTML='<button id=key-library>Key 库</button><div id=users></div>'")
    page.add_script_tag(content=HELPERS + "const safe=fn=>fn();const api=async()=>({keys:[{id:'key-one',provider:'ake',name:'测试 Key',base_url:'',models:[],configured:['api_key'],revision:'rev1',enabled:true}],providers:{ake:'阿珂'}});")
    page.add_script_tag(path=str(ROOT / 'auth_service/studio_auth/web/provisioning.js'))
    page.locator('#key-library').click()
    assert page.locator('.pms').count() == 0  # Unsaved credentials never borrow a supplier-wide list.
    page.locator('[data-key-edit]').click()
    playwright.expect(page.locator('.pms-card')).to_have_count(3)
    page.wait_for_function("statsCalls.some(u=>u.includes('key_id=key-one'))")
    page.locator('[data-pms-search]').fill('wan-exact-suffix')
    page.locator('[data-pms-pick]').click()
    assert page.locator('textarea[name=models]').input_value() == 'wan-exact-suffix'
    page.locator('[data-pms-pick]').click()
    assert page.locator('textarea[name=models]').input_value() == 'wan-exact-suffix'
    page.locator('input[name=base_url]').fill('https://custom.example')
    assert page.locator('.pms').count() == 0
    page.locator('[data-key-stats]').click()
    playwright.expect(page.locator('.pms-card')).to_have_count(3)
    assert page.locator('[data-pms-pick]').count() == 0
    assert page.locator('details').get_attribute('open') is not None


def test_stale_data_and_untrusted_model_text_are_displayed_safely(page):
    hostile = '<img src=x onerror=alert(1)>'
    data = {**DATA, 'stale': True, 'error': 'timeout', 'state': 'error', 'scope': 'request',
            'models': [{**DATA['models'][0], 'model': hostile}], 'catalog': {'models': [hostile], 'message': '此 Key 的模型'}}
    install(page, data)
    page.evaluate('(model)=>{document.body.innerHTML=ProviderModelStats.markup("ake",model,null,"bind")}', hostile)
    playwright.expect(page.locator('[data-pms-note]')).to_contain_text('旧数据，刷新失败')
    assert page.locator('img').count() == 0
    assert hostile in page.locator('.pms-cards').inner_text()
    assert page.locator('.pms-known').count() == 0
    assert '请求统计口径' in page.locator('[data-pms-time]').inner_text()


def test_pending_old_supplier_response_does_not_replace_new_supplier(page):
    install(page)
    page.evaluate("fetch=url=>new Promise(resolve=>{(window.pending??=[]).push({url,resolve})});document.body.innerHTML=ProviderModelStats.markup('ake','wan',null)")
    page.wait_for_function('window.pending?.length===1')
    page.evaluate("document.querySelector('.pms').dataset.pmsProvider='julun'")
    page.wait_for_function('pending.length===2')
    page.evaluate("pending[1].resolve({ok:true,json:async()=>({state:'ready',models:[{model:'wan',rate:80,window:'24h'}]})})")
    playwright.expect(page.locator('[data-pms-note]')).to_contain_text('80.00%')
    page.evaluate("pending[0].resolve({ok:true,json:async()=>({state:'ready',models:[{model:'wan',rate:99,window:'24h'}]})})")
    playwright.expect(page.locator('[data-pms-note]')).to_contain_text('80.00%')


def test_wizard_provider_switch_replaces_suggestions_and_keeps_selected_id(page):
    install(page)
    source = (ROOT / 'web/agent.js').read_text(encoding='utf-8')
    function = source[source.index('function wizardModel('):source.index('function wizardSave(')]
    change = " if(S.page==='wizard'&&" + source.split(" if(S.page==='wizard'&&", 1)[1].split('\n}));', 1)[0]
    setup = {'page': 'wizard', 'wizard': {'video_provider': 'ake', 'video_model': MODELS[0]},
             'boot': {'capabilities': [
                 {'id': 'ake', 'video': {'models': MODELS, 'default_model': MODELS[0]}},
                 {'id': 'julun', 'video': {'models': ['different-exact-id'], 'default_model': 'different-exact-id'}}]}}
    page.add_script_tag(content=HELPERS + 'const $=s=>document.querySelector(s);const S=' + json.dumps(setup) + ';' + function +
                             "document.addEventListener('change',e=>{const el=e.target;" + change + '});')
    page.evaluate("document.body.innerHTML='<form><select name=video_provider><option>ake</option><option>julun</option></select><label>视频模型'+wizardModel('video')+'</label></form>'")
    page.locator('select').select_option('julun')
    assert page.locator('datalist option').count() == 1
    assert page.locator('datalist option').get_attribute('value') == 'different-exact-id'
    assert page.locator('.pms').get_attribute('data-pms-provider') == 'julun'
    page.locator('.pms summary').click()
    page.locator('[data-pms-pick]').click()
    assert page.locator('input[name=video_model]').input_value() == 'different-exact-id'


def test_focusing_model_directly_opens_cards_and_clicking_away_closes_them(page):
    install(page)
    production(page)
    page.locator('input[data-setting=model]').click()
    playwright.expect(page.locator('.pms-card')).to_have_count(3)
    assert page.locator('.pms details').get_attribute('open') is not None
    assert page.locator('input[data-setting=model]').get_attribute('list') is None
    page.locator('h2').click()
    assert page.locator('.pms details').get_attribute('open') is None


def test_switching_keys_clears_previous_models_while_request_is_pending(page):
    install(page, admin=True)
    page.evaluate("fetch=url=>new Promise(resolve=>{(window.pending??=[]).push({url,resolve})});document.body.innerHTML=ProviderModelStats.markup('ake','',null,'view');document.querySelector('.pms').dataset.pmsKey='first'")
    page.wait_for_function('window.pending?.length===1')
    page.evaluate("pending[0].resolve({ok:true,json:async()=>({state:'ready',models:[{model:'first-only',rate:95}],catalog:{models:['first-only']}})})")
    playwright.expect(page.locator('.pms-card')).to_contain_text('first-only')
    page.evaluate("document.querySelector('.pms').dataset.pmsKey='second'")
    page.wait_for_function('pending.length===2')
    assert page.locator('.pms-card').count() == 0
    page.evaluate("pending[1].resolve({ok:true,json:async()=>({state:'ready',models:[{model:'second-only',rate:85}],catalog:{models:['second-only']}})})")
    playwright.expect(page.locator('.pms-card')).to_contain_text('second-only')
