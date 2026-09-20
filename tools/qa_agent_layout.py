"""Offline browser regression for long trees, sticky controls and stable media."""
from pathlib import Path
import json
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from playwright.sync_api import sync_playwright
from core import paths, agent_runtime
from core.store import Project, write_json, write_text
from server import app
from test_agent_workspace import image


def main():
    out = ROOT / "outputs" / "agent-layout-qa" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    paths.set_data_dir(str(out / "data"))
    cfg = app.load_config()
    cfg["projects_dir"] = str(out / "projects")
    cfg["providers"] = {"chaomo": {"image_1k_api_key": "QA-only", "image_4k_api_key": "QA-only"}}
    app.save_config(cfg)
    cap = next(c for c in app.caps_of(cfg) if c["id"] == "chaomo")
    pj = Project(str(out / "projects" / "313项旧项目"))
    pj.init_dirs(); pj.save_meta({"title": "313 项旧项目布局验收", "system": "v34"})
    write_text(pj.p("03_提示词", "image.txt"), "离线布局验收，不提交生产")
    tasks = [{"key": f"asset{i:03}", "episode": f"EP{i // 110 + 1:02}",
              "output": f"02_固定资产/asset{i:03}.png", "prompt_ref": "03_提示词/image.txt",
              "params": {"size": cap["image"]["sizes"][0]}, "reference_images": []} for i in range(313)]
    pj.save_tasks({"asset_tasks": tasks})
    write_json(pj.p("07_检查与记录", "production-settings.json"), {
        "asset": {"provider": "chaomo", "model": cap["image"]["default_model"], "concurrency": 3}})
    for i in range(12):
        image(pj.p("02_固定资产", f"asset{i:03}.png"))
    # Any accidental production call must fail without contacting a provider.
    def forbid(*args, **kwargs):
        raise AssertionError("Layout acceptance must never generate media")
    agent_runtime.start = forbid
    srv = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True); thread.start()
    url = f"http://127.0.0.1:{srv.server_port}"
    errors, external, checks = [], [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1050})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("request", lambda r: external.append(r.url) if not r.url.startswith(url) and not r.url.startswith("data:") else None)
            page.goto(url)
            page.locator('[data-action="open"]').click()
            page.locator('#tree-mode').select_option('episode')
            folder = page.locator('[data-folder="episode:EP01"]')
            folder.locator('summary strong').click()
            assert folder.evaluate('(e)=>e.open')
            tree = page.locator('#resource-tree')
            assert tree.evaluate('(e)=>e.scrollHeight>e.clientHeight && e.clientHeight<=620')
            page.locator('[data-action="detail"][data-id="asset000"]').click()
            page.locator('#resource-detail img').wait_for()
            page.evaluate("""() => {
              window.qaDetailImage=document.querySelector('#resource-detail img');
              document.querySelector('#resource-tree').scrollTop=1200;
              document.activeElement.blur();
            }""")
            scroll = tree.evaluate('(e)=>e.scrollTop')
            page.wait_for_timeout(8500)
            assert folder.evaluate('(e)=>e.open')
            assert abs(tree.evaluate('(e)=>e.scrollTop') - scroll) < 2
            assert page.evaluate("window.qaDetailImage===document.querySelector('#resource-detail img')")
            # The sticky parent heading remains clickable while deep in its children.
            folder.locator('summary strong').click()
            assert not folder.evaluate('(e)=>e.open')
            folder.locator('summary').focus();page.keyboard.press('Enter')
            assert folder.evaluate('(e)=>e.open')
            page.locator('[data-action="refresh"]').click()
            assert folder.evaluate('(e)=>e.open')
            checks.append('313 项旧任务独立滚动 / 父标题收起及键盘展开 / 定时刷新保留目录、滚动和详情图片')

            page.locator('#asset-search').fill('asset0')
            assert folder.evaluate('(e)=>e.open')
            folder.locator('summary strong').click()
            assert not folder.evaluate('(e)=>e.open')
            page.locator('[data-action="refresh"]').click()
            assert not folder.evaluate('(e)=>e.open')
            page.evaluate('document.activeElement.blur()')
            page.wait_for_timeout(4500)
            assert not folder.evaluate('(e)=>e.open')
            folder.locator('summary input').uncheck()
            page.wait_for_function("document.querySelector('#selection-count').textContent.includes('213/313')")
            assert not folder.evaluate('(e)=>e.open')
            checks.append('搜索时父目录可收起 / 分组勾选不触发展开 / 刷新不强制重开')

            page.locator('[data-action="preview"]').click()
            page.locator('[data-action="start"]:enabled').wait_for()
            page.evaluate('document.activeElement.blur()')
            page.wait_for_timeout(4500)
            assert page.locator('[data-action="start"]').is_enabled()
            pos = page.evaluate("""() => ({start:document.querySelector('[data-action=start]').getBoundingClientRect().top,
              services:document.querySelector('#production-services').getBoundingClientRect().top,
              header:document.querySelector('#sticky-header').getBoundingClientRect().bottom})""")
            assert pos['header'] <= pos['start'] < pos['services'], pos
            page.screenshot(path=str(out / 'production-top.png'), full_page=True)
            page.evaluate('scrollTo(0,document.documentElement.scrollHeight)')
            assert abs(page.locator('#sticky-header').bounding_box()['y']) < 1
            assert page.locator('#runtime').bounding_box()['y'] >= 0
            page.locator('#runtime summary').click()
            assert page.locator('#live-usage').is_visible()
            page.screenshot(path=str(out / 'sticky-runtime.png'))
            page.locator('#runtime summary').click()
            checks.append('开始按钮与生产计划位于顶部 / 自动刷新保留有效计划 / 运行状态吸顶')

            # A different selection invalidates the displayed plan immediately.
            page.locator('[data-action="select-all"]').click()
            page.wait_for_function("document.querySelector('#selection-count').textContent.includes('313/313')")
            assert page.locator('[data-action="start"]').is_disabled()
            page.locator('[data-action="preview"]').click()
            page.locator('[data-action="start"]:enabled').wait_for()
            page.locator('[data-page="settings"]').click()
            page.locator('[data-page="production"]').click()
            assert page.locator('[data-action="start"]').is_disabled()
            checks.append('勾选变更或切换页面使过期生产计划失效')

            page.locator('[data-page="outputs"]').click()
            page.locator('#output-kind').select_option('图片')
            page.locator('#output-search').fill('asset00')
            page.locator('h2').click()
            assert page.locator('#output-list img').count() == 10
            page.evaluate("window.qaOutputImage=document.querySelector('#output-list img');scrollTo(0,500)")
            y = page.evaluate('scrollY')
            page.wait_for_timeout(8500)
            assert page.locator('#output-search').input_value() == 'asset00'
            assert page.locator('#output-kind').input_value() == '图片'
            assert page.evaluate("window.qaOutputImage===document.querySelector('#output-list img')")
            assert abs(page.evaluate('scrollY') - y) < 2
            # New outputs arrive without replacing already mounted image elements.
            page.locator('#output-search').fill('');page.locator('h2').click()
            page.evaluate("window.qaOutputImage=document.querySelector('#output-list img')")
            image(pj.p('02_固定资产', 'asset012.png'))
            page.wait_for_function("document.querySelectorAll('#output-list img').length===13", timeout=15000)
            assert page.evaluate("window.qaOutputImage===document.querySelector('#output-list img')")
            page.screenshot(path=str(out / 'stable-images.png'))
            checks.append('图片页连续刷新保留搜索、类型、滚动与图片节点 / 新产物增量加入')

            for width in (1440, 1024, 736, 360, 320):
                page.set_viewport_size({"width": width, "height": 1000})
                page.locator('[data-page="production"]').click()
                page.locator('#asset-search').fill('');page.locator('h2').click()
                page.evaluate('scrollTo(0,document.documentElement.scrollHeight)')
                layout = page.evaluate("""() => ({w:innerWidth,doc:document.documentElement.scrollWidth,
                  header:document.querySelector('#sticky-header').getBoundingClientRect().top,
                  tree:document.querySelector('#resource-tree').clientHeight})""")
                assert layout['doc'] <= width + 1 and abs(layout['header']) < 1 and layout['tree'] <= 620, layout
                if width == 360:
                    page.screenshot(path=str(out / 'mobile-tree.png'))
            checks.append('五种宽度下长列表与固定状态栏不溢出')
            assert not errors, errors
            assert not external, external
            browser.close()
        (out / 'results.json').write_text(json.dumps({"checks": checks, "errors": errors, "external": external}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({"ok": True, "checks": len(checks), "evidence": str(out)}, ensure_ascii=False))
    finally:
        srv.shutdown();srv.server_close();thread.join(3)


if __name__ == '__main__':
    main()
