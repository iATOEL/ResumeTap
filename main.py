"""简历智能筛选与评估系统 - FastAPI 主应用。"""

import json
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from config import classify_score
from database import db_session, init_db
from evaluator import evaluate_resume
from models import CandidateDetail, CandidateResponse
from parser import parse_docx_bytes

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# 排序字段白名单（防止 SQL 注入）
ALLOWED_SORT_FIELDS = {
    "overall_score",
    "education_score",
    "experience_score",
    "project_score",
    "skill_score",
    "created_at",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    await init_db()
    yield


app = FastAPI(title="简历智能筛选与评估系统", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def _secure_filename(filename: str) -> str:
    """净化文件名，仅保留基本名称并替换危险字符，防止路径穿越。"""
    filename = os.path.basename(filename or "")
    filename = filename.replace("\\", "_").replace("\x00", "_").strip()
    if not filename or filename in {".", ".."}:
        filename = "unnamed.docx"
    return filename


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/upload")
async def upload_resume(file: UploadFile = File(...)):
    # 校验文件类型
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="仅支持 .docx 格式文件")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="文件大小超过 10MB 限制")

    # 解析简历
    try:
        raw_text = parse_docx_bytes(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"简历解析失败: {e}")

    if not raw_text.strip():
        raise HTTPException(status_code=400, detail="简历内容为空，请检查文件")

    # LLM 评估（先评估后保存，避免评估失败产生孤儿文件）
    try:
        evaluation = await evaluate_resume(raw_text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"评估失败: {e}")

    # 保存文件（同名文件自动追加唯一前缀）
    filename = _secure_filename(file.filename)
    save_path = os.path.join(UPLOAD_DIR, filename)
    if os.path.exists(save_path):
        name, ext = os.path.splitext(filename)
        filename = f"{name}_{uuid.uuid4().hex[:8]}{ext}"
        save_path = os.path.join(UPLOAD_DIR, filename)
    with open(save_path, "wb") as f:
        f.write(content)

    # 分类兜底：LLM 未返回分类时按综合得分推导
    category = evaluation.category or classify_score(evaluation.overall_score)

    # 存入数据库
    detail_json = json.dumps(
        {"summary": evaluation.summary, "details": evaluation.details},
        ensure_ascii=False,
    )

    async with db_session() as db:
        cursor = await db.execute(
            """INSERT INTO candidates
            (name, filename, raw_text, education_level, education_score,
             experience_score, project_score, skill_score, overall_score,
             evaluation_detail, category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                evaluation.name,
                filename,
                raw_text,
                evaluation.education_level,
                evaluation.education_score,
                evaluation.experience_score,
                evaluation.project_score,
                evaluation.skill_score,
                evaluation.overall_score,
                detail_json,
                category,
            ),
        )
        await db.commit()
        candidate_id = cursor.lastrowid

    return {
        "id": candidate_id,
        "name": evaluation.name,
        "overall_score": evaluation.overall_score,
        "category": category,
        "summary": evaluation.summary,
    }


@app.get("/api/candidates")
async def list_candidates(
    sort_by: str = Query("overall_score"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    category: str = Query("", description="按分类筛选"),
):
    if sort_by not in ALLOWED_SORT_FIELDS:
        raise HTTPException(status_code=400, detail=f"不支持的排序字段: {sort_by}")

    where_clause = ""
    params: list = []
    if category:
        where_clause = "WHERE category = ?"
        params.append(category)

    query = f"""
        SELECT id, name, filename, education_level, education_score,
               experience_score, project_score, skill_score, overall_score,
               category, created_at
        FROM candidates {where_clause}
        ORDER BY {sort_by} {order}
    """
    async with db_session() as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

    return [
        CandidateResponse(
            id=row["id"],
            name=row["name"],
            filename=row["filename"],
            education_level=row["education_level"],
            education_score=row["education_score"],
            experience_score=row["experience_score"],
            project_score=row["project_score"],
            skill_score=row["skill_score"],
            overall_score=row["overall_score"],
            category=row["category"],
            created_at=row["created_at"],
        ).model_dump()
        for row in rows
    ]


@app.get("/api/candidates/{candidate_id}")
async def get_candidate(candidate_id: int):
    async with db_session() as db:
        cursor = await db.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,))
        row = await cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="候选人不存在")

    try:
        detail = json.loads(row["evaluation_detail"]) if row["evaluation_detail"] else {}
    except json.JSONDecodeError:
        detail = {}

    return CandidateDetail(
        id=row["id"],
        name=row["name"],
        filename=row["filename"],
        raw_text=row["raw_text"],
        education_level=row["education_level"],
        education_score=row["education_score"],
        experience_score=row["experience_score"],
        project_score=row["project_score"],
        skill_score=row["skill_score"],
        overall_score=row["overall_score"],
        evaluation_detail=detail,
        category=row["category"],
        created_at=row["created_at"],
    ).model_dump()


@app.delete("/api/candidates/{candidate_id}")
async def delete_candidate(candidate_id: int):
    async with db_session() as db:
        cursor = await db.execute("DELETE FROM candidates WHERE id = ?", (candidate_id,))
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="候选人不存在")
    return {"message": "删除成功"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
