"""端到端界面自检：启动真实窗口，导入样例，抓取渲染结果与截图。

用法：python tools/ui_probe.py
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import webview  # noqa: E402

from app import paths, storage  # noqa: E402
from app.bridge import Bridge  # noqa: E402

OUT = ROOT / "tools" / "_probe"
OUT.mkdir(exist_ok=True)



def capture_screens(js) -> dict:
    """用 CDP 抓取各视图截图。"""
    from cdp_shot import capture

    shots = {}
    views = [
        ("gallery", "[data-view=gallery]"),
        ("stats", "[data-view=stats]"),
        ("settings", "[data-view=settings]"),
    ]
    for name, selector in views:
        js(f"document.querySelector('{selector}').click()")
        time.sleep(1.0)
        path = OUT / f"{name}.png"
        shots[name] = str(path) if capture(9333, path) else "failed"
    # 详情抽屉
    js("document.querySelector('[data-view=gallery]').click()")
    time.sleep(0.4)
    js("document.querySelector('.card').click()")
    time.sleep(1.2)
    path = OUT / "detail.png"
    shots["detail"] = str(path) if capture(9333, path) else "failed"
    js("document.getElementById('btnCloseDrawer').click()")
    return shots


def main() -> int:
    webview.settings["ALLOW_FILE_URLS"] = True
    webview.settings["REMOTE_DEBUGGING_PORT"] = 9333
    bridge = Bridge()
    index = paths.ui_dir() / "index.html"
    window = webview.create_window(
        "UI Probe", url=index.as_uri(), js_api=bridge,
        width=1440, height=920, background_color="#080b14",
    )
    bridge.attach(window)
    report = {}

    def run() -> None:
        window.events.loaded.wait()
        time.sleep(2.0)

        def js(code, default=None):
            try:
                return window.evaluate_js(code)
            except Exception as exc:  # noqa: BLE001
                report.setdefault("js_errors", []).append(f"{exc}: {code[:60]}")
                return default

        report["bridge"] = js("typeof window.pywebview")
        report["boot_done"] = js("!!(document.getElementById('miniSSR'))")
        report["empty_state"] = js("document.getElementById('emptyState').classList.contains('show')")

        # 导入样例（放到子线程，避免阻塞窗口事件循环）
        samples = sorted(str(p) for p in (ROOT / "samples").glob("*")
                         if p.suffix in {".docx", ".pdf"})
        holder = {}

        def do_import():
            holder["result"] = bridge.import_files(samples)

        thread = threading.Thread(target=do_import)
        thread.start()
        thread.join(timeout=180)
        result = holder.get("result", {"ok": False, "failures": [{"error": "timeout"}]})
        report["import_ok"] = result.get("ok")
        report["imported"] = len(result.get("imported", []))
        report["failures"] = result.get("failures", [])

        # 让前端刷新
        js("window.__probeRefresh && window.__probeRefresh()")
        time.sleep(0.6)
        js("""(async () => {
            const d = await window.pywebview.api.list_candidates();
            if (d && d.ok) {
                const app = window.__appInternals;
                if (app) app.applyData(d);
            }
        })()""")
        time.sleep(1.0)

        report["card_count"] = js("document.querySelectorAll('.card').length")
        report["ssr_cards"] = js("document.querySelectorAll('.card-rarity-ssr').length")
        report["sr_cards"] = js("document.querySelectorAll('.card-rarity-sr').length")
        report["r_cards"] = js("document.querySelectorAll('.card-rarity-r').length")
        report["names"] = js("Array.from(document.querySelectorAll('.card-name')).slice(0,6).map(n=>n.textContent)")
        report["scores"] = js("Array.from(document.querySelectorAll('.card-score')).slice(0,6).map(n=>n.textContent)")
        report["js_errors"] = js("(window.__errors||[]).slice(0,10)")

        # 打开第一张卡片详情
        js("document.querySelector('.card').click()")
        time.sleep(1.0)
        report["drawer_open"] = js("!document.getElementById('detailDrawer').classList.contains('hidden')")
        report["drawer_name"] = js("document.getElementById('drawerName').textContent")
        report["drawer_dims"] = js("document.querySelectorAll('#drawerBody .dim-row').length")
        report["drawer_skill_chips"] = js("document.querySelectorAll('#drawerBody .hit-chip').length")

        # 统计视图
        js("document.getElementById('btnCloseDrawer').click()")
        js("document.querySelector('[data-view=stats]').click()")
        time.sleep(1.0)
        report["stats_cards"] = js("document.querySelectorAll('.stat-card').length")
        report["stat_values"] = js("Array.from(document.querySelectorAll('.stat-card .value')).map(n=>n.textContent)")
        report["rarity_chart_painted"] = js("""(() => {
            const c = document.getElementById('rarityChart');
            const ctx = c.getContext('2d');
            const d = ctx.getImageData(0,0,c.width,c.height).data;
            let n=0; for (let i=3;i<d.length;i+=4) if (d[i]>0) n++;
            return n;
        })()""", 0)

        # 设置视图
        js("document.querySelector('[data-view=settings]').click()")
        time.sleep(0.6)
        report["setting_inputs"] = js("document.querySelectorAll('#weightFields input[type=range]').length")
        report["ssr_value"] = js("document.getElementById('ssrRange').value")
        report["role_options"] = js("document.getElementById('roleSelect').options.length")

        # 阈值调整生效
        js("""(() => {
            const r = document.getElementById('ssrRange');
            r.value = 95; r.dispatchEvent(new Event('input'));
            const s = document.getElementById('srRange');
            s.value = 90; s.dispatchEvent(new Event('input'));
        })()""")
        time.sleep(0.2)
        save = bridge.save_config({
            "scoring": {"weights": {"skill": 0.24, "experience": 0.2, "education": 0.12,
                                     "project": 0.2, "keyword": 0.12, "job_match": 0.12}},
            "rarity": {"ssr": 95, "sr": 90, "labels": {"SSR": "特级 · 传说", "SR": "高级 · 史诗", "R": "普通 · 稀有"}},
            "role": "auto", "keywords": [], "jd_text": "",
            "screening": {"min_score": 0, "min_years": 0, "required_degree": "",
                          "must_keywords": [], "reject_keywords": []},
            "ui": {"theme": "dark", "view": "grid", "sound": True, "animation": True},
            "privacy": {"store_raw_text": True},
            "llm": {"enabled": False},
        })
        report["save_config_ok"] = save.get("ok")
        report["rescored"] = save.get("rescored")
        report["rarity_after_high_threshold"] = sorted({c["rarity"] for c in save.get("candidates", [])})

        # 恢复默认阈值
        bridge.reset_config()
        time.sleep(0.3)

        # 截图（通过 CDP 抓取真实渲染画面）
        try:
            js("document.querySelector('[data-view=gallery]').click()")
            time.sleep(0.8)
            report["screenshots"] = capture_screens(js)
        except Exception as exc:  # noqa: BLE001
            report["screenshot_error"] = str(exc)

        # 导出
        candles = storage.list_candidates()
        report["export_csv"] = bridge.export_results("csv", [c["id"] for c in candles[:3]])
        report["export_json"] = bridge.export_results("json", None)
        report["export_md"] = bridge.export_results("md", None)

        storage.close()
        window.destroy()

    webview.start(run, debug=False)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
