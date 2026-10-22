"""v3.0 纯 AI 界面自检。"""
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

from http.server import BaseHTTPRequestHandler, HTTPServer
calls = {"n": 0}
class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        calls["n"] += 1
        n = int(self.headers.get('Content-Length') or 0); self.rfile.read(n)
        time.sleep(0.35)
        p = {"name":"AI","overall_score":76.5,"dimensions":{
            "skill":{"score":82,"reason":"技能栈与岗位高度匹配"},
            "experience":{"score":78,"reason":"工作经验充足"},
            "education":{"score":74,"reason":"本科，院校良好"},
            "project":{"score":80,"reason":"项目有量化成果"},
            "job_match":{"score":72,"reason":"整体契合度较高"}},
          "strengths":["技术栈完整","项目成果有量化"],"weaknesses":["缺少团队管理经验"],
          "summary":"整体匹配度较高，建议面试","status":"pass","screen_reasons":[]}
        out = {"choices":[{"message":{"content":json.dumps(p, ensure_ascii=False)}}]}
        d = json.dumps(out, ensure_ascii=False).encode()
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", str(len(d))); self.end_headers(); self.wfile.write(d)
srv = HTTPServer(("127.0.0.1", 0), Mock); mock_port = srv.server_port
threading.Thread(target=srv.serve_forever, daemon=True).start()

bridge = Bridge()
w = webview.create_window("probe", url=(paths.ui_dir()/"index.html").as_uri(),
                          js_api=bridge, width=1440, height=900)
bridge.attach(w)
R = {}

def run():
    w.events.loaded.wait(); time.sleep(3.2)
    def ev(code, default=None):
        try: return w.evaluate_js(code)
        except Exception as exc:
            R.setdefault("js_errors", []).append(str(exc)[:80]); return default

    R["title"] = ev("document.title")
    R["queue_bar_exists"] = ev("!!document.getElementById('queueBar')")
    R["ai_not_configured"] = ev("document.getElementById('queueStateText').textContent")

    # 未配置 AI 时导入
    samples = sorted(str(p) for p in (ROOT/"samples").glob("*"))[:4]
    holder = {}
    t = threading.Thread(target=lambda: holder.update(bridge.import_files(samples)))
    t.start(); t.join(200)
    R["imported"] = len(holder.get("imported", []))
    ev("window.__probeRefresh()"); time.sleep(2.2)
    R["scores_after_import"] = ev("window.__appInternals.state.candidates.map(c=>c.overall_score)")
    R["eval_status"] = ev("window.__appInternals.state.candidates.map(c=>c.eval_status)")
    R["empty_hint"] = ev("document.querySelector('.review-done h3')?.textContent")
    capture(PORT, OUT/"v3_pending.png")

    # 未配置时点开始 -> 应被拒绝并跳到设置
    ev("document.getElementById('btnQueueStart').click()"); time.sleep(1.2)
    R["after_start_no_ai"] = ev("document.querySelector('[data-view=settings]').classList.contains('active')")

    # 配置 AI
    ev("document.getElementById('optAiEnabled').checked=true")
    ev(f"document.getElementById('aiBaseUrl').value='http://127.0.0.1:{mock_port}/v1'")
    ev("document.getElementById('aiApiKey').value='k'")
    ev("document.getElementById('aiModel').value='m'")
    ev("document.getElementById('aiTimeout').value='30'")
    ev("document.getElementById('btnAiTest').click()"); time.sleep(4)
    R["ai_ready"] = ev("!!(window.__appInternals.state.aiStatus||{}).ready")

    # 回速筛点开始
    ev("document.querySelector('[data-view=screen]').click()"); time.sleep(1.2)
    ev("document.getElementById('btnQueueStart').click()"); time.sleep(1.5)
    R["queue_running"] = ev("window.__appInternals.state.queue?.state")
    R["queue_dot_on"] = ev("document.getElementById('queueDot').classList.contains('on')")

    # 等待完成
    for _ in range(60):
        time.sleep(1)
        st = ev("window.__appInternals.state.queue?.state")
        done = ev("window.__appInternals.state.eval?.done")
        if st == 'idle' and isinstance(done, int) and done >= 4: break
    R["final_state"] = ev("window.__appInternals.state.queue?.state")
    R["eval_summary"] = ev("JSON.stringify(window.__appInternals.state.eval)")
    R["scores"] = ev("window.__appInternals.state.candidates.map(c=>c.overall_score)")
    R["mock_calls"] = calls["n"]
    R["card_after"] = ev("document.querySelector('.screen-name')?.textContent")
    R["errors"] = ev("(window.__errors||[]).slice(0,3)")
    capture(PORT, OUT/"v3_evaluated.png")
    storage.close(); w.destroy()

webview.start(run)
srv.shutdown()
(OUT/"report6.json").write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
print("REPORT:" + json.dumps(R, ensure_ascii=False))
