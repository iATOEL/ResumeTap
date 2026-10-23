# ResumeTap 交接要点

> 给接手的 AI Agent / 开发者的最短上手路径。
> **先读这份，再读 `DEVLOG.md`（了解为什么），最后看代码。**

- 项目路径：`F:\Tool\Paper\resume-gacha`
- 仓库：https://github.com/iATOEL/ResumeTap
- 版本：v3.1.1
- 状态：可用，已发布

---

## 一、30 秒理解这个项目

**它是什么**：Windows 便携式简历初筛桌面应用。导入简历 → AI 逐份评估 → 卡片式速筛。

**核心约定**：
1. **纯 AI 驱动**——没有规则引擎。分数、等级、优缺点全部来自用户配置的 AI 接口
2. **没有 API Key 就不能用**——未配置时简历只能导入、不能评估（刻意设计，不是 bug）
3. **本地只做客观信息抽取**——姓名、电话、邮箱、教育经历、工作年限，用于展示，不参与打分
4. **稀有度由本地按阈值换算**——不采信模型返回的 `rarity` 字段

**技术栈**：Python 3.12 + pywebview（WebView2）+ SQLite + 原生 HTML/CSS/JS。无 CDN，无网络依赖。

---

## 二、目录地图

```
resume-gacha/
├── run_app.py                  PyInstaller 入口（只调 app.__main__.main）
├── build.py                    构建便携版 + zip
├── pytest.ini                  测试配置（排除 _legacy、build、release）
├── config.example.yaml         配置模板（进发布包）
├── app/                        后端，约 4200 行
│   ├── __main__.py             窗口启动、WebView2 检测、日志、API 拉起
│   ├── paths.py                ★ 便携数据目录定位（改打包先看这里）
│   ├── config.py               ★ 配置加载/校验/版本迁移（CONFIG_VERSION）
│   ├── ai_engine.py            ★★ 提示词构建 + AI 调用 + 结果解析
│   ├── eval_queue.py           ★★ 评估队列（开始/暂停/重试，原子领取）
│   ├── bridge.py               ★ pywebview JS 桥（前端所有 call() 都到这里）
│   ├── extractor.py            客观信息抽取（学历防误判逻辑在这里）
│   ├── schools.py              院校层级库（C9/985/211/双一流/海外/普通/专科）
│   ├── rules.py                岗位画像 + 章节别名 + 岗位分组（不含评分规则）
│   ├── storage.py              SQLite（含 eval_status 状态机）
│   ├── services.py             导入、重新排队、导出、统计
│   ├── parser.py               PDF / DOCX / TXT 解析
│   └── api.py                  对外 HTTP API（默认关闭）
├── ui/                         前端，约 4400 行
│   ├── index.html              单页应用骨架
│   ├── css/style.css           Facebook 风设计系统
│   └── js/app.js               全部交互逻辑
│       js/charts.js            零依赖 Canvas 图表
├── tests/
│   ├── test_ai_pipeline.py     43 项：解析/提示词/AI/队列/导出/回归
│   └── test_paths.py           7 项：便携目录逻辑
├── tools/
│   ├── release.py              ★ 推 GitHub + 上传 zip（git push + gh release）
│   ├── make_samples.py         生成示例简历
│   ├── make_icon.py            生成图标
│   ├── cdp_shot.py             CDP 截图（极简 WebSocket 实现）
│   └── ui_probe*.py            界面自检脚本
├── docs/
│   ├── DEVLOG.md               开发日志（为什么这么做）
│   └── screenshots/
├── release/                    构建产物（gitignored）
│   ├── ResumeTap/              便携目录
│   └── ResumeTap-v3.1.1-win64.zip
└── samples/                    12 份示例简历
```

---

## 三、数据流（最重要的一张图）

```
用户拖入 PDF/DOCX
    ↓
services.import_files()
    ├─ parser.parse_bytes()          解析纯文本
    ├─ extractor.extract()           抽姓名/电话/邮箱/教育/年限（不打分）
    └─ storage.insert_candidate(eval_status="pending")   ← 此时分数为 0
    ↓
eval_queue.EvalQueue（串行）
    ├─ _claim_next()                 原子领取：pending → running
    ├─ ai_engine.evaluate()          拼提示词 → 调 AI → 解析 JSON
    ├─ _compose_payload()            AI 结果 + 客观解析信息 合并
    ├─ storage.update_candidate()    写分数与优缺点
    └─ storage.set_eval_status("done")
    ↓
前端轮询/推送 → 卡片显示 → 用户 喜欢/不合适/稍后
    ↓
导出 CSV / JSON / Markdown
```

**关键点**：分数只在 `ai_engine.evaluate()` 产生。任何"本地算分"的代码都是错的。

---

## 四、核心数据结构

### 候选人记录（`candidates` 表 + `payload` JSON）

```python
{
  "id": 1,
  "name": "张伟",
  "filename": "张伟-后端.docx",
  "raw_text": "简历全文...",          # 可关闭保存（privacy.store_raw_text）
  "overall_score": 79.5,             # AI 产出，未评估时为 0
  "rarity": "SR",                    # 本地按阈值算
  "status": "pass",                  # pass / flagged / rejected（AI 判定）
  "eval_status": "done",             # ★ pending / running / done / error
  "eval_error": "",
  "review_verdict": "",              # 人工：liked / passed / skipped
  "payload": {                       # JSON 大字段
    "dimensions": [                  # AI 五维
      {"key":"skill","label":"技能匹配","score":85,"weight":0.0,
       "detail":"AI 给的判断依据","available":true}
    ],
    "strengths": ["..."],
    "weaknesses": ["..."],
    "summary": "一句话结论",
    "score_source": "ai",
    "ai_model": "gpt-4o-mini",
    "contact": {"phone":"138...","email":"..."},   # 本地抽取
    "profile": {"education_entries":[...]},        # 本地抽取
    "sections": {"education":"...","experience":"..."}
  }
}
```

### 配置（`data/config.yaml`）

```yaml
llm:
  enabled: false        # 必须 true + 有 api_key 才能用
  base_url: https://api.openai.com/v1
  api_key: ""
  model: gpt-4o-mini
  temperature: 0.2
  timeout: 90
  auto_start: true      # 导入后自动开始评估

role: ""                # ""=未选 / auto / custom / backend / frontend / ...
custom_role: {label: "", keywords: []}

screening:              # 会写进提示词，由 AI 判断
  min_score: 0
  min_years: 0
  required_degree: ""
  must_keywords: []
  reject_keywords: []

rarity: {ssr: 82, sr: 66, labels: {...}}

api: {enabled: false, port: 8765}    # 对外 HTTP API
privacy: {store_raw_text: true}
config_version: 6                    # 迁移用，改结构时递增
```

---

## 五、必须知道的 10 条规则

1. **不要加回规则评分**。如果发现 AI 结果不好，改提示词（`ai_engine.USER_PROMPT`），不要加本地算分。
2. **改配置结构必须递增 `CONFIG_VERSION`**（`app/config.py`），并在 `MIGRATIONS` 里加迁移，否则老用户配置会丢。
3. **数据库加列**：加进 `MIGRATIONS` 字典（`ALTER TABLE ADD COLUMN`），不要改 `SCHEMA` 的建表语句指望老库自动升级。索引放在 `INDEXES` 常量里，在迁移后执行。
4. **队列消费只认 `pending`**。`error` 必须由用户手动重试，否则接口故障时会无限重试。
5. **领取任务要原子**：`_claim_next()` 里 `set_eval_status("running")` 必须在锁内完成。
6. **改 HTML 结构后，`grep` 旧元素 id**。JS 里取 DOM 一律判空（`if (el) el.classList...`）。
7. **稀有度不采信 AI**。始终用 `config_module.rarity_of(score, config)`。
8. **联系方式与教育经历由本地解析**。别指望 AI 抄写这些字段。
9. **发布用 `tools/release.py`**（内部走 `git push` + `gh release`）。要求已安装 gh 并 `gh auth login`；git push 凭证由 gh 提供。若 git 协议再次被阻断，脚本会直接报错——不要悄悄换回 REST 逐文件上传，先确认网络。
10. **发布前确认 zip 里没有 `data.db` / `config.yaml`**（构建脚本会清，但改脚本时注意别破坏）。

---

## 六、常用命令

```bash
# 环境（不要用全局 python）
PY=/f/Tool/Paper/.venv/Scripts/python.exe

# 跑测试
$PY -m pytest -q                       # 期望 50 passed

# 只跑 AI 流程测试
$PY -m pytest tests/test_ai_pipeline.py -q

# 构建便携版（生成 release/ResumeTap/ 和 zip）
$PY build.py

# 只生成目录不压缩
$PY build.py --no-zip

# 单文件 exe（备选，不便携）
$PY build.py --onefile

# 发布到 GitHub（推送源码 + 上传 zip；前置：gh auth login）
$PY tools/release.py

# 只上传 zip / 只推源码
$PY tools/release.py --skip-code
$PY tools/release.py --skip-asset

# 源码运行（开发调试）
$PY run_app.py

# 带开发者工具启动
RESUME_GACHA_DEBUG=1 $PY run_app.py

# 界面自检（需要先启动带 CDP 端口的 exe）
RESUME_GACHA_CDP_PORT=9345 ./release/ResumeTap/ResumeTap.exe
$PY tools/ui_probe7.py
```

---

## 七、前端桥接接口速查

前端通过 `window.pywebview.api.<method>()` 调用 `app/bridge.py` 的方法。
所有方法返回 `{ok: true, ...}` 或 `{ok: false, error: "..."}`。

| 方法 | 用途 |
|---|---|
| `bootstrap()` | 启动数据：config、candidates、stats、queue、eval、roles、role_groups |
| `list_candidates()` | 候选人列表 + 统计 + 评估汇总 |
| `get_candidate_detail(id)` | 单份详情（含原文与 payload） |
| `pick_files()` / `pick_folder()` / `scan_folder()` | 文件选择 |
| `import_files(paths)` | 按路径导入 |
| `import_bytes(filename, b64)` | 拖拽导入（字节通道） |
| `load_samples()` | 载入示例简历 |
| **`ai_status()`** | AI 是否就绪 + 队列快照 + 评估汇总 |
| **`ai_test_connection()`** | 测试 API Key |
| **`queue_start()`** / **`queue_pause()`** | 开始 / 暂停评估队列 |
| **`queue_status()`** | 队列状态 + 列表 + 统计 |
| **`queue_retry_failed()`** | 重试失败项 |
| **`queue_requeue_all()`** | 全部重新排队 |
| **`ai_evaluate_one(id)`** | 立即评估单份（绕过队列） |
| `review_candidate(id, verdict, note)` | 人工评价：liked/passed/skipped |
| `save_config(cfg)` / `reset_config()` | 保存/重置配置 |
| `export_results(fmt, ids)` | 导出 csv/json/md |
| `api_start()` / `api_stop()` / `api_regenerate_token()` | 对外 API 控制 |
| `delete_candidates(ids)` / `clear_all()` | 删除 |
| `open_data_dir()` / `open_path(p)` | 打开目录/文件 |

**队列状态推送**：后端通过 `window.__queueUpdate(snapshot)` 主动推给前端（在 `bridge._on_queue_update`）。

---

## 八、改功能的常见入口

| 想改什么 | 改哪里 |
|---|---|
| AI 的判断标准 | `ai_engine.USER_PROMPT` + `_role_block()` + `_screening_block()` |
| 岗位库 | `rules.ROLE_PROFILES`（关键词权重）+ `ROLE_GROUPS`（分组） |
| 稀有度分级 | `config.rarity` 阈值 + `config_module.rarity_of()` |
| 学历识别 | `extractor._detect_degree()` / `_detect_school()` / `_education_like_lines()` |
| 院校层级 | `schools.py` 的名单与 `TIER_BONUS` |
| 卡片样式 | `ui/css/style.css` 的 `.person-card` / `.screen-card` |
| 速筛交互 | `ui/js/app.js` 的 `screenCardHtml()` / `decide()` |
| 队列行为 | `app/eval_queue.py` |
| 打包结构 | `build.py` 的 `assemble_portable()` |
| 数据存放位置 | `app/paths.py` 的 `data_dir()` |

---

## 九、测试怎么写的（照抄即可）

测试用**本地 mock 的 OpenAI 兼容服务**，不真的调 API：

```python
@pytest.fixture()
def mock_ai():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading, json

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            payload = {
                "overall_score": 75,
                "dimensions": {k: {"score": 75, "reason": "r"}
                               for k in ["skill","experience","education","project","job_match"]},
                "strengths": ["优势"], "weaknesses": ["不足"],
                "summary": "结论", "status": "pass",
            }
            out = {"choices": [{"message": {"content": json.dumps(payload)}}]}
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()
```

**注意**：测试要用 `config_module.save_config()` 写盘，因为 `Bridge._config()` 读的是磁盘文件，不是内存对象。

---

## 十、发布检查清单

发布前逐项确认：

- [ ] `pytest -q` 全绿（50 项）
- [ ] 版本号改了 `app/paths.py` 的 `APP_VERSION`
- [ ] `build.py` 跑通，`release/ResumeTap/` 结构正确
- [ ] zip 里没有 `data.db` / `config.yaml`
- [ ] 根目录没有测试残留的 `.docx`
- [ ] 解压 zip 到临时目录，双击能运行
- [ ] 未配置 AI 时导入简历，分数应为 0、状态 pending
- [ ] `release.py` 推送成功，Release 里有 zip 资产

---

## 十一、当前已知问题

不影响使用，但可以顺手修：

1. 前端未调用的后端方法：`next_candidate`、`export_to_folder`、`show_in_folder`、`rescore_all`（保留为兼容别名，实际前端用 `queue_requeue_all`）
2. 对外 API 不能触发 AI 评估，只读写已有结果
3. 没有代码签名，Windows SmartScreen 可能拦截首次运行
4. 没有自动更新机制
5. `samples/` 里用了真实校名（数据是虚构的）

### v3.1.1 修掉的问题（别再改回去）

| 问题 | 症状 | 现在的位置 |
|---|---|---|
| `doneEmptyHtml`/`bindDoneActions` 缺失 | 速筛看完后白屏 | `ui/js/app.js` 的 `renderScreen()` |
| `requirements.txt` 缺 `httpx` | 全新环境启动即崩 | `requirements.txt` |
| `release.py` 用前缀匹配排除目录 | 仓库里缺 `build.py`、`.gitignore` | 已改为 git push，文件清单交由 `.gitignore` 决定 |
| 章节文本赋值晚于学历推断 | 项目章节的校名被当学历 | `extractor.extract()` 的顺序 |
| `skill_hits` 无写入者 | 卡片/详情永远显示"命中技能 0" | 改为展示抽取的 `sections.skills` |
| `statistics()` 统计未评估记录 | `pass_rate` 可超过 100% | `services.statistics()` |

**界面冒烟手段**：`tools/ui_harness.py` 用 mock 桥接跑真实 `ui/`，
访问它打印的 localhost 地址即可点测速筛 / 简历库 / 统计 / 设置四个页面。

---

## 十二、和用户沟通的注意点

从历史对话看，这位用户：

- **重视实际效果大于架构洁癖**——他会明确指出"规则引擎筛得不准"，要求推倒重来
- **注重细节体验**——会指出"暗色模式滑块看不清""拖拽报错"这种具体问题
- **明确反对无用功能**——直接说"我不需要成就这种无用功能"
- **交付要求具体**——要 exe、要便携版、要 zip 上传 GitHub

**沟通建议**：
- 改动前先说清方案与影响（尤其是破坏性变更）
- 不要问"要不要我做"，直接做完汇报
- 汇报时先说结果，再说细节
- 遇到需要用户决策的（如删除数据、暴露密钥），停下来确认
