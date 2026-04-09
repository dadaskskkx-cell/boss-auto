"""根据岗位 JD 生成运行时 profile 配置。"""

from __future__ import annotations

import re


DEFAULT_BLACKLIST_KEYWORDS = ["外包", "劳务派遣", "培训"]
DEFAULT_QUESTIONS = [
    "目前是在职还是离职状态？",
    "期望薪资范围是多少？",
    "最快多久可以到岗？",
]
EDUCATION_ORDER = ["博士", "硕士", "本科", "大专", "中专", "高中"]

GENERIC_STOPWORDS = {
    "负责",
    "熟悉",
    "精通",
    "掌握",
    "具备",
    "相关",
    "工作",
    "经验",
    "能力",
    "优先",
    "以上",
    "以及",
    "能够",
    "具有",
    "岗位",
    "要求",
    "职责",
    "完成",
    "参与",
    "推进",
    "建设",
    "管理",
    "方向",
    "良好",
    "优秀",
}

KNOWN_KEYWORDS = [
    "法务", "律师", "合规", "合同", "诉讼", "仲裁", "知识产权", "风控", "商标", "版权",
    "海外", "涉外", "跨境", "GDPR", "出口管制", "投融资", "公司治理", "英语",
    "Python", "Java", "Go", "C++", "JavaScript", "TypeScript", "React", "Vue",
    "Node.js", "Redis", "MySQL", "PostgreSQL", "Docker", "Kubernetes", "算法",
    "前端", "后端", "测试", "运维", "产品", "运营", "销售", "招聘", "财务", "审计",
    "税务", "采购", "供应链", "客服", "行政", "人事",
]


def build_profile_from_jd(job_title: str, job_description: str) -> dict:
    title = (job_title or "").strip()
    description = (job_description or "").strip()
    combined = f"{title}\n{description}".strip()

    required_keywords_any = _build_required_keywords(title, description)
    keyword_pool = _build_keyword_pool(title, description, required_keywords_any)

    return {
        "job_title": title,
        "job_description": description,
        "llm_prompt": (
            "你是一个专业的HR助手。请根据以下JD描述和候选人简历，评估候选人与岗位的匹配度。\n\n"
            "岗位：{job_title}\n"
            "JD：{job_description}\n\n"
            "候选人简历：\n"
            "{resume_text}\n\n"
            "请按以下格式输出：\n"
            "评分: <0-100的整数>\n"
            "理由: <一句话说明理由>\n"
            "建议: <matched 或 rejected>\n"
        ),
        "questions": list(DEFAULT_QUESTIONS),
        "rules": {
            "blacklist_keywords": _build_blacklist_keywords(combined),
            "reject_keywords": [],
            "llm_score_threshold": 60,
            "use_llm": False,
            "max_age": _extract_max_age(combined),
            "min_education": _extract_min_education(combined),
            "min_experience": _extract_min_experience(combined),
            "max_short_stints": 1,
            "min_keyword_hits": min(2, max(1, len(required_keywords_any))),
            "required_keywords_any": required_keywords_any,
            "keyword_pool": keyword_pool,
            "salary_range": _extract_salary_range(combined),
        },
    }


def _build_blacklist_keywords(text: str) -> list[str]:
    hits = [keyword for keyword in DEFAULT_BLACKLIST_KEYWORDS if keyword in text]
    return hits or list(DEFAULT_BLACKLIST_KEYWORDS)


def _extract_min_education(text: str) -> str:
    normalized = text.replace("学历要求", "")
    for education in EDUCATION_ORDER:
        patterns = [
            rf"{education}及以上",
            rf"{education}以上",
            rf"至少{education}",
            rf"{education}学历",
            education,
        ]
        if any(re.search(pattern, normalized, re.I) for pattern in patterns):
            return education
    return "不限"


def _extract_min_experience(text: str) -> int:
    patterns = [
        r"(\d+)\s*年(?:以上|及以上)",
        r"(\d+)\s*年以上",
        r"(\d+)\s*-\s*(\d+)\s*年",
        r"(\d+)\s*至\s*(\d+)\s*年",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        values = [int(value) for value in match.groups() if value]
        if values:
            return min(values)
    return 0


def _extract_max_age(text: str) -> int:
    match = re.search(r"年龄.*?(\d{2})\s*岁", text)
    if match:
        return int(match.group(1))
    return 45


def _extract_salary_range(text: str) -> list[int]:
    match = re.search(r"(\d+)\s*[-~至到]\s*(\d+)\s*[kK]", text)
    if match:
        low, high = int(match.group(1)), int(match.group(2))
        return [low * 1000, high * 1000]
    return [15000, 50000]


def _build_required_keywords(job_title: str, job_description: str) -> list[str]:
    title_keywords = _extract_keywords(job_title)
    description_keywords = _extract_keywords(job_description)
    merged = _unique(title_keywords + description_keywords)
    return merged[:4] or ["沟通"]


def _build_keyword_pool(job_title: str, job_description: str, required_keywords_any: list[str]) -> list[str]:
    title_keywords = _extract_keywords(job_title)
    description_keywords = _extract_keywords(job_description, max_keywords=16)
    merged = _unique(required_keywords_any + title_keywords + description_keywords)
    if len(merged) < 3:
        merged.extend(keyword for keyword in ["沟通", "协作", "执行"] if keyword not in merged)
    return merged[:12]


def _extract_keywords(text: str, *, max_keywords: int = 12) -> list[str]:
    if not text:
        return []

    found: list[str] = []
    for keyword in KNOWN_KEYWORDS:
        if keyword.lower() in text.lower():
            found.append(keyword)

    chinese_terms = re.findall(r"[\u4e00-\u9fff]{2,8}", text)
    for term in chinese_terms:
        cleaned = _clean_keyword(term)
        if cleaned:
            found.append(cleaned)

    ascii_terms = re.findall(r"[A-Za-z][A-Za-z0-9+.#/-]{1,24}", text)
    for term in ascii_terms:
        found.append(term)

    unique = _unique(found)
    return unique[:max_keywords]


def _clean_keyword(term: str) -> str:
    candidate = term.strip("，。；：、（）()【】[] ")
    if len(candidate) < 2 or len(candidate) > 8:
        return ""
    if candidate in GENERIC_STOPWORDS:
        return ""
    if re.search(r"\d", candidate):
        return ""
    if any(stop in candidate for stop in ["学历", "以上", "以下", "经验", "能力要求", "任职要求"]):
        return ""
    if any(stop in candidate for stop in ["负责", "要求", "岗位", "职责", "优先", "相关经验"]):
        return ""
    return candidate


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        cleaned = (item or "").strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(cleaned)
    return result
