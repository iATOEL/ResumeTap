"""单元测试：覆盖解析、评估、分类与 API 端点。"""

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import classify_score  # noqa: E402
from evaluator import _build_result, _extract_json  # noqa: E402
from main import _secure_filename  # noqa: E402


# ---------- config ----------

@pytest.mark.parametrize(
    "score,expected",
    [
        (100, "强烈推荐"),
        (85, "强烈推荐"),
        (84.9, "推荐"),
        (70, "推荐"),
        (69.9, "待定"),
        (55, "待定"),
        (54.9, "不推荐"),
        (0, "不推荐"),
    ],
)
def test_classify_score(score, expected):
    assert classify_score(score) == expected


# ---------- main ----------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("resume.docx", "resume.docx"),
        ("../../etc/passwd.docx", "passwd.docx"),
        ("C:\\Windows\\evil.docx", "evil.docx"),
        ("", "unnamed.docx"),
        ("..", "unnamed.docx"),
    ],
)
def test_secure_filename(raw, expected):
    assert _secure_filename(raw) == expected


# ---------- evaluator ----------

def test_extract_json_plain():
    assert _extract_json('{"name": "张三"}') == {"name": "张三"}


def test_extract_json_markdown_block():
    content = '```json\n{"name": "张三", "score": 90}\n```'
    assert _extract_json(content) == {"name": "张三", "score": 90}


def test_extract_json_with_surrounding_text():
    content = '好的，结果如下：\n{"name": "李四"}\n以上。'
    assert _extract_json(content) == {"name": "李四"}


def test_extract_json_invalid():
    assert _extract_json("完全不是JSON") is None


def test_build_result_clamps_scores():
    data = {
        "name": "王五",
        "education_score": 120,
        "experience_score": -10,
        "overall_score": "88.5",
        "category": "",
        "details": "not-a-dict",
    }
    result = _build_result(data)
    assert result.education_score == 100
    assert result.experience_score == 0
    assert result.overall_score == 88.5
    assert result.category == "强烈推荐"  # 空分类时按分数兜底推导
    assert result.details == {}


# ---------- API 集成测试 ----------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """使用临时数据库的测试客户端，并 mock LLM 评估。"""
    import database

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))

    import main
    from fastapi.testclient import TestClient

    async def fake_evaluate(text):
        from models import EvaluationResult

        return EvaluationResult(
            name="测试候选人",
            education_level="本科",
            education_score=80,
            experience_score=75,
            project_score=70,
            skill_score=65,
            overall_score=72.5,
            category="推荐",
            summary="表现良好",
            details={"education_analysis": "本科毕业"},
        )

    monkeypatch.setattr(main, "evaluate_resume", fake_evaluate)

    with TestClient(main.app) as c:
        yield c


def _make_docx_bytes(text: str) -> bytes:
    from docx import Document

    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_upload_and_list_and_detail_and_delete(client):
    # 上传
    resp = client.post(
        "/api/upload",
        files={"file": ("resume.docx", _make_docx_bytes("张三的简历 五年经验"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    candidate_id = data["id"]
    assert data["category"] == "推荐"

    # 列表
    resp = client.get("/api/candidates")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # 详情
    resp = client.get(f"/api/candidates/{candidate_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "测试候选人"

    # 详情不存在
    resp = client.get("/api/candidates/9999")
    assert resp.status_code == 404

    # 非法排序字段
    resp = client.get("/api/candidates?sort_by=name;DROP TABLE candidates--")
    assert resp.status_code == 400

    # 删除
    resp = client.delete(f"/api/candidates/{candidate_id}")
    assert resp.status_code == 200
    resp = client.delete(f"/api/candidates/{candidate_id}")
    assert resp.status_code == 404


def test_upload_rejects_non_docx(client):
    resp = client.post("/api/upload", files={"file": ("resume.pdf", b"fake", "application/pdf")})
    assert resp.status_code == 400


def test_upload_rejects_empty_docx(client):
    resp = client.post(
        "/api/upload",
        files={"file": ("resume.docx", _make_docx_bytes("   "), "application/octet-stream")},
    )
    assert resp.status_code == 400
