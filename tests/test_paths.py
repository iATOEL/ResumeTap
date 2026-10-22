"""便携版路径逻辑测试（需要真实的 paths 函数，故不共用 isolate fixture）。"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import paths  # noqa: E402


@pytest.fixture(autouse=True)
def reset_migration_flag(monkeypatch):
    monkeypatch.setattr(paths, "_migrated", False)
    yield


def test_data_dir_honors_env_override(tmp_path, monkeypatch):
    target = tmp_path / "custom"
    monkeypatch.setenv("RESUME_TAP_DATA_DIR", str(target))

    assert paths.data_dir() == target
    assert paths.db_path().parent == target
    assert paths.config_path().parent == target


def test_subdirs_created_under_data_dir(tmp_path, monkeypatch):
    target = tmp_path / "d"
    monkeypatch.setenv("RESUME_TAP_DATA_DIR", str(target))

    for fn in (paths.resumes_dir, paths.exports_dir, paths.logs_dir):
        directory = fn()
        assert directory.is_dir()
        assert directory.parent == target


def test_legacy_data_migrated_once(tmp_path, monkeypatch):
    """旧 %APPDATA% 数据应被复制到便携目录，源目录保留。"""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "config.yaml").write_text("role: backend\n", encoding="utf-8")
    (legacy / "data.db").write_bytes(b"SQLite format 3\x00")
    (legacy / "resumes").mkdir()
    (legacy / "resumes" / "a.docx").write_bytes(b"x")

    target = tmp_path / "portable"
    monkeypatch.setattr(paths, "_legacy_data_dir", lambda: legacy)
    monkeypatch.setenv("RESUME_TAP_DATA_DIR", str(target))

    result = paths.data_dir()
    assert (result / "config.yaml").exists()
    assert (result / "data.db").exists()
    assert (result / "resumes" / "a.docx").exists()
    assert (legacy / "data.db").exists()


def test_no_migration_when_target_has_data(tmp_path, monkeypatch):
    """目标已有数据时不应被旧数据覆盖。"""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "config.yaml").write_text("role: old\n", encoding="utf-8")

    target = tmp_path / "portable"
    target.mkdir()
    (target / "config.yaml").write_text("role: new\n", encoding="utf-8")

    monkeypatch.setattr(paths, "_legacy_data_dir", lambda: legacy)
    monkeypatch.setenv("RESUME_TAP_DATA_DIR", str(target))

    paths.data_dir()
    assert "new" in (target / "config.yaml").read_text(encoding="utf-8")


def test_portable_flag_reflects_data_location(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr(paths, "app_dir", lambda: app)

    monkeypatch.setenv("RESUME_TAP_DATA_DIR", str(app / "data"))
    assert paths.is_portable() is True

    monkeypatch.setenv("RESUME_TAP_DATA_DIR", str(tmp_path / "elsewhere"))
    assert paths.is_portable() is False


def test_visible_ui_dir_preferred(tmp_path, monkeypatch):
    """程序目录下存在 ui/ 时优先使用（便于用户改样式）。"""
    app = tmp_path / "app"
    (app / "ui").mkdir(parents=True)
    monkeypatch.setattr(paths, "app_dir", lambda: app)
    monkeypatch.setattr(paths, "resource_dir", lambda: tmp_path / "internal")

    assert paths.ui_dir() == app / "ui"

    monkeypatch.setattr(paths, "app_dir", lambda: tmp_path / "empty")
    assert paths.ui_dir() == tmp_path / "internal" / "ui"


def test_samples_dir_visible(tmp_path, monkeypatch):
    app = tmp_path / "app"
    (app / "samples").mkdir(parents=True)
    monkeypatch.setattr(paths, "app_dir", lambda: app)
    monkeypatch.setattr(paths, "resource_dir", lambda: tmp_path / "internal")

    assert paths.samples_dir() == app / "samples"
