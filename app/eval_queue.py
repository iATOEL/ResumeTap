"""AI 评估队列：导入后入队，后台逐份调用 AI，支持开始 / 暂停 / 重试。

状态机：
    pending   —— 已导入，等待 AI 评估
    running   —— 队列正在工作
    paused    —— 用户点了暂停
    error     —— 该份评估失败（可重试）

队列本身是内存态；"哪些简历还没评估"由数据库的 eval_status 列决定，
因此重启应用后未评估的简历会自动回到队列。
"""

import logging
import threading
import time
from typing import Callable, Optional

from . import ai_engine, storage

log = logging.getLogger("resume_gacha.queue")

# 队列状态
IDLE = "idle"
RUNNING = "running"
PAUSED = "paused"


class EvalQueue:
    """单线程串行评估，避免并发打爆接口限流。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._state = IDLE
        self._current: Optional[dict] = None       # {id, name}
        self._finished = 0
        self._failed = 0
        self._last_error = ""
        self._listener: Optional[Callable[[dict], None]] = None
        self._worker: Optional[threading.Thread] = None

    # ---------------- 对外接口 ----------------

    def set_listener(self, listener: Callable[[dict], None]) -> None:
        """注册状态推送回调（前端进度）。"""
        self._listener = listener

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def snapshot(self) -> dict:
        with self._lock:
            pending = storage.count_by_eval_status("pending")
            failed = storage.count_by_eval_status("error")
            return {
                "state": self._state,
                "current": self._current,
                "finished": self._finished,
                "failed": self._failed,
                "last_error": self._last_error,
                "pending": pending,
                "errored": failed,
                "running": bool(self._worker and self._worker.is_alive()),
                "eval": storage.eval_summary(),
            }

    def start(self) -> dict:
        """开始（或从暂停恢复）评估。"""
        with self._lock:
            if self._state == RUNNING and self._worker and self._worker.is_alive():
                return self.snapshot()

            # 保证上一个工作线程已经退出，避免两条线程同时消费队列
            old = self._worker
            self._stop.set()
            self._wake.set()
        if old and old.is_alive():
            old.join(timeout=5)

        with self._lock:
            self._stop.clear()
            self._wake.clear()
            self._state = RUNNING
            self._worker = threading.Thread(target=self._run_loop, daemon=True,
                                            name="ai-eval-queue")
            self._worker.start()
        self._emit()
        return self.snapshot()

    def pause(self) -> dict:
        """暂停：当前这份做完后停下。"""
        with self._lock:
            if self._state == RUNNING:
                self._state = PAUSED
                self._stop.set()
                self._wake.set()
        self._emit()
        return self.snapshot()

    def stop_now(self) -> None:
        """退出应用时强制停止。"""
        with self._lock:
            self._state = IDLE
            self._stop.set()
            self._wake.set()

    def kick(self) -> None:
        """有新任务入队时唤醒工作线程。"""
        self._wake.set()

    # ---------------- 内部 ----------------

    def _emit(self) -> None:
        if not self._listener:
            return
        try:
            snapshot = self.snapshot()
            snapshot["eval"] = storage.eval_summary()
            self._listener(snapshot)
        except Exception:  # noqa: BLE001
            log.exception("进度回调失败")

    def _claim_next(self) -> Optional[dict]:
        """原子领取一条待评估任务（标记为 running，防止重复消费）。"""
        with self._lock:
            # 只处理 pending；error 需要用户点"重试失败"才会重新入队，
            # 否则接口持续故障时会陷入无限重试
            rows = storage.list_by_eval_status("pending", limit=1)
            if not rows:
                return None
            item = rows[0]
            storage.set_eval_status(item["id"], "running", "")
            return item

    def _run_loop(self) -> None:
        from . import config as config_module

        while not self._stop.is_set():
            item = self._claim_next()
            if not item:
                with self._lock:
                    self._state = IDLE
                    self._current = None
                self._emit()
                return

            cfg = config_module.load_config()
            if not ai_engine.is_ready(cfg):
                with self._lock:
                    self._state = PAUSED
                    self._last_error = ai_engine.readiness_error(cfg)
                    self._current = None
                self._emit()
                return

            with self._lock:
                self._current = {"id": item["id"], "name": item["name"]}
            self._emit()

            try:
                detail = storage.get_candidate(item["id"], include_raw=True)
                raw_text = (detail or {}).get("raw_text") or ""
                if not raw_text.strip():
                    raise RuntimeError("简历原文未保存，无法评估")

                result = ai_engine.evaluate(raw_text, cfg)
                payload = _compose_payload(item["id"], detail or {}, result)
                storage.update_candidate(item["id"], payload)
                storage.set_eval_status(item["id"], "done", "")
                with self._lock:
                    self._finished += 1
                    self._last_error = ""
            except Exception as exc:  # noqa: BLE001
                message = _humanize(exc)
                log.warning("AI 评估失败 id=%s: %s", item["id"], message)
                storage.set_eval_status(item["id"], "error", message)
                with self._lock:
                    self._failed += 1
                    self._last_error = message

            self._emit()
            # 短暂间隔，避免瞬间打满接口
            self._wake.wait(timeout=0.2)
            self._wake.clear()

        # 退出循环时，把因暂停/关闭而中断的 running 记录还原，下次可继续
        storage.reset_running_to_pending()
        with self._lock:
            if self._state == RUNNING:
                self._state = IDLE
            self._current = None
        self._emit()


def _humanize(exc: Exception) -> str:
    """把底层异常翻译成用户能看懂的提示。"""
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return "API Key 无效或没有权限"
        if status == 429:
            return "接口限流或额度不足"
        if status >= 500:
            return f"AI 服务端错误（HTTP {status}）"
        return f"AI 接口返回错误（HTTP {status}）"
    if isinstance(exc, httpx.TimeoutException):
        return "AI 接口超时"
    if isinstance(exc, httpx.RequestError):
        return "无法连接 AI 接口，请检查地址与网络"
    return str(exc)[:160] or "未知错误"


def _compose_payload(candidate_id: int, detail: dict, result: dict) -> dict:
    """把 AI 结果与客观解析信息合成最终存储结构。"""
    from . import extractor

    raw_text = detail.get("raw_text") or ""
    extracted = extractor.extract(raw_text, detail.get("filename", ""))

    return {
        "name": detail.get("name") or extracted.name,
        "filename": detail.get("filename", ""),
        "overall_score": result["overall_score"],
        "rarity": result["rarity"],
        "rarity_label": result["rarity_label"],
        "status": result["status"],
        "screen_reasons": result["screen_reasons"],
        "strengths": result["strengths"],
        "weaknesses": result["weaknesses"],
        "summary": result["summary"],
        "news_flash": result["news_flash"],
        "dimensions": result["dimensions"],
        "score_source": "ai",
        "ai_model": result.get("ai_model", ""),
        "evaluated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        # 客观信息（用于展示与导出，不参与打分）
        "profile": extracted.to_summary_dict(),
        "sections": {
            "education": extracted.education_text,
            "experience": extracted.experience_text,
            "projects": extracted.project_text,
            "skills": extracted.skill_text,
            "summary": extracted.summary_text,
            "awards": extracted.awards_text,
        },
        "projects": [{"title": p.title, "description": p.description}
                     for p in extracted.projects[:10]],
        "years": extracted.years,
        "years_source": extracted.years_source,
        "degree": extracted.degree,
        "school": extracted.school,
        "school_tier": extracted.school_tier,
        "school_tier_label": extracted.school_tier_label,
        "major": extracted.major,
        "contact": {"phone": extracted.phone, "email": extracted.email},
        "role_title": extracted.role_title,
        "detected_role": "",
        "detected_role_label": _role_label_from_config(),
        "role_selected": True,
        "education_source": extracted.education_source,
    }


def _role_label_from_config() -> str:
    from . import config as config_module

    cfg = config_module.load_config()
    role = str(cfg.get("role") or "").strip()
    if role == "custom":
        return str((cfg.get("custom_role") or {}).get("label") or "自定义岗位")
    if role == "auto":
        return "自动识别"
    from .rules import ROLE_PROFILES

    return ROLE_PROFILES.get(role, {}).get("label", "未指定岗位")


QUEUE = EvalQueue()
