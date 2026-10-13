"""数据模型定义。"""

from typing import Any, Optional

from pydantic import BaseModel, Field


class CandidateCreate(BaseModel):
    """候选人创建模型（预留，供未来直接创建记录使用）。"""

    name: str
    filename: str
    raw_text: str
    education_level: str = ""
    education_score: float = 0
    experience_score: float = 0
    project_score: float = 0
    skill_score: float = 0
    overall_score: float = 0
    evaluation_detail: dict = Field(default_factory=dict)
    category: str = ""


class CandidateResponse(BaseModel):
    """候选人列表响应模型。"""

    id: int
    name: str
    filename: str
    education_level: str
    education_score: float
    experience_score: float
    project_score: float
    skill_score: float
    overall_score: float
    category: str
    created_at: Optional[str] = None


class CandidateDetail(CandidateResponse):
    """候选人详情响应模型。"""

    raw_text: str
    evaluation_detail: Any = Field(default_factory=dict)


class EvaluationResult(BaseModel):
    """LLM 评估结果模型。"""

    name: str = ""
    education_level: str = ""
    education_score: float = 0
    experience_score: float = 0
    project_score: float = 0
    skill_score: float = 0
    overall_score: float = 0
    category: str = ""
    summary: str = ""
    details: dict = Field(default_factory=dict)
