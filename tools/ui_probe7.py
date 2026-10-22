"""验证：自动开始 vs 手动开始。"""
import json, socket, sys, threading, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import webview
from app import paths, storage
from app.bridge import Bridge
from cdp_shot import capture
from http.server import BaseHTTPRequestHandler, HTTPServer

OUT = ROOT / "tools" / "_probe"; OUT.mkdir(exist_ok=True)
_s = socket.socket(); _s.bind(("127.0.0.1", 0)); PORT = _s.getsockname()[1]; _s.close()
webview.settings["ALLOW_FILE_URLS"] = True
webview.settings["REMOTE_DEBUGGING_PORT"] = PORT

calls = {"n": 0}
class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        calls["n"] += 1
        n = int(self.headers.get('Content-Length') or 0); self.rfile.read(n)
        time.sleep(0.3)
        p = {"name":"AI","overall_score":75,"dimensions":{k:{"score":75,"reason":"r"} for k in ["skill","experience","education","project","job_match"]},
             "strengths":["优点"],"weaknesses":["不足"],"summary":"结论","status":"pass","screen_reasons":[]}
        out = {"choices":[{"message":{"content":json.dumps(p, ensure_ascii=False)}}]}
        d = json.dumps(out, ensure_ascii=False).encode()
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", str(len(d))); self.end_headers(); self.wfile.write(d)
srv = HTTPServer(("127.0.0.1", 0), Mock); mock_port = srv.server_port
threading.Thread(target=srv.serve_forever, daemon=True).start()

bridge = Bridge()
w = webview.create_window("probe", url=(paths.ui_dir()/"index.html").as_uri(), js_api=bridge, width=1400, height=880)
bridge.attach(w)
R = {}

def run():
    w.events.loaded.wait(); time.sleep(3.2)
    def ev(c, d=None):
        try: return w.evaluate_js(c)
        except Exception as e:
            R.setdefault("js_errors", []).append(str(e)[:80]); return d

    # 配置 AI + 关闭自动开始
    ev("document.querySelector('[data-view=settings]').click()"); time.sleep(1)
    ev("document.getElementById('optAiEnabled').checked=true")
    ev(f"document.getElementById('aiBaseUrl').value='http://127.0.0.1:{mock_port}/v1'")
    ev("document.getElementById('aiApiKey').value='k'")
    ev("document.getElementById('aiModel').value='m'")
    ev("document.getElementById('aiTimeout').value='30'")
    ev("document.getElementById('optAiAutoStart').checked=false")   # 关键：关闭自动
    ev("document.getElementById('btnSaveConfig').click()"); time.sleep(3)
    R["ai_ready"] = ev("!!(window.__appInternals.state.aiStatus||{}).ready")
    R["auto_start_off"] = ev("window.__appInternals.state.config.llm.auto_start === false")

    # 导入 -> 不应自动开始
    samples = sorted(str(p) for p in (ROOT/"samples").glob("*"))[:3]
    holder = {}
    t = threading.Thread(target=lambda: holder.update(bridge.import_files(samples))); t.start(); t.join(200)
    ev("window.__probeRefresh()"); time.sleep(2.5)
    R["imported"] = len(holder.get("imported", []))
    R["queue_state_after_import"] = ev("window.__appInternals.state.queue?.state")
    R["calls_after_import"] = calls["n"]
    R["scores"] = ev("window.__appInternals.state.candidates.map(c=>c.overall_score)")
    capture(PORT, OUT/"v3_manual_pending.png")

    # 手动点开始
    ev("document.getElementById('btnQueueStart').click()")
    for _ in range(40):
        time.sleep(1)
        if ev("window.__appInternals.state.eval?.done") == 3: break
    R["after_manual_start"] = ev("JSON.stringify(window.__appInternals.state.eval)")
    R["calls_total"] = calls["n"]
    R["scores_after"] = ev("window.__appInternals.state.candidates.map(c=>c.overall_score)")

    # 测试暂停按钮存在且可用
    R["pause_btn_disabled_when_idle"] = ev("document.getElementById('btnQueuePause').disabled")

    R["errors"] = ev("(window.__errors||[]).slice(0,3)")
    storage.close(); w.destroy()

webview.start(run)
srv.shutdown()
print("REPORT:" + json.dumps(R, ensure_ascii=False))
