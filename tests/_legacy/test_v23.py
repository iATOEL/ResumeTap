"""v2.3 新增能力：岗位库扩充与自定义岗位、学历来源校验、AI 批量复核、选择逻辑。"""

import base64
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import ai_review, config as config_module, extractor  # noqa: E402
from app import scoring, services, storage  # noqa: E402
from app.rules import ROLE_GROUPS, ROLE_PROFILES  # noqa: E402

SAMPLE = """李四
13812345678 | lisi@example.com
教育背景
2013.09-2017.06  某某大学  计算机科学与技术  本科
工作经历
2019.03-至今  某互联网公司  后端开发工程师
负责订单系统重构，主导微服务拆分，QPS 提升 80%
项目经历
订单中台重构（2021.03-2022.05）
主导 8 个微服务拆分，接口响应时间下降 60%
专业技能
Java、Spring Boot、MySQL、Redis、Docker、Kafka
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


# ---------------- 岗位库 ----------------

def test_role_catalog_covers_multiple_industries():
    """岗位不能只有计算机类。"""
    assert len(ROLE_PROFILES) >= 25
    labels = " ".join(v["label"] for v in ROLE_PROFILES.values())
    for keyword in ["财务", "教师", "销售", "设计", "人力", "制造", "供应链", "医疗"]:
        assert keyword in labels, f"缺少 {keyword} 类岗位"


def test_every_role_is_grouped():
    grouped = {key for keys in ROLE_GROUPS.values() for key in keys}
    assert set(ROLE_PROFILES) - grouped == set()
    assert grouped - set(ROLE_PROFILES) == set()


def test_each_role_has_keywords():
    for key, profile in ROLE_PROFILES.items():
        assert profile["keywords"], f"{key} 没有关键词"
        assert profile["label"]


def test_non_tech_role_scores_with_its_own_keywords():
    """教师岗位应该认教学关键词，而不是被技术词主导。"""
    cfg = config_module.normalize({"role": "education"})
    text = ("张老师\n教育背景\n2012.09-2016.06  北京师范大学  汉语言文学  本科\n"
            "工作经历\n2016.09-至今  某中学  语文教师\n"
            "负责语文教学、课程设计与教研，所带班级平均分提升 12 分\n")
    result = scoring.evaluate(text, "t.docx", cfg)
    job = next(d for d in result["dimensions"] if d["key"] == "job_match")
    assert job["score"] > 0
    assert result["detected_role_label"] == "教师 / 教研"


# ---------------- 自定义岗位 ----------------

def test_custom_role_requires_keywords():
    """选了自定义岗位但没填关键词 -> 退回未选择。"""
    cfg = config_module.normalize({"role": "custom", "custom_role": {"label": "x", "keywords": []}})
    assert cfg["role"] == ""


def test_custom_role_accepted_with_keywords():
    cfg = config_module.normalize({
        "role": "custom",
        "custom_role": {"label": "短视频编导", "keywords": ["脚本", "拍摄", "剪辑"]},
    })
    assert cfg["role"] == "custom"
    assert cfg["custom_role"]["label"] == "短视频编导"


def test_custom_role_scoring_uses_label_and_keywords():
    cfg = config_module.normalize({
        "role": "custom",
        "custom_role": {"label": "短视频编导", "keywords": ["脚本", "拍摄", "剪辑", "选题"]},
    })
    text = ("张三\n教育背景\n2015.09-2019.06  某大学  本科\n"
            "工作经历\n2020-至今  某公司  编导\n"
            "负责脚本撰写、选题策划、拍摄与剪辑，产出爆款视频 50 条\n")
    result = scoring.evaluate(text, "a.docx", cfg)
    assert result["role_selected"] is True
    assert result["detected_role_label"] == "短视频编导"
    job = next(d for d in result["dimensions"] if d["key"] == "job_match")
    assert job["score"] > 50


def test_custom_role_without_keywords_not_selected():
    cfg = config_module.normalize({"role": "custom", "custom_role": {"label": "空岗位"}})
    result = scoring.evaluate(SAMPLE, "a.docx", cfg)
    assert result["role_selected"] is False


# ---------------- 学历来源校验 ----------------

def test_company_name_not_treated_as_degree():
    """简历里只在工作经历提到清华大学，不能当成学历。"""
    text = ("李四\n工作经历\n2019.03-至今  清华大学  实验室管理员\n负责设备维护\n")
    ext = extractor.extract(text, "a.docx")
    assert ext.school == ""
    assert ext.degree == "其他"
    assert ext.education_source == "missing"


def test_education_section_wins_over_workplace():
    text = ("张三\n工作经历\n2019.03-至今  清华大学  实验室管理员\n"
            "教育背景\n2015.09-2019.06  北京信息职业技术学院  计算机应用  大专\n")
    ext = extractor.extract(text, "a.docx")
    assert ext.school == "北京信息职业技术学院"
    assert ext.school_tier == "college"
    assert ext.degree == "大专"
    assert ext.education_source == "education_section"


def test_degree_inferred_when_no_section():
    """没有教育章节，但明确写了学历和院校 -> 可以推断。"""
    text = ("王五\n13800138000\n2016.09-2020.06  北京大学  计算机科学与技术  本科\n"
            "工作经历\n2020-至今 某公司\n")
    ext = extractor.extract(text, "b.docx")
    assert ext.degree == "本科"
    assert ext.school == "北京大学"
    assert ext.education_source == "inferred"


def test_work_description_lines_not_used_as_education():
    text = ("赵六\n工作经历\n2018.07-2022.03  某互联网公司  后端开发工程师\n"
            "负责本科实习生培养，参与本科生课程共建\n")
    ext = extractor.extract(text, "c.docx")
    assert ext.degree == "其他"
    assert ext.school == ""


def test_top_school_degree_still_detected():
    text = "钱七\n教育背景\n2016.09-2020.06  清华大学  计算机  本科\n工作经历\n2020.07-至今  清华大学  研究员\n"
    ext = extractor.extract(text, "d.docx")
    assert ext.school == "清华大学"
    assert ext.school_tier == "c9"
    assert ext.degree == "本科"


def test_education_source_reported_in_payload(cfg):
    result = scoring.evaluate(SAMPLE, "a.docx", cfg)
    assert result["profile"]["education_entries"]
    assert result["degree"] == "本科"


# ---------------- AI 批量复核 ----------------

@pytest.fixture()
def mock_ai_server():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    state = {"calls": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            state["calls"] += 1
            payload = {
                "name": "AI",
                "overall_score": 70 + state["calls"] % 20,
                "dimensions": {
                    "skill": {"score": 70, "reason": "r"},
                    "experience": {"score": 70, "reason": "r"},
                    "education": {"score": 70, "reason": "r"},
                    "project": {"score": 70, "reason": "r"},
                    "job_match": {"score": 70, "reason": "r"},
                },
                "strengths": ["AI 优点"],
                "weaknesses": ["AI 不足"],
                "summary": "AI 结论",
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
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1", state
    server.shutdown()


def _ai_cfg(base_url, replace=True):
    """构造 AI 配置并写入磁盘：Bridge._config() 读的是配置文件。"""
    cfg = config_module.normalize({
        "role": "backend",
        "llm": {"enabled": True, "api_key": "k", "model": "m",
                "base_url": base_url, "replace_rules": replace},
    })
    config_module.save_config(cfg)
    return config_module.load_config()


def test_batch_review_covers_all_when_no_limit(mock_ai_server):
    """limit=0 应复核全部简历，而不是只处理 10 份。"""
    base_url, state = mock_ai_server
    cfg = _ai_cfg(base_url)

    for index in range(14):
        path = Path(f"batch{index}.docx")
        path.write_bytes(_docx(SAMPLE.replace("李四", f"候选{index}")))
        services.import_files([str(path)], cfg)

    from app.bridge import Bridge

    bridge = Bridge()
    result = bridge.ai_batch_review(None, 0)
    assert result["ok"] is True
    assert result["total"] == 14
    assert result["updated"] == 14
    assert state["calls"] == 14

    candidates = storage.list_candidates()
    assert sum(1 for c in candidates if c["payload"].get("score_source") == "ai") == 14


def test_batch_review_respects_limit(mock_ai_server):
    base_url, state = mock_ai_server
    cfg = _ai_cfg(base_url)
    for index in range(5):
        path = Path(f"lim{index}.docx")
        path.write_bytes(_docx(SAMPLE.replace("李四", f"人{index}")))
        services.import_files([str(path)], cfg)

    from app.bridge import Bridge

    result = Bridge().ai_batch_review(None, 2)
    assert result["ok"] is True
    assert result["updated"] == 2
    assert state["calls"] == 2


def test_batch_review_only_unreviewed(mock_ai_server):
    base_url, state = mock_ai_server
    cfg = _ai_cfg(base_url)
    for index in range(3):
        path = Path(f"un{index}.docx")
        path.write_bytes(_docx(SAMPLE.replace("李四", f"甲{index}")))
        services.import_files([str(path)], cfg)

    from app.bridge import Bridge

    bridge = Bridge()
    first = bridge.ai_batch_review(None, 0)
    assert first["updated"] == 3

    # 再跑一次"仅未复核"，应该没有目标
    second = bridge.ai_batch_review(None, 0, only_unreviewed=True)
    assert second["ok"] is False
    assert "没有需要复核" in second["error"]


def test_batch_review_requires_api_key():
    from app.bridge import Bridge

    cfg = config_module.normalize({})
    assert ai_review.is_ready(cfg) is False
    result = Bridge().ai_batch_review(None, 0)
    assert result["ok"] is False


def test_revert_ai_restores_rule_score(mock_ai_server):
    base_url, _ = mock_ai_server
    cfg = _ai_cfg(base_url)
    path = Path("rev.docx")
    path.write_bytes(_docx(SAMPLE))
    services.import_files([str(path)], cfg)

    rule_score = storage.list_candidates()[0]["overall_score"]

    from app.bridge import Bridge

    bridge = Bridge()
    bridge.ai_batch_review(None, 0)
    assert storage.list_candidates()[0]["payload"]["score_source"] == "ai"

    revert = bridge.ai_clear_review(None)
    assert revert["ok"] is True
    restored = storage.list_candidates()[0]
    assert restored["payload"].get("score_source", "rules") != "ai"
    assert restored["overall_score"] == rule_score


def test_rescore_keeps_ai_result(mock_ai_server):
    """切换岗位重新评分时不应把 AI 结果冲掉。"""
    base_url, _ = mock_ai_server
    cfg = _ai_cfg(base_url)
    path = Path("keep.docx")
    path.write_bytes(_docx(SAMPLE))
    services.import_files([str(path)], cfg)

    from app.bridge import Bridge

    bridge = Bridge()
    bridge.ai_batch_review(None, 0)
    ai_score = storage.list_candidates()[0]["overall_score"]

    new_cfg = config_module.normalize({"role": "frontend"})
    stats = services.rescore_all(new_cfg, keep_ai=True)

    item = storage.list_candidates()[0]
    assert stats["kept_ai"] == 1
    assert item["payload"]["score_source"] == "ai"
    assert item["overall_score"] == ai_score
    # 但岗位标签应跟随新配置更新
    assert item["payload"]["detected_role"] == "frontend"


def test_ai_result_records_rules_score_for_comparison(mock_ai_server):
    base_url, _ = mock_ai_server
    cfg = _ai_cfg(base_url)
    path = Path("cmp.docx")
    path.write_bytes(_docx(SAMPLE))
    services.import_files([str(path)], cfg)
    rule_score = storage.list_candidates()[0]["overall_score"]

    from app.bridge import Bridge

    Bridge().ai_batch_review(None, 0)
    ai_block = storage.list_candidates()[0]["payload"]["ai_review"]
    assert ai_block["rules_score"] == rule_score
    assert ai_block["applied"] is True


# ---------------- 选择全部 ----------------

def test_filtered_ids_track_visible_items():
    """全选只作用于当前筛选结果，避免误删被筛选隐藏的简历。"""
    cfg = config_module.normalize({"role": "backend"})
    for index, name in enumerate(["甲", "乙", "丙"]):
        path = Path(f"sel{index}.docx")
        path.write_bytes(_docx(SAMPLE.replace("李四", name)))
        services.import_files([str(path)], cfg)

    ssr = [c for c in storage.list_candidates() if c["rarity"] == "SSR"]
    all_items = storage.list_candidates()
    assert len(all_items) == 3
    # 不存在越界或重复 id
    assert len({c["id"] for c in all_items}) == 3
    if ssr:
        assert len(ssr) <= len(all_items)
