"""pywebview JS 桥：前端通过 window.pywebview.api 调用这些方法。"""

import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx
import webview

from . import ai_engine, api, config as config_module
from . import eval_queue
from . import paths, services, storage
from .eval_queue import QUEUE
from .rules import ROLE_GROUPS, ROLE_LABELS

log = logging.getLogger("resume_gacha")


class Bridge:
    """所有方法必须返回可 JSON 序列化的数据，异常统一转成 {ok: False, error}。"""

    def __init__(self) -> None:
        self._window: Optional[webview.Window] = None
        self._lock = threading.Lock()

    # ---------- 基础 ----------

    def attach(self, window: webview.Window) -> None:
        self._window = window
        # 队列状态变化时把快照推给前端
        QUEUE.set_listener(self._on_queue_update)
        self._last_queue_emit = 0.0

    def _on_queue_update(self, snapshot: dict) -> None:
        if not self._window:
            return
        # 每份结束都刷新，但限制频率避免刷爆 UI
        now = time.time()
        state = snapshot.get("state")
        if state == eval_queue.RUNNING and now - self._last_queue_emit < 0.35:
            return
        self._last_queue_emit = now
        try:
            self._window.evaluate_js(
                "window.__queueUpdate && window.__queueUpdate(" + _to_js(snapshot) + ")")
        except Exception:  # noqa: BLE001
            pass

    def _ok(self, **data) -> dict:
        return {"ok": True, **data}

    def _err(self, message: str) -> dict:
        return {"ok": False, "error": message}

    @staticmethod
    def _config() -> dict:
        return config_module.load_config()

    # ---------- 启动状态 ----------

    def bootstrap(self) -> dict:
        try:
            cfg = self._config()
            candidates = storage.list_candidates()
            return self._ok(
                config=cfg,
                stats=services.statistics(),
                roles=[{"key": k, "label": v} for k, v in ROLE_LABELS.items()],
                role_groups=[
                    {"label": label, "keys": keys} for label, keys in ROLE_GROUPS.items()
                ],
                meta={
                    "version": paths.APP_VERSION,
                    "data_dir": str(paths.data_dir()),
                    "app_dir": str(paths.app_dir()),
                    "portable": paths.is_portable(),
                    "config_path": str(paths.config_path()),
                    "log_path": str(paths.logs_dir() / "app.log"),
                    "api": api.status(),
                    "has_candidates": len(candidates) > 0,
                },
                review=storage.review_summary(),
                eval=storage.eval_summary(),
                queue=QUEUE.snapshot(),
                candidates=candidates,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("初始化失败")
            return self._err(f"初始化失败：{exc}")

    # ---------- 速筛 ----------

    def next_candidate(self, skip_reviewed: bool = True) -> dict:
        """取下一份待评价简历（已评估的按分数排序，未评估的排后面）。"""
        try:
            pool = storage.list_pending() if skip_reviewed else storage.list_candidates()
            if not pool:
                return self._ok(candidate=None, review=storage.review_summary())
            item = pool[0]
            detail = storage.get_candidate(item["id"], include_raw=True) or {}
            payload = detail.get("payload") or {}
            return self._ok(
                candidate={**item, "payload": payload},
                remaining=len(pool),
                review=storage.review_summary(),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("读取待速筛简历失败")
            return self._err(f"读取失败：{exc}")

    def review_candidate(self, candidate_id: int, verdict: str, note: str = "") -> dict:
        """记录速筛决定：liked / passed / skipped。"""
        if verdict not in {"liked", "passed", "skipped"}:
            return self._err("无效的评价类型")
        try:
            if not storage.set_review(int(candidate_id), verdict, note or ""):
                return self._err("简历不存在或已被删除")
            return self._ok(
                review=storage.review_summary(),
                stats=services.statistics(),
                candidates=storage.list_candidates(),
            )
        except Exception as exc:  # noqa: BLE001
            return self._err(f"保存评价失败：{exc}")

    def reset_reviews(self) -> dict:
        try:
            with storage.session() as conn:
                conn.execute("UPDATE candidates SET review_verdict='', review_note='', reviewed_at=''")
            return self._ok(review=storage.review_summary(),
                            candidates=storage.list_candidates())
        except Exception as exc:  # noqa: BLE001
            return self._err(f"重置失败：{exc}")

    # ---------- 本地 AI 审查 API ----------

    def api_status(self) -> dict:
        return self._ok(api=api.status())

    def api_start(self, port: int = 8765) -> dict:
        try:
            info = api.start(int(port) or 8765)
            return self._ok(api={**info, "base_url": f"http://127.0.0.1:{info['port']}"})
        except Exception as exc:  # noqa: BLE001
            log.exception("启动本地 API 失败")
            return self._err(f"启动失败：{exc}")

    def api_stop(self) -> dict:
        try:
            api.stop()
            return self._ok(api=api.status())
        except Exception as exc:  # noqa: BLE001
            return self._err(f"停止失败：{exc}")

    def api_regenerate_token(self) -> dict:
        try:
            api.STATE.token = __import__("secrets").token_urlsafe(24)
            return self._ok(api=api.status())
        except Exception as exc:  # noqa: BLE001
            return self._err(f"重置令牌失败：{exc}")

    # ---------- AI 评估（纯 AI 驱动） ----------

    def ai_status(self) -> dict:
        try:
            cfg = self._config()
            llm = cfg.get("llm", {})
            return self._ok(
                ready=ai_engine.is_ready(cfg),
                enabled=bool(llm.get("enabled")),
                has_key=bool(str(llm.get("api_key") or "").strip()),
                model=llm.get("model", ""),
                base_url=llm.get("base_url", ""),
                reason="" if ai_engine.is_ready(cfg) else ai_engine.readiness_error(cfg),
                queue=QUEUE.snapshot(),
                eval=storage.eval_summary(),
            )
        except Exception as exc:  # noqa: BLE001
            return self._err(f"读取 AI 状态失败：{exc}")

    def ai_test_connection(self) -> dict:
        try:
            cfg = self._config()
            result = ai_engine.test_connection(cfg)
            return self._ok(
                message=(f"连接正常，模型可用（测试简历评分 {result['overall_score']}）"),
            )
        except Exception as exc:  # noqa: BLE001
            return self._err(f"连接失败：{_humanize_ai_error(exc)}")

    def queue_start(self) -> dict:
        """开始评估队列。未配置 AI 时拒绝。"""
        try:
            cfg = self._config()
            if not ai_engine.is_ready(cfg):
                return self._err(ai_engine.readiness_error(cfg))
            QUEUE.start()
            return self._ok(queue=QUEUE.snapshot(), eval=storage.eval_summary())
        except Exception as exc:  # noqa: BLE001
            return self._err(f"启动失败：{exc}")

    def queue_pause(self) -> dict:
        try:
            QUEUE.pause()
            return self._ok(queue=QUEUE.snapshot(), eval=storage.eval_summary())
        except Exception as exc:  # noqa: BLE001
            return self._err(f"暂停失败：{exc}")

    def queue_status(self) -> dict:
        try:
            return self._ok(queue=QUEUE.snapshot(), eval=storage.eval_summary(),
                            candidates=storage.list_candidates(),
                            stats=services.statistics())
        except Exception as exc:  # noqa: BLE001
            return self._err(f"读取队列状态失败：{exc}")

    def queue_retry_failed(self) -> dict:
        """把评估失败的简历重新排队。"""
        try:
            result = services.requeue_all(self._config(), only_failed=True)
            if QUEUE.state == eval_queue.RUNNING:
                QUEUE.kick()
            return self._ok(**result, queue=QUEUE.snapshot(),
                            eval=storage.eval_summary(),
                            candidates=storage.list_candidates())
        except Exception as exc:  # noqa: BLE001
            return self._err(f"重试失败：{exc}")

    def queue_requeue_all(self) -> dict:
        """把所有简历重新排队（改岗位/改规则后用）。"""
        try:
            result = services.requeue_all(self._config(), only_failed=False)
            return self._ok(**result, queue=QUEUE.snapshot(),
                            eval=storage.eval_summary(),
                            candidates=storage.list_candidates())
        except Exception as exc:  # noqa: BLE001
            return self._err(f"重新排队失败：{exc}")

    def ai_evaluate_one(self, candidate_id: int) -> dict:
        """立即评估指定的一份（不经过队列，用于详情页重试）。"""
        try:
            candidate_id = int(candidate_id)
            # 队列正在处理这一份时拒绝，否则会重复调用 AI、双方互相覆盖结果
            current = QUEUE.snapshot().get("current") or {}
            if current.get("id") == candidate_id:
                return self._err("队列正在评估这份简历，请稍候")

            cfg = self._config()
            if not ai_engine.is_ready(cfg):
                return self._err(ai_engine.readiness_error(cfg))

            detail = storage.get_candidate(candidate_id, include_raw=True)
            if not detail:
                return self._err("简历不存在或已被删除")
            raw_text = detail.get("raw_text") or ""
            if not raw_text.strip():
                return self._err("该简历未保存原文，无法评估")

            # 先占位再调用：队列只领 pending，不会与本调用撞车
            storage.set_eval_status(candidate_id, "running", "")
            try:
                result = ai_engine.evaluate(raw_text, cfg)
            except Exception as exc:  # noqa: BLE001
                message = _humanize_ai_error(exc)
                storage.set_eval_status(candidate_id, "error", message)
                return self._err(message)

            payload = eval_queue._compose_payload(candidate_id, detail, result)
            storage.update_candidate(candidate_id, payload)
            storage.set_eval_status(candidate_id, "done", "")

            return self._ok(
                candidate=storage.get_candidate(int(candidate_id), include_raw=True),
                candidates=storage.list_candidates(),
                stats=services.statistics(),
                eval=storage.eval_summary(),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("单份评估失败")
            return self._err(f"评估失败：{exc}")

    def get_candidate_detail(self, candidate_id: int) -> dict:
        try:
            data = services.get_candidate_detail(int(candidate_id), full=True)
            if not data:
                return self._err("简历不存在或已被删除")
            return self._ok(candidate=data)
        except Exception as exc:  # noqa: BLE001
            return self._err(f"读取详情失败：{exc}")

    def list_candidates(self) -> dict:
        try:
            return self._ok(
                candidates=storage.list_candidates(),
                stats=services.statistics(),
                review=storage.review_summary(),
            )
        except Exception as exc:  # noqa: BLE001
            return self._err(f"读取列表失败：{exc}")

    # ---------- 导入 ----------

    def pick_files(self) -> dict:
        if not self._window:
            return self._err("窗口尚未就绪")
        try:
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=True,
                file_types=(
                    "简历文件 (*.pdf;*.docx;*.txt;*.md)",
                    "PDF (*.pdf)",
                    "Word 文档 (*.docx)",
                    "所有文件 (*.*)",
                ),
            )
            if not result:
                return self._ok(paths=[])
            return self._ok(paths=list(result))
        except Exception as exc:  # noqa: BLE001
            log.exception("打开文件对话框失败")
            return self._err(f"无法打开文件选择框：{exc}")

    def pick_folder(self) -> dict:
        if not self._window:
            return self._err("窗口尚未就绪")
        try:
            result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
            if not result:
                return self._ok(path=None)
            folder = result[0] if isinstance(result, (list, tuple)) else result
            return self._ok(path=str(folder))
        except Exception as exc:  # noqa: BLE001
            return self._err(f"无法打开文件夹选择框：{exc}")

    def scan_folder(self, folder: str) -> dict:
        try:
            base = Path(folder)
            if not base.is_dir():
                return self._err("目录不存在")
            files = [
                str(p) for p in sorted(base.rglob("*"))
                if p.is_file() and p.suffix.lower() in {".pdf", ".docx", ".txt", ".md"}
            ]
            return self._ok(paths=files[:500], total=len(files))
        except Exception as exc:  # noqa: BLE001
            return self._err(f"扫描目录失败：{exc}")

    def import_files(self, file_paths: list[str]) -> dict:
        """同步导入（前端放在 await 中调用，期间可显示进度）。"""
        if not isinstance(file_paths, list) or not file_paths:
            return self._err("没有选择文件")
        with self._lock:
            try:
                cfg = self._config()
                result = services.import_files(file_paths, cfg, progress=self._progress)
            except Exception as exc:  # noqa: BLE001
                log.exception("批量导入失败")
                return self._err(f"导入失败：{exc}")

        result["stats"] = services.statistics()
        result["candidates"] = storage.list_candidates()
        result["review"] = storage.review_summary()
        result["eval"] = storage.eval_summary()
        result["queue"] = QUEUE.snapshot()

        # 若用户已开启"自动开始"，导入后立即唤醒队列
        try:
            if cfg.get("llm", {}).get("auto_start", True) and ai_engine.is_ready(cfg):
                if QUEUE.state == eval_queue.RUNNING:
                    QUEUE.kick()
                elif QUEUE.state == eval_queue.IDLE:
                    QUEUE.start()
                    result["queue"] = QUEUE.snapshot()
        except Exception:  # noqa: BLE001
            log.exception("自动启动队列失败")

        return self._ok(**result)

    def _progress(self, payload: dict) -> None:
        if not self._window:
            return
        try:
            self._window.evaluate_js(
                "window.__importProgress && window.__importProgress(" +
                _to_js(payload) + ")"
            )
        except Exception:  # noqa: BLE001 - 进度更新失败不影响导入
            pass

    def import_bytes(self, filename: str, data_b64: str) -> dict:
        """拖拽/无法取路径时的导入通道：前端读字节，这里落盘为临时文件后正常导入。

        使用 base64 而不是 int 数组：一份 3MB 的 PDF 转成 JSON 数字数组会膨胀
        到 20MB+ 并卡住主线程。
        """
        try:
            import base64
            import tempfile

            if not filename:
                return self._err("文件名缺失")
            suffix = Path(filename).suffix.lower()
            if suffix not in {".pdf", ".docx", ".txt", ".md"}:
                return self._err(f"不支持的文件格式 {suffix or '（无扩展名）'}")

            raw = base64.b64decode(data_b64 or "", validate=False)
            if not raw:
                return self._err("文件内容为空")
            if len(raw) > 25 * 1024 * 1024:
                return self._err("文件超过 25MB 限制")

            # 建一个持久临时目录（不随 with 退出删除），导入完成后由 purge 清理
            staging = paths.data_dir() / "staging"
            staging.mkdir(parents=True, exist_ok=True)
            safe_name = Path(filename).name.replace("/", "_").replace("\\", "_")
            tmp_path = staging / f"drop_{uuid.uuid4().hex[:8]}_{safe_name}"
            tmp_path.write_bytes(raw)

            try:
                result = self.import_files([str(tmp_path)])
                # 失败信息里把临时文件名还原成用户看到的原名
                for failure in result.get("failures") or []:
                    if failure.get("filename") == tmp_path.name:
                        failure["filename"] = Path(filename).name
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

            # 临时文件名会污染展示，替换回原始文件名
            imported = result.get("imported") or []
            if imported:
                for item in imported:
                    storage.rename_filename(item["id"], Path(filename).name)
                result["candidates"] = storage.list_candidates()

            return result
        except Exception as exc:  # noqa: BLE001
            log.exception("拖拽导入失败")
            return self._err(f"导入失败：{exc}")

    def load_samples(self) -> dict:
        """载入随包分发的示例简历。"""
        try:
            sample_dir = paths.samples_dir()
            if not sample_dir.is_dir():
                return self._err("未找到示例简历目录")
            files = sorted(
                str(p) for p in sample_dir.iterdir()
                if p.is_file() and p.suffix.lower() in {".pdf", ".docx", ".txt", ".md"}
            )
            return self._ok(paths=files, total=len(files))
        except Exception as exc:  # noqa: BLE001
            return self._err(f"读取示例简历失败：{exc}")

    # ---------- 详情 ----------

    def get_candidate(self, candidate_id: int) -> dict:
        try:
            data = storage.get_candidate(int(candidate_id))
            if not data:
                return self._err("简历不存在或已被删除")
            return self._ok(candidate=data)
        except Exception as exc:  # noqa: BLE001
            return self._err(f"读取详情失败：{exc}")

    def delete_candidates(self, candidate_ids: list[int]) -> dict:
        try:
            removed = 0
            for raw_id in candidate_ids or []:
                stored = storage.delete_candidate(int(raw_id))
                if stored is None:
                    continue
                if stored:
                    try:
                        Path(stored).unlink(missing_ok=True)
                    except OSError:
                        pass
                removed += 1
            return self._ok(removed=removed, candidates=storage.list_candidates(),
                            stats=services.statistics())
        except Exception as exc:  # noqa: BLE001
            return self._err(f"删除失败：{exc}")

    def clear_all(self) -> dict:
        try:
            for item in storage.list_candidates():
                stored = storage.delete_candidate(item["id"])
                if stored:
                    try:
                        Path(stored).unlink(missing_ok=True)
                    except OSError:
                        pass
            services.purge_orphan_files()
            return self._ok(candidates=[], stats=services.statistics())
        except Exception as exc:  # noqa: BLE001
            return self._err(f"清空失败：{exc}")

    # ---------- 配置 ----------

    def save_config(self, new_config: dict) -> dict:
        """保存配置。

        纯 AI 模式下不做本地重算；切换岗位时只同步列表上的岗位标签，
        已有分数保留，需要按新岗位重评时用户可点「全部重新评估」。
        """
        try:
            with self._lock:
                saved = config_module.save_config(new_config)
                touched = services.sync_role_labels(saved)
            return self._ok(config=saved, touched=touched,
                            candidates=storage.list_candidates(),
                            stats=services.statistics(),
                            eval=storage.eval_summary(),
                            queue=QUEUE.snapshot())
        except Exception as exc:  # noqa: BLE001
            log.exception("保存配置失败")
            return self._err(f"保存配置失败：{exc}")

    def reset_config(self) -> dict:
        try:
            with self._lock:
                cfg = config_module.reset_config()
                touched = services.sync_role_labels(cfg)
            return self._ok(config=cfg, touched=touched,
                            candidates=storage.list_candidates(),
                            stats=services.statistics(),
                            eval=storage.eval_summary(),
                            queue=QUEUE.snapshot())
        except Exception as exc:  # noqa: BLE001
            return self._err(f"恢复默认失败：{exc}")

    def rescore_all(self) -> dict:
        try:
            with self._lock:
                cfg = self._config()
                result = services.rescore_all(cfg)
            return self._ok(**result, candidates=storage.list_candidates(),
                            stats=services.statistics())
        except Exception as exc:  # noqa: BLE001
            return self._err(f"重新评分失败：{exc}")

    def open_data_dir(self) -> dict:
        try:
            target = paths.data_dir()
            if sys.platform == "win32":
                os.startfile(str(target))  # noqa: S606 - 打开资源管理器
            else:
                subprocess.Popen(["xdg-open", str(target)])  # noqa: S603
            return self._ok(path=str(target))
        except Exception as exc:  # noqa: BLE001
            return self._err(f"无法打开目录：{exc}")

    # ---------- 导出 ----------

    def export_results(self, fmt: str, candidate_ids: Optional[list[int]] = None) -> dict:
        try:
            cfg = self._config()
            ids = [int(i) for i in candidate_ids] if candidate_ids else None
            result = services.export_candidates(fmt, ids, cfg)
            return self._ok(**result)
        except ValueError as exc:
            return self._err(str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("导出失败")
            return self._err(f"导出失败：{exc}")

    def export_to_folder(self, fmt: str, candidate_ids: Optional[list[int]],
                         folder: str) -> dict:
        try:
            cfg = self._config()
            ids = [int(i) for i in candidate_ids] if candidate_ids else None
            result = services.export_candidates(fmt, ids, cfg, target_dir=folder)
            return self._ok(**result)
        except ValueError as exc:
            return self._err(str(exc))
        except Exception as exc:  # noqa: BLE001
            return self._err(f"导出失败：{exc}")

    def open_path(self, path: str) -> dict:
        try:
            target = Path(path)
            if not target.exists():
                return self._err("文件不存在")
            if sys.platform == "win32":
                os.startfile(str(target))  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", str(target)])  # noqa: S603
            return self._ok()
        except Exception as exc:  # noqa: BLE001
            return self._err(f"无法打开：{exc}")

    def show_in_folder(self, path: str) -> dict:
        try:
            target = Path(path)
            if not target.exists():
                return self._err("文件不存在")
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(target)])  # noqa: S603,S607
            else:
                subprocess.Popen(["xdg-open", str(target.parent)])  # noqa: S603
            return self._ok()
        except Exception as exc:  # noqa: BLE001
            return self._err(f"无法定位文件：{exc}")


def _to_js(value) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)


def _humanize_ai_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return "API Key 无效或没有权限"
        if status == 429:
            return "接口限流或额度不足，请稍后重试"
        if status >= 500:
            return f"AI 服务端错误（HTTP {status}）"
        return f"AI 接口返回错误（HTTP {status}）"
    if isinstance(exc, httpx.TimeoutException):
        return "AI 接口超时，请检查网络或调大超时时间"
    if isinstance(exc, httpx.RequestError):
        return "无法连接 AI 接口，请检查地址是否正确"
    return str(exc)[:200] or "未知错误"
