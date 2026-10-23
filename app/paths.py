"""运行时路径：便携版资源与数据定位。

发布目录结构（绿色版）::

    ResumeTap/
    ├── ResumeTap.exe          启动器
    ├── _internal/             运行依赖（PyInstaller 目录版）
    ├── ui/                    界面资源（可直接改样式）
    ├── assets/                图标等静态资源
    ├── samples/               示例简历
    ├── data/                  配置、数据库、简历副本、导出、日志
    ├── config.example.yaml    配置模板
    ├── README.txt
    └── LICENSE

数据默认放在程序目录下的 data/，整个文件夹可随意拷贝到 U 盘或另一台电脑。
只有程序目录不可写时（例如放进 Program Files）才回退到用户目录。
"""

import os
import shutil
import sys
from pathlib import Path

APP_TITLE = "ResumeTap"
APP_VERSION = "3.1.2"

# 旧版本使用的 %APPDATA% 目录名，用于一次性迁移
LEGACY_DATA_DIR_NAME = "ResumeGacha"

_migrated = False


def is_frozen() -> bool:
    """是否处于打包运行状态。"""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """程序所在目录（便携版根目录）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """PyInstaller 解包目录；开发时为项目根目录。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def _legacy_data_dir() -> Path:
    base = os.getenv("APPDATA") or os.path.expanduser("~")
    return Path(base) / LEGACY_DATA_DIR_NAME


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def data_dir() -> Path:
    """可写数据目录。

    优先级：
      1. 环境变量 RESUME_TAP_DATA_DIR（便于测试或自定义位置）
      2. 打包版：<程序目录>/data （便携）
      3. 开发：<项目>/.devdata
      4. 上述不可写时回退 %APPDATA%/ResumeGacha
    """
    override = (os.getenv("RESUME_TAP_DATA_DIR") or "").strip()
    if override:
        path = Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        _migrate_once(path)
        return path

    if is_frozen():
        portable = app_dir() / "data"
        if _writable(portable):
            _migrate_once(portable)
            return portable
        # 只读目录（Program Files 等）→ 回退用户目录
        fallback = _legacy_data_dir()
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    dev = app_dir() / ".devdata"
    dev.mkdir(parents=True, exist_ok=True)
    _migrate_once(dev)
    return dev


def _migrate_once(target: Path) -> None:
    """首次使用便携目录时，把旧版 %APPDATA% 里的数据复制过来。

    只复制，不删除源目录；目标已有数据时跳过。
    """
    global _migrated
    if _migrated:
        return
    _migrated = True

    if (target / "data.db").exists() or (target / "config.yaml").exists():
        return

    legacy = _legacy_data_dir()
    if not legacy.is_dir() or not (legacy / "data.db").exists():
        return
    if legacy.resolve() == target.resolve():
        return

    try:
        for item in legacy.iterdir():
            if item.name in {"data.db", "config.yaml"}:
                shutil.copy2(item, target / item.name)
            elif item.is_dir() and item.name in {"resumes", "exports", "logs"}:
                shutil.copytree(item, target / item.name, dirs_exist_ok=True)
    except OSError:
        # 迁移失败不应阻止启动
        pass


def resumes_dir() -> Path:
    path = data_dir() / "resumes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def exports_dir() -> Path:
    path = data_dir() / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "data.db"


def config_path() -> Path:
    return data_dir() / "config.yaml"


def _prefer_visible(name: str) -> Path | None:
    """优先使用程序目录下用户可见的同名文件夹。"""
    visible = app_dir() / name
    if visible.is_dir():
        return visible
    return None


def ui_dir() -> Path:
    return _prefer_visible("ui") or (resource_dir() / "ui")


def samples_dir() -> Path:
    return _prefer_visible("samples") or (resource_dir() / "samples")


def is_portable() -> bool:
    """当前数据是否存放在程序目录内。"""
    try:
        return data_dir().parent == app_dir()
    except OSError:
        return False
