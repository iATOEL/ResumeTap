"""简历智能筛选与评估系统 - 配置加载模块。

优先级：环境变量 > config.yaml > 默认值。
"""

import os
from functools import lru_cache
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"

# 分类阈值（与前端保持一致）
CATEGORY_STRONG = "强烈推荐"
CATEGORY_RECOMMEND = "推荐"
CATEGORY_PENDING = "待定"
CATEGORY_REJECT = "不推荐"


def classify_score(score: float) -> str:
    """根据综合得分推导候选人分类。"""
    if score >= 85:
        return CATEGORY_STRONG
    if score >= 70:
        return CATEGORY_RECOMMEND
    if score >= 55:
        return CATEGORY_PENDING
    return CATEGORY_REJECT


@lru_cache(maxsize=1)
def load_config() -> dict:
    """加载配置：环境变量优先，其次 config.yaml，最后默认值。"""
    config: dict = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    llm = config.get("llm", {})

    return {
        "llm": {
            "base_url": os.getenv("LLM_BASE_URL") or llm.get("base_url", "https://api.openai.com/v1"),
            "api_key": os.getenv("LLM_API_KEY") or llm.get("api_key", ""),
            "model": os.getenv("LLM_MODEL") or llm.get("model", "gpt-4o-mini"),
            "temperature": float(os.getenv("LLM_TEMPERATURE") or llm.get("temperature", 0.3)),
        }
    }
