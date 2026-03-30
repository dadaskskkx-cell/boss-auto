"""简历筛选 - 规则硬筛选 + LLM语义评分"""

import re
from loguru import logger

from .llm_client import LLMClient


# 学历等级映射
EDUCATION_RANK = {
    "初中": 1, "中专": 2, "高中": 3, "大专": 4,
    "本科": 5, "硕士": 6, "博士": 7,
}


class ResumeFilter:
    def __init__(self, profile: dict, llm_client: LLMClient):
        self.profile = profile
        self.rules = profile.get("rules", {})
        self.llm_client = llm_client
        self.llm_score_threshold = 60

    def filter_resume(self, resume: dict) -> dict:
        """双重筛选：规则硬筛选 → LLM语义评分"""
        text = resume.get("raw_text", "") + " " + resume.get("detail_text", "")

        # 第一层：规则硬筛选
        rule_result = self._rule_filter(resume, text)
        if not rule_result["passed"]:
            return {
                "status": "rejected",
                "reason": rule_result["reason"],
                "score": 0,
            }

        # 第二层：LLM语义评分
        llm_result = self._llm_filter(text)
        return llm_result

    def _rule_filter(self, resume: dict, text: str) -> dict:
        """规则硬筛选"""
        rules = self.rules

        # 1. 学历筛选
        min_edu = rules.get("min_education")
        if min_edu:
            candidate_edu = self._extract_education(text)
            if candidate_edu:
                min_rank = EDUCATION_RANK.get(min_edu, 0)
                cand_rank = EDUCATION_RANK.get(candidate_edu, 0)
                if cand_rank < min_rank:
                    return {"passed": False, "reason": f"学历不符: {candidate_edu} < {min_edu}"}

        # 2. 工作年限筛选
        min_exp = rules.get("min_experience")
        if min_exp:
            exp = self._extract_experience(text)
            if exp is not None and exp < min_exp:
                return {"passed": False, "reason": f"经验不足: {exp}年 < {min_exp}年"}

        # 3. 年龄筛选
        max_age = rules.get("max_age")
        if max_age:
            age = self._extract_age(text)
            if age is not None and age > max_age:
                return {"passed": False, "reason": f"年龄超标: {age}岁 > {max_age}岁"}

        # 4. 薪资范围筛选
        salary_range = rules.get("salary_range")
        if salary_range and len(salary_range) == 2:
            salary = self._extract_salary(text)
            if salary is not None:
                if salary < salary_range[0] or salary > salary_range[1]:
                    return {"passed": False, "reason": f"薪资不符: {salary}"}

        # 5. 黑名单关键词
        blacklist = rules.get("blacklist_keywords", [])
        for kw in blacklist:
            if kw in text:
                return {"passed": False, "reason": f"包含黑名单关键词: {kw}"}

        return {"passed": True, "reason": "规则筛选通过"}

    def _llm_filter(self, text: str) -> dict:
        """LLM语义评分"""
        prompt_template = self.profile.get("llm_prompt", "")
        prompt = prompt_template.format(
            job_title=self.profile.get("job_title", ""),
            job_description=self.profile.get("job_description", ""),
            resume_text=text[:2000],  # 限制长度避免token过多
        )

        response = self.llm_client.chat(prompt)
        if not response:
            logger.warning("LLM返回为空，默认通过")
            return {"status": "matched", "reason": "LLM评分跳过", "score": 70}

        # 解析LLM返回
        return self._parse_llm_response(response)

    def _parse_llm_response(self, response: str) -> dict:
        """解析LLM返回的评分结果"""
        score = 0
        reason = ""
        suggestion = "rejected"

        # 提取评分
        score_match = re.search(r"评分[:：]\s*(\d+)", response)
        if score_match:
            score = int(score_match.group(1))

        # 提取理由
        reason_match = re.search(r"理由[:：]\s*(.+?)(?:\n|建议|$)", response)
        if reason_match:
            reason = reason_match.group(1).strip()

        # 提取建议
        if "matched" in response.lower() or score >= self.llm_score_threshold:
            suggestion = "matched"

        return {
            "status": suggestion,
            "reason": reason,
            "score": score,
        }

    # ---- 信息提取辅助方法 ----

    def _extract_education(self, text: str) -> str | None:
        """从文本中提取学历"""
        for edu in ["博士", "硕士", "本科", "大专", "中专", "高中"]:
            if edu in text:
                return edu
        return None

    def _extract_experience(self, text: str) -> int | None:
        """从文本中提取工作年限"""
        patterns = [
            r"(\d+)年工作经验", r"(\d+)年经验", r"工作经验(\d+)年",
            r"工作(\d+)年", r"经验(\d+)年",
        ]
        for p in patterns:
            m = re.search(p, text)
            if m:
                return int(m.group(1))
        return None

    def _extract_age(self, text: str) -> int | None:
        """从文本中提取年龄"""
        m = re.search(r"(\d{2})岁", text)
        if m:
            return int(m.group(1))
        return None

    def _extract_salary(self, text: str) -> int | None:
        """从文本中提取期望薪资（取中间值）"""
        # 匹配 "15-20K" "15K-20K" "15000-20000" 等格式
        m = re.search(r"(\d+)[kK]?[-—](\d+)[kK]", text)
        if m:
            low, high = int(m.group(1)), int(m.group(2))
            # 如果单位是K则乘1000
            if max(low, high) < 100:
                return (low + high) * 500  # 取中间值
            return (low + high) // 2
        return None
