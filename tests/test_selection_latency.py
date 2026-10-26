"""Selection saves must stay independent of project/output scans on shared disks."""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import agent_projects as A
from core.store import Project, read_json, write_json
from server import agent_api


def forbidden(*args, **kwargs):
    raise AssertionError("selection must not scan projects, capabilities or outputs")


def test_registered_project_lookup_checks_only_the_requested_directory(tmp_path, monkeypatch):
    base = tmp_path / "projects"
    other = tmp_path / "shared"
    root = base / "MixedCase"
    root.mkdir(parents=True)
    extra = other / "two"
    extra.mkdir(parents=True)
    (root / "nested").mkdir()
    file = base / "not-a-directory"
    file.write_text("file")
    cfg = {"projects_dir": str(base), "agent_project_dirs": [str(other)]}
    monkeypatch.setattr(agent_api, "projects", forbidden)
    checked = []
    isdir = agent_api.os.path.isdir
    monkeypatch.setattr(agent_api.os.path, "isdir", lambda path: checked.append(path) or isdir(path))
    assert agent_api.project(str(root), cfg).root == str(root)
    assert checked == [str(root)]
    assert agent_api.project(str(extra), cfg).root == str(extra)
    assert agent_api.project(str(root / ".." / "MixedCase"), cfg).root == str(root)
    for invalid in (base, tmp_path, root / "nested", base / "missing", file):
        with pytest.raises(ValueError, match="未登记"):
            agent_api.project(str(invalid), cfg)


def test_selection_saves_read_tasks_once_write_once_and_return_all_flags(tmp_path, monkeypatch):
    root = tmp_path / "project"
    pj = Project(str(root))
    pj.init_dirs()
    plan = {kind + "_tasks": [{"key": kind}] for kind in A.LEGACY_KINDS}
    pj.save_tasks(plan)
    cfg = {"projects_dir": str(tmp_path)}
    calls = {"tasks": 0, "writes": 0}
    original_tasks, original_write = Project.tasks, A.write_json
    def tasks(self):
        calls["tasks"] += 1
        return original_tasks(self)
    def write(*args):
        calls["writes"] += 1
        return original_write(*args)
    monkeypatch.setattr(Project, "tasks", tasks)
    monkeypatch.setattr(A, "write_json", write)
    monkeypatch.setattr(A, "view", forbidden)
    monkeypatch.setattr(agent_api, "projects", forbidden)
    app = SimpleNamespace(load_config=lambda: cfg, caps_of=forbidden)
    result = agent_api.post(app, "/api/agent/selections", {"root": str(root), "changes": {"asset": False}})
    expected = {kind: kind != "asset" for kind in A.LEGACY_KINDS}
    assert result == {"ok": True, "selections": expected}
    assert calls == {"tasks": 1, "writes": 1}
    assert read_json(pj.p("07_检查与记录", "selections.json")) == expected
    A.select(pj, {"asset": False})
    assert calls["writes"] == 1
    with pytest.raises(ValueError):
        A.select(pj, {"asset": True, "unknown": False})
    assert read_json(pj.p("07_检查与记录", "selections.json")) == expected
    assert calls["writes"] == 1


def test_reading_existing_selection_does_not_rewrite_the_shared_file(tmp_path, monkeypatch):
    pj = Project(str(tmp_path))
    write_json(pj.p("07_检查与记录", "selections.json"), {"asset": False})
    monkeypatch.setattr(A, "write_json", forbidden)
    assert A.selections(pj, [{"key": "asset"}]) == {"asset": False}


def test_selection_feedback_queue_failure_and_stale_refresh_in_browser(tmp_path):
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
          window.calls=[];window.writes=[];window.refreshes=[];
          window.serverData={root:'project-a',meta:{title:'勾选延迟测试'},production:{},publication:{revision:'r1',errors:[]},jobs:[],failures:[],running:{}};
          serverData.tasks=['parent','child','hidden'].map(key=>({key,kind:'asset',output:'images/'+key+'.png',done:false,
            exists:false,selected:false,missing_selection:key==='child'?['parent']:[],dependencies:key==='child'?['parent']:[],params:{}}));
          serverData.resources=serverData.tasks.map(t=>({id:t.key,task_key:t.key,name:t.key,semantic:'CHAR',parent_id:null,episodes:[]}));
          window.fetch=async(url,options={})=>{
            const body=options.body?JSON.parse(options.body):null;calls.push({url,body});let result={ok:true};
            if(url.endsWith('/selections')){
              const ok=await new Promise(resolve=>writes.push(resolve));
              if(!ok)return {ok:false,json:async()=>({error:'共享盘写入失败'})};
              for(const t of serverData.tasks)if(t.key in body.changes)t.selected=body.changes[t.key];
              result.selections=Object.fromEntries(serverData.tasks.map(t=>[t.key,t.selected]));
            }
            if(url.includes('/project?')){
              result=structuredClone(serverData);
              if(window.holdRefresh)await new Promise(resolve=>refreshes.push(resolve));
            }
            if(url.endsWith('/preview'))result={selected:3,reuse:0,todo:3,revision:'r1',rows:[],blocked:[],soften_rounds:0};
            return {ok:true,json:async()=>result};
          };
          const s=testUI.S;s.root='project-a';s.page='production';s.treeMode='kind';s.filters.asset='images';
          s.boot={features:{},semantics:{CHAR:'人物'},kinds:{asset:'资产图'},capabilities:[],retry:0};
          s.data=structuredClone(serverData);testUI.production();
        }""")
        checkbox = lambda key: page.locator(f'#resource-tree input[data-key="{key}"]')
        page.locator('[data-action="select-visible"]').click()
        page.wait_for_function("writes.length===1")
        assert checkbox("parent").is_checked()
        assert checkbox("child").is_checked()
        assert "正在保存勾选" in page.locator('#selection-count').inner_text()
        assert page.evaluate("testUI.S.data.tasks[1].missing_selection") == []
        page.locator('[data-action="preview"]').click()
        assert not page.evaluate("calls.some(c=>c.url.endsWith('/preview'))")
        page.evaluate("writes.shift()(true)")
        page.wait_for_function("calls.some(c=>c.url.endsWith('/preview'))")
        assert not page.evaluate("calls.some(c=>c.url.includes('/project?'))")
        page.wait_for_function("!document.querySelector('#selection-count').textContent.includes('正在保存')")

        # Two overlapping changes fail: restore the last confirmed state, never
        # the first request's unconfirmed optimistic value.
        page.locator('[data-action="clear-visible"]').click()
        page.wait_for_function("writes.length===1")
        assert not checkbox("parent").is_checked()
        checkbox("parent").check()
        page.evaluate("writes.shift()(false)")
        page.wait_for_function("writes.length===1")
        assert checkbox("parent").is_checked()
        page.evaluate("writes.shift()(false)")
        page.wait_for_function("!document.querySelector('#selection-count').textContent.includes('正在保存')")
        assert all(checkbox(key).is_checked() for key in ['parent', 'child', 'hidden'])
        assert "勾选保存失败" in page.locator('#message').inner_text()

        # A project refresh started before the edit must not undo a saved edit.
        page.evaluate("() => {window.holdRefresh=true;window.refreshDone=testUI.loadProject();}")
        page.wait_for_function("refreshes.length===1")
        page.locator('[data-action="clear-visible"]').click()
        page.wait_for_function("writes.length===1")
        page.evaluate("writes.shift()(true)")
        page.wait_for_function("!document.querySelector('#selection-count').textContent.includes('正在保存')")
        page.evaluate("async() => {refreshes.shift()();await refreshDone;}")
        assert not checkbox("parent").is_checked()
        assert page.evaluate("testUI.S.data.tasks[1].missing_selection") == ['parent']

        # A late save belongs to its original project, even after navigation.
        checkbox("parent").check()
        page.wait_for_function("writes.length===1")
        page.evaluate("() => {testUI.S.root='project-b';testUI.S.data={...structuredClone(serverData),root:'project-b'};testUI.production();}")
        page.evaluate("writes.shift()(true)")
        page.wait_for_function("calls.filter(c=>c.url.endsWith('/selections')).length===5")
        assert not checkbox("parent").is_checked()
        assert not errors, errors
        browser.close()
