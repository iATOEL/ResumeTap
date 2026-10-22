"""v2.3 界面自检：岗位分组/自定义、全选、AI 批量。"""
import json, socket, sys, threading, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import webview
from app import paths, storage
from app.bridge import Bridge
from cdp_shot import capture

OUT = ROOT / "tools" / "_probe"; OUT.mkdir(exist_ok=True)
_s = socket.socket(); _s.bind(("127.0.0.1", 0)); PORT = _s.getsockname()[1]; _s.close()
webview.settings["ALLOW_FILE_URLS"] = True
webview.settings["REMOTE_DEBUGGING_PORT"] = PORT
bridge = Bridge()
w = webview.create_window("probe", url=(paths.ui_dir()/"index.html").as_uri(),
                          js_api=bridge, width=1440, height=900)
bridge.attach(w)
R = {}

def run():
    w.events.loaded.wait(); time.sleep(3.2)
    def ev(code, default=None):
        try:
            return w.evaluate_js(code)
        except Exception as exc:
            R.setdefault("js_errors", []).append(f"{exc}: {code[:50]}")
            return default

    R["groups"] = ev("document.querySelectorAll('.job-group').length")
    R["role_buttons"] = ev("document.querySelectorAll('[data-role]:not([data-role=__custom])').length")
    R["has_custom_btn"] = ev("!!document.querySelector('[data-role=__custom]')")
    R["title"] = ev("document.title")

    # 导入示例
    samples = sorted(str(p) for p in (ROOT/"samples").glob("*"))
    holder = {}
    t = threading.Thread(target=lambda: holder.update(bridge.import_files(samples)))
    t.start(); t.join(200)
    R["imported"] = len(holder.get("imported", []))
    ev("window.__probeRefresh()"); time.sleep(2.2)

    # 选岗位
    ev("document.querySelector('[data-role=backend]').click()"); time.sleep(2.5)
    R["card"] = ev("document.querySelector('.screen-name')?.textContent")

    # 简历库全选
    ev("document.querySelector('[data-view=library]').click()"); time.sleep(1.2)
    R["cards"] = ev("document.querySelectorAll('.person-card').length")
    R["select_total"] = ev("document.getElementById('selectTotal').textContent")
    ev("document.getElementById('btnSelectAll').click()"); time.sleep(0.6)
    R["selected_after_all"] = ev("document.getElementById('selectCount').textContent")
    R["bulk_visible"] = ev("!document.getElementById('bulkBar').classList.contains('hidden')")
    R["chk_checked"] = ev("document.getElementById('chkSelectAll').checked")
    capture(PORT, OUT/"v23_library_all.png")

    # 反选
    ev("document.getElementById('btnInvertSelect').click()"); time.sleep(0.5)
    R["after_invert"] = ev("document.getElementById('selectCount').textContent")

    # Ctrl+A
    ev("document.getElementById('btnClearSelect').click()"); time.sleep(0.4)
    ev("document.dispatchEvent(new KeyboardEvent('keydown',{key:'a',ctrlKey:true,bubbles:true}))")
    time.sleep(0.6)
    R["after_ctrl_a"] = ev("document.getElementById('selectCount').textContent")

    R["errors"] = ev("(window.__errors||[]).slice(0,5)")
    storage.close(); w.destroy()

webview.start(run)
(OUT/"report4.json").write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
print("REPORT:" + json.dumps(R, ensure_ascii=False))
