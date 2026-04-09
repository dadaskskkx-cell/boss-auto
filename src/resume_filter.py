"""简历筛选 - 规则硬筛选 + LLM语义评分"""

import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
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
        self.llm_score_threshold = int(self.rules.get("llm_score_threshold", 60))
        self.use_llm = bool(self.rules.get("use_llm", True))

    def quick_filter_resume(self, resume: dict) -> dict:
        """基于卡片摘要做快速硬筛，未通过则不打开详情。"""
        vision_result = self._vision_quick_filter(resume)
        if vision_result is not None:
            return vision_result
        if resume.get("card_image") is not None:
            return {"passed": False, "reason": "视觉识别失败"}
        text = resume.get("raw_text", "")
        return self._rule_filter(resume, text, quick_only=True)

    def filter_resume(self, resume: dict) -> dict:
        """双重筛选：规则硬筛选 → LLM语义评分"""
        text = resume.get("raw_text", "") + " " + resume.get("detail_text", "")

        # 第一层：规则硬筛选
        rule_result = self._rule_filter(resume, text, quick_only=False)
        if not rule_result["passed"]:
            return {
                "status": "rejected",
                "reason": rule_result["reason"],
                "score": 0,
            }

        if not self.use_llm:
            return {
                "status": "matched",
                "reason": rule_result["reason"],
                "score": 100,
            }

        # 第二层：LLM语义评分
        llm_result = self._llm_filter(text)
        return llm_result

    def _rule_filter(self, resume: dict, text: str, *, quick_only: bool) -> dict:
        """规则硬筛选"""
        rules = self.rules
        extracted = resume.get("vision_extracted") or {}
        extracted_keywords = [str(x) for x in extracted.get("keywords", []) if x]
        extracted_text = " ".join(
            [
                text,
                extracted.get("summary", ""),
                " ".join(extracted_keywords),
                str(extracted.get("education") or ""),
                f"{extracted.get('years_experience')}年经验" if extracted.get("years_experience") is not None else "",
                f"{extracted.get('age')}岁" if extracted.get("age") is not None else "",
                f"{extracted.get('salary_k_month')}K" if extracted.get("salary_k_month") is not None else "",
            ]
        ).strip()
        text = extracted_text or text

        blacklist = rules.get("blacklist_keywords", [])
        for kw in blacklist:
            if kw in text:
                return {"passed": False, "reason": f"包含黑名单关键词: {kw}"}

        reject_keywords = rules.get("reject_keywords", ["行政", "文员", "秘书", "纯助理"])
        for kw in reject_keywords:
            if kw and kw in text:
                return {"passed": False, "reason": f"岗位方向不符: {kw}"}

        required_keywords_all = rules.get("required_keywords_all") or rules.get("required_skills", [])
        for kw in required_keywords_all:
            if kw and kw not in text:
                return {"passed": False, "reason": f"缺少必备关键词: {kw}"}

        required_keywords_any = rules.get("required_keywords_any", [])
        if required_keywords_any and not any(kw in text for kw in required_keywords_any if kw):
            return {
                "passed": False,
                "reason": f"缺少关键方向关键词: {'/'.join(required_keywords_any)}",
            }

        # 1. 学历筛选
        min_edu = rules.get("min_education")
        if min_edu:
            candidate_edu = extracted.get("education") or self._extract_education(text)
            if candidate_edu:
                min_rank = EDUCATION_RANK.get(min_edu, 0)
                cand_rank = EDUCATION_RANK.get(candidate_edu, 0)
                if cand_rank < min_rank:
                    return {"passed": False, "reason": f"学历不符: {candidate_edu} < {min_edu}"}

        # 2. 工作年限筛选
        min_exp = rules.get("min_experience")
        if min_exp:
            exp = extracted.get("years_experience")
            if exp is None:
                exp = self._extract_experience(text)
            if exp is not None and exp < min_exp:
                return {"passed": False, "reason": f"经验不足: {exp}年 < {min_exp}年"}

        # 3. 年龄筛选
        max_age = rules.get("max_age")
        if max_age:
            age = extracted.get("age")
            if age is None:
                age = self._extract_age(text)
            if age is not None and age > max_age:
                return {"passed": False, "reason": f"年龄超标: {age}岁 > {max_age}岁"}

        # 4. 薪资范围筛选
        salary_range = rules.get("salary_range")
        if salary_range and len(salary_range) == 2:
            salary = extracted.get("salary_k_month")
            if salary is not None and salary < 1000:
                salary = int(salary) * 1000
            if salary is None:
                salary = self._extract_salary(text)
            if salary is not None:
                if salary < salary_range[0] or salary > salary_range[1]:
                    return {"passed": False, "reason": f"薪资不符: {salary}"}

        min_keyword_hits = int(rules.get("min_keyword_hits", 0) or 0)
        keyword_pool = rules.get("keyword_pool") or []
        if min_keyword_hits and keyword_pool:
            hit_count = self._count_keyword_hits(text, keyword_pool)
            if hit_count < min_keyword_hits:
                return {"passed": False, "reason": f"关键词命中不足: {hit_count} < {min_keyword_hits}"}

        max_short_stints = rules.get("max_short_stints")
        if max_short_stints is not None:
            short_stints = self._count_short_stints(text)
            if short_stints > int(max_short_stints):
                return {"passed": False, "reason": f"跳槽偏频繁: {short_stints}段短任职"}

        return {"passed": True, "reason": "规则筛选通过"}

    def _vision_quick_filter(self, resume: dict) -> dict | None:
        card_image = resume.get("card_image")
        understand_image = getattr(self.llm_client, "understand_image", None)
        if card_image is None or not callable(understand_image):
            return None

        image_path = None
        temp_path = None
        try:
            if isinstance(card_image, (str, Path)):
                image_path = str(card_image)
            else:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    temp_path = Path(tmp.name)
                card_image.save(temp_path, format="PNG")
                image_path = str(temp_path)

            response = understand_image(image_path, self._build_vision_prompt())
            if not response:
                return None

            parsed = self._parse_vision_extraction(response)
            if parsed is None:
                logger.warning(f"视觉快筛结果无法解析: {response[:120]}")
                return {"passed": False, "reason": "视觉识别失败"}
            return parsed
        except Exception as exc:
            logger.warning(f"视觉快筛失败: {exc}")
            return {"passed": False, "reason": "视觉识别失败"}
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()

    def _build_vision_prompt(self) -> str:
        rules = self.rules
        lines = [
            "你在看 Boss 直聘推荐列表中的单张候选人卡片截图。",
            "只能依据截图里清晰可见的信息判断，不要猜测，不要补全不可见内容。",
            f"岗位名称：{self.profile.get('job_title', '')}",
            f"岗位JD摘要：{self.profile.get('job_description', '')[:800]}",
            "硬性快筛规则：",
        ]

        if rules.get("min_education"):
            lines.append(f"- 学历至少 {rules['min_education']}")
        if rules.get("min_experience"):
            lines.append(f"- 工作年限至少 {rules['min_experience']} 年")
        if rules.get("max_age"):
            lines.append(f"- 年龄不超过 {rules['max_age']} 岁")
        if rules.get("required_keywords_any"):
            lines.append(
                "- 候选人方向至少命中其一："
                + " / ".join(str(x) for x in rules["required_keywords_any"] if x)
            )
        if rules.get("keyword_pool"):
            lines.append(
                "- 关键词池参考："
                + " / ".join(str(x) for x in rules["keyword_pool"] if x)
            )
        if rules.get("min_keyword_hits"):
            lines.append(f"- 关键词池至少命中 {rules['min_keyword_hits']} 个")
        if rules.get("max_short_stints") is not None:
            lines.append(
                f"- 倾向稳定，近几年短任职经历不超过 {rules['max_short_stints']} 段；若卡片里看不出来，不要猜"
            )
        if rules.get("reject_keywords"):
            lines.append(
                "- 明显排除方向："
                + " / ".join(str(x) for x in rules["reject_keywords"] if x)
            )

        lines.extend(
            [
                "任务：只做卡片信息提取，不要帮我做通过/不通过决策。",
                "请从截图中尽可能提取以下字段，未知就填 null 或空数组：",
                '{"education":"本科/硕士/博士/大专/null","years_experience":8,"age":32,"salary_k_month":25,"keywords":["法务","律师","合规"],"summary":"一句话概括卡片可见信息"}',
                "其中 keywords 只保留截图里明确可见的岗位方向/能力关键词，不要猜。",
                "仅输出 JSON，不要输出其他文字。",
            ]
        )
        return "\n".join(lines)

    def _parse_vision_extraction(self, response: str) -> dict | None:
        payload = response.strip()

        code_block_match = re.search(r"```(?:json)?\s*(.*?)\s*```", payload, re.S | re.I)
        if code_block_match:
            payload = code_block_match.group(1).strip()

        data = None
        candidate_payloads = [payload]

        array_match = re.search(r"\[.*\]", payload, re.S)
        if array_match:
            candidate_payloads.append(array_match.group(0))

        object_match = re.search(r"\{.*\}", payload, re.S)
        if object_match:
            candidate_payloads.append(object_match.group(0))

        for candidate in candidate_payloads:
            try:
                data = json.loads(candidate)
                break
            except json.JSONDecodeError:
                continue

        if data is None:
            return None

        if isinstance(data, list):
            data = next((item for item in data if isinstance(item, dict)), None)
            if data is None:
                return None
        elif not isinstance(data, dict):
            return None

        extracted = {
            "education": data.get("education"),
            "years_experience": self._safe_int(data.get("years_experience")),
            "age": self._safe_int(data.get("age")),
            "salary_k_month": self._safe_int(data.get("salary_k_month")),
            "keywords": data.get("keywords") or [],
            "summary": str(data.get("summary") or "").strip(),
        }
        synthetic_resume = {"vision_extracted": extracted, "raw_text": ""}
        rule_result = self._rule_filter(synthetic_resume, "", quick_only=True)
        rule_result["vision_extracted"] = extracted
        return rule_result

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
            logger.warning("LLM返回为空，默认拒绝")
            return {"status": "rejected", "reason": "LLM评分失败", "score": 0}

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

        # 只有达到阈值才允许放行，不能只靠 matched 文案放行
        if score >= self.llm_score_threshold:
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

    def _count_keyword_hits(self, text: str, keywords: list[str]) -> int:
        return sum(1 for kw in keywords if kw and kw in text)

    def _safe_int(self, value) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _count_short_stints(self, text: str) -> int:
        now = datetime.now()
        current_total_months = now.year * 12 + now.month
        short_stints = 0
        pattern = re.compile(
            r"(\d{4})[.\-\/](\d{1,2})\s*[—\-－–~至]+\s*(?:(\d{4})[.\-\/](\d{1,2})|至今)"
        )

        for match in pattern.finditer(text):
            start_year = int(match.group(1))
            start_month = int(match.group(2))
            if match.group(3) and match.group(4):
                end_year = int(match.group(3))
                end_month = int(match.group(4))
            else:
                end_year = now.year
                end_month = now.month

            start_total = start_year * 12 + start_month
            end_total = end_year * 12 + end_month
            duration = max(0, end_total - start_total + 1)
            if current_total_months - start_total <= 48 and duration <= 12:
                short_stints += 1

        return short_stints
