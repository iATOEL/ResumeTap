"""发布到 GitHub：git push 源码 + gh release 上传便携版 zip。

用法（Git Bash）::

    <venv>/Scripts/python.exe tools/release.py               # 推送源码 + 创建/更新 Release
    <venv>/Scripts/python.exe tools/release.py --skip-code   # 只上传 zip
    <venv>/Scripts/python.exe tools/release.py --skip-asset   # 只推源码

前置：
  1. gh CLI 已安装并 `gh auth login`（git push 凭证由 gh 提供）
  2. 已运行 `build.py` 生成 release/ResumeTap-v*-win64.zip

历史说明：早期网络阻断 git 协议，这里曾用 REST API 逐 blob 上传；
网络恢复并配置 gh 后已改回 git push + gh release（v3.1.1）。
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OWNER = "iATOEL"
REPO = "ResumeTap"
BRANCH = "main"
REMOTE = f"https://github.com/{OWNER}/{REPO}.git"

# git 已接管源码同步；这些校验保留给 build.py 的 zip 内容
REQUIRED_FILES = {"build.py", "run_app.py", "requirements.txt", ".gitignore", "README.md"}


def run(cmd: list[str], check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, **kwargs)
    if check and result.returncode != 0:
        raise SystemExit(f"✗ 命令失败：{' '.join(cmd)}\n{result.stdout}{result.stderr}")
    return result


def require_gh() -> None:
    try:
        run(["gh", "auth", "status"], check=False)
    except FileNotFoundError as exc:
        raise SystemExit(
            "✗ gh CLI 不可用。请安装并运行：gh auth login\n"
            "  https://cli.github.com/") from exc


def app_version() -> str:
    for line in (ROOT / "app" / "paths.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("APP_VERSION"):
            return line.split("=", 1)[1].strip().strip("\"'")
    return "0.0.0"


# ---------------------------------------------------------------- 源码

def audit_stray_files() -> list[str]:
    """根目录散落的简历文档多为测试残留，推上去会污染仓库。"""
    stray = [p.name for p in ROOT.iterdir()
             if p.is_file() and p.suffix.lower() in {".docx", ".pdf", ".doc"}]
    return stray


def verify_required() -> None:
    missing = sorted(name for name in REQUIRED_FILES if not (ROOT / name).exists())
    if missing:
        raise SystemExit(f"✗ 缺少关键文件：{', '.join(missing)}")


def push_code(message: str) -> str:
    verify_required()
    stray = audit_stray_files()
    if stray:
        print(f"\n⚠ 根目录发现 {len(stray)} 个文档文件（疑似测试残留）：")
        for name in stray[:10]:
            print(f"    {name}")
        raise SystemExit("  请先删除或移走它们再发布。")

    run(["git", "add", "-A"])
    status = run(["git", "status", "--short"]).stdout.strip()
    if not status:
        print("[源码] 没有改动，跳过提交")
    else:
        print(f"[源码] 提交 {len(status.splitlines())} 个文件变动")
        run(["git", "commit", "-m", message])

    print(f"[源码] 推送 {OWNER}/{REPO} @ {BRANCH}")
    run(["git", "push", "origin", BRANCH])
    sha = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    print(f"  提交 {sha[:8]}")
    return sha


# ---------------------------------------------------------------- 资源

def find_archive() -> Path:
    release = ROOT / "release"
    candidates = sorted(release.glob("ResumeTap-v*-win64.zip"))
    if not candidates:
        raise SystemExit(
            "✗ 找不到发布包。请先运行：python build.py\n"
            f"  期望位置：{release}\\ResumeTap-v<版本>-win64.zip")
    return candidates[-1]


def release_notes(version: str, archive: Path) -> str:
    return f"""## ResumeTap {version} · 便携版（绿色版）

**下载下面的 `{archive.name}`，解压后双击 `ResumeTap.exe` 即可运行。**

### 目录结构

```
ResumeTap/
├── ResumeTap.exe          启动器
├── _internal/             运行依赖（请勿删除）
├── ui/                    界面文件，可自行调整
├── assets/                图标等资源
├── samples/               示例简历
├── data/                  配置、数据库、简历、导出、日志
├── config.example.yaml    配置参考
├── README.txt             使用说明
└── LICENSE
```

### 便携性

所有数据都写在 `data/` 目录内，整个文件夹可以直接拷贝到 U 盘或另一台电脑继续用。
卸载只需删除文件夹，不残留任何注册表项或用户目录文件。

### 使用前必须配置 AI

本软件**没有内置 AI**，分数、等级、优缺点全部由你配置的模型产出。
到 设置 → AI 评估 填写 Base URL、API Key、模型（支持任何 OpenAI 兼容接口）。
未配置时无法评估简历。

### 系统要求

Windows 10/11（自带 WebView2）。不支持 macOS / Linux。
"""


def upload_asset(version: str, archive: Path) -> None:
    tag = f"v{version}"
    print(f"\n[发布] {tag}")

    notes_path = ROOT / "build" / "_release_notes.md"
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(release_notes(version, archive), encoding="utf-8")

    # gh release create 在已存在时失败；对已有 tag 用 edit + upload 覆盖
    existing = run(["gh", "release", "view", tag, "--repo", f"{OWNER}/{REPO}"],
                   check=False)
    if existing.returncode != 0:
        run(["gh", "release", "create", tag, str(archive),
             "--repo", f"{OWNER}/{REPO}",
             "--title", f"ResumeTap {tag} · 便携版",
             "--notes-file", str(notes_path)])
        print(f"  新建 Release")
    else:
        run(["gh", "release", "upload", tag, str(archive), "--clobber",
             "--repo", f"{OWNER}/{REPO}"])
        run(["gh", "release", "edit", tag, "--repo", f"{OWNER}/{REPO}",
             "--title", f"ResumeTap {tag} · 便携版",
             "--notes-file", str(notes_path)])
        print(f"  更新已有 Release")

    url = run(["gh", "release", "view", tag, "--repo", f"{OWNER}/{REPO}",
               "--json", "url", "--jq", ".url"]).stdout.strip()
    print(f"  ✓ {url}")


def main() -> int:
    parser = argparse.ArgumentParser(description="发布 ResumeTap 到 GitHub")
    parser.add_argument("--skip-code", action="store_true", help="只上传 zip")
    parser.add_argument("--skip-asset", action="store_true", help="只推送源码")
    parser.add_argument("--message", default="", help="提交信息")
    args = parser.parse_args()

    version = app_version()
    archive = None if args.skip_asset else find_archive()

    if not args.skip_code:
        message = args.message or f"chore: release v{version}"
        push_code(message)

    if archive:
        require_gh()
        upload_asset(version, archive)

    print(f"\n完成：https://github.com/{OWNER}/{REPO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
