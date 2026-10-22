"""界面自检：速筛流程 + Facebook 风格截图。"""
import json, sys, threading, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import webview
from app import paths, storage
from app.bridge import Bridge
from cdp_shot import capture

OUT = ROOT / "tools" / "_probe"
OUT.mkdir(exist_ok=True)
webview.settings["ALLOW_FILE_URLS"] = True
import socket
_s = socket.socket()
_s.bind(("127.0.0.1", 0))
PORT = _s.getsockname()[1]
_s.close()
webview.settings["REMOTE_DEBUGGING_PORT"] = PORT
bridge = Bridge()
w = webview.create_window("probe", url=(paths.ui_dir()/"index.html").as_uri(),
                          js_api=bridge, width=1440, height=900)
bridge.attach(w)
report = {}

def run():
    w.events.loaded.wait(); time.sleep(3.5)
    js = lambda c, d=None: w.evaluate_js(c)
    report["bridge"] = js("typeof window.pywebview")
    report["theme_bg"] = js("getComputedStyle(document.body).backgroundColor")

    # 导入样例（线程中执行）
    samples = sorted(str(p) for p in (ROOT/"samples").glob("*"))
    holder = {}
    t = threading.Thread(target=lambda: holder.update(bridge.import_files(samples)))
    t.start(); t.join(180)
    report["imported"] = len(holder.get("imported", []))
    report["failures"] = holder.get("failures", [])

    js("window.__probeRefresh()"); time.sleep(2.4)
    report["screen_card"] = js("!!document.getElementById('screenCard')")
    report["screen_name"] = js("document.querySelector('.screen-name')?.textContent")
    report["pros_count"] = js("document.querySelectorAll('.pros-col li').length")
    report["cons_count"] = js("document.querySelectorAll('.cons-col li').length")
    report["dim_tiles"] = js("document.querySelectorAll('.score-tile').length")
    report["progress_text"] = js("document.getElementById('screenProgressText').textContent")
    report["review"] = js("JSON.stringify(window.__appInternals.state.review)")
    report["js_errors"] = js("(window.__errors||[]).slice(0,5)")
    capture(PORT, OUT/"fb_screen.png")

    # 点喜欢，验证切换到下一份
    before = report["screen_name"]
    js("document.querySelector('[data-verdict=liked]').click()")
    time.sleep(0.9)
    report["after_like_name"] = js("document.querySelector('.screen-name')?.textContent")
    report["advanced"] = report["after_like_name"] != before
    report["progress_after"] = js("document.getElementById('screenProgressText').textContent")

    # 键盘：左=不合适
    js("document.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}))")
    time.sleep(0.8)
    report["after_key_left"] = js("document.getElementById('screenProgressText').textContent")

    # 详情
    js("document.querySelector('.screen-card').click()")
    time.sleep(1.5)
    report["drawer_open"] = js("!document.getElementById('drawer').classList.contains('hidden')")
    report["drawer_strengths"] = js("document.querySelectorAll('#drawerBody .pros-col li').length")
    report["drawer_dims"] = js("document.querySelectorAll('#drawerBody .dim-row').length")
    capture(PORT, OUT/"fb_detail.png")
    js("document.getElementById('btnCloseDrawer').click()"); time.sleep(0.4)

    # 简历库
    js("document.querySelector('[data-view=library]').click()"); time.sleep(1.0)
    report["library_cards"] = js("document.querySelectorAll('.person-card').length")
    capture(PORT, OUT/"fb_library.png")

    # 统计
    js("document.querySelector('[data-view=stats]').click()"); time.sleep(1.2)
    report["stat_tiles"] = js("document.querySelectorAll('.stat-tile').length")
    capture(PORT, OUT/"fb_stats.png")

    # 设置（重点：滑块可见性）
    js("document.querySelector('[data-view=settings]').click()"); time.sleep(0.9)
    report["weight_sliders"] = js("document.querySelectorAll('.weight-range').length")
    report["slider_track_bg"] = js("getComputedStyle(document.getElementById('ssrRange')).backgroundColor")
    report["slider_thumb_check"] = js("""(() => {
        const r = document.getElementById('ssrRange');
        const cs = getComputedStyle(r);
        return {track: cs.backgroundColor, accent: cs.accentColor};
    })()""")
    report["api_box_visible"] = js("!!document.querySelector('#apiBox .status-dot')")
    report["api_running"] = js("state = window.__appInternals.state; !!(state.apiInfo && state.apiInfo.running)")
    capture(PORT, OUT/"fb_settings.png")

    # API 文档弹窗
    js("document.getElementById('btnApiDocs').click()"); time.sleep(0.6)
    report["api_docs_open"] = js("document.querySelectorAll('.modal-layer').length > 0")
    capture(PORT, OUT/"fb_apidoc.png")

    storage.close(); w.destroy()

webview.start(run)
(OUT/"report2.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
