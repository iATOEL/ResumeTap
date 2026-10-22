"""评分引擎：六维打分、稀有度映射、筛选判定与可复核理由。"""

import math
import re
from dataclasses import dataclass, field

from . import extractor
from .config import rarity_of
from .rules import DEGREE_SCORES, ROLE_PROFILES, STOPWORDS

DIMENSION_LABELS = {
    "skill": "技能匹配",
    "experience": "经验年限",
    "education": "教育背景",
    "project": "项目经历",
    "keyword": "关键词命中",
    "job_match": "岗位匹配",
}

DEGREE_RANK = {"其他": 0, "中专/高中": 1, "大专": 2, "本科": 3, "硕士": 4, "博士": 5}

JD_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,20}|[\u4e00-\u9fa5]{2,8}")


@dataclass
class Dimension:
    key: str
    score: float
    weight: float
    detail: str
    hits: list[str] = field(default_factory=list)
    available: bool = True

    @property
    def label(self) -> str:
        return DIMENSION_LABELS.get(self.key, self.key)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "score": round(self.score, 1),
            "weight": round(self.weight, 4),
            "contribution": round(self.score * self.weight, 2),
            "detail": self.detail,
            "hits": self.hits[:20],
            "available": self.available,
        }


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def extract_jd_terms(jd_text: str, limit: int = 40) -> list[str]:
    """从 JD 原文提取候选关键词（去停用词、去重、保留频次高的）。"""
    if not jd_text.strip():
        return []
    counts: dict[str, int] = {}
    for token in JD_TOKEN_RE.findall(jd_text):
        token = token.strip()
        lowered = token.lower()
        if len(token) < 2 or lowered in STOPWORDS:
            continue
        if token.isdigit():
            continue
        counts[token] = counts.get(token, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], -len(item[0])))
    return [term for term, _ in ordered[:limit]]


def _keyword_terms(config: dict) -> tuple[list[str], str]:
    custom = [k for k in config.get("keywords", []) if k]
    if custom:
        return custom, "自定义关键词"
    jd_terms = extract_jd_terms(config.get("jd_text", ""))
    if jd_terms:
        return jd_terms, "JD 自动提取"
    return [], ""


def detect_role(ext: extractor.Extracted) -> tuple[str, float]:
    """按技能命中加权覆盖率推断最匹配的岗位画像。"""
    haystack = "\n".join([
        ext.skill_text, ext.experience_text, ext.project_text,
        ext.summary_text, ext.education_text, " ".join(ext.skill_names()),
    ])
    best_key, best_ratio = "general", 0.0
    for key, profile in ROLE_PROFILES.items():
        if key == "general":
            continue
        keywords: dict[str, float] = profile["keywords"]
        total = sum(keywords.values()) or 1.0
        hit = 0.0
        for term, weight in keywords.items():
            if extractor.match_terms(haystack, [term]):
                hit += weight
        ratio = hit / total
        if ratio > best_ratio:
            best_key, best_ratio = key, ratio
    if best_ratio < 0.10:
        return "general", best_ratio
    return best_key, best_ratio


def resolve_role(ext: extractor.Extracted, config: dict) -> str:
    """返回本次评分使用的岗位画像 key。

    role 为空表示用户尚未选择岗位；auto 表示按简历内容推断；
    custom 表示使用用户在设置里自定义的岗位关键词。
    """
    role = str(config.get("role") or "").strip()
    if role == "custom":
        return "custom"
    if role in {"auto", ""}:
        detected, _ = detect_role(ext)
        return detected
    return role if role in ROLE_PROFILES else "general"


def role_label(role_key: str, config: dict) -> str:
    """岗位展示名。"""
    if role_key == "custom":
        label = str((config.get("custom_role") or {}).get("label") or "").strip()
        return label or "自定义岗位"
    return ROLE_PROFILES.get(role_key, {}).get("label", "未指定")


def role_keywords(role_key: str, config: dict) -> dict[str, float]:
    """岗位关键词权重表。自定义岗位按用户填的词派生权重。"""
    if role_key == "custom":
        words = [w for w in (config.get("custom_role") or {}).get("keywords") or [] if w]
        # 用户没写权重，统一给 2.5；越靠前越重要
        return {word: max(1.5, 3.0 - index * 0.15) for index, word in enumerate(words[:40])}
    return ROLE_PROFILES.get(role_key, ROLE_PROFILES["general"])["keywords"]


def role_is_chosen(config: dict) -> bool:
    role = str(config.get("role") or "").strip()
    if role == "custom":
        return bool((config.get("custom_role") or {}).get("keywords"))
    return bool(role) and (role in ROLE_PROFILES or role == "auto")


def _ratio_score(hit_weight: float, total_weight: float, gain: float = 1.25) -> float:
    if total_weight <= 0:
        return 0.0
    ratio = hit_weight / total_weight
    return _clamp(ratio * 100 * gain)


def score_skill(ext: extractor.Extracted, role_key: str, config: dict) -> Dimension:
    skills = ext.skills
    distinct = len(skills)
    if distinct == 0:
        # 没识别到技能不等于没有能力（可能是表述方式不同），按缺失处理
        dim = Dimension("skill", 0.0, 0.0, "简历中未识别到技能词典内的技术栈，该维度不计入综合分")
        dim.available = False
        return dim

    coverage = 100 * (1 - math.exp(-distinct / 6.0))
    categories = {hit.category for hit in skills.values()}
    breadth = min(8.0, max(0, len(categories) - 2) * 1.6)

    role_terms = {term.lower() for term in role_keywords(role_key, config)}
    core_hits = [name for name, hit in skills.items() if name.lower() in role_terms]
    core_bonus = min(10.0, len(core_hits) * 1.8)

    score = _clamp(coverage + breadth + core_bonus)
    detail = f"识别到 {distinct} 项技能，覆盖 {len(categories)} 个类别"
    if core_hits:
        detail += f"，其中 {len(core_hits)} 项与目标岗位直接相关"

    # 展示时把岗位核心技能排在前面，便于人工快速核对
    def sort_key(hit: extractor.Hit) -> tuple:
        return (0 if hit.term.lower() in role_terms else 1, -hit.count, hit.term)

    hits = [f"{hit.term}（{hit.category}·{hit.count}次）"
            for hit in sorted(skills.values(), key=sort_key)][:20]
    return Dimension("skill", score, 0.0, detail, hits)


def score_experience(ext: extractor.Extracted, cfg: dict) -> Dimension:
    params = cfg["scoring"]["experience"]
    years = max(0.0, min(ext.years, params["max_years"]))
    base = params["base"]
    divisor = max(0.5, params["curve_divisor"])
    curve = base + (100 - base) * (1 - math.exp(-years / divisor))
    bonus = 0.0
    notes = []
    if ext.seniority:
        bonus += params["seniority_bonus"]
        notes.append("含资深/高级/架构师头衔")
    if ext.management:
        bonus += params["management_bonus"]
        notes.append("含团队管理职责")
    score = _clamp(curve + bonus)

    if years <= 0:
        detail = f"未识别到工作年限（{ext.years_source}），按应届/无经验估算"
    else:
        detail = f"识别工作年限 {years:.1f} 年（{ext.years_source}）"
    if notes:
        detail += "；" + "、".join(notes)
    hits = [f"{years:.1f} 年经验"] if years else ["未识别年限"]
    hits += notes
    return Dimension("experience", score, 0.0, detail, hits)


def score_education(ext: extractor.Extracted, cfg: dict) -> Dimension:
    """学历分 = 学历层次基准分 + 院校层级加分（C9/985/211/双一流/海外/普通/专科）。"""
    params = cfg["scoring"]["education"]
    degree = ext.degree if ext.degree in DEGREE_SCORES else "其他"
    base = DEGREE_SCORES[degree]

    tier_bonus_table = {
        "c9": params.get("c9_bonus", 26.0),
        "c985": params.get("c985_bonus", 20.0),
        "overseas_top": params.get("overseas_top_bonus", 20.0),
        "c211": params.get("c211_bonus", 15.0),
        "double_first": params.get("double_first_bonus", 12.0),
        "overseas_known": params.get("overseas_known_bonus", 12.0),
        "ordinary": params.get("ordinary_bonus", 4.0),
        "college": 0.0,
        "unknown": 0.0,
    }
    bonus = tier_bonus_table.get(ext.school_tier, 0.0) if ext.school else 0.0

    notes = [f"学历：{degree}"]
    if ext.school:
        tier_text = f"{ext.school}（{ext.school_tier_label}）"
        notes.append(f"院校：{tier_text}")
        if bonus > 0:
            notes.append(f"院校层级加分 +{bonus:.0f}")
    else:
        notes.append("院校未识别，不计院校加分")

    # 简历自称 985/211 但院校库未收录时给小额补偿，避免遗漏
    if ext.school and ext.school_tier in {"ordinary", "unknown"} and re.search(
        r"985|211|双一流", ext.education_text or ""
    ):
        bonus += params.get("project_985_211_bonus", 6.0)
        notes.append("简历标注 985/211，补加分")

    if ext.major:
        notes.append(f"专业：{ext.major}")

    score = _clamp(base + bonus)
    return Dimension("education", score, 0.0, "；".join(notes), notes)


def score_project(ext: extractor.Extracted, cfg: dict) -> Dimension:
    """项目分：数量给基础盘，主导性动词与量化成果决定上限。

    一份简历没写"项目经历"章节，不等于候选人没做过项目；
    这类情况按缺失处理，给中性分并标记为不可用，而不是判 0 分。
    """
    params = cfg["scoring"]["project"]
    metrics = extractor.project_metrics(ext.projects)
    count = metrics["count"]

    if count == 0:
        dim = Dimension("project", 0.0, 0.0, "简历未提供项目经历章节，该维度不计入综合分")
        dim.available = False
        return dim

    table = params["count_scores"]
    base = table[min(count, len(table) - 1)] if table else 0.0

    verb_bonus = min(params["verb_bonus_max"], len(metrics["verb_hits"]) * params["verb_bonus_per_item"])
    metric_bonus = min(params["metric_bonus_max"], metrics["metric_count"] * params["metric_bonus_per_item"])
    scale_bonus = params["scale_bonus"] if metrics["scale_hits"] else 0.0
    score = _clamp(base + verb_bonus + metric_bonus + scale_bonus)

    # 量化成果是高质量简历的关键信号：单项目但要写得实在，也应拿到合格分
    if metrics["metric_count"] >= 2 and count >= 1:
        score = _clamp(max(score, 58.0 + min(14.0, metrics["metric_count"] * 2.5)))
    if metrics["scale_hits"] and count >= 1:
        score = _clamp(max(score, 62.0))

    detail = f"识别项目 {count} 个"
    if metrics["verb_hits"]:
        detail += f"，主导性动词 {len(metrics['verb_hits'])} 个"
    if metrics["metric_count"]:
        detail += f"，量化成果 {metrics['metric_count']} 处"
    if metrics["scale_hits"]:
        detail += "，含大规模/高并发描述"
    hits = [p.title for p in ext.projects[:8]]
    if metrics["verb_hits"]:
        hits.append("动词：" + "、".join(metrics["verb_hits"][:6]))
    if metrics["scale_hits"]:
        hits.append("规模：" + "、".join(metrics["scale_hits"][:4]))
    return Dimension("project", score, 0.0, detail, hits)


def score_keywords(ext: extractor.Extracted, config: dict) -> Dimension:
    terms, source = _keyword_terms(config)
    if not terms:
        dim = Dimension("keyword", 0.0, 0.0,
                        "未配置自定义关键词与 JD，该维度不计入综合分")
        dim.available = False
        return dim
    haystack = "\n".join([ext.skill_text, ext.experience_text, ext.project_text,
                          ext.summary_text]) or " ".join(ext.skill_names())
    hits = extractor.match_terms(haystack, terms)
    if not hits:
        return Dimension("keyword", 0.0, 0.0, f"{source} 命中 0/{len(terms)} 个",
                         [f"未命中：{t}" for t in terms[:10]])

    matched = len(hits)
    ratio = matched / max(1, len(terms))
    freq_bonus = min(12.0, sum(min(hit.count, 4) for hit in hits.values()) * 0.6)
    score = _clamp(ratio ** 0.75 * 100 * 0.85 + freq_bonus)

    detail = f"{source} 命中 {matched}/{len(terms)} 个"
    hits_list = [f"{hit.term}（{hit.count}次）" for hit in
                 sorted(hits.values(), key=lambda h: -h.count)][:20]
    return Dimension("keyword", score, 0.0, detail, hits_list)


def score_job_match(ext: extractor.Extracted, config: dict, role_key: str) -> Dimension:
    keywords: dict[str, float] = role_keywords(role_key, config)
    label = role_label(role_key, config)

    haystack = "\n".join([ext.skill_text, ext.experience_text, ext.project_text,
                          ext.summary_text, ext.education_text]) or ""
    if not haystack.strip():
        haystack = " ".join(ext.skill_names())

    hit_weight = 0.0
    total_weight = 0.0
    hits: list[str] = []
    for term, weight in keywords.items():
        total_weight += weight
        if extractor.match_terms(haystack, [term]):
            hit_weight += weight
            hits.append(f"{term}（权重{weight}）")

    score = _ratio_score(hit_weight, total_weight, gain=0.92)
    mode = "自动识别" if config.get("role") in {"auto", ""} else "指定"
    detail = f"{mode}岗位「{label}」，核心要求命中 {len(hits)}/{len(keywords)} 项"
    return Dimension("job_match", score, 0.0, detail, hits)


def screen(ext: extractor.Extracted, overall: float, config: dict) -> tuple[str, list[str]]:
    """返回 (状态, 命中/未命中说明)。状态：pass / flagged / rejected。"""
    rules = config.get("screening", {})
    reasons: list[str] = []
    rejected = False

    for term in rules.get("reject_keywords", []):
        if extractor.match_terms(
            "\n".join([ext.experience_text, ext.project_text, ext.summary_text, ext.skill_text]),
            [term],
        ):
            reasons.append(f"命中淘汰词「{term}」")
            rejected = True

    for term in rules.get("must_keywords", []):
        if not extractor.match_terms(
            "\n".join([ext.skill_text, ext.experience_text, ext.project_text,
                       ext.summary_text, ext.awards_text]),
            [term],
        ):
            reasons.append(f"缺少必备关键词「{term}」")
            rejected = True

    min_score = float(rules.get("min_score") or 0)
    if min_score > 0 and overall < min_score:
        reasons.append(f"综合分 {overall:.1f} 低于门槛 {min_score:.1f}")
        rejected = True

    min_years = float(rules.get("min_years") or 0)
    if min_years > 0 and ext.years + 1e-9 < min_years:
        reasons.append(f"经验 {ext.years:.1f} 年低于门槛 {min_years:.1f} 年")
        rejected = True

    required = rules.get("required_degree") or ""
    if required and DEGREE_RANK.get(ext.degree, 0) < DEGREE_RANK.get(required, 0):
        reasons.append(f"学历 {ext.degree} 低于要求 {required}")
        rejected = True

    if rejected:
        return "rejected", reasons
    if reasons:
        return "flagged", reasons
    return "pass", reasons


def evaluate(text: str, filename: str, config: dict) -> dict:
    """完整评估：抽取 -> 六维打分 -> 综合分 -> 稀有度 -> 筛选。"""
    ext = extractor.extract(text, filename)
    weights = config["scoring"]["weights"]
    role_key = resolve_role(ext, config)

    dimensions = [
        score_skill(ext, role_key, config),
        score_experience(ext, config),
        score_education(ext, config),
        score_project(ext, config),
        score_keywords(ext, config),
        score_job_match(ext, config, role_key),
    ]
    for dim in dimensions:
        configured = float(weights.get(dim.key, 0.0))
        dim.weight = configured if dim.available else 0.0

    # 早期候选人（经验很少）：压低经验权重、抬高学历权重，
    # 避免"没经验"把所有人都压成同一档，让院校差距体现出来
    early = config["scoring"].get("early_career", {})
    early_applied = False
    if early.get("enabled", True) and ext.years < float(early.get("junior_years", 2.0)):
        edu_mult = float(early.get("education_multiplier", 1.75))
        exp_mult = float(early.get("experience_multiplier", 0.45))
        for dim in dimensions:
            if not dim.available:
                continue
            if dim.key == "education":
                dim.weight *= edu_mult
            elif dim.key == "experience":
                dim.weight *= exp_mult
        early_applied = True

    # 只对实际参与计算的维度归一化，保证综合分仍然落在 0-100
    weight_sum = sum(dim.weight for dim in dimensions)
    if weight_sum <= 0:
        fallback = 1.0 / len(dimensions)
        for dim in dimensions:
            dim.weight = 0.0 if not dim.available else fallback
        weight_sum = sum(dim.weight for dim in dimensions) or 1.0
    for dim in dimensions:
        dim.weight = round(dim.weight / weight_sum, 4)

    overall = _clamp(sum(dim.score * dim.weight for dim in dimensions))
    overall = round(overall, 1)
    rarity = rarity_of(overall, config)
    status, screen_reasons = screen(ext, overall, config)

    summary = _build_summary(ext, overall, rarity, dimensions)
    strengths, weaknesses = _build_verdicts(ext, dimensions, role_key, overall, config)
    return {
        "name": ext.name,
        "filename": filename,
        "overall_score": overall,
        "rarity": rarity,
        "status": status,
        "screen_reasons": screen_reasons,
        "summary": summary,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "news_flash": _build_news_flash(ext, overall, rarity, strengths, weaknesses),
        "detected_role": role_key,
        "detected_role_label": role_label(role_key, config),
        "role_selected": role_is_chosen(config),
        "early_career_adjusted": early_applied,
        "dimensions": [dim.to_dict() for dim in dimensions],
        "profile": ext.to_summary_dict(),
        "sections": {
            "education": ext.education_text,
            "experience": ext.experience_text,
            "projects": ext.project_text,
            "skills": ext.skill_text,
            "summary": ext.summary_text,
            "awards": ext.awards_text,
        },
        "projects": [{"title": p.title, "description": p.description} for p in ext.projects[:10]],
        "skill_hits": [hit.to_dict() for hit in
                       sorted(ext.skills.values(), key=lambda h: (-h.count, h.term))],
        "years": ext.years,
        "years_source": ext.years_source,
        "degree": ext.degree,
        "school": ext.school,
        "school_tier": ext.school_tier,
        "school_tier_label": ext.school_tier_label,
        "major": ext.major,
        "contact": {"phone": ext.phone, "email": ext.email},
        "role_title": ext.role_title,
        "rarity_label": config["rarity"]["labels"].get(rarity, rarity),
        "text_stats": {
            "chars": len(text),
            "projects": len(ext.projects),
            "skills": len(ext.skills),
        },
    }


def _build_verdicts(ext: extractor.Extracted, dims: list[Dimension],
                    role_key: str, overall: float,
                    config: dict) -> tuple[list[str], list[str]]:
    """生成可读的优点 / 缺点，供速筛卡片展示。"""
    strengths: list[str] = []
    weaknesses: list[str] = []
    by_key = {d.key: d for d in dims}

    skill = by_key.get("skill")
    experience = by_key.get("experience")
    education = by_key.get("education")
    project = by_key.get("project")
    job = by_key.get("job_match")

    # ---- 优点 ----
    if skill and skill.score >= 70 and ext.skills:
        top = [hit.term for hit in sorted(ext.skills.values(), key=lambda h: -h.count)[:4]]
        strengths.append(f"技能栈扎实，命中 {len(ext.skills)} 项（{'、'.join(top)}）")
    if experience and ext.years >= 5:
        strengths.append(f"{ext.years:.1f} 年相关经验，积累较深")
    elif experience and 2 <= ext.years < 5:
        strengths.append(f"{ext.years:.1f} 年经验，处于成长阶段")
    if education and education.score >= 85:
        tier_text = f"{ext.school}（{ext.school_tier_label}）" if ext.school else ""
        strengths.append(f"教育背景突出：{ext.degree}{(' · ' + tier_text) if tier_text else ''}")
    if project and project.score >= 65:
        strengths.append("项目描述含量化成果，能体现实际贡献")
    elif project and project.score >= 52:
        strengths.append(f"有 {len(ext.projects)} 段项目经历可追述")
    if job and job.score >= 70:
        label = role_label(role_key, config)
        strengths.append(f"与「{label}」岗位要求高度吻合")
    if ext.management:
        strengths.append("简历体现团队管理职责")
    if not strengths:
        strengths.append("基础信息完整，可进入人工复核")

    # ---- 缺点 ----
    if ext.years < 1:
        weaknesses.append("几乎无全职经验，需按应届标准评估")
    elif ext.years < 3:
        weaknesses.append(f"经验偏少（{ext.years:.1f} 年）")
    if education and education.score < 60:
        weaknesses.append(f"学历/院校一般：{ext.degree}" + (f" · {ext.school}" if ext.school else ""))
    if skill and skill.score < 45:
        weaknesses.append("技能栈较窄或与目标岗位重合度低")
    if project and project.score < 45:
        weaknesses.append("项目经历缺少细节或量化结果")
    if job and job.score < 45:
        weaknesses.append("与当前目标岗位匹配度偏低")
    if not ext.projects:
        weaknesses.append("未识别到独立项目经历")
    if not ext.skills:
        weaknesses.append("未识别到明确技术栈")
    if not ext.school:
        weaknesses.append("院校信息未识别，教育背景需人工确认")
    if not weaknesses:
        weaknesses.append("未发现明显短板，建议面试进一步确认")
    return strengths[:5], weaknesses[:4]


def _build_news_flash(ext: extractor.Extracted, overall: float, rarity: str,
                      strengths: list[str], weaknesses: list[str]) -> str:
    """一句话结论，用在速筛页面顶部。"""
    if overall >= 82:
        lead = "整体竞争力强，建议优先沟通"
    elif overall >= 66:
        lead = "基本符合要求，值得进一步了解"
    elif overall >= 50:
        lead = "存在明显短板，可作备选"
    else:
        lead = "与当前要求差距较大"
    detail = strengths[0] if strengths else ""
    if detail:
        return f"{lead}。{detail}。"
    return f"{lead}。"


def _build_summary(ext: extractor.Extracted, overall: float, rarity: str, dims: list[Dimension]) -> str:
    parts: list[str] = []
    if ext.role_title:
        parts.append(f"{ext.role_title}")
    elif ext.school or ext.degree != "其他":
        parts.append(f"{ext.degree}{('·' + ext.school) if ext.school else ''}")
    if ext.years > 0:
        parts.append(f"{ext.years:.1f} 年经验")
    skills = ext.skill_names()
    if skills:
        parts.append("技能 " + "、".join(skills[:5]) + ("等" if len(skills) > 5 else ""))
    if ext.projects:
        parts.append(f"{len(ext.projects)} 个项目")

    top = sorted(dims, key=lambda d: -d.score * d.weight)[:2]
    if top:
        # 只把真正参与评分的维度视为短板，避免未启用的关键词维度被误标
        scored = [d for d in dims if d.available and d.weight > 0]
        parts.append(f"优势：{top[0].label}")
        if scored:
            weak = min(scored, key=lambda d: d.score)
            if weak.score < 45:
                parts.append(f"短板：{weak.label}")
    return "｜".join(parts) if parts else f"综合评分 {overall:.1f}（{rarity}）"
