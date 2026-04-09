import unittest

from src.profile_builder import build_profile_from_jd


class ProfileBuilderTests(unittest.TestCase):
    def test_build_profile_from_jd_extracts_core_rules_for_legal_role(self):
        profile = build_profile_from_jd(
            "法务总监",
            (
                "本科及以上学历，5年以上法务经验，熟悉合同审核、合规、诉讼仲裁、知识产权。"
                "有涉外、跨境、英语工作能力优先。外包、劳务派遣不考虑。"
            ),
        )

        self.assertEqual(profile["job_title"], "法务总监")
        self.assertIn("本科及以上学历", profile["job_description"])
        self.assertEqual(profile["rules"]["min_education"], "本科")
        self.assertEqual(profile["rules"]["min_experience"], 5)
        self.assertIn("法务", profile["rules"]["required_keywords_any"])
        self.assertIn("合规", profile["rules"]["keyword_pool"])
        self.assertIn("外包", profile["rules"]["blacklist_keywords"])

    def test_build_profile_from_jd_uses_fallbacks_when_hard_requirements_missing(self):
        profile = build_profile_from_jd(
            "招聘专员",
            "负责招聘、面试安排、候选人沟通和招聘数据统计。",
        )

        self.assertEqual(profile["rules"]["min_education"], "不限")
        self.assertEqual(profile["rules"]["min_experience"], 0)
        self.assertGreaterEqual(len(profile["rules"]["required_keywords_any"]), 1)
        self.assertGreaterEqual(len(profile["rules"]["keyword_pool"]), 3)


if __name__ == "__main__":
    unittest.main()
