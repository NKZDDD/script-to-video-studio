"""Offline browser acceptance. Isolated projects/config and fake media worker only.

Run: py tools/qa_agent_workspace.py
Creates evidence under outputs/agent-ui-qa/<timestamp>; never uses personal keys.
"""
from pathlib import Path
import json
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from core import paths, agent_projects as A, agent_runtime as R
from core.store import write_json
from server import app
from test_agent_workspace import plan_for, publish, image


def main():
    out = ROOT / "outputs" / "agent-ui-qa" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    paths.set_data_dir(str(out / "data"))
    cfg = app.load_config()
    cfg["projects_dir"] = str(out / "projects")
    cfg["providers"] = {"chaomo": {"image_1k_api_key": "QA-secret-never-display", "image_4k_api_key": "QA-secret-never-display"}}
    app.save_config(cfg)
    caps = app.caps_of(cfg)
    cap = next(c for c in caps if c["id"] == "chaomo")
    pj = A.create_project(cfg["projects_dir"], {"title": "浏览器验收项目", "source": "用于界面验收的原文。",
                         "options": {"image_provider": "chaomo", "image_model": cap["image"]["default_model"]}}, caps)
    publish(pj, plan_for(pj));A.scan(pj)
    calls=[]
    prompts={}
    def make_worker(project, pc, kind, llm_factory=None):
        assert pc.get("agent_mode") and llm_factory is None
        def worker(task, log, cancel):
            calls.append(task["key"])
            prompts[task["key"]]=A.inside(project.root,task["prompt_ref"]).read_text(encoding="utf-8")
            image(str(A.inside(project.root, task["output"])))
            log("本地验收生成，不请求服务商")
            return {"output":task["output"]}
        return worker
    R.produce.make_image_worker=make_worker
    srv=app.ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
    thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
    url=f"http://127.0.0.1:{srv.server_port}"
    errors,external,checks,overflow=[],[],[],[]
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel="chrome",headless=True)
            page=browser.new_page(viewport={"width":1440,"height":1050},device_scale_factor=1)
            page.on("pageerror",lambda e:errors.append(str(e)))
            page.on("request",lambda r:external.append(r.url) if not r.url.startswith(url) and not r.url.startswith("data:") else None)
            page.goto(url)
            page.locator('[data-action="open"]').first.wait_for()
            assert "QA-secret-never-display" not in page.content()
            page.locator('#project-search').fill('不存在项目')
            assert page.locator('[data-action="open"]').count()==0
            page.locator('#project-search').fill('浏览器')
            page.locator('[data-action="open"]').click()
            try:
                page.locator('#resource-tree').wait_for()
            except Exception:
                page.screenshot(path=str(out/'failure.png'),full_page=True)
                print(json.dumps({"page_errors":errors,"message":page.locator('#message').inner_text()},ensure_ascii=False))
                raise
            page.locator('#tree-mode').select_option('episode')
            page.locator('details[data-folder] summary').click(position={"x":260,"y":15})
            page.locator('input[data-key="asset0"]').wait_for(state='visible')
            page.locator('input[data-key="asset0"]').uncheck()
            page.wait_for_function("document.querySelector('#selection-count').textContent.includes('1/2')")
            page.locator('[data-action="preview"]').click()
            page.wait_for_function("document.querySelector('#production-plan').textContent.includes('缺少勾选')")
            assert page.locator('[data-action="start"]').is_disabled()
            page.locator('[data-action="dependencies"]').click()
            page.wait_for_function("document.querySelector('#selection-count').textContent.includes('2/2')")
            page.locator('[data-action="detail"][data-id="asset0"]').click()
            page.locator('[data-action="prompt"]').click()
            page.locator('#prompt-edit').wait_for()
            assert page.locator('#prompt-edit').input_value()=='生成测试图片'
            page.locator('#prompt-edit').fill('   ')
            page.locator('[data-action="save-prompt"]').click()
            assert page.locator('#prompt-edit-error').inner_text()=='提示词不能为空'
            edited='人物站在窗边，保持面部与服装一致。\n背景改为黄昏的暖色光线。'
            page.locator('#prompt-edit').fill(edited)
            page.locator('#dialog details').click()
            page.screenshot(path=str(out/'prompt-editor.png'),full_page=True)
            page.locator('[data-action="save-prompt"]').click()
            page.locator('#prompt-edit').wait_for(state='hidden')
            assert A.scan(pj)['revision']=='r0002'
            assert not calls
            assert Path(pj.p('03_提示词','releases','r0001','images','asset0.txt')).read_text(encoding='utf-8')=='生成测试图片'
            checks.append('直接编辑 / 空值拦截 / 影响范围 / 新版本保存 / 原稿保留 / 保存不生成')
            page.locator('[data-action="preview"]').click()
            page.locator('[data-action="start"]:enabled').wait_for()
            page.evaluate('scrollTo(0,0)');page.wait_for_timeout(250)
            page.screenshot(path=str(out/'production.png'),full_page=True)
            page.locator('[data-action="start"]').click()
            page.wait_for_function("document.querySelector('#jobs').textContent.includes('done')",timeout=15000)
            assert calls==['asset0','asset1'],calls
            assert prompts['asset0']==edited and prompts['asset1']=='生成测试图片',prompts
            checks.append('依赖补选 / 生产前阻塞 / 真实队列与快照 / 成品复用')
            page.locator('[data-page="outputs"]').click()
            page.locator('#output-list img').first.wait_for()
            assert page.locator('#output-list img').count()==2
            page.screenshot(path=str(out/'outputs.png'),full_page=True)
            checks.append('产物读取与图片预览')
            page.locator('[data-page="settings"]').click()
            page.locator('#provider-picker').select_option('chaomo')
            assert page.locator('[name="image_4k_api_key"]').get_attribute('type')=='password'
            assert not page.locator('[name="image_4k_api_key"]').input_value()
            page.locator('[name="poll_interval"]').fill('7')
            page.locator('[data-action="save-provider"]').click()
            page.wait_for_function("document.querySelector('[name=poll_interval]').value==='7'")
            assert app.load_config()['providers']['chaomo']['image_4k_api_key']=='QA-secret-never-display'
            page.locator('[data-tab="limits"]').click()
            page.locator('[name="global"]').fill('4')
            page.locator('[data-action="save-limits"]').click()
            page.wait_for_function("document.querySelector('#live-inflight').textContent.endsWith('/ 4')")
            page.locator('[data-tab="features"]').click()
            page.locator('[data-feature="advanced"]').check()
            page.wait_for_timeout(300)
            page.locator('[data-tab="providers"]').click()
            page.locator('#provider-picker').select_option('hvtald')
            page.locator('[name="account.0.token"]').wait_for()
            assert not page.locator('[name="account.0.token"]').input_value()
            page.screenshot(path=str(out/'settings.png'),full_page=True)
            checks.append('分组密钥留空保留 / 多账号表单 / 共享并发 / 功能包')
            page.locator('[data-page="projects"]').click()
            page.locator('[data-action="new"]').click()
            page.locator('[name="title"]').fill('向导创建验收')
            page.locator('[name="source"]').fill('主角走进森林，看到远处的灯火。')
            for _ in range(4): page.locator('[data-action="wizard-next"]').click()
            page.locator('[data-action="wizard-create"]').click()
            page.locator('#agent-template').wait_for()
            text=page.locator('#agent-template').input_value()
            assert '向导创建验收' in text and str(out/'projects') in text
            assert 'Codex' not in text and '老李' not in text
            created=list((out/'projects').glob('向导创建验收*'))
            assert len(created)==1 and len(A.file_manifest(created[0]/'00_项目说明/skill/production-skill'))==51
            page.screenshot(path=str(out/'handoff.png'),full_page=True)
            checks.append('五步向导 / 默认值 / 完整技能快照 / 本机 Agent 模板')
            # An old project's delayed preview must not overwrite the next project's plan.
            page.locator('#project-picker').select_option(pj.root)
            page.locator('[data-action="preview"]').wait_for()
            page.evaluate("""() => {
              window.qaOriginalFetch = window.fetch;
              window.fetch = async (...args) => {
                const response = await window.qaOriginalFetch(...args);
                if(String(args[0]).includes('/api/agent/preview'))
                  await new Promise(resolve => setTimeout(resolve, 1200));
                return response;
              };
            }""")
            page.locator('[data-action="preview"]').click()
            page.locator('#project-picker').select_option(str(created[0]))
            page.wait_for_timeout(1600)
            assert page.locator('#production-plan').inner_text()==''
            assert page.locator('#project-picker').input_value()==str(created[0])
            page.evaluate('window.fetch = window.qaOriginalFetch')
            checks.append('切换项目时丢弃旧项目延迟返回的生产计划')
            for width in [1440,1024,736,360,320]:
                page.set_viewport_size({"width":width,"height":1000})
                for name in ['projects','production','settings','outputs']:
                    page.locator(f'[data-page="{name}"]').click()
                    page.wait_for_timeout(100)
                    size=page.evaluate('({w:innerWidth,doc:document.documentElement.scrollWidth})')
                    if size['doc']>size['w']+1:overflow.append([width,name,size])
                if width==360:page.screenshot(path=str(out/'mobile.png'),full_page=True)
            checks.append('桌面与窄屏四页面布局')
            assert not errors,errors
            assert not external,external
            assert not overflow,overflow
            browser.close()
        (out/'results.json').write_text(json.dumps({"checks":checks,"errors":errors,"external":external,"overflow":overflow},ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({"ok":True,"checks":len(checks),"evidence":str(out)},ensure_ascii=False))
    finally:
        srv.shutdown();srv.server_close();thread.join(3)


if __name__=='__main__':
    main()
