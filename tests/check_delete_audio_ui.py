"""Manual browser smoke: py tests/check_delete_audio_ui.py (Playwright + Edge).

Runs the actual page event handlers against mocked APIs and an in-memory WAV.
Does not open user projects, delete real artifacts, or contact providers.
"""
import base64
import io
import re
import wave
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
source = (root / 'web/agent.js').read_text(encoding='utf-8')
source = source.replace('safe(async()=>{await reloadBoot();render();tick();})();', '''
loadProject=async()=>{};
window.testUI={S,loadDelete,renderDelete,audioCards,dialog,closeDialog};
''')
html = re.sub(r'<script.*?</script>', '', (root / 'web/agent.html').read_text(encoding='utf-8'))
stream=io.BytesIO()
with wave.open(stream,'wb') as f:
    f.setnchannels(1);f.setsampwidth(2);f.setframerate(8000);f.writeframes(b'\0\0'*8000)
raw=stream.getvalue()
with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True)
    page = browser.new_page(viewport={'width':1100,'height':850})
    errors=[]
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.route('**/*', lambda r: r.fulfill(status=200, body=html, content_type='text/html') if r.request.url=='http://studio.test/' else r.abort())
    page.route('**/file?*', lambda r: r.fulfill(status=200, body=raw, content_type='audio/wav'))
    page.goto('http://studio.test/')
    page.add_style_tag(content=(root/'web/agent.css').read_text(encoding='utf-8'))
    page.evaluate('''() => {
      window.calls=[];window.items=[
        {key:'root',name:'交接板',output:'board.png',kind:'handoff',relation:'本身',exists:true},
        {key:'child',name:'分段视频',output:'child.mp4',kind:'video',relation:'下游',exists:true},
        {key:'busy',name:'正在生成',output:'busy.mp4',kind:'video',relation:'下游',exists:true},
        {key:'empty',name:'没有文件',output:'empty.png',kind:'handoff',relation:'下游',exists:false}];
      window.fetch=async(url, options={})=>{
        const body=options.body?JSON.parse(options.body):null;calls.push({url,body});
        return {ok:true,json:async()=>url.includes('delete-plan')?{items}:url.endsWith('/delete')?{moved:[{key:'child'}],folder:'recycle'}:{ok:true}};
      };
    }''')
    page.add_script_tag(content=source)
    page.evaluate('''async()=>{
      const t=testUI;t.S.boot={kinds:{video:'分段视频',handoff:'交接板'}};
      t.S.data={running:{busy:{state:'running'}}};t.S.root='test-project';
      t.dialog('删除产物','');t.S.del={root:'test-project',key:'root',down:true,up:false,siblings:false,items:[],choices:{},request:0,loading:false};
      await t.loadDelete();
    }''')
    assert page.locator('[data-delete-key]:checked').count()==2
    assert page.locator('[data-delete-key="empty"]').is_disabled()
    page.locator('[data-delete-key="root"]').uncheck()
    page.locator('[data-del="up"]').check()
    assert not page.locator('[data-delete-key="root"]').is_checked()
    page.locator('[data-delete-key="busy"]').check()
    assert page.locator('[data-action="delete-run"]').is_disabled()
    page.locator('[data-action="delete-none"]').click()
    assert page.locator('[data-action="delete-run"]').is_disabled()
    page.locator('[data-action="delete-all"]').click()
    assert not page.locator('[data-delete-key="busy"]').is_checked()
    page.locator('[data-delete-key="root"]').uncheck()
    page.screenshot(path=str(root/'outputs/delete-select-ui.png'))
    page.locator('[data-action="delete-run"]').click()
    assert page.evaluate("calls.find(x=>x.url.endsWith('/delete')).body.keys")==['child']
    page.evaluate('''()=>{testUI.closeDialog();document.querySelector('#main').innerHTML=testUI.audioCards({key:'video',reference_audios:[{name:'角色声音.wav',file_ref:'voice.wav'}]});}''')
    assert page.locator('audio[controls]').count()==1
    assert 'voice.wav' in page.locator('audio').get_attribute('src')
    page.evaluate("document.querySelector('audio').load()")
    page.wait_for_function("document.querySelector('audio').duration > 0")
    page.evaluate("document.querySelector('audio').play()")
    page.wait_for_function("document.querySelector('audio').currentTime > 0")
    page.locator('[data-action="audio-remove"]').click()
    assert page.evaluate("calls.find(x=>x.url.endsWith('/audio/remove')).body.file_ref")=='voice.wav'
    page.locator('#audio-file').set_input_files({'name':'voice.wav','mimeType':'audio/wav','buffer':raw})
    page.wait_for_function("calls.some(x=>x.url.endsWith('/audio/upload'))")
    body=page.evaluate("calls.find(x=>x.url.endsWith('/audio/upload')).body")
    assert body['key']=='video' and base64.b64decode(body['content_b64'])==raw
    page.screenshot(path=str(root/'outputs/reference-audio-ui.png'))
    assert not errors, errors
    browser.close()
print('UI PASS: exact selected deletion, busy/missing guards, selection persistence, audio playback/upload/removal')
