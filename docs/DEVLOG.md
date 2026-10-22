# ResumeTap 开发日志

> 记录从"改造旧项目"到"纯 AI 驱动便携版"的完整演进过程、踩过的坑与决策依据。
> 供接手的新 Agent 理解**为什么是现在这个样子**。

- 最后更新：2026-10-22
- 当前版本：v3.1.1
- 仓库：https://github.com/iATOEL/ResumeTap
- 项目路径：`F:\Tool\Paper\resume-gacha`

---

## 一、起点：原始项目是什么

原始项目 `resume-screening-system-main` 是一个 **FastAPI + SQLite + 纯 LLM 评分**的 Web 应用：

- 只支持 `.docx`
- 评分完全依赖在线 LLM API，不配 Key 就无法使用
- 前端是 CDN 依赖的 Web 页面（Tailwind + Chart.js）
- 没有稀有度分级、没有离线能力、无法打包成 exe

用户要求改造并打包为 Windows exe。中途明确指示：**删除 Web 版，只做双击启动的桌面应用**。

---

## 二、演进时间线（按需求批次）

### 阶段 1：重构为桌面应用（v2.0）

**决策**：放弃 FastAPI Web 服务，改用 **pywebview + WebView2** 渲染原生窗口。

理由：
- Windows 10/11 自带 WebView2，用户无需装任何环境
- 不监听端口，真正做到"本地离线"
- 前端可用 Web 技术，但资源全部内置（去掉 CDN）

保留原项目的核心思路（多维度评分、SQLite 存储、文件名净化），新增 PDF 支持、R/SR/SSR 分级、批量导出。

**当时是规则引擎**：技能词典命中、年限曲线、院校名单、项目动词统计，六维加权。

### 阶段 2：界面与产品形态重做（v2.1 → v2.2）

用户连续提出：

1. 暗色主题下滑块看不清 → 换浅色主题，滑块改为 8px 轨道 + 蓝色填充 + 白描边
2. 风格不喜欢 → 重做为 **Facebook 简约风**（中性灰底、白卡片、单一蓝色主色）
3. 不符合抽卡设计 → 改为 **约会软件式逐份速筛**（一屏一份，优点/不足，喜欢/不合适/稍后）
4. 评价体系有问题（学历识别错、院校不分档）→ 引入 C9/985/211/双一流/海外/普通/专科 八档
5. 拖拽导入报"无法识别路径" → 改用字节通道（详见坑点 3）

### 阶段 3：岗位、全选、AI 复核（v2.3）

1. 岗位只有计算机类 → 扩到 **27 类，6 个行业分组**，并支持自定义岗位
2. 简历库不能全选 → 加全选 / 反选 / `Ctrl+A`
3. 学历误判（在清华工作被当成清华毕业）→ 学历只从教育章节读取
4. AI 复核不能覆盖全部 → 加"全部重新评估"，去掉硬编码 `limit=10`

### 阶段 4：架构级转向 —— 删除规则引擎（v3.0）

**这是最大的一次改动，也是最重要的一次。**

用户的判断：*"直接按照机械规则审查的话很多简历得不到应有的评价"*。

具体问题：
- 规则无法理解语义，只能数关键词
- 固定权重把差异拉平，所有人挤在 70 分
- 应届生被经验权重压死，名校与中专无法区分
- 打补丁式的调整（提学历权重、加应届保护、缺失维度不计入）治标不治本

**决策：删除整个规则引擎，评分完全交给 AI。**

- 删除 `app/scoring.py`（588 行）、`app/ai_review.py`
- 删除配置里的 `scoring` 段、`rules.py` 里的技能词典与项目动词表
- 新增 `app/ai_engine.py`：岗位画像、筛选规则、评分维度全部写进提示词
- 新增 `app/eval_queue.py`：评估队列（开始 / 暂停 / 重试）
- 本地只保留**客观信息抽取**（姓名、电话、邮箱、教育经历、工作年限），用于展示不参与打分

代价：**没有 API Key 就完全不能用**。这是刻意的取舍，不存在"降级凑合"路径。

### 阶段 5：便携版打包（v3.1）

用户要求改成绿色版目录结构，GitHub 只发 zip。

- `build.py` 重写：PyInstaller `--onedir` → 组装便携目录 → 压缩 zip
- `app/paths.py` 重写：数据目录优先放**程序目录下的 `data/`**，实现真正便携
- 新增 `tools/release.py`：推送源码 + 上传 zip 到 Release

### 阶段 6：发布后体检与缺陷修复（v3.1.1）

对 v3.1.0 的代码做了一次完整核查，修掉 4 个必修问题（详见"坑 10~13"）：

| 问题 | 影响 | 位置 |
|---|---|---|
| `doneEmptyHtml` 未定义 | **速筛看完所有简历后白屏** | `ui/js/app.js` |
| `requirements.txt` 缺 httpx | 全新环境无法启动 | `requirements.txt` |
| 发布脚本前缀匹配 | **仓库里一直缺 `build.py`** | `tools/release.py` |
| 项目章节校名当学历 | 教育信息误判 | `app/extractor.py` |

同时清理了规则引擎的残留展示层：`skill_hits` 永远为空却还在渲染"命中技能（0）"、
详情页永远不显示的 `ai_review` 规则/AI 对比块、导出里恒为 0% 的权重、以及
`pass_rate` 会算出 200% 的统计口径问题。新增 `tools/ui_harness.py`
（mock 桥接 + 真实前端）作为界面冒烟手段。

---

## 三、踩过的坑（按严重程度排序）

### 坑 1：规则引擎表达力不足（架构问题，已由 v3.0 解决）

**现象**：同一份简历，改改权重就能从 SR 变 SSR；应届生全被压成 R。

**尝试过的补丁**（都只是缓解，记录在此供参考）：

| 补丁 | 做法 | 结果 |
|---|---|---|
| 提学历权重 | 0.12 → 0.20，基准分拉大差距 | 有帮助，但仍受其他维度拖累 |
| 应届生保护 | 经验<2年时学历权重×1.75、经验×0.45 | 有效，但规则越堆越复杂 |
| 缺失维度不计入 | 没写项目 → 该维度权重归零并归一化 | 有效，避免判 0 拖垮 |
| 项目基准分上调 | 单项目 35 → 45 | 治标 |

**最终结论**：这些补丁全部随 v3.0 一起删除。教训是——**当规则需要不断打补丁时，说明抽象层次错了**。

### 坑 2：学历识别把工作单位当学校

**现象**：简历只在工作经历里写"2019-至今 清华大学 实验室管理员"，被识别成清华毕业。

**根因**：没有教育章节时，代码退化成 `edu_scope = edu_text or text`，拿整份简历去匹配院校。

**修复**（`app/extractor.py`）：

1. 优先只用「教育背景」章节
2. 没有该章节时，只采信**同时**满足以下条件的行：
   - 含学历词（博士/硕士/本科/大专…）
   - 含院校/毕业/就读/专业信号
   - **不含**工作描述词（负责/主导/参与/公司/任职）
3. 多段学历取最高

`education_source` 字段记录来源（`education_section` / `inferred` / `missing`），便于排查。

### 坑 3：拖拽导入拿不到文件路径

**现象**：拖文件进窗口提示"无法识别路径"。

**根因**：pywebview 只给**通过它自己的 DOM API 注册**的元素注入 `pywebviewFullPath`，普通 HTML 的 `drop` 事件拿到的 `File` 对象没有 `path` 属性。

**修复**：前端读 `arrayBuffer()` → base64 → `import_bytes()` → 后端落盘到 `staging/` → 正常导入 → **把临时文件名还原成用户看到的原名**。

踩到的次级问题：
- 先用 int 数组传输，3 MB 的 PDF 膨胀到 20 MB+ 且卡死主线程 → 改 base64
- 用 `TemporaryDirectory` + with 语句，导入还没完成目录就被删 → 改持久 `staging/` + 导入后清理

### 坑 4：`ai_batch_review` 无限重试

**现象**：接口持续故障时，队列疯狂重试同一份，日志刷屏。

**根因**：`list_by_eval_status("pending", "error")` 把 `error` 也当可消费任务。

**修复**：队列只消费 `pending`；失败记录进入 `error`，需用户手动点「重试失败」才重新入队。

### 坑 5：暂停后立即开始导致重复评估

**现象**：暂停再开始，同一份简历被调用两次 AI。

**根因**：旧工作线程还没退出，新线程就启动了，两条线程同时消费队列。

**修复**：
1. `start()` 先 `_stop.set()` + `join(timeout=5)` 确保旧线程退出
2. `_claim_next()` **原子领取**：取到任务立刻标记为 `running`，防止重复消费
3. 退出循环时 `reset_running_to_pending()` 把中断的任务退回

验证方式：mock 接口计数，暂停→恢复后总调用数必须等于简历数。

### 坑 6：发布包混入本地测试数据

**现象**：第一次打的 zip 里带着测试期间的 `config.yaml` 和 `data.db`，用户解压后会看到陌生简历库。

**修复**：`build.py` 加 `scrub_runtime_data()`，压缩前删掉 `data.db*`、`config.yaml`、各子目录内容（保留 `.gitkeep`）。

### 坑 7：测试残留文件被误传到 GitHub

**现象**：推送后发现仓库根目录多了 28 个 `batch0.docx`、`lim0.docx`。

**根因**：早期测试用了相对路径 `Path("batch0.docx")`，写到当前工作目录（项目根）。

**修复**：
1. 删除残留文件与已归档的旧测试
2. `tools/release.py` 加 `audit_stray_files()`：根目录发现散落文档就警告并自动排除
3. `collect_files()` 只允许 `samples/` 与 `docs/` 下出现 docx/pdf

### 坑 8：SQLite 加列时索引先于列创建

**现象**：老数据库启动报 `no such column: eval_status`。

**根因**：`SCHEMA` 里的 `CREATE INDEX idx_candidates_eval ON candidates(eval_status)` 在 `_migrate()` 加列之前执行。

**修复**：`SCHEMA` 只建表；索引语句移到 `INDEXES`，在 `_migrate()` **之后**执行。

### 坑 9：前端引用被删掉的 DOM 元素

**现象**：界面空白，`stage.innerHTML` 没执行，控制台报 `Cannot read properties of null`。

**根因**：JS 里还引用 `aiToggleBar`（HTML 里已换成 `queueBar`），异常中断了 `renderScreen`。

**教训**：改 HTML 结构后，用 `grep` 全量搜索旧元素 id。JS 里取 DOM 一律先判空。

### 坑 10：速筛队列跑完就白屏（v3.1.1 修复）

**现象**：所有简历都评价完后，速筛页整块空白，控制台报 `doneEmptyHtml is not defined`。

**根因**：`renderScreen()` 引用了 `doneEmptyHtml()` 与 `bindDoneActions()`，但这两个函数在重构时被删掉、调用点没删。异常抛在 `innerHTML` 赋值处，页面停在上一帧。

**修复**：补回两个函数，并区分三种空态——没导入 / 还没评估 / 全部看完了，避免"看完了"和"还没开始"混为一谈。

**教训**：JS 没有编译期检查，删除函数后必须 `grep` 调用点。`node --check` 只查语法，查不出未定义引用；关键路径要有真实浏览器冒烟（`tools/ui_harness.py`）。

### 坑 11：`requirements.txt` 漏了 httpx

**现象**：按 README 全新安装依赖后启动即崩。

**根因**：`ai_engine.py` 与 `bridge.py` 在模块顶层 `import httpx`，但它只是本机 venv 里偶然存在（pywebview 并不依赖它），从没写进 `requirements.txt`。

**修复**：补进 `requirements.txt`。教训：**新 import 必须同步 requirements**，光靠"我这儿能跑"会漏。

### 坑 12：发布脚本用前缀匹配目录名，误伤同名前缀文件

**现象**：GitHub 仓库里一直缺少 `build.py`，`.gitignore` 也没推上去。

**根因**：`release.py` 的 `collect_files()` 用 `rel.startswith(ex)` 判断排除目录，`"build.py".startswith("build")` 与 `".gitignore".startswith(".git")` 都成立，于是被当成构建产物排除。

**修复**：改为按路径分段匹配（`set(rel.parts[:-1]) & EXCLUDE_DIRS`），并加 `REQUIRED_FILES` 校验，缺关键文件直接中止发布。

### 坑 13：项目章节里的校名被当成学历（v3.1.1 修复）

**现象**：简历写"项目经历：华中科技大学 本科 联合培养项目"，被识别成本科学历。

**根因**：`extract()` 里 `result.project_text` 在学历推断之后才赋值，推断时拿到的 `project_text` 是空串，项目章节没被排除。

**修复**：把章节文本的赋值全部提前到学历推断之前。这种"顺序依赖"没有类型检查兜底，必须靠测试锁住（已加回归测试）。

---

## 四、关键设计决策（含理由）

### 1. 为什么用 pywebview 而不是 Electron / Tauri

| 方案 | 取舍 |
|---|---|
| Electron | 打包 150 MB+，需要 Node 构建链 |
| Tauri | 需要 Rust 工具链，构建复杂 |
| **pywebview + WebView2** ✅ | 复用系统 WebView2，包体 43 MB，纯 Python 构建 |

### 2. 为什么评分完全交给 AI

规则引擎的根本问题是**无法理解语义**。它能数出简历里有几个"高并发"，但判断不了这个人是不是真的做过高并发系统。AI 能。代价是必须联网 + 用户自备 Key，这个取舍用户明确接受了。

### 3. 为什么稀有度由本地换算而不是信模型

模型会随手给 SSR。所以 `_normalize_result()` 里**忽略**模型返回的 `rarity` 字段，一律用 `config_module.rarity_of(score, config)` 按本地阈值算。

### 4. 为什么保留本地解析（不全部交给 AI）

姓名、电话、邮箱、教育经历这些是**客观事实**，本地正则比模型更稳更快，而且不消耗 token。AI 只负责判断（分数、优劣），不负责抄写。

### 5. 为什么队列串行而不是并发

并发会瞬间打满接口限流（429）。串行 + 每份之间 0.2 秒间隔，速度可接受且稳定。

### 6. 为什么用 `eval_status` 存数据库而不是内存队列

内存队列在应用崩溃/关闭后丢失。存数据库后：
- 重启自动恢复未完成任务
- 用户可以随时暂停、关软件、下次继续
- 队列状态（pending/running/done/error）可查询、可统计

### 7. 为什么切换岗位不自动重评

规则引擎时代切岗位立刻重算（几乎零成本）；现在每次重评都要花真金白银调 API。所以切岗位只更新展示标签，并提示"建议点全部重新评估"。

### 8. 为什么便携版数据放在程序目录

绿色版的核心诉求是"整个文件夹拷走就能用"。如果数据还在 `%APPDATA%`，那就不是真正的便携。同时保留回退：程序目录不可写（如装在 `C:\Program Files`）时自动改用 `%APPDATA%`。

---

## 五、被删除/废弃的东西（避免新 Agent 重复造轮子）

| 已删除 | 原用途 | 为什么删 |
|---|---|---|
| `app/scoring.py` | 六维加权规则评分 | v3.0 改为纯 AI |
| `app/ai_review.py` | AI 复核（替换规则结果） | 合并进 `ai_engine.py` |
| 配置 `scoring` 段 | 权重、学历加分、项目基准分 | 无规则引擎了 |
| `rules.py` 的 `SKILL_CATALOG` | 技能词典 | 只服务于规则评分 |
| `rules.py` 的 `PROJECT_VERBS` | 项目动词表 | 同上 |
| `rules.py` 的 `TOP_SCHOOLS`/`DEGREE_SCORES` | 院校与学历评分表 | 同上 |
| 成就模块 | "初次开卡""百人斩" | 用户明确说无用 |
| 暗色主题 | — | 改为 Facebook 浅色风 |
| 单文件 exe | — | 改为便携目录 + zip |

**注意**：`rules.py` 仍保留 `SECTION_ALIASES`（章节别名）、`ROLE_PROFILES`（岗位画像，作为提示词素材）、`ROLE_GROUPS`（界面分组）——这些不是评分规则，别误删。

---

## 六、当前遗留问题（未做，供后续判断）

这些是核对代码时发现的，不影响功能，但可以清理：

1. **前端未使用的后端方法**：`next_candidate`、`export_to_folder`、`show_in_folder`、`rescore_all`（保留为兼容名）
2. **`services.py` 里的 `rescore_all`** 只是 `requeue_all` 的别名（注释"兼容旧调用名"）。
   设置页的按钮已改名为「全部重新排队」并调用 `queue_requeue_all`，避免"重新评分"这个误导性文案
3. **`api.py` 的对外接口**不支持触发 AI 评估，只读写已有结果
4. **`samples/` 里的示例简历**是生成的虚构数据，院校用了真实校名
5. **没有做 Windows 代码签名**，首次运行可能被 SmartScreen 拦
6. **没有自动更新机制**

---

## 七、验证方式（怎么确认改动没坏）

每次改动后按这个顺序验证：

```bash
# 1. 单元测试（45 项，纯 AI 流程用 mock 接口）
.venv/Scripts/python.exe -m pytest -q

# 2. 构建
.venv/Scripts/python.exe build.py

# 3. 检查便携目录结构
ls release/ResumeTap/

# 4. 解压 zip 到临时目录，双击运行，确认：
#    - 界面正常显示
#    - data/ 自动生成（说明便携模式生效）
#    - 未配置 AI 时导入简历分数为 0

# 5. 发布
export GITHUB_TOKEN=...
.venv/Scripts/python.exe tools/release.py
```

**界面自检脚本**（`tools/` 下）：
- `ui_probe6.py` / `ui_probe7.py`：用 CDP 驱动真实窗口，验证 AI 队列自动/手动模式
- 依赖 `cdp_shot.py`（极简 WebSocket 客户端，用于截图）
- 启动 exe 时设 `RESUME_GACHA_CDP_PORT=9345` 开启远程调试端口

---

## 八、环境与依赖

```
Python 3.12（用 F:\Tool\Paper\.venv）
pywebview>=5.3,<6      桌面窗口
python-docx            DOCX 解析
PyMuPDF                PDF 解析
PyYAML                 配置
pyinstaller>=6.11      打包
pytest                 测试
```

**环境铁律**（沿用项目 AGENTS.md）：
- 一律用 `.venv/Scripts/python.exe`，不碰全局 Python
- Git Bash 操作，不用 PowerShell
- 路径用正斜杠 `/f/Tool/Paper/...`

---

## 九、GitHub 访问的特殊情况

**直连 git 协议被阻断**：`git push` 超时（`https://github.com/.../info/refs` 无响应），但 `api.github.com` 正常。

**解决方案**：`tools/release.py` 用 **GitHub REST API** 完成推送：
1. 取当前 `main` 的 commit 作为父提交
2. 逐个文件创建 blob → 组装 tree → 创建 commit → 更新 ref

所以**不要**试图用 `git push`，用 `release.py`。

Token 通过环境变量 `GITHUB_TOKEN` 传入，不写在代码里。
