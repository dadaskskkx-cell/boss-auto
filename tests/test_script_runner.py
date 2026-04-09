import unittest
import hashlib

from src.script_runner import ScreeningScript


class ScriptRunnerTests(unittest.TestCase):
    def test_run_scans_visible_candidates_and_greets_only_matches(self):
        calls = {"greeted": [], "scrolled": 0, "opened": 0, "closed": 0, "captured": 0}

        class FakeCrawler:
            def __init__(self):
                self.pages = [
                    [
                        {"name": "李开新", "raw_text": "李开新 32岁 10年以上 本科 法务总监"},
                        {"name": "继续沟通", "raw_text": "继续沟通 已处理"},
                    ],
                    [],
                ]

            def start(self):
                return None

            def stop(self):
                return None

            def get_visible_resumes(self):
                return self.pages.pop(0) if self.pages else []

            def capture_resume_card(self, resume):
                calls["captured"] += 1
                return f"card:{resume['name']}"

            def open_resume(self, resume):
                calls["opened"] += 1
                return resume["name"] == "李开新"

            def get_active_resume_detail(self):
                return {"detail_text": "有10年法务和商业经验"}

            def close_resume_detail(self):
                calls["closed"] += 1
                return True

            def scroll_recommend_list(self, _pages=1):
                calls["scrolled"] += 1

            def greet_current_candidate(self, resume):
                calls["greeted"].append(resume["name"])
                return "sent"

        class FakeFilter:
            def quick_filter_resume(self, resume):
                return {"passed": resume["name"] == "李开新", "reason": "快筛"}

            def filter_resume(self, _resume):
                return {"status": "matched", "score": 88, "reason": "匹配"}

        class FakeMessenger:
            def __init__(self):
                self.marked = []

            def is_processed(self, _key):
                return False

            def mark_processed(self, key):
                self.marked.append(key)

        runner = ScreeningScript(
            config={"workflow": {"screen_scan_limit": 2}},
            profile={"job_title": "法务总监", "job_description": "法律岗位JD"},
            crawler=FakeCrawler(),
            resume_filter=FakeFilter(),
            messenger=FakeMessenger(),
        )

        result = runner.run()

        self.assertEqual(result["matched_sent"], 1)
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(calls["greeted"], ["李开新"])
        self.assertEqual(calls["captured"], 1)
        self.assertEqual(calls["opened"], 0)
        self.assertGreaterEqual(calls["scrolled"], 1)
        self.assertEqual(calls["closed"], 0)

    def test_run_skips_candidates_that_fail_quick_filter(self):
        calls = {"opened": 0, "greeted": 0}

        class FakeCrawler:
            def __init__(self):
                self.pages = [
                    [{"name": "张某", "raw_text": "张某 29岁 3年 大专 行政助理"}],
                    [],
                ]

            def start(self):
                return None

            def stop(self):
                return None

            def get_visible_resumes(self):
                return self.pages.pop(0) if self.pages else []

            def open_resume(self, _resume):
                calls["opened"] += 1
                return True

            def get_active_resume_detail(self):
                raise AssertionError("detail should not be fetched when quick filter fails")

            def close_resume_detail(self):
                return False

            def scroll_recommend_list(self, _pages=1):
                return None

            def greet_current_candidate(self, _resume):
                calls["greeted"] += 1
                return "sent"

        class FakeFilter:
            def quick_filter_resume(self, _resume):
                return {"passed": False, "reason": "学历不符"}

            def filter_resume(self, _resume):
                raise AssertionError("full filter should not run")

        class FakeMessenger:
            def is_processed(self, _key):
                return False

            def mark_processed(self, _key):
                return None

        runner = ScreeningScript(
            config={"workflow": {"screen_scan_limit": 2}},
            profile={"job_title": "法务总监", "job_description": "法律岗位JD"},
            crawler=FakeCrawler(),
            resume_filter=FakeFilter(),
            messenger=FakeMessenger(),
        )

        result = runner.run()

        self.assertEqual(result["matched_sent"], 0)
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(calls["opened"], 0)
        self.assertEqual(calls["greeted"], 0)

    def test_run_stops_after_configured_consecutive_empty_screens(self):
        calls = {"scrolled": 0}

        class FakeCrawler:
            def start(self):
                return None

            def stop(self):
                return None

            def get_visible_resumes(self):
                return []

            def open_resume(self, _resume):
                return False

            def get_active_resume_detail(self):
                return {}

            def close_resume_detail(self):
                return True

            def scroll_recommend_list(self, _pages=1):
                calls["scrolled"] += 1

            def greet_current_candidate(self, _resume):
                return "sent"

        class FakeFilter:
            def quick_filter_resume(self, _resume):
                return {"passed": False, "reason": "空屏"}

            def filter_resume(self, _resume):
                return {"status": "rejected", "reason": "空屏"}

        class FakeMessenger:
            def is_processed(self, _key):
                return False

            def mark_processed(self, _key):
                return None

        runner = ScreeningScript(
            config={"workflow": {"screen_scan_limit": 0, "max_empty_screens": 3}},
            profile={"job_title": "法务总监", "job_description": "法律岗位JD"},
            crawler=FakeCrawler(),
            resume_filter=FakeFilter(),
            messenger=FakeMessenger(),
        )

        result = runner.run()

        self.assertEqual(result["screens"], 3)
        self.assertEqual(result["visible"], 0)
        self.assertEqual(calls["scrolled"], 2)

    def test_run_ignores_fixed_screen_limit_when_set_to_zero(self):
        calls = {"scrolled": 0}

        class FakeCrawler:
            def __init__(self):
                self.pages = [
                    [{"name": "李开新", "raw_text": "李开新 32岁 10年以上 本科 法务总监"}],
                    [],
                    [],
                ]

            def start(self):
                return None

            def stop(self):
                return None

            def get_visible_resumes(self):
                return self.pages.pop(0) if self.pages else []

            def open_resume(self, _resume):
                return False

            def get_active_resume_detail(self):
                return {}

            def close_resume_detail(self):
                return True

            def scroll_recommend_list(self, _pages=1):
                calls["scrolled"] += 1

            def greet_current_candidate(self, _resume):
                return "sent"

        class FakeFilter:
            def quick_filter_resume(self, _resume):
                return {"passed": False, "reason": "先不匹配"}

            def filter_resume(self, _resume):
                return {"status": "rejected", "reason": "先不匹配"}

        class FakeMessenger:
            def is_processed(self, _key):
                return False

            def mark_processed(self, _key):
                return None

        runner = ScreeningScript(
            config={"workflow": {"screen_scan_limit": 0, "max_empty_screens": 2}},
            profile={"job_title": "法务总监", "job_description": "法律岗位JD"},
            crawler=FakeCrawler(),
            resume_filter=FakeFilter(),
            messenger=FakeMessenger(),
        )

        result = runner.run()

        self.assertEqual(result["screens"], 3)
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(calls["scrolled"], 2)

    def test_run_greets_stub_candidates_from_button_fallback_without_opening_detail(self):
        calls = {"opened": 0, "greeted": 0}

        class FakeCrawler:
            def __init__(self):
                self.pages = [
                    [{"name": "未知候选人", "raw_text": "", "requires_detail": True, "click_point": (100, 200)}],
                    [],
                ]

            def start(self):
                return None

            def stop(self):
                return None

            def get_visible_resumes(self):
                return self.pages.pop(0) if self.pages else []

            def open_resume(self, _resume):
                calls["opened"] += 1
                return True

            def get_active_resume_detail(self):
                raise AssertionError("detail should not be fetched in list-only mode")

            def close_resume_detail(self):
                return True

            def scroll_recommend_list(self, _pages=1):
                return None

            def greet_current_candidate(self, _resume):
                calls["greeted"] += 1
                return "sent"

        class FakeFilter:
            def quick_filter_resume(self, _resume):
                return {"passed": True, "reason": "列表匹配"}

            def filter_resume(self, _resume):
                raise AssertionError("full filter should not run in list-only mode")

        class FakeMessenger:
            def is_processed(self, _key):
                return False

            def mark_processed(self, _key):
                return None

        runner = ScreeningScript(
            config={"workflow": {"screen_scan_limit": 2}},
            profile={"job_title": "法务总监", "job_description": "法律岗位JD"},
            crawler=FakeCrawler(),
            resume_filter=FakeFilter(),
            messenger=FakeMessenger(),
        )

        result = runner.run()

        self.assertEqual(result["matched_sent"], 1)
        self.assertEqual(calls["opened"], 0)
        self.assertEqual(calls["greeted"], 1)

    def test_run_passes_captured_card_image_into_quick_filter(self):
        class FakeCrawler:
            def __init__(self):
                self.pages = [
                    [{"name": "李开新", "raw_text": "", "greet_button_point": (320, 260)}],
                    [],
                ]

            def start(self):
                return None

            def stop(self):
                return None

            def get_visible_resumes(self):
                return self.pages.pop(0) if self.pages else []

            def capture_resume_card(self, _resume):
                return "mock-card-image"

            def scroll_recommend_list(self, _pages=1):
                return None

            def greet_current_candidate(self, _resume):
                return "sent"

        class FakeFilter:
            def __init__(self):
                self.last_resume = None

            def quick_filter_resume(self, resume):
                self.last_resume = resume
                return {"passed": True, "reason": "视觉匹配"}

            def filter_resume(self, _resume):
                raise AssertionError("full filter should not run")

        class FakeMessenger:
            def is_processed(self, _key):
                return False

            def mark_processed(self, _key):
                return None

        fake_filter = FakeFilter()
        runner = ScreeningScript(
            config={"workflow": {"screen_scan_limit": 2}},
            profile={"job_title": "法务总监", "job_description": "法律岗位JD"},
            crawler=FakeCrawler(),
            resume_filter=fake_filter,
            messenger=FakeMessenger(),
        )

        runner.run()

        self.assertIsNotNone(fake_filter.last_resume)
        self.assertEqual(fake_filter.last_resume["card_image"], "mock-card-image")

    def test_run_deduplicates_same_card_even_when_name_is_unstable(self):
        calls = {"greeted": 0}

        class FakeCardImage:
            def save(self, fp, format="PNG"):
                fp.write(b"same-card")

        class FakeCrawler:
            def __init__(self):
                self.pages = [
                    [
                        {"name": "22", "raw_text": "", "greet_button_point": (320, 260), "greet_button_box": (280, 240, 80, 30)},
                        {"name": "23", "raw_text": "", "greet_button_point": (320, 260), "greet_button_box": (280, 240, 80, 30)},
                    ],
                    [],
                ]

            def start(self):
                return None

            def stop(self):
                return None

            def get_visible_resumes(self):
                return self.pages.pop(0) if self.pages else []

            def capture_resume_card(self, _resume):
                return FakeCardImage()

            def scroll_recommend_list(self, _pages=1):
                return None

            def greet_current_candidate(self, _resume):
                calls["greeted"] += 1
                return "sent"

        class FakeFilter:
            def quick_filter_resume(self, _resume):
                return {"passed": True, "reason": "规则筛选通过"}

            def filter_resume(self, _resume):
                raise AssertionError("full filter should not run")

        class FakeMessenger:
            def is_processed(self, _key):
                return False

            def mark_processed(self, _key):
                return None

        runner = ScreeningScript(
            config={"workflow": {"screen_scan_limit": 2}},
            profile={"job_title": "法务总监", "job_description": "法律岗位JD"},
            crawler=FakeCrawler(),
            resume_filter=FakeFilter(),
            messenger=FakeMessenger(),
        )

        result = runner.run()

        self.assertEqual(result["matched_sent"], 1)
        self.assertEqual(calls["greeted"], 1)


if __name__ == "__main__":
    unittest.main()
