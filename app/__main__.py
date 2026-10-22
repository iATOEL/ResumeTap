"""程序入口：启动原生桌面窗口（pywebview + WebView2），不监听任何端口。"""

import ctypes
import logging
import logging.handlers
import os
import sys
import traceback
from pathlib import Path

from . import paths

log = logging.getLogger("resume_gacha")


def _setup_logging() -> None:
    handler = logging.handlers.RotatingFileHandler(
        paths.logs_dir() / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _fatal(message: str) -> None:
    """无 GUI 时的兜底提示（例如 WebView2 缺失）。"""
    log.error(message)
    try:
        ctypes.windll.user32.MessageBoxW(0, message, paths.APP_TITLE, 0x10)
    except Exception:  # noqa: BLE001 - 非 Windows 或无法弹窗时静默
        print(message, file=sys.stderr)


def _ensure_webview2() -> bool:
    if sys.platform != "win32":
        return True
    import winreg

    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
    ]
    for root, path in keys:
        try:
            with winreg.OpenKey(root, path) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
                if version and version != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def main() -> int:
    _setup_logging()
    try:
        import webview
    except Exception as exc:  # noqa: BLE001
        _fatal(f"缺少桌面组件，无法启动：\n{exc}")
        return 1

    if not _ensure_webview2():
        _fatal(
            "未检测到 Microsoft Edge WebView2 运行时。\n\n"
            "Windows 11 与新版 Windows 10 通常已内置；\n"
            "如缺失请安装微软官方运行时后重试：\n"
            "https://developer.microsoft.com/microsoft-edge/webview2/"
        )
        return 1

    from . import api, storage
    from .bridge import Bridge

    storage.init_if_needed()

    # 恢复未完成的评估任务（上次退出时还有 pending）
    try:
        from . import config as _cfg
        from .eval_queue import QUEUE
        _settings = _cfg.load_config()
        if (_settings.get("llm", {}).get("auto_start", True)
                and storage.count_by_eval_status("pending") > 0
                and _settings.get("llm", {}).get("api_key")):
            log.info("恢复未完成的 AI 评估任务")
    except Exception:  # noqa: BLE001
        log.exception("检查待评估任务失败")

    # 本地 AI 审查 API：仅监听回环地址，是否随应用启动由配置决定
    try:
        from . import config as config_module
        api_cfg = config_module.load_config().get("api", {})
        if api_cfg.get("enabled", True):
            api.start(int(api_cfg.get("port") or 8765))
    except Exception:  # noqa: BLE001 - API 起不来不应影响主程序
        log.exception("本地审查 API 启动失败，应用继续运行")

    webview.settings["ALLOW_FILE_URLS"] = True
    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

    # 仅在显式设置环境变量时开启远程调试端口（供自动化验收使用，正常启动不开放）
    cdp_port = os.getenv("RESUME_GACHA_CDP_PORT", "").strip()
    if cdp_port.isdigit():
        webview.settings["REMOTE_DEBUGGING_PORT"] = int(cdp_port)

    index = paths.ui_dir() / "index.html"
    if not index.exists():
        _fatal(f"界面资源缺失：{index}")
        return 1

    bridge = Bridge()
    window = webview.create_window(
        paths.APP_TITLE,
        url=index.as_uri(),
        js_api=bridge,
        width=1440,
        height=920,
        min_size=(1040, 680),
        background_color="#080b14",
        text_select=True,
    )
    bridge.attach(window)

    def _on_closing():
        try:
            from .eval_queue import QUEUE
            QUEUE.stop_now()
        except Exception:  # noqa: BLE001
            pass
        try:
            api.stop()
        except Exception:  # noqa: BLE001
            pass
        storage.close()
        return True

    window.events.closing += _on_closing

    try:
        webview.start(debug=bool(os.getenv("RESUME_GACHA_DEBUG")))
    except Exception as exc:  # noqa: BLE001
        log.exception("窗口启动失败")
        _fatal(f"窗口启动失败：\n{exc}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        _fatal("程序发生未预期异常：\n\n" + traceback.format_exc(limit=3))
        sys.exit(1)
