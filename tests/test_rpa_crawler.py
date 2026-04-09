import json
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from PIL import Image

import src.rpa_crawler as rpa_crawler
from src.rpa_crawler import RPACrawler


class RPACrawlerSafetyTests(unittest.TestCase):
    def test_sidebar_region_uses_boss_window_bounds_when_available(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})

        with patch.object(
            crawler,
            "_boss_window_bounds",
            return_value=(1470, 0, 1280, 768),
        ):
            region = crawler._sidebar_region()

        self.assertEqual(region, (1470, 0, 384, 768))

    def test_boss_window_info_prefers_largest_visible_window(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        windows = [
            {
                "kCGWindowOwnerName": "BOSS直聘",
                "kCGWindowNumber": 11,
                "kCGWindowBounds": {"X": 2260, "Y": 168, "Width": 340, "Height": 500},
            },
            {
                "kCGWindowOwnerName": "BOSS直聘",
                "kCGWindowNumber": 12,
                "kCGWindowBounds": {"X": 1500, "Y": 80, "Width": 1280, "Height": 880},
            },
        ]

        with patch("src.rpa_crawler.Quartz.CGWindowListCopyWindowInfo", return_value=windows):
            info = crawler._boss_window_info()

        self.assertEqual(info["window_id"], 12)
        self.assertEqual(info["width"], 1280)

    def test_resume_detail_region_never_returns_negative_width(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})

        with patch.object(crawler, "_main_region", return_value=(2380, 224, 220, 444)):
            region = crawler._resume_detail_region()

        self.assertGreaterEqual(region[2], 0)

    def test_ensure_boss_window_usable_attempts_resize_when_window_is_too_small(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})

        with patch.object(
            crawler,
            "_boss_window_bounds",
            side_effect=[(2260, 168, 340, 500), (1500, 80, 1280, 880)],
        ), patch.object(
            crawler,
            "_resize_boss_window",
        ) as resize_window:
            crawler._ensure_boss_window_usable()

        resize_window.assert_called_once()

    def test_ensure_boss_window_usable_raises_when_window_remains_too_small(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})

        with patch.object(
            crawler,
            "_boss_window_bounds",
            side_effect=[(2260, 168, 340, 500), (2260, 168, 340, 500)],
        ), patch.object(
            crawler,
            "_resize_boss_window",
        ):
            with self.assertRaisesRegex(RuntimeError, "窗口太小"):
                crawler._ensure_boss_window_usable()

    def test_screenshot_crops_from_secondary_display_capture(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        display_image = Image.new("RGB", (1920, 1080), "white")

        with patch.object(
            crawler,
            "_boss_window_info",
            return_value=None,
        ), patch.object(
            crawler,
            "_display_for_region",
            return_value={"index": 2, "x": 1470, "y": 0, "width": 1920, "height": 1080},
        ), patch.object(
            crawler,
            "_capture_display_image",
            return_value=display_image,
        ) as capture_display:
            shot = crawler._screenshot(region=(1992, 124, 200, 100))

        self.assertEqual(shot.size, (200, 100))
        capture_display.assert_called_once_with(2)

    def test_screenshot_prefers_boss_window_capture_when_region_inside_window(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        window_image = Image.new("RGB", (1280, 768), "white")

        with patch.object(
            crawler,
            "_boss_window_info",
            return_value={"window_id": 123, "x": 1992, "y": 124, "width": 1280, "height": 768},
        ), patch.object(
            crawler,
            "_capture_window_image",
            return_value=window_image,
        ) as capture_window, patch.object(
            crawler,
            "_capture_display_image",
            side_effect=AssertionError("unexpected display capture"),
        ):
            shot = crawler._screenshot(region=(2126, 191, 200, 100))

        self.assertEqual(shot.size, (200, 100))
        capture_window.assert_called_once_with(123)

    def test_screenshot_falls_back_to_display_capture_when_window_capture_fails(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        display_image = Image.new("RGB", (1920, 1080), "white")

        with patch.object(
            crawler,
            "_boss_window_info",
            return_value={"window_id": 123, "x": 1992, "y": 124, "width": 1280, "height": 768},
        ), patch.object(
            crawler,
            "_capture_window_image",
            side_effect=RuntimeError("窗口截图失败(window_id=123)"),
        ), patch.object(
            crawler,
            "_display_for_region",
            return_value={"index": 2, "x": 1470, "y": 0, "width": 1920, "height": 1080},
        ), patch.object(
            crawler,
            "_capture_display_image",
            return_value=display_image,
        ) as capture_display:
            shot = crawler._screenshot(region=(2126, 191, 200, 100))

        self.assertEqual(shot.size, (200, 100))
        capture_display.assert_called_once_with(2)

    def test_screenshot_falls_back_to_pyautogui_when_display_capture_fails(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        full_image = Image.new("RGB", (4000, 2400), "white")

        with patch.object(
            crawler,
            "_boss_window_info",
            return_value={"window_id": 123, "x": 1992, "y": 124, "width": 1280, "height": 768},
        ), patch.object(
            crawler,
            "_capture_window_image",
            side_effect=RuntimeError("窗口截图失败(window_id=123)"),
        ), patch.object(
            crawler,
            "_display_for_region",
            return_value={"index": 2, "x": 1470, "y": 0, "width": 1920, "height": 1080},
        ), patch.object(
            crawler,
            "_capture_display_image",
            side_effect=RuntimeError("显示器截图失败(display=2): could not create image from display"),
        ), patch.object(
            crawler,
            "_capture_system_image",
            return_value=full_image,
        ) as fallback_screenshot:
            shot = crawler._screenshot(region=(2126, 191, 200, 100))

        self.assertEqual(shot.size, (200, 100))
        fallback_screenshot.assert_called_once_with()

    def test_screenshot_crops_full_pyautogui_capture_when_display_capture_fails(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        full_image = Image.new("RGB", (4000, 2400), "white")

        with patch.object(
            crawler,
            "_boss_window_info",
            return_value={"window_id": 123, "x": 1992, "y": 124, "width": 1280, "height": 768},
        ), patch.object(
            crawler,
            "_capture_window_image",
            side_effect=RuntimeError("窗口截图失败(window_id=123)"),
        ), patch.object(
            crawler,
            "_display_for_region",
            return_value={"index": 2, "x": 1470, "y": 0, "width": 1920, "height": 1080},
        ), patch.object(
            crawler,
            "_capture_display_image",
            side_effect=RuntimeError("显示器截图失败(display=2): could not create image from display"),
        ), patch.object(
            crawler,
            "_capture_system_image",
            return_value=full_image,
        ) as fallback_screenshot:
            shot = crawler._screenshot(region=(2126, 191, 200, 100))

        self.assertEqual(shot.size, (200, 100))
        fallback_screenshot.assert_called_once_with()

    def test_screenshot_falls_back_to_quartz_region_capture_when_system_capture_fails(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        region_image = Image.new("RGB", (200, 100), "white")

        with patch.object(
            crawler,
            "_boss_window_info",
            return_value={"window_id": 123, "x": 1992, "y": 124, "width": 1280, "height": 768},
        ), patch.object(
            crawler,
            "_capture_window_image",
            side_effect=RuntimeError("窗口截图失败(window_id=123)"),
        ), patch.object(
            crawler,
            "_display_for_region",
            return_value={"index": 2, "x": 1470, "y": 0, "width": 1920, "height": 1080},
        ), patch.object(
            crawler,
            "_capture_display_image",
            side_effect=RuntimeError("显示器截图失败(display=2): could not create image from display"),
        ), patch.object(
            crawler,
            "_capture_system_image",
            side_effect=RuntimeError("系统截图失败: could not create image from display"),
        ), patch.object(
            crawler,
            "_capture_quartz_region_image",
            return_value=region_image,
        ) as quartz_capture:
            shot = crawler._screenshot(region=(2126, 191, 200, 100))

        self.assertEqual(shot.size, (200, 100))
        quartz_capture.assert_called_once_with((2126, 191, 200, 100))

    def test_self_check_reports_ready_when_expected_keywords_present(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (100, 100), "white")
        ocr_items = [
            {"text": "推荐牛人", "x": 10, "y": 10, "w": 20, "h": 10},
            {"text": "职位管理", "x": 10, "y": 30, "w": 20, "h": 10},
        ]

        with patch.object(crawler, "_screenshot", return_value=screen), patch(
            "src.rpa_crawler.ocr_image", return_value=ocr_items
        ):
            result = crawler.self_check()

        self.assertTrue(result["ready"])
        self.assertEqual(result["found_keywords"], ["推荐牛人", "职位管理"])

    def test_self_check_trusts_frontmost_boss_app_even_if_ocr_is_sparse(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (100, 100), "white")

        with patch.object(crawler, "_frontmost_app_name", return_value="BOSS直聘"), patch.object(
            crawler, "_screenshot", return_value=screen
        ), patch("src.rpa_crawler.ocr_image", return_value=[{"text": "搜索", "x": 1, "y": 1, "w": 10, "h": 10}]):
            result = crawler.self_check()

        self.assertTrue(result["ready"])
        self.assertEqual(result["frontmost_app"], "BOSS直聘")

    def test_self_check_accepts_running_boss_process_even_if_not_frontmost(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (100, 100), "white")

        with patch.object(crawler, "_frontmost_app_name", return_value="Google Chrome"), patch.object(
            crawler, "_boss_app_running", return_value=True
        ), patch.object(
            crawler, "_screenshot", return_value=screen
        ), patch("src.rpa_crawler.ocr_image", return_value=[]):
            result = crawler.self_check()

        self.assertTrue(result["ready"])

    def test_ensure_boss_frontmost_activates_app_when_needed(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})

        with patch.object(crawler, "_frontmost_app_name", return_value="cmux"), patch.object(
            crawler, "_activate_boss_app"
        ) as activate:
            crawler._ensure_boss_frontmost()

        activate.assert_called_once()

    def test_start_retries_self_check_after_transient_capture_error(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})

        with patch.object(crawler, "_ensure_boss_frontmost"), patch.object(
            crawler,
            "self_check",
            side_effect=[
                RuntimeError("系统截图失败: could not create image from display"),
                {"ready": True, "found_keywords": ["推荐", "职位"]},
            ],
        ) as self_check, patch.object(crawler, "_wait") as wait:
            crawler.start()

        self.assertEqual(self_check.call_count, 2)
        wait.assert_called()

    def test_self_check_accepts_current_desktop_sidebar_labels(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (100, 100), "white")
        ocr_items = [
            {"text": "消息", "x": 10, "y": 10, "w": 20, "h": 10},
            {"text": "推荐", "x": 10, "y": 30, "w": 20, "h": 10},
            {"text": "职位", "x": 10, "y": 50, "w": 20, "h": 10},
        ]

        with patch.object(crawler, "_screenshot", return_value=screen), patch(
            "src.rpa_crawler.ocr_image", return_value=ocr_items
        ):
            result = crawler.self_check()

        self.assertTrue(result["ready"])
        self.assertEqual(result["found_keywords"], ["消息", "推荐", "职位"])

    def test_self_check_only_scans_left_sidebar_region(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (100, 100), "white")
        calls = {}

        def fake_screenshot(region=None):
            calls["region"] = region
            return screen

        with patch("src.rpa_crawler.pyautogui.size", return_value=(3000, 2000)), patch.object(
            crawler, "_boss_window_bounds", return_value=None
        ), patch.object(
            crawler, "_screenshot", side_effect=fake_screenshot
        ), patch("src.rpa_crawler.ocr_image", return_value=[]):
            crawler.self_check()

        self.assertEqual(calls["region"], (0, 0, 900, 2000))

    def test_fetch_my_jobs_only_scans_job_list_region(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (100, 100), "white")
        regions = []

        def fake_screenshot(region=None):
            regions.append(region)
            return screen

        with patch.object(crawler, "_click_text", return_value=True), patch.object(
            crawler, "_wait"
        ), patch.object(
            crawler, "_job_list_region", return_value=(100, 200, 300, 400)
        ), patch.object(
            crawler, "_screenshot", side_effect=fake_screenshot
        ), patch(
            "src.rpa_crawler.ocr_image", return_value=[]
        ):
            crawler.fetch_my_jobs()

        self.assertIn((100, 200, 300, 400), regions)

    def test_fetch_resumes_returns_empty_when_resume_ocr_times_out(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (100, 100), "white")

        with patch.object(crawler, "_click_text", return_value=True), patch.object(
            crawler, "_wait"
        ), patch.object(
            crawler, "_recommend_list_region", return_value=(10, 20, 300, 400)
        ), patch.object(
            crawler, "_screenshot", return_value=screen
        ), patch.object(
            crawler, "_parse_resumes_from_screen", side_effect=RuntimeError("OCR子进程超时")
        ), patch.object(
            crawler, "_extract_resumes_from_buttons", return_value=[]
        ), patch("src.rpa_crawler.logger") as logger:
            resumes = crawler.fetch_resumes(max_count=5)

        self.assertEqual(resumes, [])
        logger.warning.assert_called()

    def test_fetch_resumes_falls_back_to_button_regions_when_resume_ocr_times_out(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (100, 100), "white")
        fallback_resumes = [{"name": "韩宏兵", "raw_text": "韩宏兵 31岁 7年 本科 法务经理", "click_point": (120, 240)}]

        with patch.object(crawler, "_wait"), patch.object(
            crawler, "_recommend_list_region", return_value=(10, 20, 300, 400)
        ), patch.object(
            crawler, "_screenshot", return_value=screen
        ), patch.object(
            crawler, "_parse_resumes_from_screen", side_effect=RuntimeError("OCR子进程超时")
        ), patch.object(
            crawler, "_extract_resumes_from_buttons", return_value=fallback_resumes
        ), patch.object(
            crawler, "_random_wait"
        ):
            resumes = crawler.fetch_resumes(max_count=5, from_current_page=True)

        self.assertEqual(resumes, fallback_resumes)

    def test_fetch_resumes_from_current_page_skips_menu_navigation(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (100, 100), "white")

        with patch.object(
            crawler, "_click_text_in_region", side_effect=AssertionError("unexpected navigation")
        ), patch.object(
            crawler, "_wait"
        ), patch.object(
            crawler, "_recommend_list_region", return_value=(10, 20, 300, 400)
        ), patch.object(
            crawler, "_screenshot", return_value=screen
        ), patch.object(
            crawler, "_parse_resumes_from_screen", return_value=[]
        ):
            resumes = crawler.fetch_resumes(max_count=5, from_current_page=True)

        self.assertEqual(resumes, [])

    def test_fetch_resumes_scrolls_on_empty_screens_before_stopping(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (100, 100), "white")

        with patch.object(crawler, "_wait"), patch.object(
            crawler, "_recommend_list_region", return_value=(10, 20, 300, 400)
        ), patch.object(
            crawler, "_screenshot", return_value=screen
        ), patch.object(
            crawler, "_parse_resumes_from_screen", return_value=[]
        ), patch.object(
            crawler, "_scroll_down"
        ) as scroll_down, patch.object(
            crawler, "_random_wait"
        ):
            resumes = crawler.fetch_resumes(max_count=20, from_current_page=True)

        self.assertEqual(resumes, [])
        self.assertGreaterEqual(scroll_down.call_count, 1)

    def test_get_visible_resumes_falls_back_to_button_regions_when_resume_ocr_times_out(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (100, 100), "white")
        fallback_resumes = [{"name": "王双", "raw_text": "王双 32岁 8年 硕士 法务bp", "click_point": (130, 260)}]

        with patch.object(
            crawler, "_recommend_list_region", return_value=(10, 20, 300, 400)
        ), patch.object(
            crawler, "_screenshot", return_value=screen
        ), patch.object(
            crawler, "_parse_resumes_from_screen", side_effect=RuntimeError("OCR子进程超时")
        ), patch.object(
            crawler, "_extract_resumes_from_buttons", return_value=fallback_resumes
        ):
            resumes = crawler.get_visible_resumes()

        self.assertEqual(resumes, fallback_resumes)

    def test_get_visible_resumes_returns_empty_when_button_fallback_also_times_out(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (100, 100), "white")

        with patch.object(
            crawler, "_recommend_list_region", return_value=(10, 20, 300, 400)
        ), patch.object(
            crawler, "_screenshot", return_value=screen
        ), patch.object(
            crawler, "_parse_resumes_from_screen", side_effect=RuntimeError("OCR子进程超时")
        ), patch.object(
            crawler, "_extract_resumes_from_buttons", side_effect=RuntimeError("按钮邻域候选人OCR失败")
        ), patch("src.rpa_crawler.logger") as logger:
            resumes = crawler.get_visible_resumes()

        self.assertEqual(resumes, [])
        logger.warning.assert_called()

    def test_get_visible_resumes_retries_after_escape_when_first_pass_finds_nothing(self):
        crawler = RPACrawler({"rpa": {"dry_run": False}})
        screen = Image.new("RGB", (100, 100), "white")
        retry_resumes = [{"name": "韩宏兵", "raw_text": "韩宏兵 31岁 7年 本科 法务经理", "click_point": (120, 240)}]

        with patch.object(
            crawler, "_is_candidate_detail_open", return_value=False
        ), patch.object(
            crawler, "_recommend_list_region", return_value=(10, 20, 300, 400)
        ), patch.object(
            crawler, "_screenshot", return_value=screen
        ), patch.object(
            crawler, "_extract_resumes_from_buttons", side_effect=[[], retry_resumes]
        ), patch.object(
            crawler, "_press"
        ) as press, patch.object(
            crawler, "_wait"
        ):
            resumes = crawler.get_visible_resumes()

        self.assertEqual(resumes, retry_resumes)
        press.assert_called_once_with("esc")

    def test_get_visible_resumes_closes_detail_before_reading_list(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (100, 100), "white")

        with patch.object(
            crawler, "_is_candidate_detail_open", side_effect=[True, False]
        ), patch.object(
            crawler, "close_resume_detail", return_value=True
        ) as close_detail, patch.object(
            crawler, "_recommend_list_region", return_value=(10, 20, 300, 400)
        ), patch.object(
            crawler, "_screenshot", return_value=screen
        ), patch.object(
            crawler, "_parse_resumes_from_screen", return_value=[]
        ):
            crawler.get_visible_resumes()

        close_detail.assert_called_once()

    def test_click_text_in_region_offsets_click_coordinates(self):
        crawler = RPACrawler({"rpa": {"dry_run": False}})
        screen = Image.new("RGB", (100, 100), "white")

        with patch.object(
            crawler,
            "_find_text_position",
            return_value={"text": "职位管理", "x": 20, "y": 30, "w": 40, "h": 20},
        ), patch.object(
            crawler,
            "_screenshot",
            return_value=screen,
        ) as screenshot, patch("src.rpa_crawler.pyautogui.click") as click:
            ok = crawler._click_text_in_region("职位管理", (100, 200, 300, 400))

        self.assertTrue(ok)
        screenshot.assert_called_once_with(region=(100, 200, 300, 400))
        click.assert_called_once_with(140, 240)

    def test_parse_resumes_from_screen_uses_faster_ocr_settings(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (100, 100), "white")
        captured = {}

        def fake_ocr(_screen, **kwargs):
            captured.update(kwargs)
            return []

        with patch("src.rpa_crawler.ocr_image", side_effect=fake_ocr):
            resumes = crawler._parse_resumes_from_screen(screen)

        self.assertEqual(resumes, [])
        self.assertEqual(captured["max_side"], 1100)
        self.assertEqual(captured["timeout_sec"], 20)

    def test_parse_resumes_from_screen_can_fallback_to_greet_buttons(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (100, 100), "white")
        items = [
            {"text": "李开新", "x": 20, "y": 40, "w": 50, "h": 18},
            {"text": "在线", "x": 80, "y": 40, "w": 30, "h": 18},
            {"text": "北京", "x": 40, "y": 72, "w": 36, "h": 18},
            {"text": "打招呼", "x": 250, "y": 52, "w": 70, "h": 28},
        ]

        with patch("src.rpa_crawler.ocr_image", return_value=items):
            resumes = crawler._parse_resumes_from_screen(screen, origin=(100, 200))

        self.assertEqual(len(resumes), 1)
        self.assertEqual(resumes[0]["name"], "李开新")
        self.assertGreater(resumes[0]["click_point"][0], 100)
        self.assertGreater(resumes[0]["click_point"][1], 200)

    def test_parse_resumes_from_screen_uses_tiled_ocr_for_tall_regions(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (1200, 1600), "white")

        with patch.object(
            crawler,
            "_ocr_resumes_screen",
            return_value=[{"text": "打招呼", "x": 200, "y": 300, "w": 60, "h": 24}],
        ) as tiled_ocr:
            crawler._parse_resumes_from_screen(screen)

        tiled_ocr.assert_called_once()

    def test_extract_resumes_from_buttons_returns_stub_when_neighbor_ocr_fails(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (500, 300), "white")
        buttons = [{"x": 320, "y": 252, "w": 70, "h": 28}]

        with patch.object(
            crawler, "_find_greet_buttons", return_value=buttons
        ), patch.object(
            crawler, "_safe_ocr", return_value=[]
        ):
            resumes = crawler._extract_resumes_from_buttons(screen, origin=(100, 200))

        self.assertEqual(len(resumes), 1)
        self.assertTrue(resumes[0]["requires_detail"])
        self.assertEqual(resumes[0]["click_point"], (180, 266))
        self.assertIn("button:", resumes[0]["dedup_key"])
        self.assertEqual(resumes[0]["greet_button_point"], (355, 266))
        self.assertEqual(resumes[0]["greet_button_box"], (320, 252, 70, 28))

    def test_extract_resumes_from_buttons_keeps_button_geometry_for_card_capture(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (500, 300), "white")
        buttons = [{"x": 320, "y": 252, "w": 70, "h": 28}]
        ocr_items = [
            {"text": "张三", "x": 10, "y": 8, "w": 20, "h": 10},
            {"text": "本科", "x": 40, "y": 8, "w": 20, "h": 10},
            {"text": "法务", "x": 70, "y": 8, "w": 20, "h": 10},
        ]

        with patch.object(
            crawler, "_find_greet_buttons", return_value=buttons
        ), patch.object(
            crawler, "_safe_ocr", return_value=ocr_items
        ):
            resumes = crawler._extract_resumes_from_buttons(screen, origin=(100, 200))

        self.assertEqual(len(resumes), 1)
        self.assertEqual(resumes[0]["greet_button_point"], (355, 266))
        self.assertEqual(resumes[0]["greet_button_box"], (320, 252, 70, 28))

    def test_read_visible_resumes_prefers_button_fallback_by_default(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (500, 300), "white")
        expected = [{"name": "张三", "raw_text": "", "click_point": (100, 100)}]

        with patch.object(
            crawler, "_recommend_list_region", return_value=(10, 20, 500, 300)
        ), patch.object(
            crawler, "_screenshot", return_value=screen
        ), patch.object(
            crawler, "_extract_resumes_from_buttons", return_value=expected
        ) as extract_buttons, patch.object(
            crawler, "_parse_resumes_from_screen", side_effect=AssertionError("should not use full screen OCR first")
        ):
            resumes = crawler._read_visible_resumes_once()

        self.assertEqual(resumes, expected)
        extract_buttons.assert_called_once_with(screen, origin=(10, 20))

    def test_greet_current_candidate_in_dry_run_does_not_touch_ui(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})

        with patch.object(
            crawler, "_click_text", side_effect=AssertionError("unexpected click")
        ), patch("src.rpa_crawler.logger") as logger:
            result = crawler.greet_current_candidate("张三")

        self.assertEqual(result, "sent")
        logger.info.assert_called()

    def test_greet_current_candidate_falls_back_to_visible_button_geometry(self):
        crawler = RPACrawler({"rpa": {"dry_run": False}})
        screen = Image.new("RGB", (500, 300), "white")
        for x in range(320, 451):
            for y in range(40, 81):
                screen.putpixel((x, y), (0, 169, 169))

        with patch.object(
            crawler, "_click_text", side_effect=RuntimeError("OCR timeout")
        ), patch.object(
            crawler, "_is_candidate_detail_open", return_value=True
        ), patch.object(
            crawler, "_main_region", return_value=(2000, 100, 500, 300)
        ), patch.object(
            crawler, "_screenshot", return_value=screen
        ), patch.object(
            crawler, "_click_point"
        ) as click_point, patch.object(
            crawler, "_wait"
        ), patch.object(
            crawler, "_detect_greeting_limit_reached", return_value=False
        ):
            result = crawler.greet_current_candidate("张三")

        self.assertEqual(result, "sent")
        click_point.assert_called_once()

    def test_greet_current_candidate_returns_blocked_when_account_restricted(self):
        crawler = RPACrawler({"rpa": {"dry_run": False}})
        screen = Image.new("RGB", (500, 300), "white")
        for x in range(320, 451):
            for y in range(40, 81):
                screen.putpixel((x, y), (0, 169, 169))

        with patch.object(
            crawler, "_click_text", side_effect=RuntimeError("OCR timeout")
        ), patch.object(
            crawler, "_is_candidate_detail_open", return_value=True
        ), patch.object(
            crawler, "_main_region", return_value=(2000, 100, 500, 300)
        ), patch.object(
            crawler, "_screenshot", return_value=screen
        ), patch.object(
            crawler, "_click_point"
        ), patch.object(
            crawler, "_wait"
        ), patch.object(
            crawler, "_detect_greeting_limit_reached", return_value=False
        ), patch.object(
            crawler, "_detect_operation_blocked", return_value="当前账号状态异常，暂不支持此操作"
        ):
            result = crawler.greet_current_candidate("张三")

        self.assertEqual(result, "blocked")

    def test_greet_current_candidate_returns_unavailable_when_detail_page_not_open(self):
        crawler = RPACrawler({"rpa": {"dry_run": False}})

        with patch.object(
            crawler, "_is_candidate_detail_open", return_value=False
        ), patch.object(
            crawler, "_click_text", side_effect=AssertionError("unexpected click")
        ), patch.object(
            crawler, "_click_point", side_effect=AssertionError("unexpected click")
        ):
            result = crawler.greet_current_candidate("张三")

        self.assertEqual(result, "unavailable")

    def test_greet_current_candidate_clicks_visible_card_button_without_detail_page(self):
        crawler = RPACrawler({"rpa": {"dry_run": False}})
        resume = {
            "name": "张三",
            "greet_button_point": (2288, 366),
        }

        with patch.object(
            crawler, "_is_candidate_detail_open", return_value=False
        ), patch.object(
            crawler, "_click_point"
        ) as click_point, patch.object(
            crawler, "_wait"
        ), patch.object(
            crawler, "_detect_greeting_limit_reached", return_value=False
        ), patch.object(
            crawler, "_detect_operation_blocked", return_value=None
        ):
            result = crawler.greet_current_candidate(resume)

        self.assertEqual(result, "sent")
        click_point.assert_called_once_with(2288, 366)

    def test_resume_from_greet_button_attaches_button_geometry(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        button = {"text": "打招呼", "x": 420, "y": 260, "w": 80, "h": 30}
        items = [
            {"text": "李开新", "x": 120, "y": 252, "w": 60, "h": 18},
            {"text": "32岁", "x": 190, "y": 252, "w": 40, "h": 18},
            {"text": "本科", "x": 240, "y": 252, "w": 40, "h": 18},
            {"text": "法务总监", "x": 290, "y": 252, "w": 70, "h": 18},
        ]

        resume = crawler._resume_from_greet_button(button, items)

        self.assertEqual(resume["name"], "李开新")
        self.assertEqual(resume["greet_button_point"], (460, 275))
        self.assertEqual(resume["greet_button_box"], (420, 260, 80, 30))

    def test_capture_resume_card_crops_button_neighbor_region(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (600, 400), "white")
        resume = {
            "name": "李开新",
            "greet_button_box": (420, 220, 100, 36),
        }

        with patch.object(
            crawler, "_recommend_list_region", return_value=(100, 80, 600, 400)
        ), patch.object(
            crawler, "_screenshot", return_value=screen
        ):
            card = crawler.capture_resume_card(resume)

        self.assertIsNotNone(card)
        self.assertGreater(card.size[0], 200)
        self.assertGreater(card.size[1], 80)

    def test_is_candidate_detail_open_reads_resume_detail_panel_header(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (400, 300), "white")

        with patch.object(
            crawler, "_resume_detail_region", return_value=(100, 200, 400, 300)
        ), patch.object(
            crawler, "_screenshot", return_value=screen
        ) as screenshot, patch.object(
            crawler,
            "_safe_ocr",
            return_value=[
                {"text": "收藏", "x": 20, "y": 20, "w": 40, "h": 16},
                {"text": "不合适", "x": 80, "y": 20, "w": 50, "h": 16},
                {"text": "经历概览", "x": 140, "y": 20, "w": 60, "h": 16},
            ],
        ):
            opened = crawler._is_candidate_detail_open()

        self.assertTrue(opened)
        screenshot.assert_called_once_with(region=(100, 200, 400, 300))

    def test_get_active_resume_detail_scrolls_and_merges_multiple_pages(self):
        crawler = RPACrawler({"rpa": {"dry_run": True, "resume_detail_scroll_pages": 3}})
        screen = Image.new("RGB", (200, 200), "white")
        ocr_pages = [
            [
                {"text": "李开新", "x": 1, "y": 1, "w": 10, "h": 10},
                {"text": "法务总监", "x": 1, "y": 20, "w": 10, "h": 10},
            ],
            [
                {"text": "法务总监", "x": 1, "y": 1, "w": 10, "h": 10},
                {"text": "工作经历", "x": 1, "y": 20, "w": 10, "h": 10},
                {"text": "某公司 2019-至今", "x": 1, "y": 40, "w": 10, "h": 10},
            ],
            [
                {"text": "教育经历", "x": 1, "y": 1, "w": 10, "h": 10},
                {"text": "中国政法大学", "x": 1, "y": 20, "w": 10, "h": 10},
            ],
        ]

        with patch.object(
            crawler, "_resume_detail_region", return_value=(100, 200, 300, 400)
        ), patch.object(
            crawler, "_screenshot", return_value=screen
        ), patch.object(
            crawler, "_safe_ocr", side_effect=ocr_pages
        ), patch.object(
            crawler, "_scroll_region"
        ) as scroll_region, patch.object(
            crawler, "_wait"
        ):
            detail = crawler.get_active_resume_detail()

        self.assertIn("李开新", detail["detail_text"])
        self.assertIn("工作经历", detail["detail_text"])
        self.assertIn("中国政法大学", detail["detail_text"])
        self.assertEqual(scroll_region.call_count, 2)

    def test_click_first_visible_candidate_opens_first_resume(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        resumes = [
            {"name": "李开新", "click_point": (120, 240)},
            {"name": "焦娟", "click_point": (120, 420)},
        ]

        with patch.object(crawler, "get_visible_resumes", return_value=resumes), patch.object(
            crawler, "open_resume", return_value=True
        ) as open_resume:
            ok = crawler.click_first_visible_candidate()

        self.assertTrue(ok)
        open_resume.assert_called_once_with(resumes[0])

    def test_find_greet_buttons_detects_teal_button_blocks(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (360, 240), "white")
        for x in range(230, 301):
            for y in range(60, 89):
                screen.putpixel((x, y), (32, 193, 190))

        buttons = crawler._find_greet_buttons(screen, origin=(100, 200))

        self.assertEqual(len(buttons), 1)
        self.assertGreater(buttons[0]["x"], 300)
        self.assertGreater(buttons[0]["y"], 250)

    def test_find_greet_buttons_accepts_zero_red_teal_buttons(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (360, 240), "white")
        for x in range(230, 301):
            for y in range(60, 89):
                screen.putpixel((x, y), (0, 169, 169))

        buttons = crawler._find_greet_buttons(screen, origin=(100, 200))

        self.assertEqual(len(buttons), 1)

    def test_click_first_visible_candidate_falls_back_to_greet_button_geometry(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (360, 240), "white")
        for x in range(230, 301):
            for y in range(60, 89):
                screen.putpixel((x, y), (32, 193, 190))

        with patch.object(
            crawler, "_recommend_list_region", return_value=(100, 200, 360, 240)
        ), patch.object(
            crawler, "_screenshot", return_value=screen
        ), patch.object(
            crawler, "_parse_resumes_from_screen", return_value=[]
        ), patch.object(
            crawler, "_click_point"
        ) as click_point, patch.object(
            crawler, "_wait"
        ):
            ok = crawler.click_first_visible_candidate()

        self.assertTrue(ok)
        click_x, click_y = click_point.call_args.args
        self.assertLess(click_x, 330)
        self.assertGreater(click_y, 250)

    def test_click_first_visible_candidate_falls_back_when_resume_ocr_errors(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})
        screen = Image.new("RGB", (360, 240), "white")
        for x in range(230, 301):
            for y in range(60, 89):
                screen.putpixel((x, y), (0, 169, 169))

        with patch.object(
            crawler, "get_visible_resumes", side_effect=RuntimeError("OCR timeout")
        ), patch.object(
            crawler, "_recommend_list_region", return_value=(100, 200, 360, 240)
        ), patch.object(
            crawler, "_screenshot", return_value=screen
        ), patch.object(
            crawler, "_click_point"
        ) as click_point, patch.object(
            crawler, "_wait"
        ):
            ok = crawler.click_first_visible_candidate()

        self.assertTrue(ok)
        click_point.assert_called_once()

    def test_send_message_in_dry_run_does_not_touch_ui(self):
        crawler = RPACrawler({"rpa": {"dry_run": True}})

        with patch.object(
            crawler, "_click_text", side_effect=AssertionError("unexpected click")
        ), patch("src.rpa_crawler.logger") as logger:
            ok = crawler.send_message("张三", "你好")

        self.assertTrue(ok)
        logger.info.assert_called()

    def test_ocr_image_parses_worker_json(self):
        screen = Image.new("RGB", (20, 20), "white")
        worker_items = [{"text": "测试文本", "x": 1, "y": 2, "w": 10, "h": 10}]

        with patch(
            "src.rpa_crawler._run_ocr_subprocess",
            return_value=worker_items,
        ):
            items = rpa_crawler.ocr_image(screen)

        self.assertEqual(items, worker_items)

    def test_ocr_image_converts_rgba_input_to_rgb_array(self):
        screen = Image.new("RGBA", (20, 20), (255, 255, 255, 128))
        captured = {}

        def fake_run(path, timeout_sec=30):
            with Image.open(path) as img:
                captured["mode"] = img.mode
                captured["size"] = img.size
                captured["timeout_sec"] = timeout_sec
            return []

        with patch("src.rpa_crawler._run_ocr_subprocess", side_effect=fake_run):
            rpa_crawler.ocr_image(screen)

        self.assertEqual(captured["mode"], "RGB")
        self.assertEqual(captured["size"], (20, 20))

    def test_ocr_image_rescales_coordinates_back_to_original_size(self):
        screen = Image.new("RGB", (4000, 2000), "white")
        captured = {}

        def fake_run(path, timeout_sec=30):
            with Image.open(path) as img:
                captured["size"] = img.size
                captured["timeout_sec"] = timeout_sec
            return [{"text": "推荐牛人", "x": 100, "y": 50, "w": 200, "h": 100}]

        with patch("src.rpa_crawler._run_ocr_subprocess", side_effect=fake_run):
            items = rpa_crawler.ocr_image(screen)

        self.assertLess(max(captured["size"]), 4000)
        self.assertEqual(items[0]["text"], "推荐牛人")
        self.assertGreater(items[0]["x"], 100)
        self.assertGreater(items[0]["w"], 200)

    def test_run_ocr_subprocess_uses_worker_module(self):
        with patch("src.rpa_crawler.subprocess.run") as run:
            run.return_value = CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps([{"text": "职位", "x": 1, "y": 2, "w": 3, "h": 4}]),
                stderr="",
            )
            items = rpa_crawler._run_ocr_subprocess("/tmp/fake.png")

        self.assertEqual(items[0]["text"], "职位")
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[1:3], ["-m", "src.ocr_worker"])

    def test_run_ocr_subprocess_sets_project_root_as_cwd(self):
        with patch("src.rpa_crawler.subprocess.run") as run:
            run.return_value = CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
            rpa_crawler._run_ocr_subprocess("/tmp/fake.png")

        self.assertEqual(
            Path(run.call_args.kwargs["cwd"]),
            Path("/Users/a1/Projects/boss-auto"),
        )

    def test_frontmost_app_name_parses_osascript_output(self):
        with patch("src.rpa_crawler.subprocess.run") as run:
            run.return_value = CompletedProcess(args=[], returncode=0, stdout="BOSS直聘\n", stderr="")
            name = rpa_crawler.RPACrawler({"rpa": {}})._frontmost_app_name()

        self.assertEqual(name, "BOSS直聘")


if __name__ == "__main__":
    unittest.main()
