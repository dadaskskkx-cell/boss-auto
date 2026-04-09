import unittest

from PIL import Image

from src.resume_filter import ResumeFilter


class DummyLLMClient:
    def __init__(self, response: str, image_response: str = ""):
        self.response = response
        self.image_response = image_response
        self.prompts: list[str] = []
        self.image_calls: list[tuple[str, str]] = []

    def chat(self, prompt: str, system: str = "你是一个专业的HR助手。") -> str:
        self.prompts.append(prompt)
        return self.response

    def understand_image(self, image_path: str, prompt: str) -> str:
        self.image_calls.append((image_path, prompt))
        return self.image_response


class ResumeFilterTests(unittest.TestCase):
    def test_quick_filter_resume_rejects_when_card_fails_basic_requirements(self):
        llm = DummyLLMClient("")
        profile = {
            "job_title": "法务总监",
            "job_description": "法律岗位JD",
            "rules": {
                "min_education": "本科",
                "min_experience": 5,
                "required_keywords_any": ["法务", "律师"],
            },
        }
        resume = {
            "raw_text": "29岁 3年 大专 行政助理 8-12K",
            "detail_text": "",
        }

        result = ResumeFilter(profile, llm).quick_filter_resume(resume)

        self.assertFalse(result["passed"])
        self.assertTrue(
            any(token in result["reason"] for token in ("学历", "岗位方向", "经验")),
            result["reason"],
        )

    def test_filter_resume_can_match_with_rules_only_mode(self):
        llm = DummyLLMClient("")
        profile = {
            "job_title": "法务总监",
            "job_description": "涉外法务管理",
            "rules": {
                "use_llm": False,
                "required_keywords_any": ["法务", "律师"],
            },
        }
        resume = {
            "raw_text": "32岁 8年 本科 法务经理，负责合同审核和合规制度",
            "detail_text": "法务负责人，处理争议和内部制度建设",
        }

        result = ResumeFilter(profile, llm).filter_resume(resume)

        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["score"], 100)
        self.assertEqual(llm.prompts, [])

    def test_filter_resume_rejects_when_resume_is_too_hoppy(self):
        llm = DummyLLMClient("")
        profile = {
            "job_title": "法务总监",
            "job_description": "涉外法务管理",
            "rules": {
                "use_llm": False,
                "required_keywords_any": ["法务", "律师"],
                "max_short_stints": 1,
            },
        }
        resume = {
            "raw_text": "31岁 7年 本科 法务经理",
            "detail_text": (
                "法务经理\n"
                "2025.01-2025.08 某公司 法务经理\n"
                "2024.01-2024.10 某集团 法务主管\n"
                "2023.01-2023.12 某企业 法务BP\n"
            ),
        }

        result = ResumeFilter(profile, llm).filter_resume(resume)

        self.assertEqual(result["status"], "rejected")
        self.assertIn("跳槽", result["reason"])

    def test_filter_resume_rejects_when_required_keywords_all_are_missing(self):
        llm = DummyLLMClient("评分: 95\n理由: 匹配\n建议: matched")
        profile = {
            "job_title": "法务总监",
            "job_description": "涉外法务管理",
            "rules": {
                "required_keywords_all": ["法务", "海外", "合规"],
            },
        }
        resume = {
            "raw_text": "32岁 8年 本科 法律顾问，负责合同审核和诉讼处理",
            "detail_text": "律师助理，诉讼案件经验丰富",
        }

        result = ResumeFilter(profile, llm).filter_resume(resume)

        self.assertEqual(result["status"], "rejected")
        self.assertIn("缺少必备关键词", result["reason"])
        self.assertEqual(llm.prompts, [])

    def test_filter_resume_uses_llm_score_threshold_from_rules(self):
        llm = DummyLLMClient("评分: 74\n理由: 经验接近\n建议: matched")
        profile = {
            "job_title": "法务总监",
            "job_description": "涉外法务管理",
            "rules": {
                "required_keywords_any": ["法务", "律师"],
                "llm_score_threshold": 75,
            },
        }
        resume = {
            "raw_text": "30岁 7年 本科 法务经理，负责合规和合同审核",
            "detail_text": "法务负责人，处理劳动争议与合规事务",
        }

        result = ResumeFilter(profile, llm).filter_resume(resume)

        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["score"], 74)

    def test_filter_resume_rejects_when_llm_returns_empty(self):
        llm = DummyLLMClient("")
        profile = {
            "job_title": "法务总监",
            "job_description": "涉外法务管理",
            "rules": {
                "required_keywords_any": ["法务", "律师"],
            },
        }
        resume = {
            "raw_text": "31岁 6年 本科 法务经理，负责合同管理和合规制度",
            "detail_text": "法务团队管理经验，参与重大纠纷处理",
        }

        result = ResumeFilter(profile, llm).filter_resume(resume)

        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["score"], 0)
        self.assertIn("LLM", result["reason"])

    def test_quick_filter_resume_uses_structured_vision_fields_and_program_rules(self):
        llm = DummyLLMClient(
            "",
            image_response=(
                '{"education":"本科","years_experience":8,"age":32,'
                '"salary_k_month":25,'
                '"keywords":["法务","合规","合同","律师"],'
                '"summary":"本科 8年 法务 合规 合同"}'
            ),
        )
        profile = {
            "job_title": "法务总监",
            "job_description": "涉外法务管理",
            "rules": {
                "min_education": "本科",
                "min_experience": 5,
                "required_keywords_any": ["法务", "律师", "合规"],
                "keyword_pool": ["法务", "律师", "合规", "合同"],
                "min_keyword_hits": 2,
            },
        }
        resume = {
            "raw_text": "",
            "card_image": Image.new("RGB", (240, 120), "white"),
        }

        result = ResumeFilter(profile, llm).quick_filter_resume(resume)

        self.assertTrue(result["passed"])
        self.assertIn("规则筛选通过", result["reason"])
        self.assertEqual(len(llm.image_calls), 1)

    def test_quick_filter_resume_rejects_when_vision_extracted_keywords_do_not_match(self):
        llm = DummyLLMClient(
            "",
            image_response=(
                '{"education":"本科","years_experience":6,"age":30,'
                '"salary_k_month":18,'
                '"keywords":["行政","人事"],'
                '"summary":"本科 6年 行政 人事"}'
            ),
        )
        profile = {
            "job_title": "法务总监",
            "job_description": "涉外法务管理",
            "rules": {
                "min_education": "本科",
                "min_experience": 5,
                "required_keywords_any": ["法务", "律师"],
            },
        }
        resume = {
            "raw_text": "",
            "card_image": Image.new("RGB", (240, 120), "white"),
        }

        result = ResumeFilter(profile, llm).quick_filter_resume(resume)

        self.assertFalse(result["passed"])
        self.assertTrue(
            any(token in result["reason"] for token in ("缺少关键方向关键词", "岗位方向不符")),
            result["reason"],
        )
        self.assertEqual(len(llm.image_calls), 1)

    def test_quick_filter_resume_rejects_when_vision_returns_empty(self):
        llm = DummyLLMClient("", image_response="")
        profile = {
            "job_title": "法务总监",
            "job_description": "涉外法务管理",
            "rules": {
                "min_education": "本科",
                "min_experience": 5,
                "required_keywords_any": ["法务", "律师"],
            },
        }
        resume = {
            "raw_text": "29岁 3年 大专 行政助理 8-12K",
            "card_image": Image.new("RGB", (240, 120), "white"),
        }

        result = ResumeFilter(profile, llm).quick_filter_resume(resume)

        self.assertFalse(result["passed"])
        self.assertEqual(len(llm.image_calls), 1)
        self.assertIn("视觉识别失败", result["reason"])

    def test_quick_filter_resume_accepts_markdown_wrapped_json_array_from_vision(self):
        llm = DummyLLMClient(
            "",
            image_response=(
                "```json\n"
                "[\n"
                '  {"education":"本科","years_experience":8,"age":32,"salary_k_month":25,'
                '"keywords":["法务","合规","合同"],"summary":"本科 8年 法务 合规 合同"}\n'
                "]\n"
                "```"
            ),
        )
        profile = {
            "job_title": "法务总监",
            "job_description": "涉外法务管理",
            "rules": {
                "min_education": "本科",
                "min_experience": 5,
                "required_keywords_any": ["法务", "律师", "合规"],
                "keyword_pool": ["法务", "律师", "合规", "合同"],
                "min_keyword_hits": 2,
            },
        }
        resume = {
            "raw_text": "",
            "card_image": Image.new("RGB", (240, 120), "white"),
        }

        result = ResumeFilter(profile, llm).quick_filter_resume(resume)

        self.assertTrue(result["passed"])
        self.assertIn("规则筛选通过", result["reason"])
        self.assertEqual(result["vision_extracted"]["education"], "本科")
        self.assertEqual(len(llm.image_calls), 1)

    def test_quick_filter_resume_accepts_multi_item_json_array_from_vision(self):
        llm = DummyLLMClient(
            "",
            image_response=(
                "["
                '{"education":"本科","years_experience":8,"age":32,"salary_k_month":25,'
                '"keywords":["法务","合规","合同"],"summary":"本科 8年 法务 合规 合同"},'
                '{"education":"大专","years_experience":2,"age":22,"salary_k_month":8,'
                '"keywords":["行政"],"summary":"无关噪声"}'
                "]"
            ),
        )
        profile = {
            "job_title": "法务总监",
            "job_description": "涉外法务管理",
            "rules": {
                "min_education": "本科",
                "min_experience": 5,
                "required_keywords_any": ["法务", "律师", "合规"],
                "keyword_pool": ["法务", "律师", "合规", "合同"],
                "min_keyword_hits": 2,
            },
        }
        resume = {
            "raw_text": "",
            "card_image": Image.new("RGB", (240, 120), "white"),
        }

        result = ResumeFilter(profile, llm).quick_filter_resume(resume)

        self.assertTrue(result["passed"])
        self.assertIn("规则筛选通过", result["reason"])
        self.assertEqual(result["vision_extracted"]["years_experience"], 8)


if __name__ == "__main__":
    unittest.main()
