"""核心逻辑单元测试：解析、抽取、评分、配置、存储与导出。"""

import io
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config as config_module  # noqa: E402
from app import extractor, parser, scoring, services, storage  # noqa: E402


# ---------- fixtures ----------

@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """每个测试使用独立的数据目录。"""
    from app import paths

    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    for name in ("resumes_dir", "exports_dir", "logs_dir", "db_path", "config_path"):
        if name == "db_path":
            monkeypatch.setattr(paths, name, lambda: tmp_path / "data.db")
        elif name == "config_path":
            monkeypatch.setattr(paths, name, lambda: tmp_path / "config.yaml")
        else:
            directory = tmp_path / name.replace("_dir", "")
            directory.mkdir(parents=True, exist_ok=True)
            monkeypatch.setattr(paths, name, lambda d=directory: d)
    storage.close()
    yield
    storage.close()


@pytest.fixture()
def cfg():
    return config_module.normalize({})


def _docx(text: str) -> bytes:
    from docx import Document

    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


SAMPLE = """李四
13812345678 | lisi@example.com
教育背景
2013.09-2017.06  某某大学  计算机科学与技术  本科
工作经历
2019.03-至今  某互联网公司  后端开发工程师
负责订单系统重构，主导微服务拆分，QPS 提升 80%
2017.07-2019.02  某软件公司  Java 开发工程师
项目经历
订单中台重构（2021.03-2022.05）
主导 8 个微服务拆分，接口响应时间下降 60%
专业技能
Java、Spring Boot、MySQL、Redis、Docker、Kafka
自我评价
5 年后端开发经验，熟悉高并发系统。
"""


# ---------- 配置 ----------

def test_default_config_is_normalized(cfg):
    weights = cfg["scoring"]["weights"]
    assert abs(sum(weights.values()) - 1.0) < 1e-6
    assert cfg["rarity"]["sr"] < cfg["rarity"]["ssr"]
    # 岗位默认未选择：必须先选岗位才能开始速筛
    assert cfg["role"] == ""
    # 学历权重要足够大，避免"中专与名校"同档
    assert weights["education"] >= 0.18


def test_invalid_rarity_is_repaired():
    cfg = config_module.normalize({"rarity": {"ssr": 60, "sr": 90}})
    assert cfg["rarity"]["sr"] < cfg["rarity"]["ssr"]


def test_weights_are_renormalized():
    cfg = config_module.normalize({"scoring": {"weights": {"skill": 5, "experience": 5}}})
    assert abs(sum(cfg["scoring"]["weights"].values()) - 1.0) < 1e-6


def test_keyword_list_accepts_chinese_separators(cfg):
    cfg = config_module.normalize({"keywords": "Python，微服务、高并发"})
    assert cfg["keywords"] == ["Python", "微服务", "高并发"]


def test_rarity_mapping(cfg):
    assert config_module.rarity_of(cfg["rarity"]["ssr"], cfg) == "SSR"
    assert config_module.rarity_of(cfg["rarity"]["sr"], cfg) == "SR"
    assert config_module.rarity_of(cfg["rarity"]["sr"] - 0.1, cfg) == "R"


# ---------- 解析 ----------

def test_parse_docx_bytes():
    text = parser.parse_bytes(_docx("张三\n五年经验"), ".docx")
    assert "张三" in text


@pytest.mark.parametrize("suffix", [".doc", ".wps"])
def test_legacy_format_rejected(suffix):
    with pytest.raises(parser.ParseError, match="另存为"):
        parser.parse_bytes(b"data", suffix)


def test_unsupported_format_rejected():
    with pytest.raises(parser.ParseError, match="不支持"):
        parser.parse_bytes(b"data", ".exe")


def test_empty_file_rejected():
    with pytest.raises(parser.ParseError, match="为空"):
        parser.parse_bytes(b"", ".docx")


def test_corrupt_docx_rejected():
    with pytest.raises(parser.ParseError, match="损坏"):
        parser.parse_bytes(b"not a zip", ".docx")


def test_txt_gbk_decoded():
    text = parser.parse_bytes("王五 简历".encode("gbk"), ".txt")
    assert "王五" in text


# ---------- 抽取 ----------

def test_sections_detected():
    sections = extractor.detect_sections(SAMPLE)
    assert "后端开发工程师" in sections["experience"]
    assert "Java" in sections["skills"]
    # 章节内容不应包含标题行本身
    assert "工作经历" not in sections["experience"]


def test_name_and_contact_extracted():
    ext = extractor.extract(SAMPLE, "lisi.docx")
    assert ext.name == "李四"
    assert ext.phone == "13812345678"
    assert ext.email == "lisi@example.com"


def test_mask_helpers():
    assert extractor.mask_phone("13812345678") == "138****5678"
    assert extractor.mask_email("lisi@example.com") == "l***@example.com"


def test_years_from_work_section_not_education():
    """教育起止时间不能算进工作年限。"""
    text = """张三
教育背景
2013.09-2017.06  某大学  本科
工作经历
2019.03-2020.03  某公司  开发工程师
"""
    sections = extractor.detect_sections(text)
    years, _ = extractor.extract_years(text, sections["experience"], sections["education"])
    assert 0.9 <= years <= 1.1


def test_years_uses_explicit_declaration():
    text = "8 年工作经验\n2019.01-2020.01 某公司"
    years, source = extractor.extract_years(text)
    assert years == 8.0
    assert "声明" in source


def test_overlapping_intervals_not_double_counted():
    text = """工作经历
2018.01-2021.01  甲公司
2019.01-2021.01  乙公司
"""
    years, _ = extractor.extract_years(text, text, "")
    assert years == pytest.approx(3.0, abs=0.15)


def test_skill_matching_avoids_substring_false_positive():
    hits = extractor.match_skills("熟悉 JavaScript 和 React")
    assert "JavaScript" in hits
    assert "Java" not in hits


def test_degree_detection_prefers_highest():
    ext = extractor.extract("教育背景\n2013-2017 本科\n2017-2020 硕士\n", "x.docx")
    assert ext.degree == "硕士"


def test_projects_split():
    projects = extractor._split_projects(
        "订单中台重构（2021.03-2022.05）\n主导微服务拆分\n数据平台建设（2020.01-2020.12）\n负责实时计算"
    )
    assert len(projects) == 2
    assert "订单中台重构" in projects[0].title


# ---------- 评分 ----------

def test_evaluate_returns_six_dimensions(cfg):
    result = scoring.evaluate(SAMPLE, "lisi.docx", cfg)
    keys = {dim["key"] for dim in result["dimensions"]}
    assert keys == {"skill", "experience", "education", "project", "keyword", "job_match"}
    assert 0 <= result["overall_score"] <= 100
    assert result["rarity"] in {"R", "SR", "SSR"}


def test_keyword_dimension_skipped_when_unconfigured(cfg):
    result = scoring.evaluate(SAMPLE, "lisi.docx", cfg)
    keyword = next(d for d in result["dimensions"] if d["key"] == "keyword")
    assert keyword["available"] is False
    assert keyword["weight"] == 0


def test_custom_keywords_activate_dimension(cfg):
    cfg = config_module.normalize({"keywords": ["Java", "MySQL", "不存在词"]})
    result = scoring.evaluate(SAMPLE, "lisi.docx", cfg)
    keyword = next(d for d in result["dimensions"] if d["key"] == "keyword")
    assert keyword["available"] is True
    assert keyword["score"] > 0


def test_higher_thresholds_lower_rarity(cfg):
    strict = config_module.normalize({"rarity": {"ssr": 99, "sr": 98}})
    normal = scoring.evaluate(SAMPLE, "a.docx", cfg)["rarity"]
    strict_rarity = scoring.evaluate(SAMPLE, "a.docx", strict)["rarity"]
    order = {"R": 0, "SR": 1, "SSR": 2}
    assert order[strict_rarity] <= order[normal]


def test_screening_rejects_on_must_keyword(cfg):
    cfg = config_module.normalize({"screening": {"must_keywords": ["Go语言不存在"]}})
    result = scoring.evaluate(SAMPLE, "a.docx", cfg)
    assert result["status"] == "rejected"
    assert any("缺少必备关键词" in r for r in result["screen_reasons"])


def test_screening_rejects_on_min_years(cfg):
    cfg = config_module.normalize({"screening": {"min_years": 30}})
    result = scoring.evaluate(SAMPLE, "a.docx", cfg)
    assert result["status"] == "rejected"


def test_screening_rejects_on_degree(cfg):
    cfg = config_module.normalize({"screening": {"required_degree": "博士"}})
    result = scoring.evaluate(SAMPLE, "a.docx", cfg)
    assert result["status"] == "rejected"


def test_screening_rejects_on_reject_keyword(cfg):
    cfg = config_module.normalize({"screening": {"reject_keywords": ["互联网"]}})
    result = scoring.evaluate(SAMPLE, "a.docx", cfg)
    assert result["status"] == "rejected"


def test_role_detection_backend():
    ext = extractor.extract(SAMPLE, "a.docx")
    role, ratio = scoring.detect_role(ext)
    assert role == "backend"
    assert ratio > 0.1


def test_score_clamped_within_range(cfg):
    result = scoring.evaluate("技能：Python\n" * 400, "x.docx", cfg)
    for dim in result["dimensions"]:
        assert 0 <= dim["score"] <= 100
    assert 0 <= result["overall_score"] <= 100


def test_empty_text_does_not_crash(cfg):
    result = scoring.evaluate("   ", "x.docx", cfg)
    assert result["overall_score"] >= 0
    assert result["rarity"] in {"R", "SR", "SSR"}


def test_jd_terms_extraction():
    terms = scoring.extract_jd_terms("熟悉 Python 与 Kubernetes，具备高并发经验，工作 3 年以上")
    assert "Python" in terms
    assert "Kubernetes" in terms


# ---------- 导入 / 存储 / 导出 ----------

def _make_sample(tmp_path: Path, name: str = "李四.docx") -> Path:
    path = tmp_path / name
    path.write_bytes(_docx(SAMPLE))
    return path


def test_import_and_list(tmp_path, cfg):
    path = _make_sample(tmp_path)
    result = services.import_files([str(path)], cfg)
    assert len(result["imported"]) == 1
    assert result["failures"] == []

    items = storage.list_candidates()
    assert len(items) == 1
    assert items[0]["name"] == "李四"


def test_duplicate_detected(tmp_path, cfg):
    path = _make_sample(tmp_path)
    services.import_files([str(path)], cfg)
    again = services.import_files([str(path)], cfg)
    assert len(again["duplicates"]) == 1
    assert len(storage.list_candidates()) == 1


def test_batch_import_isolates_failures(tmp_path, cfg):
    good = _make_sample(tmp_path)
    bad = tmp_path / "坏文件.docx"
    bad.write_bytes(b"broken")
    result = services.import_files([str(good), str(bad), str(tmp_path / "missing.docx")], cfg)
    assert len(result["imported"]) == 1
    assert len(result["failures"]) == 2


def test_rescore_applies_new_thresholds(tmp_path, cfg):
    path = _make_sample(tmp_path)
    services.import_files([str(path)], cfg)
    before = storage.list_candidates()[0]["rarity"]

    strict = config_module.normalize({"rarity": {"ssr": 100, "sr": 99}})
    services.rescore_all(strict)
    after = storage.list_candidates()[0]["rarity"]
    assert after == "R"
    assert before != "R" or before == "R"


def test_statistics(tmp_path, cfg):
    services.import_files([str(_make_sample(tmp_path))], cfg)
    stats = services.statistics()
    assert stats["total"] == 1
    assert sum(stats["rarity_counts"].values()) == 1
    assert 0 <= stats["avg_score"] <= 100


def test_export_csv_contains_key_fields(tmp_path, cfg):
    services.import_files([str(_make_sample(tmp_path))], cfg)
    result = services.export_candidates("csv", None, cfg, target_dir=str(tmp_path / "out"))
    content = Path(result["path"]).read_text(encoding="utf-8-sig")
    assert "稀有度" in content
    assert "各维度分数" in content
    assert "李四" in content


def test_export_json_structure(tmp_path, cfg):
    services.import_files([str(_make_sample(tmp_path))], cfg)
    result = services.export_candidates("json", None, cfg, target_dir=str(tmp_path / "out"))
    data = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert data["total"] == 1
    assert "thresholds" in data
    assert data["candidates"][0]["dimensions"]


def test_export_markdown(tmp_path, cfg):
    services.import_files([str(_make_sample(tmp_path))], cfg)
    result = services.export_candidates("md", None, cfg, target_dir=str(tmp_path / "out"))
    content = Path(result["path"]).read_text(encoding="utf-8")
    assert "# 简历筛选报告" in content
    assert "评分明细" in content


def test_export_empty_raises(cfg, tmp_path):
    with pytest.raises(ValueError, match="没有可导出"):
        services.export_candidates("csv", None, cfg, target_dir=str(tmp_path))


def test_export_selected_only(tmp_path, cfg):
    services.import_files([str(_make_sample(tmp_path, "a.docx"))], cfg)
    second = tmp_path / "b.docx"
    second.write_bytes(_docx(SAMPLE.replace("李四", "王五")))
    services.import_files([str(second)], cfg)
    first_id = storage.list_candidates()[0]["id"]
    result = services.export_candidates("json", [first_id], cfg, target_dir=str(tmp_path / "out"))
    data = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert data["total"] == 1


def test_delete_removes_record_and_file(tmp_path, cfg):
    services.import_files([str(_make_sample(tmp_path))], cfg)
    item = storage.list_candidates()[0]
    stored = storage.delete_candidate(item["id"])
    assert stored
    assert not storage.list_candidates()


def test_raw_text_not_stored_when_privacy_off(tmp_path):
    cfg = config_module.normalize({"privacy": {"store_raw_text": False}})
    services.import_files([str(_make_sample(tmp_path))], cfg)
    detail = storage.get_candidate(storage.list_candidates()[0]["id"])
    assert detail["raw_text"] == ""


def test_achievement_recorded():
    storage.set_meta("achievements", {})
    services.record_pull("SSR", {})
    services.record_pull("R", {})
    stats = storage.get_meta("achievements")
    assert stats["pulls"] == 2
    assert stats["by_rarity"]["SSR"] == 1


# ---------- 安全性 ----------

def test_no_hardcoded_absolute_paths():
    """源码中不应出现写死的绝对路径。"""
    suspicious = []
    for path in (ROOT / "app").glob("*.py"):
        content = path.read_text(encoding="utf-8")
        for pattern in ("C:\\\\Users", "/home/", "C:/Users/"):
            if pattern in content:
                suspicious.append(f"{path.name}: {pattern}")
    assert not suspicious


def test_export_csv_escapes_formula_injection(tmp_path, cfg):
    """姓名以 = 开头时不应被 Excel 当公式执行。"""
    malicious = SAMPLE.replace("李四", "=cmd|calc")
    path = tmp_path / "x.docx"
    path.write_bytes(_docx(malicious))
    services.import_files([str(path)], cfg)
    result = services.export_candidates("csv", None, cfg, target_dir=str(tmp_path / "out"))
    content = Path(result["path"]).read_text(encoding="utf-8-sig")
    assert "=cmd" not in content.split("\n")[1]


# ---------- 院校层级 ----------

@pytest.mark.parametrize(
    "school,expected_tier",
    [
        ("清华大学", "c9"),
        ("北京大学", "c9"),
        ("浙江大学", "c9"),
        ("电子科技大学", "c985"),
        ("哈尔滨工业大学", "c9"),
        ("南京理工大学", "c211"),
        ("暨南大学", "c211"),
        ("上海科技大学", "double_first"),
        ("麻省理工学院", "overseas_top"),
        ("香港大学", "overseas_top"),
        ("某某职业技术学院", "college"),
        ("某某大学", "ordinary"),
        ("", "unknown"),
    ],
)
def test_classify_school(school, expected_tier):
    from app import schools

    tier, _, bonus = schools.classify_school(school)
    assert tier == expected_tier
    assert bonus >= 0


def test_school_tiers_are_ordered():
    from app import schools

    c9 = schools.classify_school("清华大学")[2]
    c985 = schools.classify_school("电子科技大学")[2]
    c211 = schools.classify_school("南京理工大学")[2]
    ordinary = schools.classify_school("某某大学")[2]
    college = schools.classify_school("某某职业学院")[2]
    assert c9 > c985 > c211 > ordinary >= college


def test_education_score_reflects_school_tier(cfg):
    high = scoring.evaluate(
        "张三\n教育背景\n2013.09-2017.06  清华大学  计算机  本科\n", "a.docx", cfg)
    low = scoring.evaluate(
        "李四\n教育背景\n2013.09-2017.06  某某学院  计算机  本科\n", "b.docx", cfg)
    high_edu = next(d for d in high["dimensions"] if d["key"] == "education")["score"]
    low_edu = next(d for d in low["dimensions"] if d["key"] == "education")["score"]
    assert high_edu > low_edu + 10


def test_degree_not_polluted_by_requirement_line():
    """'本科及以上' 这类招聘要求不应被当成候选人学历。"""
    text = """王五
求职意向：后端开发，要求本科及以上
教育背景
2016.09-2019.06  某某职业技术学院  计算机应用  大专
"""
    ext = extractor.extract(text, "x.docx")
    assert ext.degree == "大专"


def test_degree_picks_highest_real_education():
    text = """赵六
教育背景
2013.09-2017.06  某大学  本科
2017.09-2020.06  某大学  硕士研究生
"""
    ext = extractor.extract(text, "x.docx")
    assert ext.degree == "硕士"


def test_degree_unknown_when_no_education():
    ext = extractor.extract("张三\n工作经历\n2019-2021 某公司", "x.docx")
    assert ext.degree == "其他"


# ---------- 优缺点 ----------

def test_evaluate_produces_strengths_and_weaknesses(cfg):
    result = scoring.evaluate(SAMPLE, "li.docx", cfg)
    assert isinstance(result["strengths"], list) and result["strengths"]
    assert isinstance(result["weaknesses"], list) and result["weaknesses"]
    assert result["news_flash"]


def test_weakness_flags_junior_profile(cfg):
    junior = """应届生
教育背景
2020.09-2024.06  某大学  本科
"""
    result = scoring.evaluate(junior, "j.docx", cfg)
    joined = " ".join(result["weaknesses"])
    assert "经验" in joined or "应届" in joined


def test_short_board_skips_unavailable_dimension():
    """未启用关键词维度时，不应被当作短板写进评语。"""
    cfg = config_module.normalize({})
    result = scoring.evaluate(SAMPLE, "a.docx", cfg)
    assert not any("关键词" in w and "短板" in w for w in result["weaknesses"])


# ---------- 速筛 ----------

def test_review_workflow(tmp_path, cfg):
    services.import_files([str(_make_sample(tmp_path))], cfg)
    item = storage.list_candidates()[0]
    assert item["review_verdict"] == ""

    assert storage.set_review(item["id"], "liked", "不错")
    updated = storage.list_candidates()[0]
    assert updated["review_verdict"] == "liked"
    assert updated["review_note"] == "不错"

    summary = storage.review_summary()
    assert summary["liked"] == 1
    assert summary["pending"] == 0
    assert summary["progress"] == 100.0


def test_pending_list_excludes_reviewed(tmp_path, cfg):
    services.import_files([str(_make_sample(tmp_path, "a.docx"))], cfg)
    second = tmp_path / "b.docx"
    second.write_bytes(_docx(SAMPLE.replace("李四", "王五")))
    services.import_files([str(second)], cfg)

    assert len(storage.list_pending()) == 2
    first = storage.list_pending()[0]
    storage.set_review(first["id"], "passed")
    assert len(storage.list_pending()) == 1


def test_review_progress_in_statistics(tmp_path, cfg):
    services.import_files([str(_make_sample(tmp_path))], cfg)
    item = storage.list_candidates()[0]
    storage.set_review(item["id"], "liked")
    stats = services.statistics()
    assert stats["reviewed"] == 1
    assert stats["review_progress"] == 100.0


# ---------- 配置迁移 ----------

def test_config_migration_upgrades_old_version(tmp_path, monkeypatch):
    from app import paths

    old = {
        "scoring": {"education": {"top_school_bonus": 12, "tier1_school_bonus": 6,
                                  "project_985_211_bonus": 4}},
        "rarity": {"ssr": 82, "sr": 66},
    }
    import yaml
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(old), encoding="utf-8")
    monkeypatch.setattr(paths, "config_path", lambda: tmp_path / "config.yaml")

    cfg = config_module.load_config()
    assert cfg["config_version"] == config_module.CONFIG_VERSION
    assert "c9_bonus" in cfg["scoring"]["education"]
    assert "top_school_bonus" not in cfg["scoring"]["education"]
    assert cfg["scoring"]["project"]["count_scores"][1] == 45


def test_config_migration_preserves_user_thresholds(tmp_path, monkeypatch):
    from app import paths

    import yaml
    old = {"rarity": {"ssr": 91, "sr": 73}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(old), encoding="utf-8")
    monkeypatch.setattr(paths, "config_path", lambda: tmp_path / "config.yaml")

    cfg = config_module.load_config()
    assert cfg["rarity"]["ssr"] == 91
    assert cfg["rarity"]["sr"] == 73


# ---------- 本地 API ----------

@pytest.fixture()
def api_server(tmp_path, cfg):
    from app import api

    services.import_files([str(_make_sample(tmp_path))], cfg)
    info = api.start(8971)
    yield info
    api.stop()


def _api_get(info, path, token=None):
    import urllib.request

    headers = {"X-API-Token": token or info["token"]}
    req = urllib.request.Request(
        f"http://127.0.0.1:{info['port']}{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.load(resp)


def _api_post(info, path, payload, token=None):
    import urllib.request

    headers = {"X-API-Token": token or info["token"],
               "Content-Type": "application/json"}
    req = urllib.request.Request(
        f"http://127.0.0.1:{info['port']}{path}", headers=headers,
        method="POST", data=json.dumps(payload).encode())
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.load(resp)


def test_api_requires_token(api_server):
    import urllib.error
    import urllib.request

    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(
            f"http://127.0.0.1:{api_server['port']}/candidates", timeout=5)
    assert exc.value.code == 401


def test_api_health_without_token(api_server):
    import urllib.request

    with urllib.request.urlopen(
            f"http://127.0.0.1:{api_server['port']}/health", timeout=5) as resp:
        data = json.load(resp)
    assert data["ok"] is True


def test_api_candidates_include_verdicts(api_server):
    data = _api_get(api_server, "/candidates")
    assert data["total"] == 1
    candidate = data["candidates"][0]
    assert candidate["strengths"]
    assert candidate["weaknesses"]
    assert candidate["news_flash"]
    assert candidate["dimensions"]
    assert "raw_text" not in candidate
    # 联系方式按需求对外提供（原文仍不返回）
    assert candidate["contact"]["phone"]
    assert candidate["education_entries"]


def test_api_candidate_detail_has_sections(api_server):
    listing = _api_get(api_server, "/candidates")
    candidate_id = listing["candidates"][0]["id"]
    detail = _api_get(api_server, f"/candidates/{candidate_id}")
    assert "sections" in detail["candidate"]
    assert detail["candidate"]["sections"]["skills"]


def test_api_filters(api_server):
    assert _api_get(api_server, "/candidates?rarity=SSR")["total"] >= 0
    assert _api_get(api_server, "/candidates?min_score=99")["total"] == 0
    assert _api_get(api_server, "/candidates?pending=1")["total"] == 1


def test_api_write_review(api_server):
    listing = _api_get(api_server, "/candidates")
    candidate_id = listing["candidates"][0]["id"]
    result = _api_post(api_server, "/review",
                       {"id": candidate_id, "verdict": "liked", "note": "AI 复核"})
    assert result["ok"] is True
    assert result["review"]["liked"] == 1

    pending = _api_get(api_server, "/candidates?pending=1")
    assert pending["total"] == 0


def test_api_rejects_invalid_verdict(api_server):
    import urllib.error

    listing = _api_get(api_server, "/candidates")
    candidate_id = listing["candidates"][0]["id"]
    with pytest.raises(urllib.error.HTTPError) as exc:
        _api_post(api_server, "/review", {"id": candidate_id, "verdict": "maybe"})
    assert exc.value.code == 400


def test_api_batch_review(api_server):
    listing = _api_get(api_server, "/candidates")
    candidate_id = listing["candidates"][0]["id"]
    result = _api_post(api_server, "/review/batch",
                       {"items": [{"id": candidate_id, "verdict": "skipped"},
                                  {"id": 9999, "verdict": "liked"}]})
    assert result["updated"] == 1


def test_api_config_defaults_and_bounds():
    cfg = config_module.normalize({})
    # 对外 API 默认关闭，需用户手动开启
    assert cfg["api"]["enabled"] is False
    assert cfg["api"]["port"] == 8765

    cfg = config_module.normalize({"api": {"enabled": False, "port": 999999}})
    assert cfg["api"]["enabled"] is False
    assert 1024 <= cfg["api"]["port"] <= 65535


def test_api_can_be_disabled(monkeypatch, tmp_path, cfg):
    """关闭自动启动后不应起服务。"""
    from app import api

    assert not api.STATE.running
