# ResumeTap 开发指南

面向参与开发的协作者：如何准备环境、运行、测试、构建与发布，以及代码结构和必须遵守的约定。

> 本文是**公开**文档，随仓库发布。
> 维护者的个人开发日志（演进过程、踩坑记录、本机环境备注）不随仓库发布。

---

## 目录

- [技术栈](#技术栈)
- [环境准备](#环境准备)
- [运行与调试](#运行与调试)
- [测试](#测试)
- [构建](#构建)
- [发布](#发布)
- [代码结构](#代码结构)
- [数据流](#数据流)
- [必须遵守的约定](#必须遵守的约定)
- [常见改动入口](#常见改动入口)
- [前端桥接接口](#前端桥接接口)
- [提交与 PR](#提交与-pr)
- [已知限制](#已知限制)

---

## 技术栈

| 层 | 选型 |
|---|---|
| 桌面窗口 | Python + [pywebview](https://pywebview.flowrl.com/)（Windows 使用系统自带 WebView2） |
| 界面 | 原生 HTML / CSS / JavaScript，无构建链、无 CDN、无 Node 依赖 |
| 存储 | SQLite（单文件） |
| 文档解析 | python-docx（DOCX）、PyMuPDF（PDF） |
| AI 调用 | httpx，调用任意 OpenAI 兼容接口（`/chat/completions`） |

运行平台：Windows 10/11。开发与测试可在其他平台进行，但窗口层仅支持 Windows。

---

## 环境准备

```bash
git clone https://github.com/iATOEL/ResumeTap.git
cd ResumeTap
python -m venv .venv

# Windows
.venv/Scripts/pip.exe install -r requirements.txt

# macOS / Linux（仅开发与测试）
.venv/bin/pip install -r requirements.txt
```

依赖全部列在 `requirements.txt`。注意 `httpx` 是运行必需依赖——AI 评估直接用它发请求，
漏掉会导致启动即崩。

---

## 运行与调试

```bash
# 源码运行
.venv/Scripts/python.exe run_app.py

# 打开开发者工具（WebView2 DevTools）
RESUME_GACHA_DEBUG=1 .venv/Scripts/python.exe run_app.py
```

常用环境变量：

| 变量 | 作用 |
|---|---|
| `RESUME_TAP_DATA_DIR` | 指定数据目录（测试/多实例时用） |
| `RESUME_GACHA_DEBUG` | 打开 DevTools |
| `RESUME_GACHA_CDP_PORT` | 开启 CDP 远程调试端口，供自动化脚本驱动窗口 |

数据目录优先级：`RESUME_TAP_DATA_DIR` → 打包版 `程序目录/data/` →
源码运行 `项目/.devdata/` → 不可写时回退 `%APPDATA%/ResumeGacha`。

---

## 测试

```bash
.venv/Scripts/python.exe -m pytest -q
```

当前共 51 项，分两个文件：

| 文件 | 覆盖内容 |
|---|---|
| `tests/test_ai_pipeline.py` | 解析、章节切分、客观信息抽取、提示词、AI 调用（本地 mock HTTP 服务）、评估队列、导入去重、导出、统计、回归 |
| `tests/test_paths.py` | 便携数据目录定位与旧数据迁移 |

约定：

- 测试不访问外网。AI 相关用例用本地 `HTTPServer` 模拟 OpenAI 兼容响应。
- 改动行为时补充对应测试；修 bug 时优先写一个能复现的用例。
- 每个测试用临时目录隔离数据库与配置，不要依赖开发机上的 `.devdata/`。

---

## 构建

```bash
.venv/Scripts/python.exe build.py            # 便携目录 + zip（推荐）
.venv/Scripts/python.exe build.py --no-zip   # 只生成目录
.venv/Scripts/python.exe build.py --onefile  # 单文件 exe（启动慢，不便携）
```

产物：

```
release/
├── ResumeTap/                     便携目录
│   ├── ResumeTap.exe
│   ├── _internal/                 PyInstaller 运行依赖
│   ├── ui/  assets/  samples/     用户可见、可改的资源
│   ├── data/                      空的首次启动状态
│   ├── config.example.yaml
│   ├── README.txt
│   └── LICENSE
└── ResumeTap-v<版本>-win64.zip    用于 Release
```

构建脚本会自动清掉 `data/` 下的本地 `config.yaml`、`data.db` 与简历副本，
保证发布包是干净的首次启动状态。改构建脚本时注意别破坏这一步。

版本号只在 `app/paths.py` 的 `APP_VERSION` 一处维护，构建与发布脚本都读它。

---

## 发布

前置：[GitHub CLI](https://cli.github.com/) 安装并完成 `gh auth login`
（git 推送凭证由 gh 提供，无需在脚本里放 token）。

```bash
.venv/Scripts/python.exe tools/release.py               # 推送源码 + 创建/更新 Release
.venv/Scripts/python.exe tools/release.py --skip-code    # 只上传 zip
.venv/Scripts/python.exe tools/release.py --skip-asset   # 只推源码
```

脚本行为：

1. `git add -A` + `git commit` + `git push origin main`
2. `gh release create`（新版本）或 `gh release upload --clobber`（已有版本）
3. Release 说明由脚本生成，zip 作为资产上传

发布前确认：测试全绿、版本号已改、zip 内没有 `data.db` / `config.yaml`。

---

## 代码结构

```
app/
├── __main__.py     启动窗口、WebView2 检测、日志、本地 API 拉起
├── paths.py        便携数据目录定位（改打包先看这里）
├── config.py       配置加载/校验/版本迁移（CONFIG_VERSION、MIGRATIONS）
├── ai_engine.py    提示词构建 + AI 调用 + 结果解析
├── eval_queue.py   评估队列（开始/暂停/重试，原子领取）
├── bridge.py       pywebview JS 桥（前端所有 call() 都到这里）
├── extractor.py    客观信息抽取（姓名/电话/邮箱/教育/年限，不参与打分）
├── schools.py      院校层级库
├── rules.py        岗位画像、章节别名、岗位分组
├── storage.py      SQLite（含 eval_status 状态机）
├── services.py     导入、重新排队、导出、统计
├── parser.py       PDF / DOCX / TXT 解析
└── api.py          对外 HTTP API（默认关闭，仅 127.0.0.1）

ui/
├── index.html      单页应用骨架
├── css/style.css   设计系统
└── js/app.js       全部交互逻辑
    js/charts.js    零依赖 Canvas 图表
```

---

## 数据流

```
导入 PDF/DOCX/TXT
    ↓
services.import_files()
    ├─ parser.parse_bytes()        解析纯文本
    ├─ extractor.extract()         抽客观信息（不打分）
    └─ storage.insert_candidate(eval_status="pending")
    ↓
eval_queue.EvalQueue（串行）
    ├─ _claim_next()               pending → running（锁内原子）
    ├─ ai_engine.evaluate()        拼提示词 → 调 AI → 解析 JSON
    ├─ _compose_payload()          AI 结果 + 本地客观信息合并
    └─ storage.set_eval_status("done")
    ↓
前端轮询/推送 → 卡片展示 → 人工 喜欢/不合适/稍后
    ↓
导出 CSV / JSON / Markdown
```

关键点：**分数只在 `ai_engine.evaluate()` 产生**。任何"本地算分"的代码都是错的。

---

## 必须遵守的约定

1. **评分只能由 AI 产出**。不要加回本地规则评分；AI 结果不理想就改提示词
   （`ai_engine.USER_PROMPT` / `_role_block` / `_screening_block` / `_extra_block`）。
2. **稀有度不采信模型返回值**，始终用 `config.rarity_of(score, config)` 按本地阈值换算。
3. **联系方式与教育经历由本地解析**（`extractor.py`），用于展示与导出，不参与打分。
4. **改配置结构必须递增 `CONFIG_VERSION`** 并在 `MIGRATIONS` 中添加迁移，
   否则老用户配置会丢字段。
5. **数据库加列走 `MIGRATIONS`**（`ALTER TABLE ADD COLUMN`），不要改建表语句指望老库自动升级；
   索引放在 `INDEXES` 常量里，在迁移之后执行。
6. **队列只消费 `pending`**。`error` 必须由用户手动重试，否则接口持续故障时会无限重试。
7. **领取任务要原子**：`_claim_next()` 中把状态改成 `running` 必须在锁内完成。
8. **改 HTML 结构后，用 `grep` 搜索旧元素 id**；JS 取 DOM 一律判空。
9. **发布前确认 zip 内没有 `data.db` / `config.yaml`**。
10. **不要把密钥写进代码或提交**。`config.yaml`、`data/` 已在 `.gitignore` 中排除；
    新增依赖时同步更新 `requirements.txt`。

---

## 常见改动入口

| 想改什么 | 改哪里 |
|---|---|
| AI 的判断标准 | `ai_engine.USER_PROMPT`、`_role_block()`、`_screening_block()`、`_extra_block()` |
| 岗位库 | `rules.ROLE_PROFILES`（关键词权重）、`ROLE_GROUPS`（界面分组） |
| 稀有度分级 | `config.rarity` 阈值 + `config.rarity_of()` |
| 学历识别 | `extractor._detect_degree()` / `_detect_school()` / `_education_like_lines()` |
| 院校层级库 | `schools.py` |
| 卡片样式 | `ui/css/style.css`（`.person-card` / `.screen-card`） |
| 速筛交互 | `ui/js/app.js`（`screenCardHtml()` / `decide()` / `doneEmptyHtml()`） |
| 队列行为 | `app/eval_queue.py` |
| 打包结构 | `build.py`（`assemble_portable()`） |
| 数据目录 | `app/paths.py`（`data_dir()`） |

---

## 前端桥接接口

前端通过 `window.pywebview.api.<method>()` 调用 `app/bridge.py`。
所有方法返回 `{ok: true, ...}` 或 `{ok: false, error: "..."}`。

| 方法 | 用途 |
|---|---|
| `bootstrap()` | 启动数据：config、candidates、stats、queue、eval、roles |
| `list_candidates()` | 候选人列表 + 统计 + 评估汇总 |
| `get_candidate_detail(id)` | 单份详情（含原文与 payload） |
| `pick_files()` / `pick_folder()` / `scan_folder()` | 文件选择 |
| `import_files(paths)` | 按路径导入 |
| `import_bytes(filename, b64)` | 拖拽导入（字节通道） |
| `load_samples()` | 载入示例简历 |
| `ai_status()` | AI 是否就绪 + 队列快照 + 评估汇总 |
| `ai_test_connection()` | 测试 API Key |
| `queue_start()` / `queue_pause()` | 开始 / 暂停评估队列 |
| `queue_status()` | 队列状态 + 列表 + 统计 |
| `queue_retry_failed()` | 重试失败项 |
| `queue_requeue_all()` | 全部重新排队 |
| `ai_evaluate_one(id)` | 立即评估单份（绕过队列） |
| `review_candidate(id, verdict, note)` | 人工评价：liked/passed/skipped |
| `save_config(cfg)` / `reset_config()` | 保存 / 重置配置 |
| `export_results(fmt, ids)` | 导出 csv/json/md |
| `api_start()` / `api_stop()` / `api_regenerate_token()` | 本地 API 控制 |
| `delete_candidates(ids)` / `clear_all()` | 删除 |
| `open_data_dir()` / `open_path(p)` | 打开目录/文件 |

队列状态由后端通过 `window.__queueUpdate(snapshot)` 主动推送（见 `bridge._on_queue_update`）。

---

## 提交与 PR

- 提交信息使用 Conventional Commits：`feat:` / `fix:` / `docs:` / `build:` / `chore:` / `refactor:`。
- 一次提交只做一件事，便于回滚与审阅。
- 提交前跑 `.venv/Scripts/python.exe -m pytest -q`，保持全绿。
- 不要提交 `release/`、`build/`、`data/`、`.devdata/`、`config.yaml`（均已在 `.gitignore`）。
- 新增 Python 依赖：同步更新 `requirements.txt`；新增前端资源：确认不依赖 CDN。
- 涉及界面改动时，建议附截图或说明验证方式。

---

## 已知限制

- 仅支持 Windows（依赖 WebView2）；macOS / Linux 只能跑测试。
- 没有代码签名，首次运行可能被 SmartScreen 拦截。
- 没有自动更新机制。
- 对外审查 API 只读写已有结果，不触发 AI 评估。
- 扫描版 PDF（纯图片）需要先 OCR，程序本身不做 OCR。
- `samples/` 为虚构数据，但使用了真实院校名称。
