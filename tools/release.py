"""发布到 GitHub：推送源码 + 上传便携版 zip 到 Release。

用法（Git Bash）::

    export GITHUB_TOKEN=你的token
    python tools/release.py                 # 推送源码 + 创建/更新 Release
    python tools/release.py --skip-code     # 只上传 zip
    python tools/release.py --skip-asset    # 只推源码

前置：先运行 `python build.py` 生成 release/ResumeTap-v*-win64.zip
"""

import argparse
import base64
import http.client
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OWNER = "iATOEL"
REPO = "ResumeTap"
BRANCH = "main"
API = "https://api.github.com"

EXCLUDE_DIRS = {
    ".git", "build", "dist", "release", ".devdata", "__pycache__",
    ".venv", ".pytest_cache", "_probe", "_bad", "_legacy",
}
EXCLUDE_PATHS = {"tools/_probe", "tools/_bad", "tests/_legacy", "tools/_remote"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".exe", ".zip", ".spec"}
EXCLUDE_NAMES = {
    "config.yaml", "data.db", "data.db-shm", "data.db-wal",
    "Thumbs.db", ".DS_Store",
}
# 只允许 samples/ 与 docs/ 下出现二进制文档，根目录的散落简历一律视为测试残留
ALLOWED_DOC_DIRS = ("samples/", "docs/")
DOC_SUFFIX = {".docx", ".pdf", ".doc", ".md", ".txt"}

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()


def api(method: str, path: str, payload=None):
    url = path if path.startswith("http") else API + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:500]
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc


def app_version() -> str:
    for line in (ROOT / "app" / "paths.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("APP_VERSION"):
            return line.split("=", 1)[1].strip().strip("\"'")
    return "0.0.0"


# ---------------------------------------------------------------- 源码

def collect_files() -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        rel_posix = rel.as_posix()
        # 只按路径分段匹配目录名，避免 "build.py" 被 "build" 前缀误伤
        if set(rel.parts[:-1]) & EXCLUDE_DIRS or rel.parts[0] in EXCLUDE_DIRS:
            continue
        if any(rel_posix == ex or rel_posix.startswith(ex + "/") for ex in EXCLUDE_PATHS):
            continue
        if path.suffix.lower() in EXCLUDE_SUFFIX:
            continue
        if rel.name in EXCLUDE_NAMES:
            continue
        if rel.suffix.lower() in {".docx", ".pdf", ".doc"}:
            if not rel_posix.startswith(ALLOWED_DOC_DIRS):
                continue
        files[rel_posix] = path
    return files


def audit_stray_files() -> list[str]:
    """找出根目录下不该提交的散落文档（多为测试残留）。"""
    stray = []
    for path in ROOT.iterdir():
        if path.is_file() and path.suffix.lower() in {".docx", ".pdf", ".doc"}:
            stray.append(path.name)
    return stray


REQUIRED_FILES = {"build.py", "run_app.py", "requirements.txt", ".gitignore", "README.md"}


def verify_required(files: dict[str, Path]) -> None:
    """关键文件缺失时中止，否则仓库会悄悄少文件（build.py 曾被误排除）。"""
    missing = sorted(REQUIRED_FILES - set(files))
    if missing:
        raise SystemExit(f"✗ 待推送文件中缺少关键文件：{', '.join(missing)}")


def push_code(message: str) -> str:
    stray = audit_stray_files()
    if stray:
        print(f"\n⚠ 根目录发现 {len(stray)} 个文档文件，已自动排除（疑似测试残留）：")
        for name in stray[:10]:
            print(f"    {name}")
        print("  建议手动删除，避免污染工作目录。")

    print(f"\n[源码] 推送 {OWNER}/{REPO} @ {BRANCH}")
    ref = api("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}")
    parent_sha = ref["object"]["sha"]
    parent = api("GET", f"/repos/{OWNER}/{REPO}/git/commits/{parent_sha}")
    print(f"  父提交 {parent_sha[:8]}")

    files = collect_files()
    verify_required(files)
    print(f"  本地文件 {len(files)} 个，上传 blob…")
    entries = []
    for index, (rel, local) in enumerate(sorted(files.items()), start=1):
        content = base64.b64encode(local.read_bytes()).decode()
        blob = api("POST", f"/repos/{OWNER}/{REPO}/git/blobs",
                   {"content": content, "encoding": "base64"})
        entries.append({
            "path": rel,
            "mode": "100755" if rel.endswith(".bat") else "100644",
            "type": "blob",
            "sha": blob["sha"],
        })
        if index % 10 == 0 or index == len(files):
            print(f"    {index}/{len(files)}")

    tree = api("POST", f"/repos/{OWNER}/{REPO}/git/trees", {"tree": entries})
    commit = api("POST", f"/repos/{OWNER}/{REPO}/git/commits", {
        "message": message,
        "tree": tree["sha"],
        "parents": [parent_sha],
    })
    api("PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}",
        {"sha": commit["sha"], "force": False})
    print(f"  提交 {commit['sha'][:8]}")
    return commit["sha"]


# ---------------------------------------------------------------- 资源

def find_archive() -> Path:
    release = ROOT / "release"
    candidates = sorted(release.glob("ResumeTap-v*-win64.zip"))
    if not candidates:
        raise SystemExit(
            "✗ 找不到发布包。请先运行：python build.py\n"
            f"  期望位置：{release}\\ResumeTap-v<版本>-win64.zip")
    return candidates[-1]


def upload_asset(version: str, archive: Path, notes: str) -> None:
    tag = f"v{version}"
    print(f"\n[发布] {tag}")

    existing = None
    try:
        existing = api("GET", f"/repos/{OWNER}/{REPO}/releases/tags/{tag}")
    except RuntimeError:
        existing = None

    if existing:
        release = api("PATCH", f"/repos/{OWNER}/{REPO}/releases/{existing['id']}",
                      {"name": f"ResumeTap {tag} · 便携版",
                       "body": notes})
        print(f"  更新已有 Release：{release['html_url']}")
    else:
        release = api("POST", f"/repos/{OWNER}/{REPO}/releases", {
            "tag_name": tag,
            "target_commitish": BRANCH,
            "name": f"ResumeTap {tag} · 便携版",
            "body": notes,
            "draft": False,
            "prerelease": False,
        })
        print(f"  新建 Release：{release['html_url']}")

    # 清掉同版本旧资产（同名会冲突）
    assets = api("GET", f"/repos/{OWNER}/{REPO}/releases/{release['id']}/assets")
    for asset in assets if isinstance(assets, list) else []:
        if asset["name"] == archive.name:
            print(f"  删除旧资产 {asset['name']}")
            api("DELETE", f"/repos/{OWNER}/{REPO}/releases/assets/{asset['id']}")

    print(f"  上传 {archive.name}（{archive.stat().st_size/1048576:.1f} MB）…")
    size = archive.stat().st_size
    conn = http.client.HTTPSConnection("uploads.github.com", timeout=900)
    conn.putrequest(
        "POST",
        f"/repos/{OWNER}/{REPO}/releases/{release['id']}/assets?name={archive.name}")
    conn.putheader("Authorization", f"Bearer {TOKEN}")
    conn.putheader("Accept", "application/vnd.github+json")
    conn.putheader("Content-Type", "application/zip")
    conn.putheader("Content-Length", str(size))
    conn.endheaders()
    with open(archive, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            conn.send(chunk)
    response = conn.getresponse()
    body = response.read().decode()
    conn.close()
    if response.status != 201:
        raise SystemExit(f"✗ 上传失败 HTTP {response.status}: {body[:400]}")
    data = json.loads(body)
    print(f"  ✓ {data['browser_download_url']}")


def release_notes(version: str) -> str:
    archive = find_archive()
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


def main() -> int:
    if not TOKEN:
        print("缺少环境变量 GITHUB_TOKEN")
        return 1

    parser = argparse.ArgumentParser(description="发布 ResumeTap 到 GitHub")
    parser.add_argument("--skip-code", action="store_true", help="只上传 zip")
    parser.add_argument("--skip-asset", action="store_true", help="只推送源码")
    parser.add_argument("--message", default="", help="提交信息")
    args = parser.parse_args()

    version = app_version()
    archive = None if args.skip_asset else find_archive()

    if not args.skip_code:
        message = args.message or (
            f"build: 便携版打包（v{version}）\n\n"
            "打包方式改为绿色版目录结构：exe + _internal 依赖 + ui/assets/samples 资源 "
            "+ data 数据目录，整体压缩为 zip 发布。\n"
            "数据默认保存在程序目录下的 data/，实现真正便携。"
        )
        push_code(message)

    if archive:
        upload_asset(version, archive, release_notes(version))

    print(f"\n完成：https://github.com/{OWNER}/{REPO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
