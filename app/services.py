"""业务服务层：导入、重评分、导出、统计与设置。"""

import csv
import hashlib
import logging
import shutil
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import extractor, parser, storage
from .paths import exports_dir, resumes_dir

log = logging.getLogger("resume_gacha")


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique_path(directory: Path, filename: str) -> Path:
    safe = Path(filename).name
    target = directory / safe
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for index in range(1, 1000):
        candidate = directory / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}_{int(time.time())}{suffix}"


def _pending_payload(text: str, filename: str) -> dict:
    """导入时的占位载荷：只有客观信息，没有分数。"""
    ext = extractor.extract(text, filename)
    return {
        "name": ext.name,
        "filename": filename,
        "overall_score": 0.0,
        "rarity": "R",
        "rarity_label": "待评估",
        "status": "pass",
        "screen_reasons": [],
        "strengths": [],
        "weaknesses": [],
        "summary": "等待 AI 评估",
        "news_flash": "等待 AI 评估",
        "dimensions": [],
        "score_source": "",
        "profile": ext.to_summary_dict(),
        "sections": {
            "education": ext.education_text,
            "experience": ext.experience_text,
            "projects": ext.project_text,
            "skills": ext.skill_text,
            "summary": ext.summary_text,
            "awards": ext.awards_text,
        },
        "projects": [{"title": p.title, "description": p.description}
                     for p in ext.projects[:10]],
        "years": ext.years,
        "years_source": ext.years_source,
        "degree": ext.degree,
        "school": ext.school,
        "school_tier": ext.school_tier,
        "school_tier_label": ext.school_tier_label,
        "major": ext.major,
        "contact": {"phone": ext.phone, "email": ext.email},
        "role_title": ext.role_title,
        "detected_role": "",
        "detected_role_label": "",
        "role_selected": True,
        "education_source": ext.education_source,
    }


def import_files(paths: Iterable[str], config: dict,
                 progress: Optional[Callable[[dict], None]] = None) -> dict:
    """批量导入；单文件失败不影响其他文件。"""
    path_list = [p for p in paths if p]
    total = len(path_list)
    imported, duplicates, failures = [], [], []

    for index, raw_path in enumerate(path_list, start=1):
        filename = Path(raw_path).name
        if progress:
            progress({"index": index, "total": total, "filename": filename, "stage": "parsing"})

        try:
            data = Path(raw_path).read_bytes()
        except FileNotFoundError:
            failures.append({"filename": filename, "error": "文件不存在或已被移动"})
            continue
        except PermissionError:
            failures.append({"filename": filename, "error": "文件被占用，请关闭后重试"})
            continue
        except OSError as exc:
            failures.append({"filename": filename, "error": f"读取失败：{exc.strerror or exc}"})
            continue

        digest = _hash_bytes(data)
        existing = storage.find_by_hash(digest)
        if existing:
            duplicates.append({"filename": filename, "id": existing["id"],
                               "name": existing["name"]})
            continue

        try:
            text = parser.parse_bytes(data, Path(raw_path).suffix)
        except parser.ParseError as exc:
            failures.append({"filename": filename, "error": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 - 防止异常文件导致崩溃
            log.exception("解析 %s 失败", filename)
            failures.append({"filename": filename, "error": f"解析失败：{exc}"})
            continue

        if progress:
            progress({"index": index, "total": total, "filename": filename, "stage": "queued"})

        # 只做客观解析，评分交给 AI 队列（此时还没有分数）
        payload = _pending_payload(text, filename)

        stored_path = ""
        try:
            target = _unique_path(resumes_dir(), filename)
            shutil.copyfile(raw_path, target)
            stored_path = str(target)
        except OSError as exc:
            log.warning("保存原始文件失败：%s", exc)

        raw_text = text if config["privacy"]["store_raw_text"] else ""
        try:
            candidate_id = storage.insert_candidate(
                name=payload["name"], filename=filename, stored_path=stored_path,
                file_hash=digest, raw_text=raw_text, payload=payload,
                eval_status="pending",
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("写入数据库失败")
            failures.append({"filename": filename, "error": f"保存失败：{exc}"})
            continue

        imported.append({
            "id": candidate_id,
            "name": payload["name"],
            "filename": filename,
            "eval_status": "pending",
        })

    return {"imported": imported, "duplicates": duplicates, "failures": failures,
            "total": total}


def requeue_all(config: dict, only_failed: bool = False) -> dict:
    """把所有简历重新标记为待评估（下一次开始队列时会重新调用 AI）。

    规则引擎已移除，改岗位/改规则后必须重新问 AI。
    """
    with storage.session() as conn:
        if only_failed:
            cursor = conn.execute(
                "UPDATE candidates SET eval_status='pending', eval_error='' "
                "WHERE eval_status='error'")
        else:
            cursor = conn.execute(
                "UPDATE candidates SET eval_status='pending', eval_error=''")
        return {"queued": cursor.rowcount}


# 兼容旧调用名
def rescore_all(config: dict, keep_ai: bool = True) -> dict:
    return requeue_all(config, only_failed=False)


def statistics() -> dict:
    candidates = storage.list_candidates()
    total = len(candidates)
    counts = {"R": 0, "SR": 0, "SSR": 0}
    status_counts = {"pass": 0, "flagged": 0, "rejected": 0}
    review_counts = {"": 0, "liked": 0, "passed": 0, "skipped": 0}
    scores, years = [], []
    evaluated = 0
    for item in candidates:
        review_counts[item.get("review_verdict", "")] = review_counts.get(item.get("review_verdict", ""), 0) + 1
        # 稀有度与筛选结论都由 AI 产出：未评估的记录只是占位值（R / pass），
        # 计入会让 pass_rate、ssr_rate 超过 100%
        if item.get("eval_status") != "done":
            continue
        counts[item["rarity"]] = counts.get(item["rarity"], 0) + 1
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
        evaluated += 1
        scores.append(item["overall_score"])
        years.append(item.get("years") or 0)

    passed = status_counts["pass"]
    best = max((c for c in candidates if c.get("eval_status") == "done"),
               key=lambda c: c["overall_score"], default=None)
    reviewed = total - review_counts[""]
    eval_stats = storage.eval_summary()
    return {
        "total": total,
        "evaluated": evaluated,
        "eval": eval_stats,
        "rarity_counts": counts,
        "status_counts": status_counts,
        "review_counts": review_counts,
        "reviewed": reviewed,
        "review_progress": round(reviewed / total * 100, 1) if total else 0.0,
        "avg_score": round(sum(scores) / evaluated, 1) if evaluated else 0.0,
        "max_score": round(max(scores), 1) if scores else 0.0,
        "avg_years": round(sum(years) / evaluated, 1) if evaluated else 0.0,
        "pass_rate": round(passed / evaluated * 100, 1) if evaluated else 0.0,
        "ssr_rate": round(counts["SSR"] / evaluated * 100, 1) if evaluated else 0.0,
        "best": {"name": best["name"], "score": best["overall_score"],
                 "rarity": best["rarity"]} if best else None,
    }


def get_candidate_detail(candidate_id: int, full: bool = False) -> Optional[dict]:
    """返回给前端的候选人数据：列表字段 + 可选的完整评分载荷。"""
    data = storage.get_candidate(candidate_id, include_raw=full)
    if not data:
        return None
    if not full:
        # 速筛只需要卡片字段，避免一次性传输大量原文
        data.pop("raw_text", None)
        data.pop("payload", None)
        return data
    return data


def export_candidates(fmt: str, candidate_ids: Optional[list[int]], config: dict,
                      target_dir: Optional[str] = None) -> dict:
    """导出 CSV / JSON / Markdown 报告；ids 为空表示导出全部。"""
    all_items = storage.list_candidates()
    if candidate_ids:
        wanted = set(candidate_ids)
        all_items = [item for item in all_items if item["id"] in wanted]
    if not all_items:
        raise ValueError("没有可导出的简历")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    directory = Path(target_dir) if target_dir else exports_dir()
    directory.mkdir(parents=True, exist_ok=True)

    fmt = fmt.lower()
    if fmt == "csv":
        path = directory / f"筛选结果_{stamp}.csv"
        _write_csv(path, all_items)
    elif fmt == "json":
        path = directory / f"筛选结果_{stamp}.json"
        _write_json(path, all_items, config)
    elif fmt in {"md", "markdown"}:
        path = directory / f"筛选报告_{stamp}.md"
        _write_markdown(path, all_items, config)
    else:
        raise ValueError(f"不支持的导出格式：{fmt}")

    return {"path": str(path), "count": len(all_items), "format": fmt}


def _eval_label(item: dict) -> str:
    status = item.get("eval_status") or "pending"
    return {"pending": "待评估", "running": "评估中",
            "done": "已评估", "error": "评估失败"}.get(status, status)


def _status_label(item: dict) -> str:
    """筛选结论由 AI 给出；未评估时不能冒充"通过"。"""
    if item.get("eval_status") != "done":
        return "待评估"
    return {"pass": "通过", "flagged": "待复核", "rejected": "淘汰"}.get(
        item.get("status", ""), item.get("status", ""))


def _dimension_text(item: dict) -> str:
    dims = {d.get("key"): d for d in item.get("dimensions", [])}

    def fmt(key: str) -> str:
        dim = dims.get(key) or {}
        if dim.get("available") is False:
            return "--"
        return f"{dim.get('score', 0):.0f}"

    return " / ".join(
        f"{label}{fmt(key)}" for key, label in
        [("skill", "技能"), ("experience", "经验"), ("education", "学历"),
         ("project", "项目"), ("job_match", "岗位")]
    )


def _education_entries(item: dict) -> list[dict]:
    return (((item.get("payload") or {}).get("profile") or {})
            .get("education_entries") or [])


def _education_text(item: dict) -> str:
    """把多段教育经历拼成一行文本。"""
    entries = ((item.get("payload") or {}).get("profile") or {}).get("education_entries") or []
    parts = []
    for entry in entries:
        chunk = " ".join(filter(None, [
            entry.get("period", ""), entry.get("school", ""),
            entry.get("degree", ""), entry.get("major", ""),
            f"({entry.get('tier_label')})" if entry.get("tier_label") else "",
        ]))
        if chunk.strip():
            parts.append(chunk.strip())
    return " | ".join(parts)


def _payload(item: dict) -> dict:
    return item.get("payload") or {}


def _csv_safe(value) -> str:
    """避免 Excel 把文本当公式执行（CSV 注入防护）。"""
    text = "" if value is None else str(value)
    if text[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + text
    return text


def _write_csv(path: Path, items: list[dict]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "排名", "姓名", "电话", "邮箱", "稀有度", "综合分", "状态", "人工评价",
            "评估状态", "经验年限", "学历", "院校", "院校层级", "教育经历",
            "文件名", "各维度分数", "AI 模型", "优点", "不足", "待评估原因",
            "评语", "导入时间",
        ])
        for index, item in enumerate(items, start=1):
            writer.writerow([
                index,
                _csv_safe(item["name"]),
                _csv_safe(_payload(item).get("contact", {}).get("phone", "")),
                _csv_safe(_payload(item).get("contact", {}).get("email", "")),
                item["rarity"],
                item["overall_score"],
                _status_label(item),
                {"liked": "喜欢", "passed": "不合适", "skipped": "稍后"}.get(item.get("review_verdict", ""), "未评价"),
                _eval_label(item), item.get("years", 0),
                _csv_safe(item.get("degree", "")), _csv_safe(item.get("school", "")),
                _csv_safe(item.get("school_tier_label", "")),
                _csv_safe(_education_text(item)), _csv_safe(item["filename"]),
                _dimension_text(item), _csv_safe(_payload(item).get("ai_model", "")),
                _csv_safe("；".join(item.get("strengths", []))),
                _csv_safe("；".join(item.get("weaknesses", []))),
                _csv_safe(item.get("eval_error", "")),
                _csv_safe(item.get("summary", "")),
                item.get("created_at", ""),
            ])


def _write_json(path: Path, items: list[dict], config: dict) -> None:
    import json
    payload = {
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(items),
        "thresholds": config["rarity"],
        "scoring": "AI（无规则引擎）",
        "role": config.get("role", ""),
        "candidates": [
            {
                "id": item["id"], "name": item["name"], "filename": item["filename"],
                "rarity": item["rarity"], "score": item["overall_score"],
                "status": item["status"], "years": item.get("years"),
                "degree": item.get("degree"), "school": item.get("school"),
                "school_tier": item.get("school_tier"),
                "school_tier_label": item.get("school_tier_label"),
                "contact": _payload(item).get("contact") or {},
                "education_entries": _education_entries(item),
                "score_source": _payload(item).get("score_source", ""),
                "ai_model": _payload(item).get("ai_model", ""),
                "summary": item.get("summary"),
                "news_flash": _payload(item).get("news_flash", ""),
                "strengths": _payload(item).get("strengths", []),
                "weaknesses": _payload(item).get("weaknesses", []),
                "review_verdict": item.get("review_verdict", ""),
                "review_note": item.get("review_note", ""),
                "dimensions": _payload(item).get("dimensions", []),
                "skill_hits": _payload(item).get("skill_hits", []),
                "screen_reasons": _payload(item).get("screen_reasons", []),
                "eval_status": item.get("eval_status", "pending"),
                "eval_error": item.get("eval_error", ""),
                "created_at": item.get("created_at"),
            }
            for item in items
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_markdown(path: Path, items: list[dict], config: dict) -> None:
    lines = [
        "# 简历筛选报告",
        "",
        f"- 导出时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 样本数量：{len(items)}",
        f"- 稀有度阈值：SSR ≥ {config['rarity']['ssr']:.0f}，SR ≥ {config['rarity']['sr']:.0f}",
        "",
        "| 排名 | 姓名 | 稀有度 | 综合分 | 经验 | 学历 | 院校 | 人工评价 | 状态 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, item in enumerate(items, start=1):
        status = _status_label(item)
        verdict = {"liked": "喜欢", "passed": "不合适", "skipped": "稍后"}.get(item.get("review_verdict", ""), "未评价")
        school = item.get("school", "")
        if school and item.get("school_tier_label"):
            school = f"{school}（{item['school_tier_label']}）"
        score_text = f"{item['overall_score']:.1f}" if item.get("eval_status") == "done" else "—"
        lines.append(
            f"| {index} | {item['name']} | {item['rarity']} | {score_text} | "
            f"{item.get('years', 0):.1f} 年 | {item.get('degree', '')} | {school} | {verdict} | {status} |"
        )

    lines += ["", "## 评分明细", ""]
    for item in items:
        if item.get("eval_status") != "done":
            lines.append(f"### {item['name']}（{_eval_label(item)}）")
            lines.append("")
            if item.get("eval_error"):
                lines.append(f"- 失败原因：{item['eval_error']}")
            lines.append("")
            continue
        lines.append(f"### {item['name']}（{item['rarity']} / {item['overall_score']:.1f}）")
        lines.append("")
        if item.get("summary"):
            lines.append(f"- 评语：{item['summary']}")
        if item.get("strengths"):
            lines.append("- 优点：" + "；".join(item["strengths"]))
        if item.get("weaknesses"):
            lines.append("- 不足：" + "；".join(item["weaknesses"]))
        for dim in item.get("dimensions", []):
            lines.append(
                f"- {dim.get('label')}：{dim.get('score', 0):.1f} 分"
                f" — {dim.get('detail', '')}"
            )
        if item.get("screen_reasons"):
            lines.append("- 筛选提示：" + "；".join(item["screen_reasons"]))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def purge_orphan_files() -> int:
    """清理数据库中已不存在的原始文件副本。"""
    stored = storage.all_stored_paths()
    directory = resumes_dir()
    removed = 0
    for file_path in directory.glob("*"):
        if file_path.is_file() and str(file_path) not in stored:
            try:
                file_path.unlink()
                removed += 1
            except OSError:
                pass
    return removed




def sync_role_labels(config: dict) -> int:
    """把当前岗位名称同步到所有简历的展示标签（不改分数）。"""
    from .eval_queue import _role_label_from_config
    import json as _json

    label = _role_label_from_config()
    touched = 0
    with storage.session() as conn:
        rows = conn.execute("SELECT id, payload FROM candidates").fetchall()
        for row in rows:
            try:
                payload = _json.loads(row["payload"] or "{}")
            except _json.JSONDecodeError:
                continue
            if payload.get("detected_role_label") == label:
                continue
            payload["detected_role_label"] = label
            conn.execute("UPDATE candidates SET payload=? WHERE id=?",
                         (_json.dumps(payload, ensure_ascii=False), row["id"]))
            touched += 1
    return touched
