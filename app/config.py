"""配置加载与校验：所有阈值、权重、关键词均可通过 config.yaml 调整。"""

import copy
from typing import Any

import yaml

from . import paths
from .rules import DEGREE_ORDER, ROLE_PROFILES

DEFAULT_CONFIG: dict[str, Any] = {
    "rarity": {
        "ssr": 82.0,
        "sr": 66.0,
        "labels": {
            "SSR": "特级 · 传说",
            "SR": "高级 · 史诗",
            "R": "普通 · 稀有",
        },
    },
    "role": "",
    # 自定义岗位：role 为 "custom" 时生效
    "custom_role": {"label": "", "keywords": []},
    "keywords": [],
    "jd_text": "",
    "screening": {
        "min_score": 0.0,
        "min_years": 0.0,
        "required_degree": "",
        "must_keywords": [],
        "reject_keywords": [],
    },
    "ui": {
        "theme": "light",
        "view": "list",
        "sound": True,
        "animation": True,
    },
    "api": {
        "enabled": False,
        "port": 8765,
    },
    "privacy": {
        "store_raw_text": True,
    },
    "llm": {
        "enabled": False,
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-4o-mini",
        "temperature": 0.2,
        "timeout": 90,
        "auto_start": True,
    },
}

CONFIG_VERSION = 6

# 旧版本配置升级：patch 里的键一律以新默认值为准（这些是算法常量，不是用户偏好）
MIGRATIONS = [
    (4, {"api.enabled": False}),
]


def _drop_legacy_keys(raw: dict) -> None:
    """删除已被新结构替代的旧键，避免 normalize 把它们原样保留。"""
    education = raw.get("scoring", {}).get("education")
    if isinstance(education, dict):
        education.pop("top_school_bonus", None)
        education.pop("tier1_school_bonus", None)
    # 纯 AI 驱动后不再有规则权重与 replace_rules
    raw.pop("scoring", None)
    llm = raw.get("llm")
    if isinstance(llm, dict):
        llm.pop("replace_rules", None)
        llm.setdefault("auto_start", True)
        llm.setdefault("timeout", 90)


def _set_path(data: dict, dotted: str, value: Any) -> None:
    node = data
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
        if not isinstance(node, dict):
            return
    node[parts[-1]] = value


def _get_path(data: dict, dotted: str) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _migrate(raw: dict) -> tuple[dict, bool]:
    """按版本升级配置，返回 (配置, 是否发生升级)。"""
    if not isinstance(raw, dict):
        return {}, False
    version = int(raw.get("config_version") or 1)
    if version >= CONFIG_VERSION:
        return raw, False

    _drop_legacy_keys(raw)
    for target_version, patch in MIGRATIONS:
        if version >= target_version:
            continue
        for dotted, value in patch.items():
            _set_path(raw, dotted, copy.deepcopy(value))
        version = target_version

    raw["config_version"] = CONFIG_VERSION
    return raw, True


CONFIG_TEMPLATE = """# ResumeTap · 配置文件
# 修改后可在应用「设置」页点击“保存并重新评分”，或直接重启应用。
# 本文件保存在用户数据目录，升级/重装 exe 不会丢失。

rarity:
  ssr: 82     # 综合分 >= 82 为 SSR
  sr: 66      # 综合分 >= 66 为 SR，其余为 R
  labels:
    SSR: 特级 · 传说
    SR: 高级 · 史诗
    R: 普通 · 稀有

# 目标岗位（空 = 尚未选择，需在界面选择后才能开始评分）
#   auto 自动识别 / general 通用 / backend 后端 / frontend 前端 / fullstack 全栈
#   data 数据分析 / algorithm 算法 / product 产品 / test 测试 / ops 运维
role: ""

# 自定义关键词（留空则该维度不计入综合分）
keywords: []

# 岗位 JD 原文（可选，用于提取额外关键词提升岗位匹配度）
jd_text: ""

screening:
  min_score: 0
  min_years: 0
  required_degree: ""      # 不限 / 大专 / 本科 / 硕士 / 博士
  must_keywords: []        # 必须全部命中
  reject_keywords: []      # 命中任意一个即淘汰

ui:
  theme: light
  view: list
  sound: true
  animation: true

# 本地 AI 审查 API（仅监听 127.0.0.1，供本机 AI / Agent 调用）
# 默认关闭；在设置页手动启动后，令牌会显示在页面上
api:
  enabled: false
  port: 8765       # 端口被占用时自动顺延

privacy:
  store_raw_text: true     # 是否保存简历原文（关闭后仅保留评分与结构化信息）

# AI 评估（必需）：本软件为纯 AI 驱动，未配置时无法进行简历评估
# 支持任何 OpenAI 兼容接口（OpenAI / DeepSeek / 通义 / 本地 Ollama 等）
llm:
  enabled: false           # 必须勾选并填 api_key 才可用
  base_url: https://api.openai.com/v1
  api_key: ""
  model: gpt-4o-mini
  temperature: 0.2
  timeout: 90
  auto_start: true         # 导入后自动开始评估；false 则需手动点"开始评估"
"""


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _as_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.replace("，", ",").replace("、", ",").replace("\n", ",").split(",")
        return [p.strip() for p in parts if p.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def normalize(config: dict) -> dict:
    """把任意来源的配置整理为合法结构（越界值会被修正）。"""
    cfg = _deep_merge(DEFAULT_CONFIG, config if isinstance(config, dict) else {})

    rarity = cfg["rarity"]
    ssr = _as_float(rarity.get("ssr"), 82.0, 1.0, 100.0)
    sr = _as_float(rarity.get("sr"), 66.0, 0.0, 100.0)
    if sr >= ssr:
        sr = max(0.0, ssr - 1.0)
    rarity["ssr"] = round(ssr, 1)
    rarity["sr"] = round(sr, 1)

    role = str(cfg.get("role") or "").strip()
    cfg["role"] = role if (role in ROLE_PROFILES or role in {"auto", "", "custom"}) else ""

    custom = cfg.get("custom_role") or {}
    custom_label = str(custom.get("label") or "").strip()[:40]
    custom_keywords = _as_list(custom.get("keywords"))[:60]
    cfg["custom_role"] = {"label": custom_label, "keywords": custom_keywords}
    # 选了自定义岗位但没填关键词 -> 退回未选择，避免评分失真
    if role == "custom" and not custom_keywords:
        cfg["role"] = ""

    cfg["keywords"] = _as_list(cfg.get("keywords"))[:200]
    cfg["jd_text"] = str(cfg.get("jd_text") or "")[:20000]

    screening = cfg["screening"]
    screening["min_score"] = _as_float(screening.get("min_score"), 0.0, 0.0, 100.0)
    screening["min_years"] = _as_float(screening.get("min_years"), 0.0, 0.0, 40.0)
    degree = str(screening.get("required_degree") or "").strip()
    screening["required_degree"] = degree if degree in DEGREE_ORDER else ""
    screening["must_keywords"] = _as_list(screening.get("must_keywords"))[:100]
    screening["reject_keywords"] = _as_list(screening.get("reject_keywords"))[:100]

    theme = str(cfg["ui"].get("theme") or "dark")
    cfg["ui"]["theme"] = theme if theme in {"dark", "light"} else "dark"
    view = str(cfg["ui"].get("view") or "list")
    cfg["ui"]["view"] = view if view in {"grid", "list"} else "list"
    cfg["ui"]["sound"] = bool(cfg["ui"].get("sound", True))
    cfg["ui"]["animation"] = bool(cfg["ui"].get("animation", True))
    cfg["privacy"]["store_raw_text"] = bool(cfg["privacy"].get("store_raw_text", True))

    api_cfg = cfg["api"]
    api_cfg["enabled"] = bool(api_cfg.get("enabled", True))
    api_cfg["port"] = int(_as_float(api_cfg.get("port"), 8765, 1024, 65535))

    llm = cfg["llm"]
    llm["enabled"] = bool(llm.get("enabled", False))
    llm["base_url"] = str(llm.get("base_url") or DEFAULT_CONFIG["llm"]["base_url"]).rstrip("/")
    llm["api_key"] = str(llm.get("api_key") or "")
    llm["model"] = str(llm.get("model") or DEFAULT_CONFIG["llm"]["model"])
    llm["temperature"] = _as_float(llm.get("temperature"), 0.2, 0.0, 1.0)
    llm["timeout"] = int(_as_float(llm.get("timeout"), 90, 10, 300))
    llm["auto_start"] = bool(llm.get("auto_start", True))
    if not llm["api_key"].strip():
        llm["enabled"] = False

    cfg["config_version"] = CONFIG_VERSION
    return cfg


def load_config() -> dict:
    """加载配置；首次运行时写入带注释的模板，旧版本配置自动补齐新字段。"""
    path = paths.config_path()
    if not path.exists():
        path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        raw = {}

    raw, migrated = _migrate(raw if isinstance(raw, dict) else {})
    cfg = normalize(raw)
    if migrated:
        # 升级后回写，保证磁盘上的配置与内存一致（保留用户已设置的值）
        try:
            path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                            encoding="utf-8")
        except OSError:
            pass
    return cfg


def save_config(config: dict) -> dict:
    cfg = normalize({**config, "config_version": CONFIG_VERSION})
    path = paths.config_path()
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(path)
    return cfg


def reset_config() -> dict:
    paths.config_path().write_text(CONFIG_TEMPLATE, encoding="utf-8")
    return load_config()


def rarity_of(score: float, config: dict) -> str:
    if score >= config["rarity"]["ssr"]:
        return "SSR"
    if score >= config["rarity"]["sr"]:
        return "SR"
    return "R"


RARITY_ORDER = {"SSR": 3, "SR": 2, "R": 1}
