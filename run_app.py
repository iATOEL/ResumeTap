"""桌面应用入口（供 PyInstaller 直接打包，保持绝对导入）。"""

import sys
import traceback

from app.__main__ import main

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0, "程序启动失败：\n\n" + traceback.format_exc(limit=4),
                "简历抽卡", 0x10,
            )
        except Exception:  # noqa: BLE001
            print(traceback.format_exc(), file=sys.stderr)
        sys.exit(1)
