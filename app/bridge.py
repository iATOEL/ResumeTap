"""pywebview JS 桥：前端通过 window.pywebview.api 调用这些方法。"""

import logging
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Optional

import httpx
import webview

from . import ai_review, api, config as config_module
from . import paths, services, storage
from .rules import ROLE_LABELS

log = logging.getLogger("resume_gacha")


class Bridge:
    """所有方法必须返回可 JSON 序列化的数据，异常统一转成 {ok: False, error}。"""

    def __init__(self) -> None:
        self._window: Optional[webview.Window] = None
        self._lock = threading.Lock()

    # ---------- 基础 ----------

    def attach(self, window: webview.Window) -> None:
        self._window = window

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
                achievements=services.global_stats(),
                roles=[{"key": k, "label": v} for k, v in ROLE_LABELS.items()],
                meta={
                    "version": paths.APP_VERSION,
                    "data_dir": str(paths.data_dir()),
                    "config_path": str(paths.config_path()),
                    "log_path": str(paths.logs_dir() / "app.log"),
                    "api": api.status(),
                    "has_candidates": len(candidates) > 0,
                },
                review=storage.review_summary(),
                candidates=candidates,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("初始化失败")
            return self._err(f"初始化失败：{exc}")

    # ---------- 速筛 ----------

    def next_candidate(self, skip_reviewed: bool = True) -> dict:
        """取下一份待评价简历（按综合分从高到低）。"""
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

    # ---------- AI 复核（可选，用户自备接口） ----------

    def ai_status(self) -> dict:
        try:
            cfg = self._config()
            llm = cfg.get("llm", {})
            return self._ok(
                ready=ai_review.is_ready(cfg),
                enabled=bool(llm.get("enabled")),
                has_key=bool(str(llm.get("api_key") or "").strip()),
                model=llm.get("model", ""),
                base_url=llm.get("base_url", ""),
                replace_rules=bool(llm.get("replace_rules", True)),
            )
        except Exception as exc:  # noqa: BLE001
            return self._err(f"读取 AI 状态失败：{exc}")

    def ai_review_candidate(self, candidate_id: int) -> dict:
        """对单份简历执行 AI 复核；成功后可选择替换规则引擎结果。"""
        try:
            cfg = self._config()
            if not ai_review.is_ready(cfg):
                return self._err("AI 复核未启用，请先在设置页填写 API Key 并开启")

            data = storage.get_candidate(int(candidate_id), include_raw=True)
            if not data:
                return self._err("简历不存在或已被删除")

            raw_text = data.get("raw_text") or ""
            if not raw_text.strip():
                return self._err("该简历未保存原文，无法进行 AI 复核")

            payload = data.get("payload") or {}
            role_label = payload.get("detected_role_label") or "未指定"

            try:
                ai_result = ai_review.review_resume(raw_text, role_label, cfg)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in {401, 403}:
                    return self._err("API Key 无效或没有权限，请检查设置")
                if status == 429:
                    return self._err("请求过于频繁或额度不足，请稍后重试")
                return self._err(f"AI 接口返回错误（HTTP {status}）")
            except httpx.TimeoutException:
                return self._err("AI 接口超时，请检查网络或调大超时时间")
            except Exception as exc:  # noqa: BLE001
                log.exception("AI 复核失败")
                return self._err(f"AI 复核失败：{exc}")

            weights = {d["key"]: d.get("weight", 0.0) for d in payload.get("dimensions", [])}
            merged = ai_review.apply_to_payload(payload, ai_result, cfg, weights)
            merged["filename"] = payload.get("filename") or data.get("filename", "")

            storage.update_candidate(int(candidate_id), merged)
            updated = storage.get_candidate(int(candidate_id), include_raw=True)
            return self._ok(
                candidate=updated,
                applied=merged.get("score_source") == "ai",
                ai=ai_result,
                candidates=storage.list_candidates(),
                stats=services.statistics(),
                review=storage.review_summary(),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("AI 复核处理失败")
            return self._err(f"AI 复核失败：{exc}")

    def ai_batch_review(self, candidate_ids: list[int] | None = None,
                        limit: int = 10) -> dict:
        """批量 AI 复核（顺序执行，避免并发触发限流）。"""
        try:
            cfg = self._config()
            if not ai_review.is_ready(cfg):
                return self._err("AI 复核未启用，请先在设置页填写 API Key 并开启")

            ids = [int(i) for i in candidate_ids] if candidate_ids else None
            targets = []
            for item in storage.list_candidates():
                if ids is not None and item["id"] not in ids:
                    continue
                detail = storage.get_candidate(item["id"], include_raw=True)
                if detail and (detail.get("raw_text") or "").strip():
                    targets.append(detail)
                if len(targets) >= max(1, min(limit, 50)):
                    break

            if not targets:
                return self._err("没有可用于复核的简历（需要已保存原文）")

            done, failed = 0, []
            for detail in targets:
                payload = detail.get("payload") or {}
                role_label = payload.get("detected_role_label") or "未指定"
                try:
                    ai_result = ai_review.review_resume(detail["raw_text"], role_label, cfg)
                    weights = {d["key"]: d.get("weight", 0.0)
                               for d in payload.get("dimensions", [])}
                    merged = ai_review.apply_to_payload(payload, ai_result, cfg, weights)
                    merged["filename"] = payload.get("filename") or detail.get("filename", "")
                    storage.update_candidate(detail["id"], merged)
                    done += 1
                except Exception as exc:  # noqa: BLE001
                    failed.append({"id": detail["id"], "name": detail.get("name"),
                                   "error": str(exc)[:120]})

            return self._ok(
                updated=done,
                failed=failed,
                candidates=storage.list_candidates(),
                stats=services.statistics(),
            )
        except Exception as exc:  # noqa: BLE001
            return self._err(f"批量复核失败：{exc}")

    def ai_test_connection(self) -> dict:
        """用一条极短请求验证 Key 与地址是否可用。"""
        try:
            cfg = self._config()
            if not ai_review.is_ready(cfg):
                return self._err("请先填写 API Key 并开启 AI 复核")
            probe = "姓名：测试\n技能：Python\n工作经历：1 年开发经验"
            result = ai_review.review_resume(probe, "测试", cfg)
            return self._ok(message=f"连接正常，模型返回综合分 {result['overall_score']}")
        except Exception as exc:  # noqa: BLE001
            return self._err(f"连接失败：{exc}")

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

        for item in result["imported"]:
            services.record_pull(item["rarity"], cfg)
        result["stats"] = services.statistics()
        result["achievements"] = services.global_stats()
        result["candidates"] = storage.list_candidates()
        result["review"] = storage.review_summary()
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
            sample_dir = paths.resource_dir() / "samples"
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
        try:
            with self._lock:
                saved = config_module.save_config(new_config)
                rescored = services.rescore_all(saved)
            return self._ok(config=saved, rescored=rescored,
                            candidates=storage.list_candidates(),
                            stats=services.statistics())
        except Exception as exc:  # noqa: BLE001
            log.exception("保存配置失败")
            return self._err(f"保存配置失败：{exc}")

    def reset_config(self) -> dict:
        try:
            with self._lock:
                cfg = config_module.reset_config()
                rescored = services.rescore_all(cfg)
            return self._ok(config=cfg, rescored=rescored,
                            candidates=storage.list_candidates(),
                            stats=services.statistics())
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
