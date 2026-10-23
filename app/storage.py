"""SQLite 持久化：候选人、评分结果与配置版本。"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional

from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    filename TEXT NOT NULL,
    stored_path TEXT DEFAULT '',
    file_hash TEXT UNIQUE,
    raw_text TEXT DEFAULT '',
    overall_score REAL DEFAULT 0,
    rarity TEXT DEFAULT 'R',
    status TEXT DEFAULT 'pass',
    role TEXT DEFAULT '',
    years REAL DEFAULT 0,
    degree TEXT DEFAULT '',
    school TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    payload TEXT DEFAULT '{}',
    created_at TEXT DEFAULT '',
    review_verdict TEXT DEFAULT '',   -- liked / passed / skipped / ''
    review_note TEXT DEFAULT '',
    reviewed_at TEXT DEFAULT '',
    eval_status TEXT DEFAULT 'pending',  -- pending / running / done / error
    eval_error TEXT DEFAULT ''
);
"""

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None


# 旧库升级：列名 -> 建列语句（ALTER 只加不删）
MIGRATIONS = {
    "review_verdict": "ALTER TABLE candidates ADD COLUMN review_verdict TEXT DEFAULT ''",
    "review_note": "ALTER TABLE candidates ADD COLUMN review_note TEXT DEFAULT ''",
    "reviewed_at": "ALTER TABLE candidates ADD COLUMN reviewed_at TEXT DEFAULT ''",
    "eval_status": "ALTER TABLE candidates ADD COLUMN eval_status TEXT DEFAULT 'pending'",
    "eval_error": "ALTER TABLE candidates ADD COLUMN eval_error TEXT DEFAULT ''",
}


INDEXES = """
CREATE INDEX IF NOT EXISTS idx_candidates_score ON candidates(overall_score DESC);
CREATE INDEX IF NOT EXISTS idx_candidates_rarity ON candidates(rarity);
CREATE INDEX IF NOT EXISTS idx_candidates_eval ON candidates(eval_status);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """先补列，再建索引——索引可能引用新列。"""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(candidates)")}
    for column, statement in MIGRATIONS.items():
        if column not in existing:
            conn.execute(statement)
    conn.executescript(INDEXES)


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(paths.db_path(), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
        _migrate(_conn)
        _conn.commit()
    return _conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def init_if_needed() -> None:
    """启动时初始化连接与表结构（幂等）。"""
    with _lock:
        _connect()


def _row_to_dict(row: sqlite3.Row) -> dict:
    try:
        payload = json.loads(row["payload"] or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {
        "id": row["id"],
        "name": row["name"],
        "filename": row["filename"],
        "overall_score": row["overall_score"],
        "rarity": row["rarity"],
        "status": row["status"],
        "role": row["role"],
        "years": row["years"],
        "degree": row["degree"],
        "school": row["school"],
        "summary": row["summary"],
        "created_at": row["created_at"],
        "review_verdict": row["review_verdict"] or "",
        "review_note": row["review_note"] or "",
        "reviewed_at": row["reviewed_at"] or "",
        "eval_status": row["eval_status"] or "pending",
        "eval_error": row["eval_error"] or "",
        "has_raw_text": bool(row["raw_text"]),
        "profile": payload.get("profile", {}),
        "dimensions": payload.get("dimensions", []),
        "skill_hits": payload.get("skill_hits", []),
        "projects": payload.get("projects", []),
        "projects_count": len(payload.get("projects", [])),
        "screen_reasons": payload.get("screen_reasons", []),
        "strengths": payload.get("strengths", []),
        "weaknesses": payload.get("weaknesses", []),
        "news_flash": payload.get("news_flash", ""),
        "school_tier": payload.get("school_tier", ""),
        "school_tier_label": payload.get("school_tier_label", ""),
        "major": payload.get("major", ""),
        "detected_role": payload.get("detected_role", ""),
        "detected_role_label": payload.get("detected_role_label", ""),
        "rarity_label": payload.get("rarity_label", ""),
        "years_source": payload.get("years_source", ""),
        "payload": payload,
    }


def insert_candidate(*, name: str, filename: str, stored_path: str, file_hash: str,
                     raw_text: str, payload: dict, eval_status: str = "pending") -> int:
    with session() as conn:
        cursor = conn.execute(
            """INSERT INTO candidates
               (name, filename, stored_path, file_hash, raw_text, overall_score, rarity,
                status, role, years, degree, school, summary, payload, created_at, eval_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name, filename, stored_path, file_hash, raw_text,
                payload.get("overall_score", 0.0), payload.get("rarity", "R"),
                payload.get("status", "pass"), payload.get("role_title", ""),
                payload.get("years", 0.0), payload.get("degree", ""),
                payload.get("school", ""), payload.get("summary", ""),
                json.dumps(payload, ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
                eval_status,
            ),
        )
        return int(cursor.lastrowid)


# ---------------------------------------------------------------- 评估状态

def set_eval_status(candidate_id: int, status: str, error: str = "") -> bool:
    with session() as conn:
        cursor = conn.execute(
            "UPDATE candidates SET eval_status=?, eval_error=? WHERE id=?",
            (status, error[:300], candidate_id),
        )
        return cursor.rowcount > 0


def list_by_eval_status(*statuses: str, limit: int = 0) -> list[dict]:
    """按评估状态取简历；error 状态会被视为"可重试"，排在 pending 之后。"""
    if not statuses:
        return []
    placeholders = ",".join("?" for _ in statuses)
    sql = f"SELECT * FROM candidates WHERE eval_status IN ({placeholders})"
    # pending 优先，其次 error
    sql += " ORDER BY CASE eval_status WHEN 'pending' THEN 0 ELSE 1 END, id ASC"
    if limit > 0:
        sql += f" LIMIT {int(limit)}"
    with session() as conn:
        rows = conn.execute(sql, statuses).fetchall()
    return [_row_to_dict(row) for row in rows]


def count_by_eval_status(*statuses: str) -> int:
    if not statuses:
        return 0
    placeholders = ",".join("?" for _ in statuses)
    with session() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM candidates WHERE eval_status IN ({placeholders})",
            statuses,
        ).fetchone()
    return int(row["n"]) if row else 0


def eval_summary() -> dict:
    with session() as conn:
        rows = conn.execute(
            "SELECT eval_status, COUNT(*) AS n FROM candidates GROUP BY eval_status"
        ).fetchall()
    counts = {"pending": 0, "running": 0, "done": 0, "error": 0}
    for row in rows:
        counts[row["eval_status"] or "pending"] = row["n"]
    total = sum(counts.values())
    return {"total": total, **counts,
            "progress": round(counts["done"] / total * 100, 1) if total else 0.0}


def update_candidate(candidate_id: int, payload: dict) -> bool:
    with session() as conn:
        cursor = conn.execute(
            """UPDATE candidates SET overall_score=?, rarity=?, status=?, years=?,
                   degree=?, school=?, summary=?, payload=?, name=?, role=?
               WHERE id=?""",
            (
                payload.get("overall_score", 0.0), payload.get("rarity", "R"),
                payload.get("status", "pass"), payload.get("years", 0.0),
                payload.get("degree", ""), payload.get("school", ""),
                payload.get("summary", ""), json.dumps(payload, ensure_ascii=False),
                payload.get("name", ""), payload.get("role_title", ""),
                candidate_id,
            ),
        )
        return cursor.rowcount > 0


def list_candidates() -> list[dict]:
    with session() as conn:
        rows = conn.execute(
            "SELECT * FROM candidates ORDER BY overall_score DESC, id DESC"
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_candidate(candidate_id: int, include_raw: bool = True) -> Optional[dict]:
    with session() as conn:
        row = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    if not row:
        return None
    data = _row_to_dict(row)
    if include_raw:
        data["raw_text"] = row["raw_text"] or ""
        data["stored_path"] = row["stored_path"] or ""
    return data


def find_by_hash(file_hash: str) -> Optional[dict]:
    with session() as conn:
        row = conn.execute("SELECT * FROM candidates WHERE file_hash=?", (file_hash,)).fetchone()
    return _row_to_dict(row) if row else None


def delete_candidate(candidate_id: int) -> Optional[str]:
    """删除记录并返回被删除记录的存盘路径（由调用方决定是否删文件）。"""
    with session() as conn:
        row = conn.execute(
            "SELECT stored_path FROM candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM candidates WHERE id=?", (candidate_id,))
    return row["stored_path"] or ""


def all_stored_paths() -> set[str]:
    with session() as conn:
        rows = conn.execute("SELECT stored_path FROM candidates WHERE stored_path != ''").fetchall()
    return {row["stored_path"] for row in rows}


def set_review(candidate_id: int, verdict: str, note: str = "") -> bool:
    """记录人工速筛结果：liked（喜欢）/ passed（不合适）/ skipped（稍后）。"""
    with session() as conn:
        cursor = conn.execute(
            "UPDATE candidates SET review_verdict=?, review_note=?, reviewed_at=? WHERE id=?",
            (verdict, note, datetime.now().isoformat(timespec="seconds"), candidate_id),
        )
        return cursor.rowcount > 0


def list_pending() -> list[dict]:
    """待速筛的简历（尚未给出评价），按综合分从高到低。"""
    with session() as conn:
        rows = conn.execute(
            "SELECT * FROM candidates WHERE review_verdict='' "
            "ORDER BY overall_score DESC, id DESC"
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def review_summary() -> dict:
    with session() as conn:
        rows = conn.execute(
            "SELECT review_verdict, COUNT(*) AS n FROM candidates GROUP BY review_verdict"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM candidates").fetchone()["n"]
    counts = {"": 0, "liked": 0, "passed": 0, "skipped": 0}
    for row in rows:
        counts[row["review_verdict"] or ""] = row["n"]
    done = total - counts[""]
    return {
        "total": total,
        "pending": counts[""],
        "liked": counts["liked"],
        "passed": counts["passed"],
        "skipped": counts["skipped"],
        "progress": round(done / total * 100, 1) if total else 0.0,
    }


def rename_filename(candidate_id: int, filename: str) -> bool:
    """把记录里的文件名改成用户看到的原始名字（拖拽导入会用临时文件名）。"""
    with session() as conn:
        cursor = conn.execute(
            "UPDATE candidates SET filename=? WHERE id=?", (filename, candidate_id))
        return cursor.rowcount > 0


def reset_running_to_pending() -> int:
    """把因暂停/退出而中断的 running 记录退回 pending。"""
    with session() as conn:
        cursor = conn.execute(
            "UPDATE candidates SET eval_status='pending' WHERE eval_status='running'")
        return cursor.rowcount
