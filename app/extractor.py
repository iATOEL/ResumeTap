"""结构化抽取：把简历纯文本解析为可评分的字段。"""

import re
from dataclasses import dataclass, field
from datetime import date

from .rules import (
    PROJECT_VERBS,
    SECTION_ALIASES,
    SKILL_CATALOG,
)
from .schools import find_school

DEGREE_LEVELS = {
    "博士": 5,
    "硕士": 4,
    "本科": 3,
    "大专": 2,
    "中专/高中": 1,
    "其他": 0,
}

# 学历识别：按长度优先，避免 "本科" 误命中 "本科及以上" 要求句
_DEGREE_PATTERNS = [
    (re.compile(r"博士(?:研究生|后)?(?:学历|学位)?"), "博士"),
    (re.compile(r"(?:硕士|研究生)(?:学历|学位)?"), "硕士"),
    (re.compile(r"(?:本科|学士)(?:学历|学位)?"), "本科"),
    (re.compile(r"(?:大专|专科|高职)(?:学历)?"), "大专"),
    (re.compile(r"(?:中专|中职|技校|高中)(?:学历)?"), "中专/高中"),
]

# 行内出现这些词，说明该行是"招聘要求"而不是候选人学历
_REQUIREMENT_HINT_RE = re.compile(
    r"要求|门槛|及以上|以上学历|最低|优先|招聘|岗位|职位|任职|期望|目标|应聘条件|须具备",
)

# 候选人自身学历的强信号
_SELF_HINT_RE = re.compile(r"毕业|就读|在读|统招|全日制|专升本|学位|学历|专业")

# 日期行（教育经历里的时间行）
_DATE_ONLY_RE = re.compile(r"^\s*(19|20)\d{2}\s*[年./\-]")

# 公司/机构名干扰
_ORG_HINT_RE = re.compile(r"公司|集团|有限|银行|医院|研究所|培训|事业部|中心")

DATE_RE = re.compile(
    r"(19|20)\d{2}\s*[年./\-]\s*(0?[1-9]|1[0-2])?\s*月?"
)
RANGE_RE = re.compile(
    r"(?P<start>(?:19|20)\d{2}\s*[年./\-]\s*(?:0?[1-9]|1[0-2])?\s*月?)"
    r"\s*(?:-|–|—|~|至|到|－|to)\s*"
    r"(?P<end>(?:19|20)\d{2}\s*[年./\-]\s*(?:0?[1-9]|1[0-2])?\s*月?|至今|今|现在|present|now)",
    re.IGNORECASE,
)
PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
PHONE_LOOSE_RE = re.compile(r"(?<!\d)(1[3-9]\d[\s\-]?\d{4}[\s\-]?\d{4})(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
YEARS_RE = re.compile(
    r"(\d{1,2}(?:\.\d)?)\s*(?:\+)?\s*年(?:以上)?(?:的)?\s*"
    r"(?:工作|开发|研发|从业|相关|行业|项目|产品|测试|运营|算法|后端|前端|全栈|技术|管理|测试|数据)?\s*经验"
)
HEADER_NAME_RE = re.compile(r"^(?:姓\s*名|名字|Name)\s*[:：]\s*([^\s|，,（(]{2,20})", re.IGNORECASE)
GENDER_RE = re.compile(r"(?:性\s*别\s*[:：]\s*)?(男|女)(?![性生])")
AGE_RE = re.compile(r"(?:年\s*龄\s*[:：]\s*)?(\d{2})\s*岁")
GRAD_RE = re.compile(r"(19|20)\d{2}\s*[年./\-]?\s*(?:0?[1-9]|1[0-2])?\s*月?\s*(?:毕业|届)")

SENIOR_TITLES = ["架构师", "技术专家", "资深", "高级", "senior", "staff", "principal", "lead", "负责人", "主管"]
MANAGEMENT_TITLES = ["技术经理", "项目经理", "团队负责人", "研发经理", "总监", "cto", "leader", "组长", "team lead"]

METRIC_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(%|％|万|亿|w|k|qps|tps|ms|人|倍|条|次/秒)", re.IGNORECASE
)
SCALE_WORDS = ["千万级", "百万级", "十万级", "亿级", "高并发", "大规模", "海量", "亿级流量"]

CATEGORY_LABELS = {name: cat for cat, entries in SKILL_CATALOG.items() for name in entries}

# 别名 -> 规范名（长别名优先，避免 "c" 命中 "c++" 之类的误判）
ALIAS_MAP: dict[str, str] = {}
for _category, _items in SKILL_CATALOG.items():
    for _canonical, _aliases in _items.items():
        for _alias in _aliases:
            ALIAS_MAP.setdefault(_alias.lower(), _canonical)
        ALIAS_MAP.setdefault(_canonical.lower(), _canonical)


@dataclass
class Hit:
    """一次关键词命中。"""

    term: str
    category: str
    count: int
    contexts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "term": self.term,
            "category": self.category,
            "count": self.count,
            "contexts": self.contexts[:3],
        }


@dataclass
class ProjectItem:
    title: str
    description: str


@dataclass
class Extracted:
    name: str = "未知候选人"
    phone: str = ""
    email: str = ""
    years: float = 0.0
    years_source: str = ""
    degree: str = "其他"
    school: str = ""
    school_tier: str = "unknown"
    school_tier_label: str = "未识别"
    school_bonus: float = 0.0
    major: str = ""
    education_entries: list[dict] = field(default_factory=list)
    role_title: str = ""
    seniority: bool = False
    management: bool = False
    skills: dict[str, Hit] = field(default_factory=dict)          # 规范名 -> Hit
    extra_terms: dict[str, Hit] = field(default_factory=dict)     # 其他术语命中
    projects: list[ProjectItem] = field(default_factory=list)
    sections: dict[str, str] = field(default_factory=dict)
    education_text: str = ""
    experience_text: str = ""
    project_text: str = ""
    skill_text: str = ""
    summary_text: str = ""
    awards_text: str = ""

    def skill_names(self) -> list[str]:
        return sorted(self.skills.keys())

    def to_summary_dict(self) -> dict:
        return {
            "name": self.name,
            "phone_masked": mask_phone(self.phone),
            "email_masked": mask_email(self.email),
            "years": self.years,
            "degree": self.degree,
            "school": self.school,
            "school_tier": self.school_tier,
            "school_tier_label": self.school_tier_label,
            "major": self.major,
            "education_entries": self.education_entries,
            "role_title": self.role_title,
            "skills": self.skill_names(),
            "project_count": len(self.projects),
        }


def mask_phone(phone: str) -> str:
    if not phone or len(phone) < 7:
        return phone or ""
    return phone[:3] + "*" * (len(phone) - 7) + phone[-4:]


def mask_email(email: str) -> str:
    if not email or "@" not in email:
        return email or ""
    local, domain = email.split("@", 1)
    local = local[:1] + "***" if local else "***"
    return f"{local}@{domain}"


def _normalize_line(line: str) -> str:
    return re.sub(r"[\s\u3000]+", "", line)


def detect_sections(text: str) -> dict[str, str]:
    """按章节标题切分文本，返回 {section_key: content}。"""
    lines = text.split("\n")
    index_map: dict[int, str] = {}
    for i, line in enumerate(lines):
        normalized = _normalize_line(line).strip(":：|·•-—【】[]")
        if not normalized or len(normalized) > 16:
            continue
        for key, aliases in SECTION_ALIASES.items():
            if any(normalized == alias or normalized.startswith(alias) for alias in aliases):
                index_map[i] = key
                break

    if not index_map:
        return {}

    ordered = sorted(index_map.items())
    sections: dict[str, str] = {}
    for pos, (line_no, key) in enumerate(ordered):
        end = ordered[pos + 1][0] if pos + 1 < len(ordered) else len(lines)
        content = "\n".join(lines[line_no + 1:end]).strip()
        if content:
            sections[key] = (sections.get(key, "") + "\n" + content).strip()
    return sections


def _parse_date(token: str, end: bool = False) -> tuple[int, int] | None:
    token = token.strip()
    if not token:
        return None
    if re.search(r"至今|今|现在|present|now", token, re.IGNORECASE):
        today = date.today()
        return today.year, today.month
    match = re.search(r"((?:19|20)\d{2})\s*[年./\-]?\s*(\d{1,2})?", token)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2)) if match.group(2) else (12 if end else 1)
    if not 1 <= month <= 12:
        month = 12 if end else 1
    return year, month


def _to_month_index(value: tuple[int, int]) -> int:
    return value[0] * 12 + (value[1] - 1)


def _merge_interval_months(intervals: list[tuple[int, int, float]]) -> float:
    """按月合并重叠区间：同一时间只计一次，实习期按半权计。"""
    if not intervals:
        return 0.0
    origin = min(start for start, _, _ in intervals)
    span = max(end for _, end, _ in intervals) - origin
    weights = [0.0] * span
    for start, end, weight in intervals:
        for month in range(start - origin, end - origin):
            weights[month] = max(weights[month], weight)
    return float(sum(weights))


def extract_years(text: str, experience_text: str = "", education_text: str = "") -> tuple[float, str]:
    """优先解析显式年限，其次合并工作日期区间。

    计算区间时优先只看工作经历章节，避免把教育起止时间误算为工作经验。
    """
    explicit = [float(m.group(1)) for m in YEARS_RE.finditer(text)]
    explicit = [y for y in explicit if 0 < y <= 45]

    scope = experience_text.strip()
    if not scope:
        # 无工作章节时退化为全文，但剔除教育章节中的时间段
        scope = text
        if education_text.strip():
            scope = scope.replace(education_text, "\n")

    intervals: list[tuple[int, int]] = []
    for match in RANGE_RE.finditer(scope):
        start = _parse_date(match.group("start"))
        end = _parse_date(match.group("end"), end=True)
        if not start or not end:
            continue
        start_idx, end_idx = _to_month_index(start), _to_month_index(end)
        if end_idx <= start_idx:
            continue
        if end_idx - start_idx > 12 * 45:
            continue
        # 该行若标注为实习/兼职，按半权计入
        line = scope[max(0, match.start() - 10): match.end() + 12]
        weight = 0.5 if re.search(r"实习|兼职|intern|part[- ]time", line, re.IGNORECASE) else 1.0
        intervals.append((start_idx, end_idx, weight))

    computed = round(min(_merge_interval_months(intervals) / 12.0, 45.0), 1)

    if explicit and computed:
        return max(max(explicit), computed), "max(简历声明, 任职区间)"
    if explicit:
        return max(explicit), "简历文本声明"
    if computed:
        return computed, "任职日期区间推算"
    return 0.0, "未识别"


def _guess_name(text: str, filename: str = "") -> str:
    match = HEADER_NAME_RE.search(text[:2000])
    if match:
        return match.group(1).strip()

    for line in text.split("\n")[:12]:
        candidate = line.strip().strip("|·•-—【】[]")
        if not candidate or len(candidate) > 24:
            continue
        if any(ch in candidate for ch in "@:/\\0123456789"):
            continue
        if re.search(r"简历|个人|求职|应聘|resume|curriculum|vitae", candidate, re.IGNORECASE):
            continue
        if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", candidate):
            return candidate
        if re.fullmatch(r"[A-Z][a-z]+(?:\s[A-Z][a-z]+){1,3}", candidate):
            return candidate

    # 从文件名兜底：张三-高级Java工程师-简历.docx
    stem = re.sub(r"\.(pdf|docx|txt|md)$", "", filename, flags=re.IGNORECASE)
    for token in re.split(r"[-_—\s（(【\[]", stem):
        token = token.strip()
        if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", token) and not re.search(
            r"简历|应聘|求职|模板|resume", token, re.IGNORECASE
        ):
            return token
    return "未知候选人"


def _detect_degree(text: str) -> str:
    """从教育背景文本判断最高学历。

    策略：按行扫描，跳过"要求类"句子，优先采用含日期/院校/毕业等强信号的行；
    没有强信号时再退回全文统计，取最高层级。
    """
    if not text.strip():
        return "其他"

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    strong_hits: list[tuple[int, int, str]] = []   # (level, -score, degree)
    weak_hits: list[tuple[int, int, str]] = []

    for line in lines:
        if _REQUIREMENT_HINT_RE.search(line):
            continue
        for pattern, degree in _DEGREE_PATTERNS:
            if not pattern.search(line):
                continue
            level = DEGREE_LEVELS[degree]
            score = 0
            if _SELF_HINT_RE.search(line):
                score += 2
            if _DATE_ONLY_RE.search(line):
                score += 2
            if re.search(r"大学|学院|学校", line):
                score += 1
            item = (level, -score, degree)
            if score >= 2:
                strong_hits.append(item)
            else:
                weak_hits.append(item)
            break  # 一行只取最高层级的一次命中

    pool = strong_hits or weak_hits
    if not pool:
        return "其他"
    return max(pool, key=lambda item: (item[0], item[1]))[2]


def _parse_education_entries(text: str, limit: int = 6) -> list[dict]:
    """把教育背景按行拆成多段，提取每段的院校、学历、专业、时间。

    简历里常见两种写法：
      2013.09-2017.06  某某大学  计算机科学与技术  本科
      某某大学  本科  计算机专业  2013-2017
    """
    if not text.strip():
        return []

    entries: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or len(line) < 4:
            continue
        if _REQUIREMENT_HINT_RE.search(line):
            continue

        school, tier, bonus = find_school(line)
        tier_label = _tier_label(tier) if school else ""
        degree = "其他"
        for pattern, degree_name in _DEGREE_PATTERNS:
            if pattern.search(line):
                degree = degree_name
                break

        if not school and degree == "其他":
            continue

        # 时间范围（允许只有年份）
        period = ""
        range_match = RANGE_RE.search(line)
        if range_match:
            period = f"{range_match.group('start').strip()} - {range_match.group('end').strip()}"
        else:
            years = re.findall(r"(?:19|20)\d{2}", line)
            if len(years) >= 2:
                period = f"{years[0]} - {years[1]}"
            elif years:
                period = years[0]

        major = _detect_major(line)
        entries.append({
            "school": school,
            "tier": tier,
            "tier_label": tier_label if school else "",
            "degree": degree,
            "major": major,
            "period": period,
            "raw": line[:120],
        })

    # 去掉完全重复的行
    seen = set()
    unique = []
    for entry in entries:
        key = (entry["school"], entry["degree"], entry["period"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return unique[:limit]


def _detect_school(text: str) -> tuple[str, str, str, float]:
    """返回 (院校名, 层级key, 层级标签, 加分)。"""
    if not text.strip():
        return "", "unknown", "未识别", 0.0

    best = ("", "unknown", "未识别", 0.0, 99)
    for line in text.split("\n"):
        line = line.strip()
        if not line or _REQUIREMENT_HINT_RE.search(line):
            continue
        # 公司名里常带"学院/大学"字样，交给层级库判定，未命中则跳过
        name, tier, bonus = find_school(line)
        if not name:
            continue
        if _ORG_HINT_RE.search(name) and tier in {"unknown", "ordinary"}:
            continue
        rank = {
            "c9": 0, "c985": 1, "overseas_top": 2, "c211": 3,
            "double_first": 4, "overseas_known": 5, "ordinary": 6,
            "college": 7, "unknown": 8,
        }.get(tier, 9)
        if rank < best[4]:
            best = (name, tier, _tier_label(tier), bonus, rank)

    if best[0]:
        return best[0], best[1], best[2], best[3]
    return "", "unknown", "未识别", 0.0


def _tier_label(tier: str) -> str:
    from .schools import TIER_LABELS
    return TIER_LABELS.get(tier, "未识别")


def _detect_major(text: str) -> str:
    match = re.search(
        r"(?:专业|主修)\s*[:：]\s*([^\n|，,。；;]{2,24})|"
        r"([\u4e00-\u9fa5]{2,12}(?:工程|科学|技术|管理|学|专业))(?:专业|硕士|本科|学士|博士)",
        text,
    )
    if not match:
        return ""
    value = (match.group(1) or match.group(2) or "").strip()
    return value[:24]


def _detect_role_title(text: str, head_chars: int = 500) -> str:
    head = text[:head_chars]
    match = re.search(
        r"(?:求职意向|应聘职位|目标岗位|职位|Title)\s*[:：]\s*([^\n|，,。；;]{2,30})",
        head,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    title_pool = [
        "java开发工程师", "python开发工程师", "前端开发工程师", "后端开发工程师",
        "全栈工程师", "算法工程师", "数据分析师", "数据工程师", "产品经理",
        "测试工程师", "运维工程师", "架构师", "技术经理", "项目经理", "ui设计师",
    ]
    lowered = head.lower()
    for title in title_pool:
        if title in lowered:
            return title
    return ""


def _split_projects(section_text: str, limit: int = 12) -> list[ProjectItem]:
    if not section_text.strip():
        return []

    blocks: list[str] = []
    current: list[str] = []
    bullet_re = re.compile(r"^\s*(?:[•·▪◦*\-–—]|\d+[)）.、]|\(\d+\))\s*")
    for line in section_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        is_heading = (
            not bullet_re.match(stripped)
            and len(stripped) <= 60
            and (
                DATE_RE.search(stripped)
                or re.search(r"(项目|系统|平台|模块|app|网站)$", stripped, re.IGNORECASE)
                or "：" in stripped[:30]
                or ":" in stripped[:30]
            )
        )
        if is_heading and current:
            blocks.append("\n".join(current))
            current = [stripped]
        else:
            current.append(stripped)
    if current:
        blocks.append("\n".join(current))

    projects: list[ProjectItem] = []
    for block in blocks[:limit]:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        title = re.sub(r"\s+", " ", lines[0])[:60]
        description = "\n".join(lines[1:])[:1500]
        if not description and len(lines) == 1:
            description = title
        projects.append(ProjectItem(title=title, description=description))
    if not projects and section_text.strip():
        projects.append(ProjectItem(title="项目经历", description=section_text.strip()[:1500]))
    return projects


def _contexts(text: str, alias: str, limit: int = 3) -> list[str]:
    results = []
    pattern = re.compile(re.escape(alias), re.IGNORECASE)
    for match in pattern.finditer(text):
        start = max(0, match.start() - 28)
        end = min(len(text), match.end() + 28)
        snippet = text[start:end].replace("\n", " ").strip()
        snippet = re.sub(r"\s+", " ", snippet)
        if snippet and snippet not in results:
            results.append(snippet)
        if len(results) >= limit:
            break
    return results


def _count_occurrences(text_lower: str, alias: str) -> int:
    if not alias:
        return 0
    if re.fullmatch(r"[a-z0-9+#./\- ]+", alias):
        pattern = r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"
    else:
        pattern = re.escape(alias)
    return len(re.findall(pattern, text_lower))


def match_skills(text: str) -> dict[str, Hit]:
    text_lower = text.lower()
    hits: dict[str, Hit] = {}
    for canonical, aliases in (
        (name, aliases) for entries in SKILL_CATALOG.values() for name, aliases in entries.items()
    ):
        counts = 0
        matched_alias = ""
        for alias in aliases:
            count = _count_occurrences(text_lower, alias.lower())
            if count:
                counts += count
                matched_alias = matched_alias or alias
        if counts:
            contexts = _contexts(text, matched_alias or canonical)
            hits[canonical] = Hit(
                term=canonical,
                category=CATEGORY_LABELS.get(canonical, "其他"),
                count=min(counts, 20),
                contexts=contexts,
            )
    return hits


def match_terms(text: str, terms: list[str]) -> dict[str, Hit]:
    text_lower = text.lower()
    hits: dict[str, Hit] = {}
    for term in terms:
        term = term.strip()
        if not term:
            continue
        count = _count_occurrences(text_lower, term.lower())
        if count:
            canonical = ALIAS_MAP.get(term.lower(), term)
            hits[canonical] = Hit(
                term=term,
                category=CATEGORY_LABELS.get(canonical, "自定义"),
                count=min(count, 20),
                contexts=_contexts(text, term),
            )
    return hits


def extract(text: str, filename: str = "") -> Extracted:
    sections = detect_sections(text)
    result = Extracted(sections=sections)
    result.name = _guess_name(text, filename)

    phone_match = PHONE_RE.search(text) or PHONE_LOOSE_RE.search(text)
    if phone_match:
        result.phone = re.sub(r"[\s\-]", "", phone_match.group(1))
    email_match = EMAIL_RE.search(text)
    if email_match:
        result.email = email_match.group(0)

    result.experience_text = sections.get("experience", "")
    result.education_text = sections.get("education", "")

    years, source = extract_years(text, result.experience_text, result.education_text)
    result.years = years
    result.years_source = source

    edu_text = result.education_text
    edu_scope = edu_text or text
    result.degree = _detect_degree(edu_scope)
    school, tier, tier_label, bonus = _detect_school(edu_scope)
    result.school = school
    result.school_tier = tier
    result.school_tier_label = tier_label
    result.school_bonus = bonus
    result.major = _detect_major(edu_scope)
    result.education_entries = _parse_education_entries(edu_scope)

    result.project_text = sections.get("projects", "")
    result.skill_text = sections.get("skills", "")
    result.summary_text = sections.get("summary", "")
    result.awards_text = sections.get("awards", "")

    result.role_title = _detect_role_title(text)
    lowered_head = text[:800].lower()
    result.seniority = any(title in lowered_head for title in SENIOR_TITLES)
    result.management = any(title in lowered_head for title in MANAGEMENT_TITLES)

    result.skills = match_skills(text)
    result.projects = _split_projects(result.project_text)
    return result


def project_metrics(projects: list[ProjectItem]) -> dict:
    """统计项目数量、强动词、量化指标与规模描述。

    标题行也参与统计：不少简历把成果写在标题里（如「…累计 800 名用户」）。
    """
    descriptions = "\n".join(f"{p.title}\n{p.description}" for p in projects)
    verb_hits = [verb for verb in PROJECT_VERBS if verb in descriptions]
    metric_hits = METRIC_RE.findall(descriptions)
    scale_hits = [word for word in SCALE_WORDS if word in descriptions]
    return {
        "count": len(projects),
        "verb_hits": verb_hits,
        "metric_count": len(metric_hits),
        "scale_hits": scale_hits,
    }
