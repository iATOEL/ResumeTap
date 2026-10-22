"""院校层级库：按 C9 / 985 / 211 / 双一流 / 普通本科 / 专科 / 海外院校分层。

分层只影响教育背景的院校加分，不影响学历本身的判断。
名单不追求穷尽，可在 config.yaml 的 education 段调整每档加分。
"""

import re

# C9 联盟
C9 = [
    "北京大学", "清华大学", "复旦大学", "上海交通大学", "浙江大学",
    "南京大学", "中国科学技术大学", "哈尔滨工业大学", "西安交通大学",
]

# 985（不含 C9）
C985 = [
    "北京航空航天大学", "北京理工大学", "中国农业大学", "北京师范大学",
    "中央民族大学", "南开大学", "天津大学", "大连理工大学", "吉林大学",
    "东北大学", "同济大学", "华东师范大学", "东南大学", "山东大学",
    "中国海洋大学", "武汉大学", "华中科技大学", "湖南大学", "中南大学",
    "中山大学", "华南理工大学", "四川大学", "电子科技大学", "重庆大学",
    "西北工业大学", "兰州大学", "国防科技大学", "西北农林科技大学",
]

# 211（不含 985）
C211 = [
    "北京交通大学", "北京工业大学", "北京科技大学", "北京化工大学",
    "北京邮电大学", "北京林业大学", "北京中医药大学", "北京外国语大学",
    "中国传媒大学", "中央财经大学", "对外经济贸易大学", "北京体育大学",
    "中央音乐学院", "中国政法大学", "华北电力大学", "天津医科大学",
    "河北工业大学", "太原理工大学", "内蒙古大学", "辽宁大学", "大连海事大学",
    "延边大学", "东北师范大学", "哈尔滨工程大学", "东北农业大学",
    "东北林业大学", "华东理工大学", "东华大学", "上海外国语大学",
    "上海财经大学", "上海大学", "苏州大学", "南京航空航天大学",
    "南京理工大学", "中国矿业大学", "南京邮电大学", "河海大学", "江南大学",
    "南京农业大学", "中国药科大学", "南京师范大学", "安徽大学", "合肥工业大学",
    "福州大学", "南昌大学", "郑州大学", "中国地质大学", "武汉理工大学",
    "华中农业大学", "华中师范大学", "中南财经政法大学", "湖南师范大学",
    "暨南大学", "华南师范大学", "广西大学", "海南大学", "西南大学",
    "四川农业大学", "西南财经大学", "贵州大学", "云南大学", "西藏大学",
    "西北大学", "西安电子科技大学", "长安大学", "陕西师范大学", "青海大学",
    "宁夏大学", "新疆大学", "石河子大学", "第二军医大学", "第四军医大学",
]

# 双一流（非 211）
DOUBLE_FIRST = [
    "上海科技大学", "南方科技大学", "中国科学院大学", "宁波大学", "河南大学",
    "山西大学", "湘潭大学", "南京林业大学", "南京信息工程大学", "南京医科大学",
    "南京中医药大学", "上海海洋大学", "上海中医药大学", "上海体育学院",
    "上海音乐学院", "首都师范大学", "外交学院", "中国人民公安大学",
    "天津工业大学", "天津中医药大学", "河北大学", "华北水利水电大学",
    "广州中医药大学", "广州医科大学", "成都理工大学", "成都中医药大学",
    "西南石油大学", "西南交通大学", "首都医科大学", "天津科技大学",
]

# 海外与港澳台知名院校
OVERSEAS_TOP = [
    "哈佛大学", "斯坦福大学", "麻省理工学院", "牛津大学", "剑桥大学",
    "加州大学伯克利分校", "卡内基梅隆大学", "帝国理工学院", "伦敦大学学院",
    "新加坡国立大学", "南洋理工大学", "东京大学", "京都大学",
    "多伦多大学", "苏黎世联邦理工学院", "加州理工学院", "耶鲁大学",
    "普林斯顿大学", "哥伦比亚大学", "芝加哥大学", "宾夕法尼亚大学",
    "康奈尔大学", "密歇根大学", "香港大学", "香港中文大学", "香港科技大学",
    "台湾大学", "墨尔本大学", "悉尼大学", "新南威尔士大学",
]

OVERSEAS_KNOWN = [
    "香港城市大学", "香港理工大学", "澳门大学", "早稻田大学", "庆应义塾大学",
    "首尔大学", "高丽大学", "延世大学", "曼彻斯特大学", "爱丁堡大学",
    "伦敦国王学院", "华威大学", "布里斯托大学", "格拉斯哥大学",
    "昆士兰大学", "莫纳什大学", "西澳大学", "阿德莱德大学",
    "加州大学洛杉矶分校", "加州大学圣地亚哥分校", "纽约大学",
    "杜克大学", "西北大学", "约翰霍普金斯大学", "华盛顿大学",
]

TIER_ORDER = ["c9", "c985", "overseas_top", "c211", "double_first", "overseas_known", "ordinary", "college"]

TIER_LABELS = {
    "c9": "C9",
    "c985": "985",
    "overseas_top": "海外名校",
    "c211": "211",
    "double_first": "双一流",
    "overseas_known": "海外院校",
    "ordinary": "普通本科",
    "college": "专科院校",
    "unknown": "未识别",
}

TIER_BONUS = {
    "c9": 22.0,
    "c985": 18.0,
    "overseas_top": 18.0,
    "c211": 13.0,
    "double_first": 10.0,
    "overseas_known": 10.0,
    "ordinary": 4.0,
    "college": 0.0,
    "unknown": 0.0,
}

_SCHOOL_MAP: dict[str, str] = {}
for _names, _tier in (
    (C9, "c9"),
    (C985, "c985"),
    (C211, "c211"),
    (DOUBLE_FIRST, "double_first"),
    (OVERSEAS_TOP, "overseas_top"),
    (OVERSEAS_KNOWN, "overseas_known"),
):
    for _name in _names:
        _SCHOOL_MAP.setdefault(_name, _tier)

# 长名字优先，避免 "北京大学" 先匹配到更短的别名
_SORTED_SCHOOLS = sorted(_SCHOOL_MAP.items(), key=lambda item: -len(item[0]))

COLLEGE_HINT_RE = re.compile(r"职业技术学院|职业学院|高等专科学校|专科学校|高职|技师学院")
SCHOOL_SUFFIX_RE = re.compile(r"([\u4e00-\u9fa5]{1,14}(?:大学|学院|学校))")


def _compact(text: str) -> str:
    return re.sub(r"[\s\u3000]+", "", text or "")


def classify_school(school: str) -> tuple[str, str, float]:
    """返回 (tier_key, tier_label, 加分)。"""
    name = _compact(school)
    if not name:
        return "unknown", TIER_LABELS["unknown"], 0.0
    for known, tier in _SORTED_SCHOOLS:
        if known in name:
            return tier, TIER_LABELS[tier], TIER_BONUS[tier]
    if COLLEGE_HINT_RE.search(name) or re.search(r"专科", name):
        return "college", TIER_LABELS["college"], TIER_BONUS["college"]
    if re.search(r"大学|学院", name):
        return "ordinary", TIER_LABELS["ordinary"], TIER_BONUS["ordinary"]
    return "unknown", TIER_LABELS["unknown"], 0.0


def find_school(text: str) -> tuple[str, str, float]:
    """从一段文本中找出层级最高的院校。"""
    compact = _compact(text)
    if not compact:
        return "", "unknown", 0.0

    best = ("", "unknown", 0.0, len(TIER_ORDER))
    for known, tier in _SORTED_SCHOOLS:
        index = compact.find(known)
        if index < 0:
            continue
        rank = TIER_ORDER.index(tier)
        if rank < best[3]:
            best = (known, tier, TIER_BONUS[tier], rank)
    if best[0]:
        return best[0], best[1], best[2]

    match = SCHOOL_SUFFIX_RE.search(compact)
    if match:
        name = match.group(1)
        tier, label, bonus = classify_school(name)
        if tier == "unknown":
            # "某大学"/"某学院" 这类占位名：仍记录院校名，按普通院校处理
            tier, label, bonus = "ordinary", TIER_LABELS["ordinary"], TIER_BONUS["ordinary"]
        return name, tier, bonus
    return "", "unknown", 0.0
