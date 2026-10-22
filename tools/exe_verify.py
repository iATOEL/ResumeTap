"""对已运行的 exe 做验收（速筛界面版）。

用法：
    RESUME_GACHA_CDP_PORT=9335 dist/简历抽卡.exe
    RESUME_GACHA_CDP_PORT=9335 python tools/exe_verify.py
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cdp_shot import WS, capture, find_page  # noqa: E402

OUT = Path(__file__).resolve().parent / "_probe"
PORT = int(os.environ.get("RESUME_GACHA_CDP_PORT", "9333"))


def js(ws, expr, await_promise=True):
    result = ws.call("Runtime.evaluate", {
        "expression": expr,
        "awaitPromise": await_promise,
        "returnByValue": True,
    })
    payload = result.get("result", {}).get("result", {})
    if payload.get("subtype") == "error":
        return {"error": payload.get("description")}
    return payload.get("value")


def main() -> int:
    url = find_page(PORT)
    if not url:
        print(f"未找到调试页面，请先以 RESUME_GACHA_CDP_PORT={PORT} 启动应用")
        return 1

    ws = WS(url)
    ws.call("Runtime.enable")
    ws.call("Page.enable")
    report = {}

    report["bridge"] = js(ws, "typeof window.pywebview.api")
    report["theme"] = js(ws, "getComputedStyle(document.body).backgroundColor")
    report["screen_card_before"] = js(ws, "!!document.getElementById('screenCard')")

    # 载入示例
    js(ws, "document.getElementById('btnLoadSamples').click(); true", await_promise=False)
    for _ in range(45):
        time.sleep(1.0)
        if js(ws, "!!document.getElementById('screenCard')"):
            break

    report["screen_name"] = js(ws, "document.querySelector('.screen-name')?.textContent")
    report["rarity"] = js(ws, "document.querySelector('.rarity-badge')?.textContent")
    report["pros"] = js(ws, "document.querySelectorAll('.pros-col li').length")
    report["cons"] = js(ws, "document.querySelectorAll('.cons-col li').length")
    report["dim_tiles"] = js(ws, "document.querySelectorAll('.score-tile').length")
    report["progress"] = js(ws, "document.getElementById('screenProgressText').textContent")
    report["errors"] = js(ws, "(window.__errors||[]).slice(0,5)")
    capture(PORT, OUT / "exe2_screen.png")

    # 喜欢 → 下一份
    before = report["screen_name"]
    js(ws, "document.querySelector('[data-verdict=liked]').click(); true", await_promise=False)
    time.sleep(1.0)
    report["next_name"] = js(ws, "document.querySelector('.screen-name')?.textContent")
    report["advanced"] = report["next_name"] != before
    report["progress_after"] = js(ws, "document.getElementById('screenProgressText').textContent")

    # 详情
    js(ws, "document.querySelector('.screen-card').click(); true", await_promise=False)
    time.sleep(1.4)
    report["drawer_open"] = js(ws, "!document.getElementById('drawer').classList.contains('hidden')")
    report["drawer_dims"] = js(ws, "document.querySelectorAll('#drawerBody .dim-row').length")
    capture(PORT, OUT / "exe2_detail.png")
    js(ws, "document.getElementById('btnCloseDrawer').click(); true", await_promise=False)
    time.sleep(0.5)

    # 简历库
    js(ws, "document.querySelector('[data-view=library]').click(); true", await_promise=False)
    time.sleep(1.2)
    report["library_cards"] = js(ws, "document.querySelectorAll('.person-card').length")
    capture(PORT, OUT / "exe2_library.png")

    # 搜索
    js(ws, """(()=>{const s=document.getElementById('searchInput'); s.value='Python';
        s.dispatchEvent(new Event('input'));})()""", await_promise=False)
    time.sleep(0.7)
    report["search_python"] = js(ws, "document.querySelectorAll('.person-card').length")
    js(ws, """(()=>{const s=document.getElementById('searchInput'); s.value='';
        s.dispatchEvent(new Event('input'));})()""", await_promise=False)
    time.sleep(0.4)

    # 筛选
    js(ws, "document.querySelector('#rarityTabs [data-rarity=SSR]').click(); true", await_promise=False)
    time.sleep(0.6)
    report["filter_ssr"] = js(ws, "document.querySelectorAll('.person-card').length")
    js(ws, 'document.querySelector(\'#rarityTabs [data-rarity=""]\').click(); true', await_promise=False)
    time.sleep(0.4)

    # 统计
    js(ws, "document.querySelector('[data-view=stats]').click(); true", await_promise=False)
    time.sleep(1.2)
    report["stat_tiles"] = js(ws, "document.querySelectorAll('.stat-tile').length")
    capture(PORT, OUT / "exe2_stats.png")

    # 设置
    js(ws, "document.querySelector('[data-view=settings]').click(); true", await_promise=False)
    time.sleep(1.0)
    report["sliders"] = js(ws, "document.querySelectorAll('.weight-range').length")
    report["slider_colors"] = js(ws, """(()=>{
        const el = document.getElementById('ssrRange');
        const cs = getComputedStyle(el);
        return {track: cs.backgroundColor, height: cs.height};
    })()""")
    report["api_running"] = js(ws, "(window.__appInternals.state.apiInfo||{}).running")
    report["api_port"] = js(ws, "(window.__appInternals.state.apiInfo||{}).port")
    capture(PORT, OUT / "exe2_settings.png")

    # 阈值生效
    js(ws, """(()=>{const r=document.getElementById('ssrRange'); r.value=95;
        r.dispatchEvent(new Event('input')); const s=document.getElementById('srRange');
        s.value=90; s.dispatchEvent(new Event('input'));})()""", await_promise=False)
    rarities = js(ws, """(async()=>{
        document.getElementById('btnSaveConfig').click();
        await new Promise(r=>setTimeout(r,3000));
        return window.__appInternals.state.candidates.map(c=>c.rarity).join(',');
    })()""")
    report["rarities_after_high_threshold"] = rarities

    # 恢复默认
    js(ws, "(async()=>{ await window.pywebview.api.reset_config(); return true; })()")
    time.sleep(1.0)

    # 导出
    report["export"] = js(ws, "window.pywebview.api.export_results('csv', null)")

    # 本地 API 实测
    report["api_health"] = js(ws, """(async()=>{
        const info = window.__appInternals.state.apiInfo || {};
        if (!info.running) return 'not running';
        try {
            const resp = await fetch(info.base_url + '/health');
            return await resp.json();
        } catch (e) { return 'fetch failed: ' + e.message; }
    })()""")

    ws.close()
    OUT.mkdir(exist_ok=True)
    (OUT / "exe2_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
