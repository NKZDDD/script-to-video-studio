"""Exercise actual output-page event handlers in an isolated browser with mocked APIs."""
import re
from pathlib import Path

import pytest


def test_output_selection_batch_delete_and_image_reroll(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    root = Path(__file__).resolve().parents[1]
    source = (root / "web/agent.js").read_text(encoding="utf-8")
    startup = "safe(async()=>{await window.StudioLogin.ready;await reloadBoot();render();tick();await refreshCloudConfiguration();})();"
    assert startup in source
    source = source.replace(startup, "loadProject=async()=>{outputList();};window.testUI={S,outputs,outputList};")
    html = re.sub(r"<script.*?</script>", "", (root / "web/agent.html").read_text(encoding="utf-8"))
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", lambda r: r.fulfill(status=200, body=html, content_type="text/html")
                   if r.request.url == "http://studio.test/" else r.abort())
        page.goto("http://studio.test/")
        page.add_style_tag(content=(root / "web/agent.css").read_text(encoding="utf-8"))
        page.evaluate("""() => {
          window.calls=[];window.prompts=[];window.confirm=msg=>{prompts.push(msg);return true;};
          window.fetch=async(url,options={})=>{
            const body=options.body?JSON.parse(options.body):null;calls.push({url,body});
            let result={ok:true};
            if(url.includes('/files?'))result={items:[]};
            if(url.endsWith('/delete-plan'))result={items:body.keys.map(key=>({key,name:key,kind:'asset',output:key+'.png',exists:true,relation:'本身'}))};
            if(url.endsWith('/delete')){
              result={moved:body.keys.map(key=>({key})),folder:'recycle'};
              for(const t of testUI.S.data.tasks)if(body.keys.includes(t.key))t.done=false;
            }
            if(url.endsWith('/reroll'))result={backup:'images/_抽卡备份/asset.png',job_id:'test'};
            return {ok:true,json:async()=>result};
          };
        }""")
        page.add_script_tag(content=source)
        page.evaluate("""() => {
          const s=testUI.S;s.root='project-a';s.page='outputs';s.boot={features:{},semantics:{CHAR:'人物'},kinds:{asset:'资产图',video:'视频'}};
          s.data={meta:{title:'测试'},resources:[],publication:{},running:{busy:{}},tasks:[
            {key:'asset',kind:'asset',output:'asset.png',done:true},
            {key:'board',kind:'board',output:'board.png',done:true},
            {key:'video',kind:'video',output:'video.mp4',done:true},
            {key:'busy',kind:'asset',output:'busy.png',done:true}]};
          testUI.outputs();
        }""")
        assert page.locator('[data-output-key="busy"]').is_disabled()
        page.locator('[data-action="reroll"][data-key="asset"]').click()
        page.wait_for_function("calls.some(x=>x.url.endsWith('/reroll'))")
        assert page.evaluate("calls.find(x=>x.url.endsWith('/reroll')).body") == {"root": "project-a", "key": "asset"}
        assert "下游" in page.evaluate("prompts[0]")
        page.locator('[data-output-key="asset"]').check()
        page.locator('#output-search').fill('board')
        page.locator('[data-action="output-all"]').click()
        assert page.evaluate("[...testUI.S.outputSelection.keys]") == ["asset", "board"]
        page.evaluate("testUI.outputList()")
        assert page.locator('[data-output-key="board"]').is_checked()
        page.locator('[data-action="output-delete"]').click()
        page.wait_for_selector('[data-delete-key="asset"]')
        request = page.evaluate("calls.find(x=>x.url.endsWith('/delete-plan')).body")
        assert request == {"root": "project-a", "keys": ["asset", "board"], "up": False, "down": False, "siblings": False}
        assert not page.locator('[data-del="down"]').is_checked()
        page.locator('[data-delete-key="board"]').uncheck()
        page.locator('[data-action="delete-run"]').click()
        page.wait_for_function("calls.some(x=>x.url.endsWith('/delete'))")
        assert page.evaluate("calls.find(x=>x.url.endsWith('/delete')).body.keys") == ['asset']
        assert page.evaluate("[...testUI.S.outputSelection.keys]") == ['board']
        page.locator('[data-action="output-none"]').click()
        assert page.locator('[data-action="output-delete"]').is_disabled()
        page.locator('[data-action="output-all"]').click()
        page.evaluate("testUI.S.root='project-b';testUI.outputList()")
        assert page.evaluate("testUI.S.outputSelection.keys.size") == 0
        page.screenshot(path=str(tmp_path / 'output-actions.png'))
        assert not errors, errors
        browser.close()



def test_production_delete_waits_for_saved_selection_and_reuses_preview(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    root = Path(__file__).resolve().parents[1]
    source = (root / "web/agent.js").read_text(encoding="utf-8")
    startup = "safe(async()=>{await window.StudioLogin.ready;await reloadBoot();render();tick();await refreshCloudConfiguration();})();"
    assert startup in source
    source = source.replace(startup, "window.testUI={S,production,loadProject};")
    html = re.sub(r"<script.*?</script>", "", (root / "web/agent.html").read_text(encoding="utf-8"))
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 950})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", lambda r: r.fulfill(status=200, body=html, content_type="text/html")
                   if r.request.url == "http://studio.test/" else r.abort())
        page.goto("http://studio.test/")
        page.add_style_tag(content=(root / "web/agent.css").read_text(encoding="utf-8"))
        page.add_script_tag(content=source)
        page.evaluate("""() => {
          window.calls=[];
          window.serverData={root:'project-a',meta:{title:'生产页删除测试'},production:{},publication:{revision:'r1',errors:[]},jobs:[],failures:[],running:{busy:{state:'running'}}};
          serverData.tasks=['asset','hidden','invalid','empty','busy','other'].map(key=>({key,kind:'asset',output:'images/'+key+'.png',
            done:!['invalid','empty'].includes(key),exists:key!=='empty',selected:!['asset','other'].includes(key),missing_selection:[],dependencies:[],params:{}}));
          serverData.resources=serverData.tasks.map(t=>({id:t.key,task_key:t.key,name:t.key,semantic:'CHAR',parent_id:null,episodes:[]}));
          window.fetch=async(url,options={})=>{
            const body=options.body?JSON.parse(options.body):null;calls.push({url,body});let result={ok:true};
            if(url.endsWith('/selections')){
              await new Promise(resolve=>{window.releaseSelection=resolve;});
              for(const t of serverData.tasks)if(t.key in body.changes)t.selected=body.changes[t.key];
            }
            if(url.includes('/project?'))result=structuredClone(serverData);
            if(url.endsWith('/delete-plan'))result={items:body.keys.map(key=>{const t=serverData.tasks.find(t=>t.key===key);return {...t,name:key,relation:'本身'};})};
            if(url.endsWith('/delete')){
              result={moved:body.keys.map(key=>({key})),folder:'recycle'};
              for(const t of serverData.tasks)if(body.keys.includes(t.key)){t.done=false;t.exists=false;}
            }
            return {ok:true,json:async()=>result};
          };
          const s=testUI.S;s.root='project-a';s.page='production';s.filters.asset='asset';
          s.boot={features:{},semantics:{CHAR:'人物'},kinds:{asset:'资产图'},capabilities:[],retry:0};
          s.data=structuredClone(serverData);testUI.production();
        }""")
        page.locator('[data-action="detail"][data-id="asset"]').click()
        assert page.locator('[data-action="delete"][data-key="asset"]').count() == 1
        page.locator('[data-action="select-visible"]').click()
        page.wait_for_function("typeof releaseSelection==='function'")
        page.locator('[data-action="production-delete"]').click()
        assert not page.evaluate("calls.some(x=>x.url.endsWith('/delete-plan'))")
        page.evaluate("releaseSelection()")
        page.wait_for_selector('[data-delete-key="asset"]')
        assert page.locator('#resource-tree input[data-key="asset"]').is_checked()
        assert '5/6' in page.locator('#selection-count').inner_text()
        request = page.evaluate("calls.find(x=>x.url.endsWith('/delete-plan')).body")
        assert request == {"root":"project-a", "keys":["asset","hidden","invalid","empty","busy"], "up":False,"down":False,"siblings":False}
        assert page.locator('[data-delete-key="hidden"]').is_checked()
        assert page.locator('[data-delete-key="invalid"]').is_checked()
        assert page.locator('[data-delete-key="empty"]').is_disabled()
        assert not page.locator('[data-delete-key="busy"]').is_checked()
        page.locator('[data-delete-key="busy"]').check()
        assert page.locator('[data-action="delete-run"]').is_disabled()
        page.locator('[data-delete-key="busy"]').uncheck()
        page.locator('[data-delete-key="hidden"]').uncheck()
        page.screenshot(path=str(tmp_path / 'production-delete-preview.png'))
        page.locator('[data-action="delete-run"]').click()
        page.wait_for_function("calls.some(x=>x.url.endsWith('/delete'))")
        assert page.evaluate("calls.find(x=>x.url.endsWith('/delete')).body.keys") == ['asset','invalid']
        page.wait_for_function("!document.querySelector('#dialog').open && !testUI.S.data.tasks[0].exists")
        assert page.evaluate("testUI.S.page") == 'production'
        assert page.evaluate("testUI.S.data.tasks.length") == 6
        assert page.evaluate("testUI.S.data.tasks.find(t=>t.key==='hidden').exists")
        assert page.locator('[data-action="start"]').is_disabled()
        count = page.evaluate("calls.filter(x=>x.url.endsWith('/delete-plan')).length")
        page.evaluate("async()=>{serverData.tasks.forEach(t=>t.selected=false);await testUI.loadProject();}")
        page.locator('[data-action="production-delete"]').click()
        page.wait_for_function("document.querySelector('#message').textContent.includes('请先在生产树勾选')")
        assert page.evaluate("calls.filter(x=>x.url.endsWith('/delete-plan')).length") == count
        assert not errors, errors
        browser.close()
