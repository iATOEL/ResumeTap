"""数据库模块：SQLite 连接管理与表结构初始化。"""

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data.db")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    filename TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    education_level TEXT DEFAULT '',
    education_score REAL DEFAULT 0,
    experience_score REAL DEFAULT 0,
    project_score REAL DEFAULT 0,
    skill_score REAL DEFAULT 0,
    overall_score REAL DEFAULT 0,
    evaluation_detail TEXT DEFAULT '{}',
    category TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


async def get_db() -> aiosqlite.Connection:
    """打开一个数据库连接（调用方负责关闭）。"""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


@asynccontextmanager
async def db_session() -> AsyncIterator[aiosqlite.Connection]:
    """数据库连接的异步上下文管理器，退出时自动关闭。"""
    db = await get_db()
    try:
        yield db
    finally:
        await db.close()


async def init_db() -> None:
    """初始化数据库表结构。"""
    async with db_session() as db:
        await db.execute(CREATE_TABLE_SQL)
        await db.commit()
