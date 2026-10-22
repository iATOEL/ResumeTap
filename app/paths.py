"""运行时路径：区分打包资源目录与用户数据目录。"""

import os
import sys
from pathlib import Path

# 数据目录名：沿用 Resumegacha 时期的目录，避免老用户升级后数据丢失
APP_NAME = "ResumeGacha"
APP_TITLE = "ResumeTap"
APP_VERSION = "2.3.0"


def resource_dir() -> Path:
    """打包后返回 PyInstaller 解包目录，开发时返回项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """可写的用户数据目录（Windows: %APPDATA%\\ResumeGacha）。"""
    base = os.getenv("APPDATA") or os.path.expanduser("~")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def ui_dir() -> Path:
    return resource_dir() / "ui"


def assets_dir() -> Path:
    return resource_dir() / "assets"
