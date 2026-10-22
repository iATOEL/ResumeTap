"""新增能力测试：学历权重、岗位必选、联系方式、拖拽导入、AI 复核。"""

import base64
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import ai_review, config as config_module, extractor, parser  # noqa: E402
from app import scoring, services, storage  # noqa: E402

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


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    from app import paths

    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(paths, "db_path", lambda: tmp_path / "data.db")
    monkeypatch.setattr(paths, "config_path", lambda: tmp_path / "config.yaml")
    for name in ("resumes_dir", "exports_dir", "logs_dir"):
        directory = tmp_path / name.replace("_dir", "")
        directory.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(paths, name, lambda d=directory: d)
    storage.close()
    yield
    storage.close()


@pytest.fixture()
def cfg():
    return config_module.normalize({"role": "backend"})


def _docx(text: str) -> bytes:
    from docx import Document

    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _make_sample(tmp_path: Path, name: str = "李四.docx") -> Path:
    path = tmp_path / name
    path.write_bytes(_docx(SAMPLE))
    return path


# ---------------- 学历权重与院校差距 ----------------

def test_education_weight_is_meaningful():
    cfg = config_module.normalize({})
    assert cfg["scoring"]["weights"]["education"] >= 0.18


def test_vocational_vs_top_school_junior_gap():
    """同样无经验：中专 vs 北大，分数应明显拉开。"""
    cfg = config_module.normalize({"role": "auto"})
    low = "张三\n教育背景\n2016.09-2019.06  某某职业技术学院  计算机应用  中专\n"
    high = "李四\n教育背景\n2016.09-2020.06  北京大学  计算机科学与技术  本科\n"
    low_result = scoring.evaluate(low, "a.docx", cfg)
    high_result = scoring.evaluate(high, "b.docx", cfg)
    assert high_result["overall_score"] - low_result["overall_score"] > 25
    assert high_result["overall_score"] > low_result["overall_score"]


def test_early_career_reweights_education():
    cfg = config_module.normalize({"role": "auto"})
    text = "王五\n教育背景\n2016.09-2020.06  北京大学  计算机  本科\n"
    result = scoring.evaluate(text, "x.docx", cfg)
    assert result["early_career_adjusted"] is True
    weights = {d["key"]: d["weight"] for d in result["dimensions"]}
    assert weights["education"] > weights["experience"]


def test_experienced_candidate_not_reweighted():
    cfg = config_module.normalize({"role": "auto"})
    text = ("老王\n教育背景\n2005.09-2009.06  某大学  本科\n"
            "工作经历\n2009.07-至今  某公司  开发工程师\n")
    result = scoring.evaluate(text, "x.docx", cfg)
    assert result["early_career_adjusted"] is False


def test_missing_project_is_not_zero_scored():
    """没写项目章节不该判 0 分拖垮总分。"""
    cfg = config_module.normalize({"role": "auto"})
    text = "张三\n教育背景\n2016.09-2020.06  北京大学  计算机  本科\n"
    result = scoring.evaluate(text, "x.docx", cfg)
    project = next(d for d in result["dimensions"] if d["key"] == "project")
    assert project["available"] is False
    assert project["weight"] == 0


# ---------------- 岗位必选 ----------------

def test_role_selected_flag():
    unset = scoring.evaluate(SAMPLE, "a.docx", config_module.normalize({}))
    assert unset["role_selected"] is False

    chosen = config_module.normalize({"role": "backend"})
    result = scoring.evaluate(SAMPLE, "a.docx", chosen)
    assert result["role_selected"] is True
    assert result["detected_role"] == "backend"


def test_invalid_role_falls_back_to_unset():
    cfg = config_module.normalize({"role": "不存在的岗位"})
    assert cfg["role"] == ""


def test_different_roles_give_different_scores():
    backend = config_module.normalize({"role": "backend"})
    frontend = config_module.normalize({"role": "frontend"})
    b = scoring.evaluate(SAMPLE, "a.docx", backend)
    f = scoring.evaluate(SAMPLE, "a.docx", frontend)
    assert b["overall_score"] != f["overall_score"]


# ---------------- 联系方式与教育经历 ----------------

def test_contact_extracted_into_payload(cfg):
    result = scoring.evaluate(SAMPLE, "a.docx", cfg)
    assert result["contact"]["phone"] == "13812345678"
    assert result["contact"]["email"] == "lisi@example.com"


def test_education_entries_parsed(cfg):
    text = """王芳
教育背景
2017.09-2021.06  某大学  数学与应用数学  本科
2021.09-2024.06  上海交通大学  计算机技术  硕士
"""
    result = scoring.evaluate(text, "x.docx", cfg)
    entries = result["profile"]["education_entries"]
    assert len(entries) == 2
    assert entries[1]["school"] == "上海交通大学"
    assert entries[1]["tier"] == "c9"
    assert entries[1]["degree"] == "硕士"
    assert "2021" in entries[1]["period"]


def test_education_entries_empty_without_section(cfg):
    result = scoring.evaluate("张三\n工作经历\n2019-2021 某公司", "x.docx", cfg)
    assert result["profile"]["education_entries"] == []


# ---------------- 拖拽导入通道 ----------------

def test_import_bytes_roundtrip():
    from app.bridge import Bridge

    bridge = Bridge()
    data = _docx(SAMPLE)
    result = bridge.import_bytes("张三的简历.docx", base64.b64encode(data).decode())
    assert result["ok"] is True
    assert len(result["imported"]) == 1
    # 展示名应保留原始文件名，而不是临时文件名
    assert result["candidates"][0]["filename"] == "张三的简历.docx"


def test_import_bytes_rejects_bad_input():
    from app.bridge import Bridge

    bridge = Bridge()
    assert bridge.import_bytes("x.exe", "AAAA")["ok"] is False
    assert bridge.import_bytes("x.docx", "")["ok"] is False
    assert bridge.import_bytes("", "AAAA")["ok"] is False


def test_import_bytes_rejects_oversize():
    from app.bridge import Bridge

    bridge = Bridge()
    big = base64.b64encode(b"0" * (26 * 1024 * 1024)).decode()
    result = bridge.import_bytes("big.docx", big)
    assert result["ok"] is False
    assert "25MB" in result["error"]


# ---------------- AI 复核 ----------------

@pytest.fixture()
def mock_ai():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    captured = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length))
            captured["auth"] = self.headers.get("Authorization")
            captured["model"] = body.get("model")
            payload = {
                "name": "AI测试",
                "overall_score": 77,
                "dimensions": {
                    "skill": {"score": 80, "reason": "AI 技能理由"},
                    "experience": {"score": 60, "reason": "AI 经验理由"},
                    "education": {"score": 85, "reason": "AI 学历理由"},
                    "project": {"score": 70, "reason": "AI 项目理由"},
                    "job_match": {"score": 72, "reason": "AI 岗位理由"},
                },
                "strengths": ["AI 优点A", "AI 优点B"],
                "weaknesses": ["AI 不足A"],
                "summary": "AI 总结",
            }
            out = {"choices": [{"message": {
                "content": json.dumps(payload, ensure_ascii=False)}}]}
            data = json.dumps(out, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/v1", captured
    server.shutdown()


def _ai_config(base_url, replace=True):
    return config_module.normalize({
        "role": "backend",
        "llm": {"enabled": True, "api_key": "test-key", "model": "mock-model",
                "base_url": base_url, "replace_rules": replace},
    })


def test_ai_not_ready_without_key():
    assert ai_review.is_ready(config_module.normalize({})) is False


def test_ai_review_parses_response(mock_ai):
    base_url, captured = mock_ai
    cfg = _ai_config(base_url)
    assert ai_review.is_ready(cfg) is True

    result = ai_review.review_resume(SAMPLE, "后端开发", cfg)
    assert result["overall_score"] == 77
    assert result["strengths"] == ["AI 优点A", "AI 优点B"]
    assert captured["auth"] == "Bearer test-key"
    assert captured["model"] == "mock-model"


def test_ai_apply_replaces_scores(cfg):
    payload = scoring.evaluate(SAMPLE, "a.docx", cfg)
    ai_result = {
        "overall_score": 90,
        "dimensions": [{"key": "skill", "label": "技能匹配", "score": 95,
                        "detail": "AI", "weight": 0, "contribution": 0,
                        "hits": [], "available": True}],
        "strengths": ["AI 强"], "weaknesses": ["AI 弱"],
        "summary": "AI 结论", "model": "m",
    }
    merged = ai_review.apply_to_payload(payload, ai_result, _ai_config("http://x", True), {})
    assert merged["overall_score"] == 90
    assert merged["strengths"] == ["AI 强"]
    assert merged["score_source"] == "ai"


def test_ai_apply_reference_only_when_not_replacing(cfg):
    payload = scoring.evaluate(SAMPLE, "a.docx", cfg)
    original = payload["overall_score"]
    ai_result = {"overall_score": 10, "dimensions": [], "strengths": ["AI"],
                 "weaknesses": [], "summary": "AI", "model": "m"}
    merged = ai_review.apply_to_payload(payload, ai_result,
                                        _ai_config("http://x", False), {})
    assert merged["overall_score"] == original
    assert merged["ai_review"]["applied"] is False


def test_ai_endpoint_normalization():
    assert ai_review._build_endpoint("https://api.openai.com/v1") == \
        "https://api.openai.com/v1/chat/completions"
    assert ai_review._build_endpoint("https://x.com") == \
        "https://x.com/v1/chat/completions"
    assert ai_review._build_endpoint("https://x.com/v1/chat/completions") == \
        "https://x.com/v1/chat/completions"


def test_ai_bridge_reports_missing_key():
    from app.bridge import Bridge

    bridge = Bridge()
    assert bridge.ai_review_candidate(1)["ok"] is False


# ---------------- 导出新字段 ----------------

def test_csv_export_includes_contact_and_education(tmp_path, cfg):
    services.import_files([str(_make_sample(tmp_path))], cfg)
    result = services.export_candidates("csv", None, cfg, target_dir=str(tmp_path / "out"))
    content = Path(result["path"]).read_text(encoding="utf-8-sig")
    header = content.split("\n")[0]
    assert "电话" in header and "邮箱" in header
    assert "教育经历" in header and "评分来源" in header


def test_json_export_includes_contact(tmp_path, cfg):
    services.import_files([str(_make_sample(tmp_path))], cfg)
    result = services.export_candidates("json", None, cfg, target_dir=str(tmp_path / "out"))
    data = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    candidate = data["candidates"][0]
    assert candidate["contact"]["phone"]
    assert "education_entries" in candidate
