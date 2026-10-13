# 简历智能筛选与评估系统

AI 驱动的简历批量解析与多维度评估平台。上传 `.docx` 简历后，系统通过 LLM 从学历、工作经验、项目经历、专业技能四个维度自动打分，并生成综合竞争力排名、分类标签与可视化图表。

## 功能特性

- **批量上传**：支持拖拽/多选上传 `.docx` 简历，自动解析段落与表格内容
- **AI 多维度评估**：学历 / 经验 / 项目 / 技能四维打分 + 综合得分与分类（强烈推荐 / 推荐 / 待定 / 不推荐）
- **可视化看板**：竞争力排行榜柱状图、候选人能力雷达图（最多 5 人对比）、分类统计卡片
- **排序与筛选**：按任意维度分数或上传时间排序，按分类筛选
- **详情报告**：查看完整评估分析、综合评语与简历原文
- **安全防护**：文件名净化（防路径穿越）、上传大小限制（10MB）、前端 XSS 转义、排序字段白名单（防 SQL 注入）

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI、Python 3.10+ |
| 数据库 | SQLite（aiosqlite 异步驱动） |
| 简历解析 | python-docx |
| LLM | 任意 OpenAI 兼容 API（GPT、DeepSeek、通义等） |
| 前端 | Jinja2 模板 + Tailwind CSS + Chart.js |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 LLM API

复制配置示例并填入你的 API Key：

```bash
cp config.example.yaml config.yaml
```

编辑 `config.yaml`：

```yaml
llm:
  base_url: "https://api.openai.com/v1"   # 任意 OpenAI 兼容端点
  api_key: "sk-your-api-key-here"
  model: "gpt-4o-mini"
  temperature: 0.3
```

> API Key 也可以通过环境变量 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` 设置，环境变量优先于配置文件。`config.yaml` 已被 `.gitignore` 排除，不会提交到仓库。

### 3. 启动

```bash
python main.py
# 或
uvicorn main:app --reload
```

- 访问 Web 界面：http://127.0.0.1:8000
- 访问 API 文档（Swagger UI）：http://127.0.0.1:8000/docs

Windows 用户也可直接双击 `启动.bat`。

## 项目结构

```
├── main.py               # FastAPI 应用与 API 路由
├── config.py             # 配置加载（环境变量 > config.yaml）
├── database.py           # SQLite 连接管理与表结构
├── parser.py             # .docx 简历解析（段落 + 表格）
├── evaluator.py          # LLM 评估调用（重试、JSON 提取、分数约束）
├── models.py             # Pydantic 数据模型
├── config.example.yaml   # 配置示例
├── requirements.txt      # Python 依赖
├── tests/                # 单元测试与 API 集成测试
├── static/               # 前端静态资源（CSS/JS）
├── templates/            # Jinja2 模板
└── uploads/              # 简历文件存储目录（运行时生成，不提交）
```

## API 概览

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/upload` | 上传并评估简历（multipart 表单，字段名 `file`） |
| `GET` | `/api/candidates` | 候选人列表，支持 `sort_by` / `order` / `category` 参数 |
| `GET` | `/api/candidates/{id}` | 候选人详情 |
| `DELETE` | `/api/candidates/{id}` | 删除候选人 |

## 运行测试

```bash
pip install pytest
pytest tests/ -v
```

## 环境要求

- Python 3.10+
- 可用的 OpenAI 兼容 LLM API

## 许可证

[MIT License](LICENSE)
