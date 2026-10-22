"""构建 Windows 便携版（绿色版）发布目录，并打成一个 zip。

用法::

    <venv>/Scripts/python.exe build.py              # 目录版 + zip（默认，推荐）
    <venv>/Scripts/python.exe build.py --onefile    # 单文件 exe（备选，不做 zip）
    <venv>/Scripts/python.exe build.py --no-zip     # 只生成目录，不压缩

产物::

    release/ResumeTap/                  ← 便携版根目录，可直接运行/拷贝
        ResumeTap.exe
        _internal/                      运行依赖
        ui/ assets/ samples/            可直接查看与修改的资源
        data/                           配置、数据库、简历、导出、日志
        config.example.yaml
        README.txt  LICENSE
    release/ResumeTap-v3.1.0-win64.zip  ← 上传 GitHub Release 用这个
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "ResumeTap"
BUILD = ROOT / "build"
RELEASE = ROOT / "release"
APP_DIR = RELEASE / APP_NAME

VERSION = "3.1.1"


def _read_version() -> str:
    """从 app/paths.py 读版本号，避免两处不一致。"""
    try:
        text = (ROOT / "app" / "paths.py").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("APP_VERSION"):
                return line.split("=", 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return VERSION


def ensure_icon() -> None:
    if not (ROOT / "assets" / "icon.ico").exists():
        print("[1/6] 生成图标…")
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)
    else:
        print("[1/6] 图标已存在")


def ensure_samples() -> None:
    sample_dir = ROOT / "samples"
    files = list(sample_dir.glob("*.docx")) if sample_dir.is_dir() else []
    if not files:
        print("[2/6] 生成示例简历…")
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_samples.py")], check=True)
    else:
        print(f"[2/6] 示例简历 {len(files)} 份")


def clean() -> None:
    print("[3/6] 清理旧产物…")
    for folder in (BUILD, RELEASE):
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
    # 旧版本遗留在根目录的单文件 exe
    for stale in (ROOT / f"{APP_NAME}.exe", ROOT / "简历TAP.exe"):
        if stale.exists():
            try:
                stale.unlink()
            except OSError as exc:
                print(f"  ! 无法删除旧的 {stale.name}：{exc}（请先关闭程序）")


def run_pyinstaller(onefile: bool) -> int:
    print("[4/6] PyInstaller 打包（首次约 1-3 分钟）…")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--distpath", str(RELEASE if not onefile else ROOT),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
    ]
    cmd += ["--onefile" if onefile else "--onedir", "--name", APP_NAME]

    cmd += [
        "--windowed",
        "--add-data", f"{ROOT / 'ui'}{os.pathsep}ui",
        "--add-data", f"{ROOT / 'assets'}{os.pathsep}assets",
        "--add-data", f"{ROOT / 'samples'}{os.pathsep}samples",
        "--hidden-import", "webview.platforms.edgechromium",
        "--hidden-import", "clr_loader",
        "--hidden-import", "pythonnet",
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "PIL",
        "--exclude-module", "pytest",
    ]

    icon = ROOT / "assets" / "icon.ico"
    if icon.exists():
        cmd += ["--icon", str(icon)]
    cmd.append(str(ROOT / "run_app.py"))

    started = time.time()
    result = subprocess.run(cmd, cwd=str(ROOT))
    print(f"      耗时 {time.time() - started:.0f}s")
    return result.returncode


# ---------------------------------------------------------------- 组装便携目录

PORTABLE_README = """ResumeTap {version} —— 便携版（绿色版）
=================================================================

【怎么用】
  1. 双击 ResumeTap.exe 启动（或右键 → 发送到 → 桌面快捷方式）
  2. 首次启动需要配置 AI：设置 → AI 评估 → 填接口地址、API Key、模型
  3. 顶部选择招聘岗位，然后导入简历，AI 会逐份评估

  本软件没有内置 AI，必须使用你自己的 OpenAI 兼容接口
  （OpenAI / DeepSeek / 通义 / 本地 Ollama 等均可）。
  未配置时无法评估简历，这是故意设计——分数全部由 AI 产出。

【目录说明】
  ResumeTap.exe          启动器
  _internal\\             程序运行依赖，请勿删除或移动
  ui\\                   界面文件（HTML/CSS/JS），可自行调整外观
  assets\\               图标等静态资源
  samples\\              示例简历，可用于试用
  data\\                 你的全部数据（见下）
  config.example.yaml    配置参考，含全部可调项说明
  LICENSE                开源许可证

  data\\config.yaml       你的配置（API Key、岗位、筛选规则、阈值）
  data\\data.db           简历库与评估结果
  data\\resumes\\         导入的简历文件副本
  data\\exports\\         导出的 CSV / JSON / Markdown
  data\\logs\\            运行日志

【便携性】
  所有数据都在本文件夹内，可以直接拷贝到 U 盘或另一台电脑，拷过去即可继续用。
  想备份就整包复制；想彻底卸载，删掉这个文件夹即可。

  如果放在 C:\\Program Files 这类无写入权限的位置，程序会自动改用
  %APPDATA%\\ResumeGacha 存放数据（此时不再是便携模式）。
  想强制指定数据位置，可设置环境变量 RESUME_TAP_DATA_DIR。

【常见问题】
  · 双击没反应 / 闪退
      看 data\\logs\\app.log。常见原因是缺少 WebView2 运行时
      （Windows 11 与新版 Win10 已内置），或杀毒软件误拦。

  · 提示"AI 未配置"
      到设置页填 API Key。本软件不联网评估，必须配置你自己的接口。

  · 支持哪些简历格式
      PDF / DOCX / TXT。扫描版 PDF（纯图片）需先做 OCR。
      旧版 .doc / .wps 不支持，请另存为 .docx。

  · 改了岗位，为什么分数没变
      切换岗位只更新标签。要按新岗位重评，点设置页的「全部重新评估」，
      再到速筛页点「开始评估」。

【隐私】
  · 简历只发送到你自己填写的 AI 接口，不经过任何第三方服务器
  · 其余数据全部保存在本文件夹
  · 日志只记录文件名与错误类型，不记录简历正文

项目主页：https://github.com/iATOEL/ResumeTap
"""


def assemble_portable() -> None:
    """在 release/ResumeTap 下补齐便携版所需的目录与说明文件。"""
    print("[5/6] 组装便携目录…")
    if not (APP_DIR / f"{APP_NAME}.exe").exists():
        raise SystemExit(f"✗ 未找到 {APP_DIR / (APP_NAME + '.exe')}")

    # 资源目录：PyInstaller 已放进 _internal，这里额外放一份用户可见的副本，
    # 便于用户直接改 ui/ 下的样式或查看示例简历。
    for name in ("ui", "assets", "samples"):
        src = ROOT / name
        if not src.is_dir():
            continue
        dst = APP_DIR / name
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)

    # 数据目录：空壳 + 配置模板
    data = APP_DIR / "data"
    for sub in ("resumes", "exports", "logs"):
        (data / sub).mkdir(parents=True, exist_ok=True)

    example = ROOT / "config.example.yaml"
    if example.exists():
        shutil.copy2(example, APP_DIR / "config.example.yaml")
        shutil.copy2(example, data / "config.example.yaml")

    readme = APP_DIR / "README.txt"
    readme.write_text(PORTABLE_README.format(version=_read_version()), encoding="utf-8")

    license_file = ROOT / "LICENSE"
    if license_file.exists():
        shutil.copy2(license_file, APP_DIR / "LICENSE")

    # 空目录在 zip 里会丢失，用 .gitkeep 占位
    for sub in ("resumes", "exports", "logs"):
        (data / sub / ".gitkeep").write_text("", encoding="utf-8")


def scrub_runtime_data() -> None:
    """压缩前清掉本地测试产生的数据，保证发布包是干净的首次启动状态。"""
    data = APP_DIR / "data"
    for name in ("data.db", "data.db-shm", "data.db-wal", "config.yaml"):
        target = data / name
        if target.exists():
            try:
                target.unlink()
            except OSError as exc:
                print(f"  ! 无法删除 {target.name}：{exc}（请先关闭正在运行的程序）")
    for sub in ("resumes", "exports", "logs"):
        folder = data / sub
        if not folder.is_dir():
            continue
        for item in folder.iterdir():
            if item.name == ".gitkeep":
                continue
            try:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink()
            except OSError:
                pass


def make_zip() -> Path:
    print("[6/6] 压缩发布包…")
    version = _read_version()
    archive = RELEASE / f"{APP_NAME}-v{version}-win64.zip"
    if archive.exists():
        archive.unlink()

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(APP_DIR.rglob("*")):
            if path.is_dir():
                continue
            # 相对路径保留顶层目录名，解压后得到 ResumeTap/...
            arcname = Path(APP_NAME) / path.relative_to(APP_DIR)
            zf.write(path, arcname.as_posix())
    return archive


def _dir_size(path: Path) -> float:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total / (1024 * 1024)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="构建 ResumeTap 便携版发布目录（并打包 zip）")
    parser.add_argument("--onefile", action="store_true",
                        help="改为构建单文件 exe（不生成便携目录与 zip）")
    parser.add_argument("--no-zip", action="store_true", help="只生成目录，不压缩")
    parser.add_argument("--keep", action="store_true", help="保留旧的 build/release")
    args = parser.parse_args()

    ensure_icon()
    ensure_samples()
    if not args.keep:
        clean()

    code = run_pyinstaller(onefile=args.onefile)
    if code != 0:
        print("✗ 打包失败，请查看上面的 PyInstaller 输出")
        return code

    if args.onefile:
        target = ROOT / f"{APP_NAME}.exe"
        print(f"\n✓ 构建完成：{target}  ({target.stat().st_size/1048576:.1f} MB)")
        print("  提示：单文件版启动较慢，且不便携（数据仍会写到程序目录）。")
        return 0

    try:
        assemble_portable()
    except SystemExit as exc:
        print(exc)
        return 1

    scrub_runtime_data()

    print(f"\n✓ 便携目录：{APP_DIR}")
    print(f"  目录大小：{_dir_size(APP_DIR):.1f} MB")

    if not args.no_zip:
        archive = make_zip()
        print(f"✓ 发布压缩包：{archive}  ({archive.stat().st_size/1048576:.1f} MB)")
        print("\n把上面这个 zip 上传到 GitHub Release 即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
