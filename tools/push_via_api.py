"""通过 GitHub REST API 把本地项目提交到仓库（绕过 git 协议被阻断的问题）。

流程：
  1. 取 main 当前 commit 作为父提交（保留历史）
  2. 为每个本地文件创建 blob
  3. 构造新 tree：本地文件全量加入，旧仓库中已不存在的路径自动消失
  4. 创建 commit 并更新 main 引用

用法：python tools/push_via_api.py
凭据从环境变量 GITHUB_TOKEN 读取，不写死在文件里。
"""

import base64
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

# 不提交的文件/目录
EXCLUDE_DIRS = {".git", "build", "dist", "__pycache__", ".venv", ".pytest_cache", "tools/_probe", "tools/_bad"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".exe", ".spec"}
EXCLUDE_NAMES = {"config.yaml", "data.db", "data.db-shm", "data.db-wal", "Thumbs.db", ".DS_Store"}

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()


def api(method: str, path: str, payload=None, raw: bool = False):
    url = path if path.startswith("http") else API + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            return json.loads(body) if body and not raw else body
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:600]
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc


def collect_files() -> dict[str, Path]:
    """收集要提交的文件，返回 {仓库内相对路径: 本地路径}。"""
    files: dict[str, Path] = {}
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        parts = set(rel.parts)
        if parts & EXCLUDE_DIRS:
            continue
        if any(str(rel).startswith(ex) for ex in EXCLUDE_DIRS):
            continue
        if path.suffix.lower() in EXCLUDE_SUFFIX:
            continue
        if rel.name in EXCLUDE_NAMES:
            continue
        files[rel.as_posix()] = path
    return files


def main() -> int:
    if not TOKEN:
        print("缺少环境变量 GITHUB_TOKEN")
        return 1

    print(f"仓库：{OWNER}/{REPO}  分支：{BRANCH}")

    # 1. 当前分支头
    ref = api("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}")
    parent_sha = ref["object"]["sha"]
    parent_commit = api("GET", f"/repos/{OWNER}/{REPO}/git/commits/{parent_sha}")
    print(f"父提交：{parent_sha[:8]} - {parent_commit['message'].splitlines()[0][:50]}")

    # 2. 旧仓库文件清单（用于统计）
    old_tree = api("GET", f"/repos/{OWNER}/{REPO}/git/trees/{parent_commit['tree']['sha']}?recursive=1")
    old_paths = {item["path"] for item in old_tree.get("tree", []) if item["type"] == "blob"}
    print(f"旧文件：{len(old_paths)} 个")

    # 3. 上传 blob
    files = collect_files()
    print(f"本地文件：{len(files)} 个，开始上传…")
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
        print(f"  [{index}/{len(files)}] {rel}")

    # 4. 新 tree（不指定 base_tree，结果只包含本次上传的文件 => 旧文件自动移除）
    tree = api("POST", f"/repos/{OWNER}/{REPO}/git/trees", {"tree": entries})
    print(f"新 tree：{tree['sha'][:8]}")

    new_paths = set(files)
    removed = sorted(old_paths - new_paths)
    print(f"将移除旧文件 {len(removed)} 个")

    # 5. commit
    message = (
        "feat: 重建为ResumeTap（桌面端简历速筛）\n"
        "\n"
        "- 从 FastAPI Web 应用重构为 pywebview 桌面应用，打包为单文件 exe\n"
        "- 约会软件式逐份速筛：自动给出优点/不足，支持 喜欢 / 不合适 / 稍后\n"
        "- 六维评分（技能/经验/学历/项目/关键词/岗位匹配），权重与阈值可配置\n"
        "- 院校分层（C9/985/211/双一流/海外/普通/专科），学历权重上调并保护应届生\n"
        "- 必须先选定招聘岗位，再按该岗位画像评分\n"
        "- 卡片展示毕业院校全名与联系方式（手机/邮箱）\n"
        "- 支持自定义 OpenAI 兼容接口做 AI 复核（默认关闭，失败回退规则评分）\n"
        "- 对外审查 API 默认关闭，回环 + 令牌鉴权\n"
        "- 修复拖拽导入（改用字节通道，保留原文件名）\n"
        "- 移除成就模块；106 项自动化测试\n"
        "\n"
        "BREAKING CHANGE: 原 Web 服务版本已被桌面应用取代"
    )
    commit = api("POST", f"/repos/{OWNER}/{REPO}/git/commits", {
        "message": message,
        "tree": tree["sha"],
        "parents": [parent_sha],
    })
    print(f"新提交：{commit['sha'][:8]}")

    # 6. 更新分支
    api("PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}",
        {"sha": commit["sha"], "force": False})
    print("分支已更新")

    print("\n完成：")
    print(f"  https://github.com/{OWNER}/{REPO}")
    print(f"  提交：{commit['sha']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
