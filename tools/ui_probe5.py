"""验证岗位分组页 + 自定义岗位 + AI 批量复核。"""
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

# mock AI
from http.server import BaseHTTPRequestHandler, HTTPServer
calls = {"n": 0}
class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        calls["n"] += 1
        n = int(self.headers.get('Content-Length') or 0); self.rfile.read(n)
        payload = {"name":"AI","overall_score":70,"dimensions":{
            "skill":{"score":72,"reason":"AI 技能"}, "experience":{"score":68,"reason":"AI 经验"},
            "education":{"score":75,"reason":"AI 学历"}, "project":{"score":65,"reason":"AI 项目"},
            "job_match":{"score":70,"reason":"AI 岗位"}},
          "strengths":["AI 优点"], "weaknesses":["AI 不足"], "summary":"AI 结论"}
        out = {"choices":[{"message":{"content":json.dumps(payload, ensure_ascii=False)}}]}
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
            R.setdefault("js_errors", []).append(f"{str(exc)[:80]}")
            return default

    R["groups"] = ev("document.querySelectorAll('.job-group').length")
    R["group_titles"] = ev("Array.from(document.querySelectorAll('.job-group h4')).map(e=>e.textContent)")
    capture(PORT, OUT/"v23_jobgroups.png")

    # 先导入简历
    samples = sorted(str(p) for p in (ROOT/"samples").glob("*"))
    holder = {}
    t = threading.Thread(target=lambda: holder.update(bridge.import_files(samples)))
    t.start(); t.join(200)
    R["imported"] = len(holder.get("imported", []))
    ev("window.__probeRefresh()"); time.sleep(2.0)

    # 自定义岗位（刷新后再填，避免被重绘清空）
    ev("document.querySelector('[data-role=__custom]').click()"); time.sleep(0.5)
    R["custom_box_visible"] = ev("!document.getElementById('customRoleBox').classList.contains('hidden')")
    ev("document.getElementById('customRoleLabel').value='短视频编导'")
    ev("document.getElementById('customRoleKeywords').value='脚本, 拍摄, 剪辑, 选题'")
    ev("document.getElementById('btnSaveCustomRole').click()"); time.sleep(3)
    R["role"] = ev("window.__appInternals.state.config.role")
    R["custom_label"] = ev("window.__appInternals.state.config.custom_role.label")
    R["card"] = ev("document.querySelector('.screen-name')?.textContent")
    R["role_label_in_card"] = ev("document.querySelector('.screen-sub')?.textContent")

    # AI 批量：先配置
    ev("document.querySelector('[data-view=settings]').click()"); time.sleep(1)
    ev("document.getElementById('optAiEnabled').checked=true")
    ev(f"document.getElementById('aiBaseUrl').value='http://127.0.0.1:{mock_port}/v1'")
    ev("document.getElementById('aiApiKey').value='k'")
    ev("document.getElementById('aiModel').value='m'")
    ev("document.getElementById('btnAiTest').click()"); time.sleep(4)
    R["ai_ready"] = ev("!!(window.__appInternals.state.aiStatus||{}).ready")

    # 批量复核全部
    R["has_batch_all"] = ev("!!document.getElementById('btnAiBatchAll')")
    ev("document.getElementById('btnAiBatchAll').click()"); time.sleep(1.2)
    ev("document.querySelector('.modal-card [data-answer=yes]')?.click()")
    for _ in range(60):
        time.sleep(1)
        ai_count = ev("window.__appInternals.state.candidates.filter(c=>(c.payload||{}).score_source==='ai').length")
        if isinstance(ai_count, int) and ai_count >= 12: break
    R["ai_count"] = ev("window.__appInternals.state.candidates.filter(c=>(c.payload||{}).score_source==='ai').length")
    R["mock_calls"] = calls["n"]
    R["errors"] = ev("(window.__errors||[]).slice(0,3)")
    capture(PORT, OUT/"v23_ai_batch.png")
    storage.close(); w.destroy()

webview.start(run)
srv.shutdown()
(OUT/"report5.json").write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
print("REPORT:" + json.dumps(R, ensure_ascii=False))
