"""v2.2 界面自检：岗位门禁、联系方式、教育经历、AI 开关、移除成就。"""
import json, sys, threading, time, socket
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import webview
from app import paths, storage
from app.bridge import Bridge
from cdp_shot import capture

OUT = ROOT / "tools" / "_probe"
OUT.mkdir(exist_ok=True)
_s = socket.socket(); _s.bind(("127.0.0.1", 0)); PORT = _s.getsockname()[1]; _s.close()
webview.settings["ALLOW_FILE_URLS"] = True
webview.settings["REMOTE_DEBUGGING_PORT"] = PORT
bridge = Bridge()
w = webview.create_window("probe", url=(paths.ui_dir()/"index.html").as_uri(),
                          js_api=bridge, width=1440, height=900)
bridge.attach(w)
report = {}

def run():
    w.events.loaded.wait(); time.sleep(3.2)
    js = lambda c, d=None: w.evaluate_js(c)

    report["title"] = js("document.title")
    report["brand"] = js("document.querySelector('.logo-text strong').textContent")
    report["job_warn_shown"] = js("!document.getElementById('jobWarn').classList.contains('hidden')")
    report["need_job"] = js("!!document.querySelector('.need-job')")

    # 载入示例
    samples = sorted(str(p) for p in (ROOT/"samples").glob("*"))
    holder = {}
    t = threading.Thread(target=lambda: holder.update(bridge.import_files(samples)))
    t.start(); t.join(200)
    report["imported"] = len(holder.get("imported", []))
    js("window.__probeRefresh()"); time.sleep(2.2)
    report["still_need_job"] = js("!!document.querySelector('.need-job')")

    # 选择岗位
    js("document.querySelector('[data-role=backend]').click()")
    time.sleep(2.5)
    report["after_job_card"] = js("!!document.getElementById('screenCard')")
    report["job_picker_value"] = js("document.getElementById('jobSelect').value")
    report["contact_phones"] = js("Array.from(document.querySelectorAll('.contact-item')).map(e=>e.textContent.trim())")
    report["edu_entries"] = js("document.querySelectorAll('.edu-item').length")
    report["first_edu"] = js("document.querySelector('.edu-school')?.textContent.trim()")
    report["edu_tier"] = js("document.querySelector('.edu-tier')?.textContent.trim()")
    report["achievements_gone"] = js("!document.getElementById('achievementList')")
    report["ai_checkbox_disabled"] = js("document.getElementById('optAiReview').disabled")
    report["ai_hint"] = js("document.getElementById('aiToggleHint').textContent")
    report["errors"] = js("(window.__errors||[]).slice(0,5)")
    capture(PORT, OUT/"v22_screen.png")

    # 详情
    js("document.querySelector('.screen-card').click()"); time.sleep(1.4)
    report["drawer_contact"] = js("document.querySelectorAll('#drawerBody .contact-item').length")
    report["drawer_edu"] = js("document.querySelectorAll('#drawerBody .edu-item').length")
    capture(PORT, OUT/"v22_detail.png")
    js("document.getElementById('btnCloseDrawer').click()"); time.sleep(0.4)

    # 简历库 + 搜索
    js("document.querySelector('[data-view=library]').click()"); time.sleep(1.2)
    report["library_cards"] = js("document.querySelectorAll('.person-card').length")
    capture(PORT, OUT/"v22_library.png")

    # 统计（无成就）
    js("document.querySelector('[data-view=stats]').click()"); time.sleep(1.2)
    report["has_achievement_text"] = js("document.body.textContent.includes('成就')")

    # 设置：AI 面板
    js("document.querySelector('[data-view=settings]').click()"); time.sleep(1.0)
    report["ai_fields"] = js("['optAiEnabled','aiBaseUrl','aiApiKey','aiModel','aiTimeout','optAiReplace','btnAiTest'].every(id=>!!document.getElementById(id))")
    report["api_default_off"] = js("(window.__appInternals.state.apiInfo||{}).running === false")
    try:
        capture(PORT, OUT/"v22_settings.png")
    except Exception as exc:
        report["settings_shot_error"] = str(exc)[:80]

    print("REPORT:" + json.dumps(report, ensure_ascii=False))
    storage.close(); w.destroy()

webview.start(run)
(OUT/"report3.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
