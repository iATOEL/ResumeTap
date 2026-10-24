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
    "你是一位资深招聘专家，按岗位要求评估简历。"
    "判断必须基于简历中的事实，不得编造；信息不足时在理由中说明依据不足。"
    "全部用简体中文，只输出一个 JSON 对象，不要 markdown 代码块，不要任何解释文字。"
)

USER_PROMPT = """评估下面这份简历与目标岗位的匹配程度，只输出 JSON。

## 目标岗位
{role_block}

## 筛选规则（不满足时把 status 设为 rejected，并在 screen_reasons 说明）
{screening_block}

## 评分标尺（各维度与综合分共用，请按此拉开区分度）
- 90 以上：显著超出岗位要求，同岗位候选人中前 5%
- 80–89：明显超出要求，有可验证的亮点
- 70–79：基本满足要求，无明显短板也无突出亮点
- 60–69：部分满足，存在明显缺口
- 60 以下：不满足要求

## 评分维度（各 0–100 分）
- skill：技术栈/专业技能与岗位要求的匹配度
- experience：工作年限、行业相关性、职级成长
- education：学历层次与院校水平
- project：项目数量、复杂度、成果量化程度
- job_match：综合来看与目标岗位的整体契合度

## 输出格式（只返回这个 JSON，不要代码块包裹）
{{
  "name": "候选人姓名",
  "overall_score": 0-100 的综合分（数字，保留一位小数，与各维度分数一致）,
  "status": "pass | flagged | rejected",
  "dimensions": {{
    "skill": {{"score": 0-100, "reason": "判断依据"}},
    "experience": {{"score": 0-100, "reason": "判断依据"}},
    "education": {{"score": 0-100, "reason": "判断依据"}},
    "project": {{"score": 0-100, "reason": "判断依据"}},
    "job_match": {{"score": 0-100, "reason": "判断依据"}}
  }},
  "strengths": ["优势1", "优势2"],
  "weaknesses": ["不足1", "不足2"],
  "summary": "一句话结论",
  "screen_reasons": ["若 status 非 pass，列出不满足的具体规则"]
}}

## 表达要求
- reason 必须引用简历里的具体事实：年限、技术栈、项目规模、量化成果（如 QPS、百分比）。写不出事实就写"简历未体现"并据此压分。
- strengths 2–4 条，每条不超过 40 字，格式"强在哪 + 依据"。
- weaknesses 1–3 条，每条不超过 40 字，格式"缺什么 + 对这个岗位的影响"。
- 禁止套话：不要出现"未发现明显短板""综合素质良好""有待进一步了解"这类没有信息量的话。确实没有明显短板时，写最值得在面试中确认的一个疑点。
- summary 不超过 30 字，直接给出行动建议（如"建议优先推进""谨慎考虑""不推荐"）。
- 一批简历的分数要有区分度：多数简历不该落在 70–80 之间。

## 简历内容
{resume}"""

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


def _extra_block(config: dict) -> str:
    """用户补充的关键词与 JD：写进提示词，作为岗位画像之外的额外要求。"""
    lines: list[str] = []
    keywords = [str(k).strip() for k in (config.get("keywords") or []) if str(k).strip()]
    if keywords:
        lines.append("额外关注关键词：" + "、".join(keywords[:40]))
    jd = str(config.get("jd_text") or "").strip()
    if jd:
        lines.append("岗位 JD（节选，供参考）：\n" + jd[:2000])
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
    role_block = "\n".join(part for part in (_role_block(config), _extra_block(config)) if part)
    return USER_PROMPT.format(
        role_block=role_block,
        screening_block=_screening_block(config),
        resume=resume_text[:MAX_RESUME_CHARS],
    )


# ---------------------------------------------------------------- 调用

def _iter_json_objects(text: str):
    """按括号配对逐个取出完整的顶层 {...} 对象（跳过字符串里的花括号）。"""
    depth, start, in_string, escape = 0, -1, False, False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                yield text[start:index + 1]
                start = -1


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    # 模型偶尔在 JSON 前后加说明文字、甚至输出两个对象：
    # 逐个尝试括号配对的完整对象，取第一个能解析成功的
    for candidate in _iter_json_objects(text):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
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
        if response.status_code in (400, 422):
            # 部分兼容服务不支持 response_format，去掉后重试一次
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
