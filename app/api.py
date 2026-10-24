"""本地 AI 审查接口：仅供本机程序读取的结构化 JSON API。

默认只监听 127.0.0.1，且必须携带启动时生成的令牌。
用途：让外部 AI / Agent 读取简历结构化数据与评分，给出审查意见。
简历原文默认不返回，需显式请求且配置允许。
"""

import hmac
import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import services, storage
from .paths import APP_VERSION

log = logging.getLogger("resume_gacha.api")

DEFAULT_PORT = 8765


class ApiState:
    def __init__(self) -> None:
        self.token = secrets.token_urlsafe(24)
        self.port = DEFAULT_PORT
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.server is not None

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


STATE = ApiState()


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _candidate_brief(item: dict, include_sections: bool = False) -> dict:
    """对外输出的候选人结构：包含评分理由与优缺点，默认不含隐私原文。"""
    payload = item.get("payload") or item.get("payload_full") or {}
    brief = {
        "id": item["id"],
        "name": item["name"],
        "filename": item["filename"],
        "score": item["overall_score"],
        "rarity": item["rarity"],
        "status": item["status"],
        "review_verdict": item.get("review_verdict", ""),
        "review_note": item.get("review_note", ""),
        "years": item.get("years", 0),
        "degree": item.get("degree", ""),
        "school": item.get("school", ""),
        "school_tier": payload.get("school_tier", ""),
        "school_tier_label": payload.get("school_tier_label", ""),
        "detected_role": payload.get("detected_role", ""),
        "detected_role_label": payload.get("detected_role_label", ""),
        "summary": item.get("summary", ""),
        "strengths": payload.get("strengths", []),
        "weaknesses": payload.get("weaknesses", []),
        "news_flash": payload.get("news_flash", ""),
        "screen_reasons": item.get("screen_reasons", []),
        "dimensions": [
            {
                "key": dim.get("key"),
                "label": dim.get("label"),
                "score": dim.get("score"),
                "weight": dim.get("weight"),
                "reason": dim.get("detail"),
                "available": dim.get("available", True),
            }
            for dim in item.get("dimensions", [])
        ],
        "skills": [
            {"name": hit.get("term"), "category": hit.get("category"),
             "count": hit.get("count")}
            for hit in item.get("skill_hits", [])
        ],
        "contact": payload.get("contact", {}),
        "education_entries": (payload.get("profile") or {}).get("education_entries", []),
        "score_source": payload.get("score_source", ""),
        "ai_model": payload.get("ai_model", ""),
        "eval_status": item.get("eval_status", "pending"),
        "eval_error": item.get("eval_error", ""),
        "created_at": item.get("created_at", ""),
    }
    if include_sections:
        brief["sections"] = payload.get("sections", {})
    return brief


class _Handler(BaseHTTPRequestHandler):
    server_version = f"ResumeGachaAPI/{APP_VERSION}"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        log.info("api %s - %s", self.address_string(), fmt % args)

    # ---------- 鉴权 ----------
    def _authorized(self) -> bool:
        token = STATE.token
        header = self.headers.get("X-API-Token", "")
        if header and hmac.compare_digest(header, token):
            return True
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return hmac.compare_digest(auth[7:].strip(), token)
        return False

    def _guard(self) -> bool:
        if self._authorized():
            return True
        _json_response(self, 401, {
            "error": "unauthorized",
            "message": "缺少或错误的令牌。请在请求头加入 X-API-Token（令牌见应用设置页）。",
        })
        return False

    # ---------- 路由 ----------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if path == "/health":
            _json_response(self, 200, {
                "ok": True,
                "app": "resume-gacha",
                "version": APP_VERSION,
                "auth_required": True,
            })
            return

        if not self._guard():
            return

        try:
            if path == "/":
                _json_response(self, 200, self._describe())
            elif path == "/candidates":
                _json_response(self, 200, self._list_candidates(query))
            elif path.startswith("/candidates/"):
                candidate_id = int(path.rsplit("/", 1)[-1])
                _json_response(self, 200, self._one(candidate_id, query))
            elif path == "/config":
                from . import config as config_module
                _json_response(self, 200, {"config": config_module.load_config()})
            elif path == "/stats":
                _json_response(self, 200, {
                    "stats": services.statistics(),
                    "review": storage.review_summary(),
                })
            elif path == "/export":
                fmt = (query.get("format", ["json"])[0] or "json").lower()
                result = services.export_candidates(fmt, None, _config())
                _json_response(self, 200, result)
            else:
                _json_response(self, 404, {"error": "not_found", "path": path})
        except ValueError as exc:
            _json_response(self, 400, {"error": "bad_request", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            log.exception("API 处理失败 %s", path)
            _json_response(self, 500, {"error": "internal", "message": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if not self._guard():
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            _json_response(self, 400, {"error": "invalid_json"})
            return

        try:
            if path == "/review":
                _json_response(self, 200, self._set_review(body))
            elif path == "/review/batch":
                _json_response(self, 200, self._set_review_batch(body))
            else:
                _json_response(self, 404, {"error": "not_found", "path": path})
        except ValueError as exc:
            _json_response(self, 400, {"error": "bad_request", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            log.exception("API 写入失败 %s", path)
            _json_response(self, 500, {"error": "internal", "message": str(exc)})

    # ---------- 具体实现 ----------
    def _describe(self) -> dict:
        return {
            "name": "ResumeTap · 本地 AI 审查 API",
            "version": APP_VERSION,
            "auth": "在请求头加入 X-API-Token: <token>",
            "endpoints": {
                "GET /health": "健康检查（无需令牌）",
                "GET /": "接口说明",
                "GET /candidates": "候选人列表，支持 ?rarity=SSR&min_score=70&pending=1&sections=1",
                "GET /candidates/{id}": "单个候选人完整评分与优缺点",
                "GET /stats": "统计与速筛进度",
                "GET /config": "当前岗位、阈值与筛选规则",
                "GET /export?format=json|csv|md": "导出并返回文件路径",
                "POST /review": '{"id":1,"verdict":"liked|passed|skipped","note":"..."}',
                "POST /review/batch": '{"items":[{"id":1,"verdict":"liked"}]}',
            },
            "privacy": {
                "raw_text_available": False,
                "note": "接口不返回简历原文；联系方式随候选人记录返回，若需脱敏请自行处理。",
            },
        }

    def _list_candidates(self, query: dict) -> dict:
        include_sections = query.get("sections", ["0"])[0] in {"1", "true", "yes"}
        pending_only = query.get("pending", ["0"])[0] in {"1", "true", "yes"}
        rarity = (query.get("rarity", [""])[0] or "").upper()
        min_score = float(query.get("min_score", ["0"])[0] or 0)
        limit = min(500, max(1, int(query.get("limit", ["100"])[0] or 100)))

        items = storage.list_pending() if pending_only else storage.list_candidates(full=True)
        if rarity in {"R", "SR", "SSR"}:
            items = [i for i in items if i["rarity"] == rarity]
        if min_score > 0:
            items = [i for i in items if i["overall_score"] >= min_score]

        result = []
        for item in items[:limit]:
            detail = storage.get_candidate(item["id"], include_raw=False) or item
            result.append(_candidate_brief(detail, include_sections))
        return {"total": len(items), "returned": len(result), "candidates": result}

    def _one(self, candidate_id: int, query: dict) -> dict:
        detail = storage.get_candidate(candidate_id, include_raw=True)
        if not detail:
            raise ValueError("候选人不存在")
        include_sections = query.get("sections", ["1"])[0] not in {"0", "false"}
        data = _candidate_brief(detail, include_sections)
        data["projects"] = (detail.get("payload") or {}).get("projects", [])
        return {"candidate": data}

    def _set_review(self, body: dict) -> dict:
        candidate_id = int(body.get("id") or 0)
        verdict = str(body.get("verdict") or "").strip()
        if verdict not in {"liked", "passed", "skipped"}:
            raise ValueError("verdict 必须是 liked / passed / skipped")
        if not storage.set_review(candidate_id, verdict, str(body.get("note") or "")):
            raise ValueError("候选人不存在")
        return {"ok": True, "id": candidate_id, "verdict": verdict,
                "review": storage.review_summary()}

    def _set_review_batch(self, body: dict) -> dict:
        items = body.get("items") or []
        if not isinstance(items, list):
            raise ValueError("items 必须是数组")
        updated = 0
        for entry in items:
            if not isinstance(entry, dict):
                continue
            verdict = str(entry.get("verdict") or "").strip()
            if verdict not in {"liked", "passed", "skipped"}:
                continue
            if storage.set_review(int(entry.get("id") or 0), verdict,
                                  str(entry.get("note") or "")):
                updated += 1
        return {"ok": True, "updated": updated, "review": storage.review_summary()}


def _config() -> dict:
    from . import config as config_module
    return config_module.load_config()


def start(port: int = DEFAULT_PORT) -> dict:
    """启动本地 API。端口被占用时自动顺延。"""
    if STATE.running:
        return {"running": True, "port": STATE.port, "token": STATE.token}

    last_error = None
    for candidate_port in range(port, port + 12):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate_port), _Handler)
        except OSError as exc:
            last_error = exc
            continue
        STATE.server = server
        STATE.port = candidate_port
        STATE.thread = threading.Thread(target=server.serve_forever, daemon=True,
                                        name="resume-gacha-api")
        STATE.thread.start()
        log.info("本地 AI 审查 API 已启动：%s", STATE.base_url())
        return {"running": True, "port": candidate_port, "token": STATE.token}

    raise RuntimeError(f"无法启动本地 API：{last_error}")


def stop() -> None:
    if STATE.server:
        STATE.server.shutdown()
        STATE.server.server_close()
        STATE.server = None
        STATE.thread = None


def status() -> dict:
    return {
        "running": STATE.running,
        "port": STATE.port,
        "base_url": STATE.base_url() if STATE.running else "",
        "token": STATE.token,
    }
