"""评估模块：调用 LLM API 对简历进行多维度评估。"""

import json
import re
from typing import Optional

import httpx

from config import classify_score, load_config
from models import EvaluationResult

REQUEST_TIMEOUT = 60.0
MAX_RETRIES = 2
MAX_RESUME_CHARS = 12000  # 防止超出 LLM 上下文限制

EVAL_PROMPT = """你是一位资深的HR招聘专家，请对以下简历进行全面评估。

请严格按照以下JSON格式返回评估结果（不要包含其他文字，只返回JSON）：

{{
  "name": "候选人姓名（从简历中提取）",
  "education_level": "学历层次（博士/硕士/本科/大专/其他）",
  "education_score": 学历得分（0-100的数值）,
  "experience_score": 工作经验得分（0-100，考虑工作年限、行业匹配度、职级成长）,
  "project_score": 项目经历得分（0-100，考虑项目数量、复杂度、成果量化）,
  "skill_score": 专业技能得分（0-100，考虑技术栈广度与深度、证书资质）,
  "overall_score": 综合竞争力得分（0-100）,
  "category": "分类（强烈推荐/推荐/待定/不推荐，对应分数>=85/70-84/55-69/<55）",
  "summary": "50字以内的简要评语",
  "details": {{
    "education_analysis": "学历分析说明",
    "experience_analysis": "工作经验分析说明",
    "project_analysis": "项目经历分析说明",
    "skill_analysis": "专业技能分析说明"
  }}
}}

简历内容：
{resume_text}"""

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(content: str) -> Optional[dict]:
    """从 LLM 返回内容中提取 JSON（兼容 markdown 代码块包裹）。"""
    text = content.strip()
    if text.startswith("```"):
        # 去掉首尾的 ``` 语言标记
        lines = text.splitlines()
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _clamp_score(value, default: float = 0.0) -> float:
    """将得分约束到 0-100 范围。"""
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return default


def _build_result(data: dict) -> EvaluationResult:
    """将 LLM 返回的字典转换为 EvaluationResult，分数做边界约束。"""
    overall = _clamp_score(data.get("overall_score"))
    category = data.get("category") or classify_score(overall)
    return EvaluationResult(
        name=str(data.get("name") or "未知"),
        education_level=str(data.get("education_level") or "其他"),
        education_score=_clamp_score(data.get("education_score")),
        experience_score=_clamp_score(data.get("experience_score")),
        project_score=_clamp_score(data.get("project_score")),
        skill_score=_clamp_score(data.get("skill_score")),
        overall_score=overall,
        category=str(category),
        summary=str(data.get("summary") or ""),
        details=data.get("details") if isinstance(data.get("details"), dict) else {},
    )


async def evaluate_resume(resume_text: str) -> EvaluationResult:
    """调用 LLM API 对简历进行评估，失败时自动重试。"""
    llm_config = load_config()["llm"]
    if not llm_config["api_key"]:
        raise RuntimeError("未配置 LLM API Key，请在 config.yaml 或环境变量 LLM_API_KEY 中设置")

    prompt = EVAL_PROMPT.format(resume_text=resume_text[:MAX_RESUME_CHARS])
    headers = {
        "Authorization": f"Bearer {llm_config['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": llm_config["model"],
        "messages": [
            {
                "role": "system",
                "content": "你是一位专业的HR招聘专家，擅长简历评估和人才筛选。请严格按照要求的JSON格式返回评估结果。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": llm_config["temperature"],
    }

    last_error: Optional[Exception] = None
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await client.post(
                    f"{llm_config['base_url']}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]

                parsed = _extract_json(content)
                if parsed is not None:
                    return _build_result(parsed)

                last_error = ValueError("LLM 返回内容不是有效的 JSON")
            except (httpx.HTTPError, KeyError, ValueError) as e:
                last_error = e

    raise RuntimeError(f"LLM 评估失败（重试 {MAX_RETRIES} 次后仍失败）: {last_error}")
