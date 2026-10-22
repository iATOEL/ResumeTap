"""AI 复核（可选）：调用用户自备的 OpenAI 兼容接口，生成优缺点与评分。

设计要点：
- 默认关闭，未填 API Key 时完全不联网
- 用户可决定是否用 AI 结果替换规则引擎（llm.replace_rules）
- 失败时回退规则引擎结果，不影响主流程
- 日志不记录简历正文与 Key
"""

import json
import logging
import re
from typing import Optional

import httpx

from . import config as config_module
from . import extractor

log = logging.getLogger("resume_gacha.ai")

MAX_RESUME_CHARS = 14000

SYSTEM_PROMPT = (
    "你是一位资深招聘专家。请基于简历内容给出客观、可验证的评估，"
    "只返回 JSON，不要输出任何解释文字。"
)

USER_PROMPT = """请评估以下简历，严格按此 JSON 结构返回（不要加其他文字）：

{{
  "name": "候选人姓名",
  "overall_score": 0-100 的综合分（数字）,
  "dimensions": {{
    "skill": {{"score": 0-100, "reason": "技能匹配理由"}},
    "experience": {{"score": 0-100, "reason": "经验年限理由"}},
    "education": {{"score": 0-100, "reason": "教育背景理由"}},
    "project": {{"score": 0-100, "reason": "项目经历理由"}},
    "job_match": {{"score": 0-100, "reason": "与目标岗位的匹配度理由"}}
  }},
  "strengths": ["优点1", "优点2", "优点3"],
  "weaknesses": ["不足1", "不足2"],
  "summary": "一句话结论（30 字以内）"
}}

目标岗位：{role}
要求：
- 评分要有区分度，不要集中在 70-80
- 优点与不足必须来自简历中能找到的事实，不要编造
- 若信息不足，在对应 reason 中说明，不要臆测

简历内容：
{resume}"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def is_ready(config: dict) -> bool:
    llm = config.get("llm", {})
    return bool(llm.get("enabled")) and bool(str(llm.get("api_key") or "").strip())


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    match = _JSON_RE.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _clamp(value, default: float = 0.0) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return default


def _clean_list(value, limit: int = 5) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text[:120])
        if len(out) >= limit:
            break
    return out


def _build_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if not base.endswith("/v1"):
        base += "/v1"
    return base + "/chat/completions"


def review_resume(resume_text: str, role_label: str, config: dict) -> dict:
    """调用用户配置的接口做一次 AI 复核。异常会抛出，由调用方决定回退。"""
    llm = config["llm"]
    if not is_ready(config):
        raise RuntimeError("AI 复核未启用或未填写 API Key")

    prompt = USER_PROMPT.format(role=role_label or "未指定", resume=resume_text[:MAX_RESUME_CHARS])
    payload = {
        "model": llm["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": llm["temperature"],
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {llm['api_key']}",
        "Content-Type": "application/json",
    }

    url = _build_endpoint(llm["base_url"])
    with httpx.Client(timeout=float(llm.get("timeout", 60))) as client:
        response = client.post(url, headers=headers, json=payload)
        if response.status_code == 400:
            # 部分服务不支持 response_format，去掉后重试一次
            payload.pop("response_format", None)
            response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("AI 接口返回结构异常") from exc

    parsed = _extract_json(content)
    if parsed is None:
        raise RuntimeError("AI 返回内容不是有效 JSON")

    dims_raw = parsed.get("dimensions") if isinstance(parsed.get("dimensions"), dict) else {}
    dimensions = []
    labels = {
        "skill": "技能匹配",
        "experience": "经验年限",
        "education": "教育背景",
        "project": "项目经历",
        "job_match": "岗位匹配",
    }
    for key, label in labels.items():
        entry = dims_raw.get(key) if isinstance(dims_raw.get(key), dict) else {}
        dimensions.append({
            "key": key,
            "label": label,
            "score": round(_clamp(entry.get("score")), 1),
            "weight": 0.0,
            "contribution": 0.0,
            "detail": str(entry.get("reason") or "AI 未说明")[:300],
            "hits": [],
            "available": True,
        })

    overall = _clamp(parsed.get("overall_score"))
    if overall <= 0:
        overall = sum(d["score"] for d in dimensions) / max(1, len(dimensions))
    overall = round(overall, 1)

    return {
        "name": str(parsed.get("name") or "").strip()[:40],
        "overall_score": overall,
        "dimensions": dimensions,
        "strengths": _clean_list(parsed.get("strengths"), 5),
        "weaknesses": _clean_list(parsed.get("weaknesses"), 4),
        "summary": str(parsed.get("summary") or "").strip()[:160],
        "model": llm["model"],
    }


def apply_to_payload(payload: dict, ai_result: dict, config: dict,
                     ranked_weights: dict | None = None) -> dict:
    """把 AI 结果合并进评分载荷。

    replace_rules=True 时用 AI 的分数与优缺点替换规则引擎结果；
    否则只把 AI 意见附加为参考，不改变分数。
    """
    llm = config.get("llm", {})
    replace = bool(llm.get("replace_rules", True))
    merged = dict(payload)

    ai_block = {
        "model": ai_result.get("model", ""),
        "overall_score": ai_result["overall_score"],
        "dimensions": ai_result["dimensions"],
        "strengths": ai_result["strengths"],
        "weaknesses": ai_result["weaknesses"],
        "summary": ai_result["summary"],
        "applied": replace,
    }
    merged["ai_review"] = ai_block

    if not replace:
        return merged

    # 替换分数：保留权重以便界面展示贡献
    weights = ranked_weights or {}
    dims = []
    for dim in ai_result["dimensions"]:
        weight = float(weights.get(dim["key"], 0.0))
        dims.append({**dim, "weight": round(weight, 4),
                     "contribution": round(dim["score"] * weight, 2)})
    merged["dimensions"] = dims
    merged["overall_score"] = ai_result["overall_score"]
    merged["rarity"] = config_module.rarity_of(ai_result["overall_score"], config)
    merged["rarity_label"] = config["rarity"]["labels"].get(merged["rarity"], merged["rarity"])
    if ai_result["strengths"]:
        merged["strengths"] = ai_result["strengths"]
    if ai_result["weaknesses"]:
        merged["weaknesses"] = ai_result["weaknesses"]
    if ai_result["summary"]:
        merged["news_flash"] = ai_result["summary"]
    merged["score_source"] = "ai"
    return merged
