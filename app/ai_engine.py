"""纯 AI 评估引擎：简历评分、优缺点、结论全部由用户配置的模型产出。

规则引擎只保留"客观信息抽取"（姓名/电话/邮箱/教育经历/工作年限），
用于界面展示与导出；分数、等级、优缺点、筛选结论均来自 AI。

设计要点：
- 未配置 AI 时不允许评估（由上层拒绝）
- 提示词里写入岗位画像、筛选规则、评分维度，让模型按招聘要求判断
- 输出严格 JSON，解析失败重试一次
- 失败时抛异常，由队列记录并在界面提示，不编造分数
"""

import json
import logging
import re
from typing import Optional

import httpx

from . import config as config_module
from .rules import ROLE_PROFILES

log = logging.getLogger("resume_gacha.ai")

MAX_RESUME_CHARS = 16000

DIMENSION_KEYS = ["skill", "experience", "education", "project", "job_match"]
DIMENSION_LABELS = {
    "skill": "技能匹配",
    "experience": "经验年限",
    "education": "教育背景",
    "project": "项目经历",
    "job_match": "岗位匹配",
}

SYSTEM_PROMPT = (
    "你是一位资深招聘专家，负责按岗位要求评估简历。"
    "你的判断必须基于简历中的事实，不得编造；信息不足时在理由中说明。"
    "只输出 JSON，不要输出任何额外文字。"
)

USER_PROMPT = """请评估下面这份简历与目标岗位的匹配程度，严格按 JSON 结构返回。

## 目标岗位
{role_block}

## 筛选规则（如果不满足，请把 status 设为 rejected 并说明原因）
{screening_block}

## 评分维度（各 0-100 分）
- skill：技术栈/专业技能与岗位要求的匹配度
- experience：工作年限、行业相关性、职级成长
- education：学历层次与院校水平
- project：项目数量、复杂度、成果量化程度
- job_match：综合来看与目标岗位的整体契合度

## 输出格式（只返回这个 JSON）
{{
  "name": "候选人姓名",
  "overall_score": 0-100 的综合分（数字，保留一位小数）,
  "rarity": "SSR | SR | R（由综合分与阈值决定）",
  "status": "pass | flagged | rejected",
  "dimensions": {{
    "skill": {{"score": 0-100, "reason": "判断依据，引用简历中的具体内容"}},
    "experience": {{"score": 0-100, "reason": "..."}},
    "education": {{"score": 0-100, "reason": "..."}},
    "project": {{"score": 0-100, "reason": "..."}},
    "job_match": {{"score": 0-100, "reason": "..."}}
  }},
  "strengths": ["优势1（引用简历事实）", "优势2", "优势3"],
  "weaknesses": ["不足1", "不足2"],
  "summary": "一句话结论，30 字以内",
  "screen_reasons": ["若 status 非 pass，列出不满足的具体规则"]
}}

评分要求：
- 要有区分度，不要所有简历都给 70-80 分
- strengths/weaknesses 必须能对应到简历里的具体内容
- 综合分应与各维度分数基本一致（按加权或整体判断均可）
- 稀有度阈值：SSR >= {ssr}，SR >= {sr}，其余为 R

## 简历内容
{resume}"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


# ---------------------------------------------------------------- 配置就绪

def is_ready(config: dict) -> bool:
    llm = config.get("llm", {})
    return bool(llm.get("enabled")) and bool(str(llm.get("api_key") or "").strip())


def readiness_error(config: dict) -> str:
    """未就绪时返回给用户看的原因。"""
    llm = config.get("llm", {})
    if not llm.get("enabled"):
        return "AI 评估未启用：请在设置页勾选「启用 AI 评估」并填写 API Key"
    if not str(llm.get("api_key") or "").strip():
        return "AI 评估缺少 API Key：请在设置页填写"
    return ""


# ---------------------------------------------------------------- 提示词

def _role_block(config: dict) -> str:
    role = str(config.get("role") or "").strip()

    if role == "custom":
        custom = config.get("custom_role") or {}
        label = str(custom.get("label") or "自定义岗位").strip()
        keywords = [str(k) for k in (custom.get("keywords") or []) if str(k).strip()]
        lines = [f"岗位名称：{label}"]
        if keywords:
            lines.append("岗位核心关键词（越靠前越重要）：" + "、".join(keywords))
        return "\n".join(lines)

    if role == "auto":
        return ("岗位：由你根据简历内容判断最匹配的岗位方向，"
                "并在理由中说明你判断的岗位。")

    profile = ROLE_PROFILES.get(role) if role else None
    if not profile:
        return "岗位：未指定具体岗位，请按简历所体现的专业方向评估其整体竞争力。"

    lines = [f"岗位名称：{profile['label']}"]
    weighted = sorted(profile["keywords"].items(), key=lambda kv: -kv[1])
    core = [f"{term}（权重 {weight}）" for term, weight in weighted[:12]]
    lines.append("该岗位看重的核心能力：" + "、".join(core))
    return "\n".join(lines)


def _screening_block(config: dict) -> str:
    rules = config.get("screening", {}) or {}
    parts = []
    if rules.get("min_score"):
        parts.append(f"- 综合分不得低于 {float(rules['min_score']):g}")
    if rules.get("min_years"):
        years = float(rules["min_years"])
        parts.append(f"- 工作年限不得少于 {years:g} 年")
    if rules.get("required_degree"):
        parts.append(f"- 最低学历要求：{rules['required_degree']}")
    must = [k for k in (rules.get("must_keywords") or []) if str(k).strip()]
    if must:
        parts.append("- 必须命中以下关键词：" + "、".join(str(k) for k in must))
    reject = [k for k in (rules.get("reject_keywords") or []) if str(k).strip()]
    if reject:
        parts.append("- 出现以下任一关键词即淘汰：" + "、".join(str(k) for k in reject))

    if not parts:
        return "无额外硬性规则，按岗位匹配度正常评估。"
    return "\n".join(parts)


def build_prompt(resume_text: str, config: dict) -> str:
    rarity = config.get("rarity", {})
    return USER_PROMPT.format(
        role_block=_role_block(config),
        screening_block=_screening_block(config),
        ssr=rarity.get("ssr", 82),
        sr=rarity.get("sr", 66),
        resume=resume_text[:MAX_RESUME_CHARS],
    )


# ---------------------------------------------------------------- 调用

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
            out.append(text[:160])
        if len(out) >= limit:
            break
    return out


def _endpoint(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if not base.endswith("/v1"):
        base += "/v1"
    return base + "/chat/completions"


def _post(config: dict, prompt: str) -> dict:
    llm = config["llm"]
    payload = {
        "model": llm["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": llm.get("temperature", 0.2),
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {llm['api_key']}",
        "Content-Type": "application/json",
    }
    url = _endpoint(llm["base_url"])

    with httpx.Client(timeout=float(llm.get("timeout", 90))) as client:
        response = client.post(url, headers=headers, json=payload)
        if response.status_code == 400:
            # 部分兼容服务不支持 response_format
            payload.pop("response_format", None)
            response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    try:
        return {"content": data["choices"][0]["message"]["content"]}
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("AI 接口返回结构异常") from exc


def _normalize_result(data: dict, config: dict) -> dict:
    """把模型输出整理为内部统一结构。"""
    dims_raw = data.get("dimensions") if isinstance(data.get("dimensions"), dict) else {}

    dimensions = []
    for key in DIMENSION_KEYS:
        entry = dims_raw.get(key) if isinstance(dims_raw.get(key), dict) else {}
        reason = str(entry.get("reason") or "").strip()
        dimensions.append({
            "key": key,
            "label": DIMENSION_LABELS[key],
            "score": round(_clamp(entry.get("score")), 1),
            "weight": 0.0,
            "contribution": 0.0,
            "detail": reason[:400] or "AI 未说明理由",
            "hits": [],
            "available": True,
        })

    overall = _clamp(data.get("overall_score"))
    if overall <= 0:
        # 模型没给综合分时，用维度均分兜底
        overall = sum(d["score"] for d in dimensions) / max(1, len(dimensions))
    overall = round(overall, 1)

    # 等级一律按本地阈值换算，避免模型自行乱填
    rarity = config_module.rarity_of(overall, config)

    status = str(data.get("status") or "").strip().lower()
    if status not in {"pass", "flagged", "rejected"}:
        status = "pass"

    strengths = _clean_list(data.get("strengths"), 6)
    weaknesses = _clean_list(data.get("weaknesses"), 5)
    summary = str(data.get("summary") or "").strip()[:160]

    if not strengths:
        strengths = ["AI 未给出明确优势"]
    if not weaknesses:
        weaknesses = ["AI 未给出明确不足"]

    return {
        "overall_score": overall,
        "rarity": rarity,
        "rarity_label": config["rarity"]["labels"].get(rarity, rarity),
        "status": status,
        "screen_reasons": _clean_list(data.get("screen_reasons"), 6),
        "dimensions": dimensions,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "summary": summary,
        "news_flash": summary or (strengths[0] if strengths else "已完成 AI 评估"),
        "score_source": "ai",
        "ai_model": config["llm"].get("model", ""),
    }


def evaluate(resume_text: str, config: dict) -> dict:
    """调用 AI 评估一份简历。未配置或失败时抛异常。"""
    if not is_ready(config):
        raise RuntimeError(readiness_error(config))

    prompt = build_prompt(resume_text, config)
    payload = _post(config, prompt)

    parsed = _extract_json(payload["content"])
    if parsed is None:
        # 重试一次（有些模型首次会带解释性文字）
        payload = _post(config, prompt)
        parsed = _extract_json(payload["content"])
    if parsed is None:
        raise RuntimeError("AI 返回内容不是有效 JSON")

    return _normalize_result(parsed, config)


def test_connection(config: dict) -> dict:
    """用一条极短请求验证 Key 与地址可用。"""
    if not is_ready(config):
        raise RuntimeError(readiness_error(config))
    result = evaluate(
        "姓名：测试\n教育背景\n2015.09-2019.06 某大学 计算机 本科\n"
        "工作经历\n2019.07-至今 某公司 开发工程师\n技能：Python、MySQL",
        config,
    )
    return result
