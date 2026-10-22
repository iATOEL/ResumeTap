"""纯 AI 架构测试：解析层、AI 引擎、评估队列、导入入队、导出。

规则引擎已删除，所有分数断言都通过 mock AI 服务完成。
"""

import base64
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import ai_engine, config as config_module, extractor, parser  # noqa: E402
from app import services, storage  # noqa: E402
from app.eval_queue import QUEUE  # noqa: E402

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


# ---------------------------------------------------------------- fixtures

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
    QUEUE.stop_now()
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


def _sample_file(tmp_path: Path, name: str = "李四.docx", text: str = SAMPLE) -> Path:
    path = tmp_path / name
    path.write_bytes(_docx(text))
    return path


@pytest.fixture()
def mock_ai():
    """本地 mock 的 OpenAI 兼容接口。"""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    state = {"calls": 0, "payloads": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            state["calls"] += 1
            state["payloads"].append(body)

            score = 60 + (state["calls"] % 30)
            payload = {
                "name": "AI候选人",
                "overall_score": score,
                "rarity": "SSR",
                "status": "pass",
                "dimensions": {
                    "skill": {"score": score - 2, "reason": "技能与岗位匹配"},
                    "experience": {"score": score, "reason": "经验满足要求"},
                    "education": {"score": score + 3, "reason": "本科"},
                    "project": {"score": score - 5, "reason": "项目有量化"},
                    "job_match": {"score": score + 1, "reason": "整体契合"},
                },
                "strengths": ["技术栈完整", "项目有量化成果"],
                "weaknesses": ["缺少团队管理经验"],
                "summary": "整体匹配度高",
                "screen_reasons": [],
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


def _ai_config(base_url, **overrides):
    data = {
        "role": "backend",
        "llm": {"enabled": True, "api_key": "test-key", "model": "mock-model",
                "base_url": base_url, "auto_start": True, "timeout": 30},
    }
    data["llm"].update(overrides)
    cfg = config_module.normalize(data)
    config_module.save_config(cfg)
    return config_module.load_config()


# ---------------------------------------------------------------- 解析层

def test_parse_docx_and_contact_extraction():
    ext = extractor.extract(SAMPLE, "lisi.docx")
    assert ext.name == "李四"
    assert ext.phone == "13812345678"
    assert ext.email == "lisi@example.com"


def test_education_entries_from_section():
    text = ("王芳\n教育背景\n2017.09-2021.06  某大学  数学  本科\n"
            "2021.09-2024.06  上海交通大学  计算机  硕士\n")
    ext = extractor.extract(text, "x.docx")
    entries = ext.education_entries
    assert len(entries) == 2
    assert entries[1]["school"] == "上海交通大学"
    assert entries[1]["tier"] == "c9"
    assert entries[1]["degree"] == "硕士"


def test_company_not_treated_as_school():
    """只在工作经历提到清华大学，不能算学历。"""
    text = "李四\n工作经历\n2019.03-至今  清华大学  实验室管理员\n负责设备维护\n"
    ext = extractor.extract(text, "a.docx")
    assert ext.school == ""
    assert ext.degree == "其他"
    assert ext.education_source == "missing"


def test_education_beats_workplace():
    text = ("张三\n工作经历\n2019.03-至今  清华大学  实验室管理员\n"
            "教育背景\n2015.09-2019.06  北京信息职业技术学院  计算机应用  大专\n")
    ext = extractor.extract(text, "a.docx")
    assert ext.school == "北京信息职业技术学院"
    assert ext.degree == "大专"


def test_years_excludes_education_period():
    text = ("张三\n教育背景\n2013.09-2017.06  某大学  本科\n"
            "工作经历\n2019.03-2020.03  某公司  开发工程师\n")
    sections = extractor.detect_sections(text)
    years, _ = extractor.extract_years(text, sections["experience"], sections["education"])
    assert 0.9 <= years <= 1.1


def test_parser_rejects_bad_files():
    with pytest.raises(parser.ParseError):
        parser.parse_bytes(b"", ".docx")
    with pytest.raises(parser.ParseError):
        parser.parse_bytes(b"not a zip", ".docx")
    with pytest.raises(parser.ParseError, match="另存为"):
        parser.parse_bytes(b"data", ".doc")


# ---------------------------------------------------------------- AI 就绪判定

def test_ai_not_ready_without_key(cfg):
    assert ai_engine.is_ready(cfg) is False
    assert "API Key" in ai_engine.readiness_error(cfg) or "未启用" in ai_engine.readiness_error(cfg)


def test_ai_not_ready_when_disabled():
    cfg = config_module.normalize({"llm": {"enabled": False, "api_key": "k"}})
    assert ai_engine.is_ready(cfg) is False


def test_ai_ready_with_key():
    cfg = config_module.normalize({"llm": {"enabled": True, "api_key": "k"}})
    assert ai_engine.is_ready(cfg) is True


# ---------------------------------------------------------------- 提示词

def test_prompt_includes_role_and_rules():
    cfg = config_module.normalize({
        "role": "backend",
        "screening": {"min_years": 3, "must_keywords": ["Java"], "reject_keywords": ["外包"]},
        "rarity": {"ssr": 80, "sr": 60},
    })
    prompt = ai_engine.build_prompt("简历正文", cfg)
    assert "后端开发" in prompt
    assert "3 年" in prompt
    assert "Java" in prompt
    assert "外包" in prompt
    assert "80" in prompt and "60" in prompt
    assert "简历正文" in prompt


def test_prompt_includes_custom_role():
    cfg = config_module.normalize({
        "role": "custom",
        "custom_role": {"label": "短视频编导", "keywords": ["脚本", "剪辑"]},
    })
    prompt = ai_engine.build_prompt("正文", cfg)
    assert "短视频编导" in prompt
    assert "脚本" in prompt


def test_prompt_truncates_long_resume():
    cfg = config_module.normalize({"role": "backend"})
    prompt = ai_engine.build_prompt("字" * 50000, cfg)
    assert len(prompt) < 30000


# ---------------------------------------------------------------- AI 评估

def test_evaluate_returns_ai_result(mock_ai):
    base_url, _ = mock_ai
    cfg = _ai_config(base_url)
    result = ai_engine.evaluate(SAMPLE, cfg)
    assert result["score_source"] == "ai"
    assert 0 <= result["overall_score"] <= 100
    assert len(result["dimensions"]) == 5
    assert result["strengths"]
    assert result["weaknesses"]


def test_evaluate_rarity_uses_local_threshold(mock_ai):
    """稀有度必须按本地阈值算，不能信模型乱填。"""
    base_url, _ = mock_ai
    cfg = _ai_config(base_url)
    cfg["rarity"]["ssr"] = 99
    cfg["rarity"]["sr"] = 98
    result = ai_engine.evaluate(SAMPLE, cfg)
    assert result["rarity"] == "R"


def test_evaluate_requires_config():
    cfg = config_module.normalize({})
    with pytest.raises(RuntimeError):
        ai_engine.evaluate(SAMPLE, cfg)


def test_evaluate_handles_non_json(mock_ai, monkeypatch):
    """模型返回非 JSON 时抛错，不编造分数。"""
    base_url, _ = mock_ai
    cfg = _ai_config(base_url)
    monkeypatch.setattr(ai_engine, "_post", lambda c, p: {"content": "抱歉我无法评估"})
    with pytest.raises(RuntimeError, match="JSON"):
        ai_engine.evaluate(SAMPLE, cfg)


def test_test_connection(mock_ai):
    base_url, _ = mock_ai
    cfg = _ai_config(base_url)
    result = ai_engine.test_connection(cfg)
    assert "overall_score" in result


# ---------------------------------------------------------------- 导入入队

def test_import_does_not_score(tmp_path, cfg):
    """导入只做解析，不产生分数。"""
    services.import_files([str(_sample_file(tmp_path))], cfg)
    item = storage.list_candidates()[0]
    assert item["overall_score"] == 0
    assert item["eval_status"] == "pending"
    assert item["summary"] in ("等待 AI 评估", "")


def test_import_extracts_display_info(tmp_path, cfg):
    services.import_files([str(_sample_file(tmp_path))], cfg)
    detail = storage.get_candidate(storage.list_candidates()[0]["id"])
    payload = detail["payload"]
    assert payload["contact"]["phone"] == "13812345678"
    assert payload["profile"]["education_entries"]
    assert payload["school"]


def test_import_dedup(tmp_path, cfg):
    path = _sample_file(tmp_path)
    services.import_files([str(path)], cfg)
    again = services.import_files([str(path)], cfg)
    assert len(again["duplicates"]) == 1
    assert len(storage.list_candidates()) == 1


def test_import_isolates_failures(tmp_path, cfg):
    good = _sample_file(tmp_path, "good.docx")
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"broken")
    result = services.import_files([str(good), str(bad)], cfg)
    assert len(result["imported"]) == 1
    assert len(result["failures"]) == 1


# ---------------------------------------------------------------- 队列

def test_queue_requires_ai(tmp_path):
    """未配置 AI 时不能开始评估。"""
    cfg = config_module.normalize({})
    services.import_files([str(_sample_file(tmp_path))], cfg)
    from app.bridge import Bridge

    result = Bridge().queue_start()
    assert result["ok"] is False
    assert "AI" in result["error"] or "API Key" in result["error"]


def test_queue_evaluates_all(tmp_path, mock_ai):
    base_url, state = mock_ai
    cfg = _ai_config(base_url)
    for index in range(3):
        services.import_files(
            [str(_sample_file(tmp_path, f"q{index}.docx", SAMPLE.replace("李四", f"人{index}")))],
            cfg)

    assert storage.eval_summary()["pending"] == 3
    QUEUE.start()
    for _ in range(60):
        import time
        time.sleep(0.2)
        if QUEUE.state == "idle":
            break

    summary = storage.eval_summary()
    assert summary["done"] == 3
    assert summary["pending"] == 0
    assert state["calls"] == 3

    items = storage.list_candidates()
    assert all(i["overall_score"] > 0 for i in items)
    assert all(i["eval_status"] == "done" for i in items)


def test_queue_pause_resume_no_duplicate(tmp_path, mock_ai):
    """暂停再恢复，不应重复评估同一份。"""
    import time

    base_url, state = mock_ai
    cfg = _ai_config(base_url)
    for index in range(5):
        services.import_files(
            [str(_sample_file(tmp_path, f"p{index}.docx", SAMPLE.replace("李四", f"甲{index}")))],
            cfg)

    QUEUE.start()
    time.sleep(0.3)
    QUEUE.pause()
    time.sleep(1.2)
    paused_done = storage.eval_summary()["done"]

    QUEUE.start()
    for _ in range(80):
        time.sleep(0.2)
        if QUEUE.state == "idle":
            break

    summary = storage.eval_summary()
    assert summary["done"] == 5
    # 调用次数等于简历数，说明没有重复评估
    assert state["calls"] == 5
    assert paused_done <= 5


def test_queue_records_error_and_continues(tmp_path, mock_ai, monkeypatch):
    """单份失败不应中断整个队列。"""
    import time

    base_url, _ = mock_ai
    cfg = _ai_config(base_url)
    for index in range(3):
        services.import_files(
            [str(_sample_file(tmp_path, f"e{index}.docx", SAMPLE.replace("李四", f"乙{index}")))],
            cfg)

    original = ai_engine.evaluate
    calls = {"n": 0}

    def flaky(text, config):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("模拟接口故障")
        return original(text, config)

    monkeypatch.setattr(ai_engine, "evaluate", flaky)

    QUEUE.start()
    for _ in range(80):
        time.sleep(0.2)
        if QUEUE.state == "idle":
            break

    summary = storage.eval_summary()
    assert summary["done"] == 2
    assert summary["error"] == 1
    failed = [c for c in storage.list_candidates() if c["eval_status"] == "error"]
    assert "模拟接口故障" in failed[0]["eval_error"]


def test_retry_failed_requeues(tmp_path, mock_ai, monkeypatch):
    import time

    base_url, _ = mock_ai
    cfg = _ai_config(base_url)
    services.import_files([str(_sample_file(tmp_path))], cfg)

    monkeypatch.setattr(ai_engine, "evaluate",
                        lambda t, c: (_ for _ in ()).throw(RuntimeError("fail")))
    QUEUE.start()
    for _ in range(40):
        time.sleep(0.2)
        if QUEUE.state == "idle":
            break
    assert storage.eval_summary()["error"] == 1

    monkeypatch.undo()
    from app.bridge import Bridge

    result = Bridge().queue_retry_failed()
    assert result["ok"] is True
    assert storage.eval_summary()["pending"] == 1


def test_requeue_all_resets_scores(tmp_path, mock_ai):
    base_url, _ = mock_ai
    cfg = _ai_config(base_url)
    services.import_files([str(_sample_file(tmp_path))], cfg)

    from app.bridge import Bridge

    Bridge().ai_evaluate_one(storage.list_candidates()[0]["id"])
    assert storage.eval_summary()["done"] == 1

    result = Bridge().queue_requeue_all()
    assert result["ok"] is True
    assert storage.eval_summary()["pending"] == 1


def test_compose_payload_keeps_display_info(tmp_path, mock_ai):
    """AI 结果合并后，联系方式与教育经历仍然保留。"""
    base_url, _ = mock_ai
    cfg = _ai_config(base_url)
    services.import_files([str(_sample_file(tmp_path))], cfg)

    from app.bridge import Bridge

    cid = storage.list_candidates()[0]["id"]
    Bridge().ai_evaluate_one(cid)

    detail = storage.get_candidate(cid)
    payload = detail["payload"]
    assert payload["contact"]["phone"] == "13812345678"
    assert payload["profile"]["education_entries"]
    assert payload["score_source"] == "ai"
    assert payload["dimensions"]


# ---------------------------------------------------------------- 统计与导出

def test_statistics_counts_only_evaluated(tmp_path, mock_ai):
    base_url, _ = mock_ai
    cfg = _ai_config(base_url)
    for index in range(3):
        services.import_files(
            [str(_sample_file(tmp_path, f"s{index}.docx", SAMPLE.replace("李四", f"丙{index}")))],
            cfg)

    from app.bridge import Bridge

    cid = storage.list_candidates()[0]["id"]
    Bridge().ai_evaluate_one(cid)

    stats = services.statistics()
    assert stats["total"] == 3
    assert stats["evaluated"] == 1
    assert stats["avg_score"] > 0


def test_export_csv_has_eval_status(tmp_path, mock_ai):
    base_url, _ = mock_ai
    cfg = _ai_config(base_url)
    services.import_files([str(_sample_file(tmp_path))], cfg)
    result = services.export_candidates("csv", None, cfg, target_dir=str(tmp_path / "out"))
    content = Path(result["path"]).read_text(encoding="utf-8-sig")
    header = content.split("\n")[0]
    assert "评估状态" in header
    assert "AI 模型" in header


def test_export_json_structure(tmp_path, cfg):
    services.import_files([str(_sample_file(tmp_path))], cfg)
    result = services.export_candidates("json", None, cfg, target_dir=str(tmp_path / "out"))
    data = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert data["total"] == 1
    assert "scoring" in data


def test_export_empty_raises(cfg, tmp_path):
    with pytest.raises(ValueError, match="没有可导出"):
        services.export_candidates("csv", None, cfg, target_dir=str(tmp_path))


# ---------------------------------------------------------------- 配置

def test_config_has_no_rule_engine(cfg):
    assert "scoring" not in cfg


def test_config_migrates_old_scoring(tmp_path, monkeypatch):
    """旧配置里的 scoring 段应被清除。"""
    from app import paths

    import yaml
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "scoring": {"weights": {"skill": 0.5}},
        "llm": {"enabled": True, "api_key": "k", "replace_rules": True},
    }), encoding="utf-8")
    monkeypatch.setattr(paths, "config_path", lambda: tmp_path / "config.yaml")

    cfg = config_module.load_config()
    assert "scoring" not in cfg
    assert "auto_start" in cfg["llm"]
    assert "replace_rules" not in cfg["llm"]


def test_rarity_thresholds_configurable():
    cfg = config_module.normalize({"rarity": {"ssr": 90, "sr": 70}})
    assert cfg["rarity"]["ssr"] == 90
    assert cfg["rarity"]["sr"] == 70
    assert config_module.rarity_of(95, cfg) == "SSR"
    assert config_module.rarity_of(75, cfg) == "SR"
    assert config_module.rarity_of(50, cfg) == "R"


# ---------------------------------------------------------------- 岗位切换

def test_save_config_updates_role_label_only(tmp_path, mock_ai):
    """切换岗位不应清掉已有分数，只更新展示标签。"""
    base_url, state = mock_ai
    cfg = _ai_config(base_url)
    services.import_files([str(_sample_file(tmp_path))], cfg)

    from app.bridge import Bridge

    bridge = Bridge()
    cid = storage.list_candidates()[0]["id"]
    bridge.ai_evaluate_one(cid)
    score_before = storage.list_candidates()[0]["overall_score"]
    assert score_before > 0

    # 切到前端岗位
    new_cfg = config_module.normalize({"role": "frontend"})
    result = bridge.save_config(new_cfg)
    assert result["ok"] is True

    item = storage.list_candidates()[0]
    assert item["overall_score"] == score_before      # 分数保留
    assert item["payload"]["detected_role_label"] == "前端开发"


def test_requeue_needed_after_role_change(tmp_path, mock_ai):
    """切岗位后重新排队，才会按新岗位重评。"""
    base_url, state = mock_ai
    cfg = _ai_config(base_url)
    services.import_files([str(_sample_file(tmp_path))], cfg)

    from app.bridge import Bridge

    bridge = Bridge()
    bridge.ai_evaluate_one(storage.list_candidates()[0]["id"])
    assert storage.eval_summary()["done"] == 1

    bridge.save_config(config_module.normalize({"role": "frontend"}))
    assert storage.eval_summary()["done"] == 1   # 仍是已评估

    bridge.queue_requeue_all()
    assert storage.eval_summary()["pending"] == 1


def test_eval_status_not_lost_after_queue(tmp_path, mock_ai):
    """队列跑完后所有记录都必须是 done，不能被重置回 pending。"""
    import time

    base_url, _ = mock_ai
    cfg = _ai_config(base_url)
    for index in range(4):
        services.import_files(
            [str(_sample_file(tmp_path, f"z{index}.docx", SAMPLE.replace("李四", f"丁{index}")))],
            cfg)

    QUEUE.start()
    for _ in range(80):
        time.sleep(0.2)
        if QUEUE.state == "idle":
            break

    items = storage.list_candidates()
    assert len(items) == 4
    assert all(i["eval_status"] == "done" for i in items)
    assert all(i["overall_score"] > 0 for i in items)


# ---------------------------------------------------------------- 回归测试

def test_project_school_not_treated_as_education():
    """项目章节里出现的校名不能算作学历（v3.1.1 修复）。"""
    text = "李四\n项目经历\n华中科技大学 本科 联合培养项目\n负责平台开发\n"
    ext = extractor.extract(text, "a.docx")
    assert ext.school == ""
    assert ext.degree == "其他"
    assert ext.education_source == "missing"


def test_statistics_rates_ignore_unevaluated():
    """未评估的简历只是占位值，不能让 pass_rate / ssr_rate 超过 100%。"""
    storage.insert_candidate(name="甲", filename="a.docx", stored_path="", file_hash="h1",
                             raw_text="x", payload={"overall_score": 0}, eval_status="pending")
    done_id = storage.insert_candidate(
        name="乙", filename="b.docx", stored_path="", file_hash="h2",
        raw_text="x", payload={"overall_score": 90}, eval_status="done")
    storage.update_candidate(
        done_id,
        {"overall_score": 90, "rarity": "SSR", "status": "pass", "summary": "s", "name": "乙"})

    stats = services.statistics()
    assert stats["evaluated"] == 1
    assert stats["pass_rate"] <= 100
    assert stats["ssr_rate"] <= 100
    assert stats["rarity_counts"]["R"] == 0


def test_export_status_marks_pending(tmp_path, cfg):
    """未评估的简历导出时不能显示为"通过"。"""
    services.import_files([str(_sample_file(tmp_path))], cfg)
    result = services.export_candidates("csv", None, cfg, target_dir=str(tmp_path / "out"))
    content = Path(result["path"]).read_text(encoding="utf-8-sig")
    row = content.split("\n")[1]
    assert "待评估" in row
    assert "通过" not in row


def test_export_ai_model_column(tmp_path, mock_ai):
    """CSV 的 AI 模型列应取自已保存的 payload。"""
    base_url, _ = mock_ai
    cfg = _ai_config(base_url)
    services.import_files([str(_sample_file(tmp_path))], cfg)

    from app.bridge import Bridge

    Bridge().ai_evaluate_one(storage.list_candidates()[0]["id"])
    result = services.export_candidates("csv", None, cfg, target_dir=str(tmp_path / "out"))
    content = Path(result["path"]).read_text(encoding="utf-8-sig")
    assert "mock-model" in content


def test_markdown_omits_zero_weight(tmp_path, mock_ai):
    """规则引擎已删除，报告里不应再出现恒为 0% 的权重。"""
    base_url, _ = mock_ai
    cfg = _ai_config(base_url)
    services.import_files([str(_sample_file(tmp_path))], cfg)

    from app.bridge import Bridge

    Bridge().ai_evaluate_one(storage.list_candidates()[0]["id"])
    result = services.export_candidates("md", None, cfg, target_dir=str(tmp_path / "out"))
    content = Path(result["path"]).read_text(encoding="utf-8")
    assert "权重" not in content
