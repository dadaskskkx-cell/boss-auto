"""Boss直聘RPA爬虫 - 基于pyautogui+OCR，自动化PC客户端"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

import pyautogui
import Quartz
from PIL import Image
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_OCR_SIDE = 1600
OCR_TIMEOUT_SEC = 30
FAST_LIST_OCR_SIDE = 1100
FAST_LIST_OCR_TIMEOUT_SEC = 20
DETAIL_OCR_SIDE = 1400
DETAIL_OCR_TIMEOUT_SEC = 25
SIDEBAR_OCR_SIDE = 900
SIDEBAR_OCR_TIMEOUT_SEC = 15


def _prepare_image_for_ocr(
    image: Image.Image, max_side: int = MAX_OCR_SIDE
) -> tuple[Image.Image, float]:
    rgb_image = image.convert("RGB")
    width, height = rgb_image.size
    scale = 1.0
    longest_side = max(width, height)
    if longest_side > max_side:
        scale = longest_side / max_side
        resized = (
            max(1, int(round(width / scale))),
            max(1, int(round(height / scale))),
        )
        rgb_image = rgb_image.resize(resized)
    return rgb_image, scale


def _run_ocr_subprocess(
    image_path: str, timeout_sec: int = OCR_TIMEOUT_SEC
) -> list[dict]:
    env = os.environ.copy()
    env.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT) if not pythonpath else f"{str(PROJECT_ROOT)}{os.pathsep}{pythonpath}"
    )
    cmd = [sys.executable, "-m", "src.ocr_worker", image_path]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
            cwd=str(PROJECT_ROOT),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"OCR子进程超时({timeout_sec}s)") from exc
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"OCR子进程失败(exit={result.returncode}): {stderr}")
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OCR输出无法解析: {exc}") from exc


def ocr_image(
    image: Image.Image,
    max_side: int = MAX_OCR_SIDE,
    timeout_sec: int = OCR_TIMEOUT_SEC,
) -> list[dict]:
    """对图片做OCR，返回 [{text, x, y, w, h}, ...]"""
    rgb_image, scale = _prepare_image_for_ocr(image, max_side=max_side)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            temp_path = Path(tmp.name)
        rgb_image.save(temp_path, format="PNG")
        items = _run_ocr_subprocess(str(temp_path), timeout_sec=timeout_sec)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()

    if scale == 1.0:
        return items

    scaled_items = []
    for item in items:
        scaled_items.append(
            {
                "text": item["text"],
                "x": int(round(item["x"] * scale)),
                "y": int(round(item["y"] * scale)),
                "w": int(round(item["w"] * scale)),
                "h": int(round(item["h"] * scale)),
            }
        )
    return scaled_items


class RPACrawler:
    """通过RPA操作Boss直聘PC客户端"""

    # 页面上的通用文字，不是候选人名字
    _GENERIC_WORDS = frozenset({
        "工作台", "推荐", "搜索", "职位", "互动", "消息", "更多", "全部", "未读",
        "沟通中", "新招呼", "已约面", "在线", "刚刚活跃", "活跃", "查看详情",
        "牛人管理", "道具", "工具箱", "招聘规范", "客服", "面试", "招聘数据",
        "账号", "升级", "加载中", "筛选", "推荐牛人", "职位管理", "沟通",
        "已读", "未读", "今日", "昨日", "最新", "收藏", "不合适", "感兴趣",
        "打招呼", "继续沟通", "交换微信", "约面试", "查看全部", "展开",
        "收起", "立即沟通", "立即开聊", "发消息",
    })

    def __init__(self, config: dict):
        self.config = config
        self.rpa_config = config.get("rpa", {})
        self.dry_run = bool(self.rpa_config.get("dry_run", False))
        self.prefer_button_fallback = bool(self.rpa_config.get("prefer_button_fallback", True))
        self.app_name = self.rpa_config.get("app_name", "BOSS直聘")
        self.app_path = self.rpa_config.get("app_path", "/Applications/BOSS直聘.app")
        self.required_keywords = self.rpa_config.get(
            "required_keywords",
            ["消息", "推荐", "搜索", "职位", "互动", "推荐牛人", "职位管理", "沟通"],
        )
        self.minimum_keywords = int(self.rpa_config.get("minimum_keywords", 2))
        self.last_self_check: dict = {}
        self.current_resume: dict | None = None
        pyautogui.PAUSE = 0.5  # 每次操作间隔0.5秒
        pyautogui.FAILSAFE = True  # 鼠标移到左上角可紧急停止

    def _screenshot(self, region=None) -> Image.Image:
        """截屏"""
        window = self._boss_window_info()
        if region is None:
            if window:
                region = (window["x"], window["y"], window["width"], window["height"])
            else:
                return self._capture_system_image()

        if window:
            window_bounds = (window["x"], window["y"], window["width"], window["height"])
            if (
                window_bounds[0] <= region[0] <= window_bounds[0] + window_bounds[2]
                and window_bounds[1] <= region[1] <= window_bounds[1] + window_bounds[3]
                and region[0] + region[2] <= window_bounds[0] + window_bounds[2]
                and region[1] + region[3] <= window_bounds[1] + window_bounds[3]
            ):
                try:
                    window_image = self._capture_window_image(window["window_id"])
                    left = int(region[0] - window["x"])
                    top = int(region[1] - window["y"])
                    right = left + int(region[2])
                    bottom = top + int(region[3])
                    return window_image.crop((left, top, right, bottom))
                except Exception as exc:
                    logger.warning(f"窗口截图失败，回退到显示器截图: {exc}")

        display = self._display_for_region(region)
        if not display:
            return self._crop_from_full_screenshot(region)

        try:
            display_image = self._capture_display_image(display["index"])
        except Exception as exc:
            logger.warning(f"显示器截图失败，回退到系统截图: {exc}")
            try:
                return self._crop_from_full_screenshot(region)
            except Exception as system_exc:
                logger.warning(f"系统截图失败，回退到Quartz区域截图: {system_exc}")
                return self._capture_quartz_region_image(region)
        expected_size = (display["width"], display["height"])
        if display_image.size != expected_size:
            display_image = display_image.resize(expected_size)

        left = max(0, int(region[0] - display["x"]))
        top = max(0, int(region[1] - display["y"]))
        right = min(display["width"], left + int(region[2]))
        bottom = min(display["height"], top + int(region[3]))
        return display_image.crop((left, top, right, bottom))

    def _crop_from_full_screenshot(self, region: tuple[int, int, int, int]) -> Image.Image:
        full_image = self._capture_system_image()
        displays = self._display_catalog()
        min_x = min(display["x"] for display in displays)
        min_y = min(display["y"] for display in displays)
        max_x = max(display["x"] + display["width"] for display in displays)
        max_y = max(display["y"] + display["height"] for display in displays)
        logical_width = max_x - min_x
        logical_height = max_y - min_y

        if full_image.size != (logical_width, logical_height):
            full_image = full_image.resize((logical_width, logical_height))

        left = max(0, int(region[0] - min_x))
        top = max(0, int(region[1] - min_y))
        right = min(full_image.width, left + int(region[2]))
        bottom = min(full_image.height, top + int(region[3]))
        return full_image.crop((left, top, right, bottom))

    def _capture_system_image(self) -> Image.Image:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                temp_path = Path(temp_file.name)
            result = subprocess.run(
                ["screencapture", "-x", str(temp_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"系统截图失败: {(result.stderr or result.stdout or '').strip()}"
                )
            with Image.open(temp_path) as img:
                return img.convert("RGB").copy()
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()

    def _capture_quartz_region_image(self, region: tuple[int, int, int, int]) -> Image.Image:
        rect = Quartz.CGRectMake(
            int(region[0]),
            int(region[1]),
            int(region[2]),
            int(region[3]),
        )
        image_ref = Quartz.CGWindowListCreateImage(
            rect,
            Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID,
            Quartz.kCGWindowImageDefault,
        )
        if not image_ref:
            raise RuntimeError(f"Quartz区域截图失败(region={region})")
        image = self._cgimage_to_pil(image_ref)
        if image.size != (int(region[2]), int(region[3])):
            image = image.resize((int(region[2]), int(region[3])))
        return image

    def _display_catalog(self) -> list[dict]:
        try:
            _, display_ids, _ = Quartz.CGGetOnlineDisplayList(16, None, None)
        except Exception:
            display_ids = ()

        displays: list[dict] = []
        for index, display_id in enumerate(display_ids, start=1):
            bounds = Quartz.CGDisplayBounds(display_id)
            displays.append(
                {
                    "id": int(display_id),
                    "index": index,
                    "x": int(bounds.origin.x),
                    "y": int(bounds.origin.y),
                    "width": int(bounds.size.width),
                    "height": int(bounds.size.height),
                }
            )
        if displays:
            return displays

        screen_w, screen_h = pyautogui.size()
        return [{"id": 0, "index": 1, "x": 0, "y": 0, "width": screen_w, "height": screen_h}]

    def _display_for_region(self, region: tuple[int, int, int, int]) -> dict | None:
        cx = int(region[0] + region[2] / 2)
        cy = int(region[1] + region[3] / 2)
        for display in self._display_catalog():
            if (
                display["x"] <= cx < display["x"] + display["width"]
                and display["y"] <= cy < display["y"] + display["height"]
            ):
                return display

        for display in self._display_catalog():
            if (
                display["x"] <= region[0] < display["x"] + display["width"]
                and display["y"] <= region[1] < display["y"] + display["height"]
            ):
                return display
        return None

    def _capture_display_image(self, display_index: int) -> Image.Image:
        temp_path = None
        try:
            temp_path = Path(tempfile.gettempdir()) / f"boss-display-{display_index}-{time.time_ns()}.png"
            result = subprocess.run(
                ["screencapture", "-x", "-D", str(display_index), str(temp_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"显示器截图失败(display={display_index}): {(result.stderr or result.stdout or '').strip()}"
                )
            with Image.open(temp_path) as img:
                return img.convert("RGB").copy()
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()

    def _capture_window_image(self, window_id: int) -> Image.Image:
        image_ref = Quartz.CGWindowListCreateImage(
            Quartz.CGRectNull,
            Quartz.kCGWindowListOptionIncludingWindow,
            window_id,
            Quartz.kCGWindowImageBoundsIgnoreFraming,
        )
        if not image_ref:
            raise RuntimeError(f"窗口截图失败(window_id={window_id})")
        return self._cgimage_to_pil(image_ref)

    def _cgimage_to_pil(self, image_ref) -> Image.Image:
        width = Quartz.CGImageGetWidth(image_ref)
        height = Quartz.CGImageGetHeight(image_ref)
        provider = Quartz.CGImageGetDataProvider(image_ref)
        data = Quartz.CGDataProviderCopyData(provider)
        return Image.frombuffer("RGBA", (width, height), data, "raw", "BGRA", 0, 1).convert("RGB")

    def _boss_window_info(self) -> dict | None:
        try:
            windows = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly,
                Quartz.kCGNullWindowID,
            )
        except Exception:
            windows = []

        candidates: list[dict] = []
        for window in windows or []:
            owner = (window.get("kCGWindowOwnerName") or "").strip()
            if self.app_name not in owner and "BOSS" not in owner.upper():
                continue
            bounds = window.get("kCGWindowBounds") or {}
            width = int(bounds.get("Width", 0))
            height = int(bounds.get("Height", 0))
            if width <= 0 or height <= 0:
                continue
            candidates.append(
                {
                    "window_id": int(window.get("kCGWindowNumber", 0)),
                    "x": int(bounds.get("X", 0)),
                    "y": int(bounds.get("Y", 0)),
                    "width": width,
                    "height": height,
                }
            )
        if candidates:
            candidates.sort(key=lambda item: (item["width"] * item["height"], item["width"]), reverse=True)
            return candidates[0]

        return None

    def _resize_boss_window(self):
        window = self._boss_window_bounds()
        if not window:
            return

        display = self._display_for_region(window)
        if not display:
            return

        target_width = min(display["width"] - 120, 1440)
        target_height = min(display["height"] - 120, 920)
        target_x = display["x"] + max(20, int((display["width"] - target_width) / 2))
        target_y = display["y"] + 40

        script = (
            f'tell application "System Events" to tell process "{self.app_name}" '
            f'to if (count of windows) > 0 then '
            f'set position of front window to {{{target_x}, {target_y}}}\n'
            f'tell front window to set size to {{{target_width}, {target_height}}}'
        )
        try:
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return

    def _ensure_boss_window_usable(self):
        min_width = int(self.rpa_config.get("min_window_width", 960))
        min_height = int(self.rpa_config.get("min_window_height", 720))

        bounds = self._boss_window_bounds()
        if not bounds:
            return

        _, _, width, height = bounds
        if width >= min_width and height >= min_height:
            return

        logger.warning(f"检测到Boss窗口较小({width}x{height})，尝试自动放大")
        self._resize_boss_window()
        self._wait(1.2)

        bounds = self._boss_window_bounds()
        if not bounds:
            return
        _, _, width, height = bounds
        if width < min_width or height < min_height:
            raise RuntimeError(
                f"Boss客户端窗口太小({width}x{height})，请先将窗口放大到至少 {min_width}x{min_height}"
            )

    def _boss_window_bounds(self) -> tuple[int, int, int, int] | None:
        window = self._boss_window_info()
        if window:
            return (window["x"], window["y"], window["width"], window["height"])

        try:
            result = subprocess.run(
                [
                    "osascript",
                    "-e",
                    (
                        f'tell application "System Events" to tell process "{self.app_name}" '
                        'to if (count of windows) > 0 then get {{position, size}} of front window'
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return None

        numbers = [int(value) for value in re.findall(r"-?\\d+", result.stdout or "")]
        if len(numbers) < 4:
            return None
        return tuple(numbers[:4])

    def _sidebar_region(self) -> tuple[int, int, int, int]:
        window = self._boss_window_bounds()
        if window:
            x, y, w, h = window
            sidebar_w = max(120, int(w * 0.3))
            return (x, y, sidebar_w, h)

        screen_w, screen_h = pyautogui.size()
        sidebar_w = max(300, int(screen_w * 0.3))
        return (0, 0, sidebar_w, screen_h)

    def _main_region(self) -> tuple[int, int, int, int]:
        window = self._boss_window_bounds()
        if window:
            x, y, w, h = window
            sidebar_w = max(120, int(w * 0.1))
            top_h = max(56, int(h * 0.08))
            return (x + sidebar_w, y + top_h, w - sidebar_w, h - top_h)

        screen_w, screen_h = pyautogui.size()
        sidebar_w = max(300, int(screen_w * 0.18))
        top_h = max(120, int(screen_h * 0.08))
        return (sidebar_w, top_h, screen_w - sidebar_w, screen_h - top_h)

    def _recommend_list_region(self) -> tuple[int, int, int, int]:
        x, y, w, h = self._main_region()
        list_x = max(0, x + 6)
        list_y = y + 6
        list_w = max(300, w - 18)
        list_h = max(240, h - 18)
        return (list_x, list_y, list_w, list_h)

    def _resume_detail_region(self) -> tuple[int, int, int, int]:
        x, y, w, h = self._main_region()
        if w <= 0 or h <= 0:
            return (x, y, 0, max(0, h))
        detail_w = max(0, min(w, int(w * 0.58)))
        detail_x = x + max(0, w - detail_w)
        return (detail_x, y, detail_w, h)

    def _job_list_region(self) -> tuple[int, int, int, int]:
        x, y, w, h = self._main_region()
        return (x, y, w, h)

    def _message_input_point(self) -> tuple[int, int]:
        x, y, w, h = self._resume_detail_region()
        return (x + int(w * 0.55), y + h - 110)

    def _find_text_position(
        self,
        text: str,
        screenshot: Image.Image = None,
        *,
        max_side: int = MAX_OCR_SIDE,
        timeout_sec: int = OCR_TIMEOUT_SEC,
    ) -> dict | None:
        """在屏幕上查找指定文字的位置"""
        if screenshot is None:
            screenshot = self._screenshot()

        items = ocr_image(screenshot, max_side=max_side, timeout_sec=timeout_sec)
        for item in items:
            if text in item["text"]:
                return item
        return None

    def _find_all_text(
        self,
        text: str,
        screenshot: Image.Image = None,
        *,
        max_side: int = MAX_OCR_SIDE,
        timeout_sec: int = OCR_TIMEOUT_SEC,
    ) -> list[dict]:
        """查找屏幕上所有包含指定文字的位置"""
        if screenshot is None:
            screenshot = self._screenshot()

        items = ocr_image(screenshot, max_side=max_side, timeout_sec=timeout_sec)
        return [item for item in items if text in item["text"]]

    def _click_text(
        self,
        text: str,
        screenshot: Image.Image = None,
        *,
        max_side: int = MAX_OCR_SIDE,
        timeout_sec: int = OCR_TIMEOUT_SEC,
    ) -> bool:
        """点击屏幕上指定文字"""
        if self.dry_run:
            logger.info(f"[dry-run] 跳过点击: {text}")
            return True

        pos = self._find_text_position(
            text,
            screenshot,
            max_side=max_side,
            timeout_sec=timeout_sec,
        )
        if pos:
            center_x = pos["x"] + pos["w"] // 2
            center_y = pos["y"] + pos["h"] // 2
            pyautogui.click(center_x, center_y)
            logger.info(f"点击: {text} @ ({center_x}, {center_y})")
            return True
        return False

    def _click_text_in_region(
        self,
        text: str,
        region: tuple[int, int, int, int],
        *,
        max_side: int = MAX_OCR_SIDE,
        timeout_sec: int = OCR_TIMEOUT_SEC,
    ) -> bool:
        if self.dry_run:
            logger.info(f"[dry-run] 跳过区域点击: {text}")
            return True

        screen = self._screenshot(region=region)
        pos = self._find_text_position(
            text,
            screen,
            max_side=max_side,
            timeout_sec=timeout_sec,
        )
        if not pos:
            return False

        origin_x, origin_y, _, _ = region
        center_x = origin_x + pos["x"] + pos["w"] // 2
        center_y = origin_y + pos["y"] + pos["h"] // 2
        pyautogui.click(center_x, center_y)
        logger.info(f"区域点击: {text} @ ({center_x}, {center_y})")
        return True

    def _type_text(self, text: str):
        """模拟键盘输入"""
        if self.dry_run:
            logger.info(f"[dry-run] 跳过输入: {text[:30]}")
            return
        pyautogui.typewrite(text, interval=0.05)

    def _press(self, key: str):
        """按键"""
        if self.dry_run:
            logger.info(f"[dry-run] 跳过按键: {key}")
            return
        pyautogui.press(key)

    def _wait(self, seconds: float = 3):
        time.sleep(seconds)

    def _random_wait(self, min_s: float = 1.5, max_s: float = 4.0):
        import random
        time.sleep(random.uniform(min_s, max_s))

    def _frontmost_app_name(self) -> str:
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get name of first application process whose frontmost is true',
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return (result.stdout or "").strip()

    def _boss_app_running(self) -> bool:
        try:
            result = subprocess.run(
                ["pgrep", "-f", self.app_path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and (result.stdout or "").strip():
                return True
        except Exception:
            pass
        return False

    def _activate_boss_app(self):
        commands = [
            ["osascript", "-e", f'tell application "{self.app_name}" to activate'],
            ["open", "-a", self.app_path],
        ]
        for cmd in commands:
            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                time.sleep(1.5)
                frontmost = self._frontmost_app_name()
                if self.app_name in frontmost or "BOSS" in frontmost:
                    logger.info(f"已切换到前台应用: {frontmost}")
                    return
            except Exception:
                continue
        logger.warning("未能自动切换到Boss客户端前台，将继续尝试OCR检测")

    def _ensure_boss_frontmost(self):
        frontmost = self._frontmost_app_name()
        if self.app_name in frontmost or "BOSS" in frontmost:
            return
        logger.info(f"当前前台应用不是Boss: {frontmost}，尝试切换...")
        self._activate_boss_app()

    def _click_point(self, x: int, y: int):
        if self.dry_run:
            logger.info(f"[dry-run] 跳过点击坐标: ({x}, {y})")
            return
        pyautogui.click(x, y)

    # 通用词：菜单项、按钮文字、状态标签，不是人名
    _GENERIC_WORDS = frozenset({
        "工作台", "推荐", "搜索", "职位", "互动", "消息", "更多", "全部", "未读",
        "沟通中", "新招呼", "已约面", "在线", "刚刚活跃", "活跃", "查看详情",
        "筛", "选", "过滤", "筛选", "排序", "最新", "推荐牛人", "牛人管理",
        "道具", "工具箱", "招聘规范", "客服", "面试", "招聘数据", "账号", "升级",
        "加载", "加载中", "暂无数据", "上一页", "下一页", "收起", "展开",
        "男", "女", "今日", "昨日", "本周",
    })

    def _candidate_name_from_line(self, items: list[dict]) -> str:
        """从一行OCR文字中提取候选人名字。
        策略：最左侧的2-4字纯中文词，且不在通用词表里。
        """
        for item in sorted(items, key=lambda x: x["x"]):
            text = item["text"].strip()
            if not text or text in self._GENERIC_WORDS:
                continue
            # 纯2-4字中文 → 很可能是名字
            if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", text):
                return text
        # fallback: 取最左侧非通用短词
        for item in sorted(items, key=lambda x: x["x"]):
            text = item["text"].strip()
            if not text or text in self._GENERIC_WORDS:
                continue
            if len(text) <= 8:
                return text
        return "未知"

    def _resume_from_greet_button(
        self, button_item: dict, items: list[dict]
    ) -> dict | None:
        row_items = [
            item for item in items
            if abs((item["y"] + item["h"] / 2) - (button_item["y"] + button_item["h"] / 2)) < 48
            and item["x"] <= button_item["x"]
        ]
        if not row_items:
            return None

        name = self._candidate_name_from_line(row_items)
        if not name or name == "未知":
            return None

        left_x = min(item["x"] for item in row_items)
        top_y = min(item["y"] for item in row_items)
        bottom_y = max(item["y"] + item["h"] for item in row_items)
        click_point = (
            int((left_x + button_item["x"]) / 2),
            int((top_y + bottom_y) / 2),
        )
        line_text = " ".join(item["text"] for item in sorted(row_items, key=lambda x: (x["y"], x["x"])))
        return {
            "name": name,
            "raw_text": line_text,
            "dedup_key": line_text[:40] or name,
            "position": row_items[0],
            "click_point": click_point,
            "greet_button_point": (
                int(button_item["x"] + button_item["w"] / 2),
                int(button_item["y"] + button_item["h"] / 2),
            ),
            "greet_button_box": (
                int(button_item["x"]),
                int(button_item["y"]),
                int(button_item["w"]),
                int(button_item["h"]),
            ),
        }

    def _find_greet_buttons(
        self, screen: Image.Image, origin: tuple[int, int] = (0, 0)
    ) -> list[dict]:
        image = screen.convert("RGB")
        width, height = image.size
        pixels = image.load()
        visited: set[tuple[int, int]] = set()
        buttons: list[dict] = []
        ox, oy = origin

        def is_button_color(r: int, g: int, b: int) -> bool:
            return 0 <= r <= 110 and 140 <= g <= 230 and 140 <= b <= 230 and (g + b) - r >= 250

        for y in range(height):
            for x in range(width):
                if (x, y) in visited:
                    continue
                r, g, b = pixels[x, y]
                if not is_button_color(r, g, b):
                    continue

                queue = deque([(x, y)])
                visited.add((x, y))
                xs = []
                ys = []

                while queue:
                    cx, cy = queue.popleft()
                    xs.append(cx)
                    ys.append(cy)
                    for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                        if not (0 <= nx < width and 0 <= ny < height):
                            continue
                        if (nx, ny) in visited:
                            continue
                        nr, ng, nb = pixels[nx, ny]
                        if not is_button_color(nr, ng, nb):
                            continue
                        visited.add((nx, ny))
                        queue.append((nx, ny))

                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                box_w = max_x - min_x + 1
                box_h = max_y - min_y + 1
                area = box_w * box_h
                if area < 1200 or box_w < 45 or box_h < 18:
                    continue

                buttons.append(
                    {
                        "text": "打招呼",
                        "x": ox + min_x,
                        "y": oy + min_y,
                        "w": box_w,
                        "h": box_h,
                    }
                )

        buttons.sort(key=lambda item: (item["y"], item["x"]))
        return buttons

    def _safe_ocr(
        self,
        screen: Image.Image,
        *,
        purpose: str,
        max_side: int = MAX_OCR_SIDE,
        timeout_sec: int = OCR_TIMEOUT_SEC,
    ) -> list[dict]:
        try:
            return ocr_image(screen, max_side=max_side, timeout_sec=timeout_sec)
        except RuntimeError as exc:
            logger.warning(f"{purpose}失败: {exc}")
            return []

    def _ocr_resumes_screen(self, screen: Image.Image) -> list[dict]:
        width, height = screen.size
        if height <= 900:
            return ocr_image(
                screen,
                max_side=FAST_LIST_OCR_SIDE,
                timeout_sec=FAST_LIST_OCR_TIMEOUT_SEC,
            )

        overlap = 80
        midpoint = height // 2
        bands = [
            (0, min(height, midpoint + overlap)),
            (max(0, midpoint - overlap), height),
        ]
        merged: list[dict] = []
        seen: set[tuple[str, int, int]] = set()
        for top, bottom in bands:
            band = screen.crop((0, top, width, bottom))
            items = ocr_image(
                band,
                max_side=FAST_LIST_OCR_SIDE,
                timeout_sec=FAST_LIST_OCR_TIMEOUT_SEC,
            )
            for item in items:
                adjusted = dict(item)
                adjusted["y"] += top
                key = (
                    adjusted["text"],
                    int(round(adjusted["x"] / 8)),
                    int(round(adjusted["y"] / 8)),
                )
                if key in seen:
                    continue
                seen.add(key)
                merged.append(adjusted)
        return merged

    # ==================== 主流程 ====================

    def self_check(self) -> dict:
        """OCR检测当前屏幕是否处于Boss直聘客户端主界面"""
        frontmost = self._frontmost_app_name()
        screen = self._screenshot(region=self._sidebar_region())
        items = ocr_image(screen)
        found_keywords: list[str] = []
        sorted_keywords = sorted(self.required_keywords, key=len, reverse=True)
        for item in items:
            text = item["text"]
            matched = next((keyword for keyword in sorted_keywords if keyword in text), None)
            if matched and matched not in found_keywords:
                found_keywords.append(matched)
        frontmost_upper = frontmost.upper()
        frontmost_is_boss = self.app_name in frontmost or "BOSS" in frontmost_upper or "BOSS" in self.app_name.upper() and "BOSS" in frontmost_upper
        app_running = self._boss_app_running()
        result = {
            "ready": frontmost_is_boss or app_running or len(found_keywords) >= self.minimum_keywords,
            "ocr_item_count": len(items),
            "frontmost_app": frontmost,
            "found_keywords": found_keywords,
            "missing_keywords": [
                keyword for keyword in self.required_keywords if keyword not in found_keywords
            ],
            "app_running": app_running,
        }
        self.last_self_check = result
        return result

    def start(self):
        """检查Boss直聘客户端是否已打开"""
        logger.info("请确保Boss直聘PC客户端已打开并登录")
        logger.info("紧急停止：将鼠标移到屏幕左上角")
        if self.dry_run:
            logger.info("当前处于 dry-run 模式：只识别界面，不执行点击和发消息")

        self._ensure_boss_frontmost()
        self._ensure_boss_window_usable()
        retries = int(self.rpa_config.get("start_retry_count", 3))
        retry_delay = float(self.rpa_config.get("start_retry_delay", 1.5))
        last_result = None
        last_error = None

        for attempt in range(1, retries + 1):
            try:
                result = self.self_check()
                last_result = result
                if result["ready"]:
                    logger.info(f"检测到Boss直聘客户端界面: {result['found_keywords']}")
                    return
                logger.warning(
                    f"启动自检未通过（第 {attempt}/{retries} 次）: "
                    f"前台={result.get('frontmost_app', '?')} "
                    f"关键词={result.get('found_keywords', [])}"
                )
            except Exception as exc:
                last_error = exc
                logger.warning(f"启动自检异常（第 {attempt}/{retries} 次）: {exc}")

            if attempt < retries:
                self._wait(retry_delay)

        if last_error is not None:
            raise RuntimeError(f"启动自检失败: {last_error}") from last_error

        raise RuntimeError(
            "未检测到Boss直聘客户端主界面，请先把客户端切到前台并确保侧边栏可见"
            if last_result is None
            else (
                "未检测到Boss直聘客户端主界面，请先把客户端切到前台并确保侧边栏可见；"
                f"当前前台={last_result.get('frontmost_app', '?')} "
                f"关键词={last_result.get('found_keywords', [])}"
            )
        )

    # ==================== 岗位管理 ====================

    def fetch_my_jobs(self) -> list[dict]:
        """从职位管理页面抓取岗位列表"""
        jobs = []
        logger.info("正在抓取岗位列表...")

        # 点击「职位管理」
        sidebar_region = self._sidebar_region()
        if not self._click_text_in_region(
            "职位管理",
            sidebar_region,
            max_side=SIDEBAR_OCR_SIDE,
            timeout_sec=SIDEBAR_OCR_TIMEOUT_SEC,
        ):
            logger.warning("未找到「职位管理」，尝试其他方式...")
            self._click_text_in_region(
                "职位",
                sidebar_region,
                max_side=SIDEBAR_OCR_SIDE,
                timeout_sec=SIDEBAR_OCR_TIMEOUT_SEC,
            )

        self._wait(5)

        # 截屏OCR读取岗位列表
        region = self._job_list_region()
        screen = self._screenshot(region=region)
        items = self._safe_ocr(
            screen,
            purpose="岗位列表OCR",
            max_side=DETAIL_OCR_SIDE,
            timeout_sec=DETAIL_OCR_TIMEOUT_SEC,
        )

        # 查找类似岗位名称的文字（通常在列表区域）
        for item in items:
            text = item["text"].strip()
            # 过滤掉菜单项
            if text in ["职位管理", "推荐牛人", "搜索", "沟通", "互动", "牛人管理"]:
                continue
            # 岗位名称通常包含特定关键词
            if 3 < len(text) < 30 and text not in jobs:
                # 检查是否像岗位名称（包含总监、经理、工程师等）
                job_keywords = ["总监", "经理", "工程师", "主管", "专员", "助理", "主管",
                                "开发", "设计", "运营", "编辑", "顾问", "分析师"]
                if any(kw in text for kw in job_keywords):
                    jobs.append({"title": text, "summary": text})

        logger.info(f"抓取到 {len(jobs)} 个岗位")
        return jobs

    # ==================== 推荐牛人 ====================

    def fetch_resumes(self, max_count: int = 20, from_current_page: bool = False) -> list[dict]:
        """抓取推荐牛人列表"""
        resumes = []
        empty_screens = 0
        max_empty_screens = 3
        if from_current_page:
            logger.info("从当前推荐页开始抓取候选人...")
        else:
            logger.info("开始抓取推荐牛人...")

            # 点击「推荐牛人」
            sidebar_region = self._sidebar_region()
            if not self._click_text_in_region(
                "推荐牛人",
                sidebar_region,
                max_side=SIDEBAR_OCR_SIDE,
                timeout_sec=SIDEBAR_OCR_TIMEOUT_SEC,
            ):
                if not self._click_text_in_region(
                    "推荐",
                    sidebar_region,
                    max_side=SIDEBAR_OCR_SIDE,
                    timeout_sec=SIDEBAR_OCR_TIMEOUT_SEC,
                ):
                    logger.error("未找到推荐牛人入口")
                    return resumes

            self._wait(5)

        # 循环截屏+OCR读取简历
        for i in range(max_count // 5 + 1):
            region = self._recommend_list_region()
            screen = self._screenshot(region=region)
            try:
                page_resumes = self._parse_resumes_from_screen(
                    screen, origin=(region[0], region[1])
                )
            except RuntimeError as exc:
                logger.warning(f"推荐列表OCR失败，改用按钮区域兜底: {exc}")
                page_resumes = self._extract_resumes_from_buttons(
                    screen, origin=(region[0], region[1])
                )

            new_count = 0
            for r in page_resumes:
                if r.get("name") and r["name"] not in [ex.get("name") for ex in resumes]:
                    resumes.append(r)
                    new_count += 1

            logger.info(f"本屏识别到 {new_count} 份新简历，累计 {len(resumes)}")

            if new_count == 0:
                empty_screens += 1
            else:
                empty_screens = 0

            if len(resumes) >= max_count:
                break
            if empty_screens >= max_empty_screens:
                logger.info("连续多屏未发现新候选人，停止下滑")
                break

            # 向下滚动加载更多
            self._scroll_down()
            self._random_wait(2, 4)

        logger.info(f"共抓取 {len(resumes)} 份简历")
        return resumes

    def get_visible_resumes(self) -> list[dict]:
        """只解析当前屏幕可见候选人，不滚动。"""
        if self._is_candidate_detail_open():
            self.close_resume_detail()
        resumes = self._read_visible_resumes_once()
        if resumes:
            return resumes

        # 有时上一位候选人的详情页还停留在前台，先按 Esc 返回列表再重试一次。
        self._press("esc")
        self._wait(0.8)
        return self._read_visible_resumes_once()

    def _read_visible_resumes_once(self) -> list[dict]:
        region = self._recommend_list_region()
        screen = self._screenshot(region=region)
        if self.prefer_button_fallback:
            try:
                return self._extract_resumes_from_buttons(screen, origin=(region[0], region[1]))
            except RuntimeError as exc:
                logger.warning(f"按钮区域候选人识别失败，回退到整屏OCR: {exc}")
        try:
            return self._parse_resumes_from_screen(screen, origin=(region[0], region[1]))
        except RuntimeError as exc:
            logger.warning(f"当前屏候选人OCR失败，改用按钮区域兜底: {exc}")
            try:
                return self._extract_resumes_from_buttons(screen, origin=(region[0], region[1]))
            except RuntimeError as fallback_exc:
                logger.warning(f"按钮区域兜底也失败，返回空列表: {fallback_exc}")
                return []

    def click_first_visible_candidate(self) -> bool:
        """点击当前推荐页第一张可见候选人卡片。"""
        try:
            resumes = self.get_visible_resumes()
        except Exception as exc:
            logger.warning(f"可见候选人OCR失败，改用按钮兜底: {exc}")
            resumes = []
        if resumes:
            first = sorted(
                resumes,
                key=lambda item: (item.get("click_point", (10**9, 10**9))[1], item.get("click_point", (10**9, 10**9))[0]),
            )[0]
            return self.open_resume(first)

        region = self._recommend_list_region()
        screen = self._screenshot(region=region)
        buttons = self._find_greet_buttons(screen, origin=(region[0], region[1]))
        if not buttons:
            logger.warning("当前屏幕未识别到可点击候选人")
            return False

        first_button = buttons[0]
        click_x = max(region[0] + 80, first_button["x"] - max(180, int(first_button["w"] * 2.6)))
        click_y = first_button["y"] + first_button["h"] // 2
        self._click_point(click_x, click_y)
        self._wait(1.5)
        logger.info(f"已按按钮位置推断并点击候选人 @ ({click_x}, {click_y})")
        return True

    def _parse_resumes_from_screen(self, screen: Image.Image, origin: tuple[int, int] = (0, 0)) -> list[dict]:
        """从截屏中解析简历信息"""
        resumes = []
        items = self._ocr_resumes_screen(screen)
        ox, oy = origin
        for item in items:
            item["x"] += ox
            item["y"] += oy

        # 按Y坐标分组（同一行的文字Y坐标接近）
        items.sort(key=lambda x: x["y"])

        # 找出简历区域（排除菜单栏）
        # 菜单栏通常在左侧，简历在右侧/主区域
        if not items:
            return resumes

        # 按Y坐标聚类
        lines = []
        current_line = []
        current_y = -1

        for item in items:
            if current_y < 0 or abs(item["y"] - current_y) < 20:
                current_line.append(item)
                current_y = item["y"] if current_y < 0 else current_y
            else:
                if current_line:
                    lines.append(current_line)
                current_line = [item]
                current_y = item["y"]
        if current_line:
            lines.append(current_line)

        greet_buttons = [
            item for item in items
            if "打招呼" in item["text"] or "立即沟通" in item["text"] or "继续沟通" in item["text"]
        ]
        if greet_buttons:
            seen_keys: set[str] = set()
            for button in greet_buttons:
                candidate = self._resume_from_greet_button(button, items)
                if not candidate:
                    continue
                key = candidate.get("dedup_key") or candidate.get("name")
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                resumes.append(candidate)
            if resumes:
                return resumes

        # 从行中提取简历信息
        skip_keywords = ["推荐牛人", "职位管理", "搜索", "沟通", "互动", "牛人管理",
                         "道具", "工具箱", "更多", "招聘规范", "客服", "面试",
                         "招聘数据", "账号", "升级", "加载中", "筛选"]

        for line in lines:
            line_text = " ".join([item["text"] for item in line])

            # 跳过菜单栏行
            if any(kw in line_text for kw in skip_keywords):
                continue

            # 判断是否像简历行（包含年龄、学历、经验等关键词）
            resume_keywords = ["岁", "年", "本科", "硕士", "博士", "大专", "经验",
                               "工作", "学历", "男", "女"]
            if any(kw in line_text for kw in resume_keywords) and len(line_text) > 15:
                name = self._candidate_name_from_line(line)
                xs = [item["x"] for item in line]
                ys = [item["y"] for item in line]
                ws = [item["x"] + item["w"] for item in line]
                hs = [item["y"] + item["h"] for item in line]
                click_point = (
                    int((min(xs) + max(ws)) / 2),
                    int((min(ys) + max(hs)) / 2),
                )

                resumes.append({
                    "name": name,
                    "raw_text": line_text,
                    "dedup_key": line_text[:40],  # 去重用，基于内容前40字
                    "position": line[0],  # 记录位置，后续可点击
                    "click_point": click_point,
                })

        return resumes

    def _extract_resumes_from_buttons(
        self, screen: Image.Image, origin: tuple[int, int] = (0, 0)
    ) -> list[dict]:
        """整屏OCR失败时，按按钮附近小区域逐个提取候选人摘要。"""
        buttons = self._find_greet_buttons(screen, origin=origin)
        if not buttons:
            return []

        ox, oy = origin
        screen_w, screen_h = screen.size
        resumes: list[dict] = []
        seen_keys: set[str] = set()

        for button in buttons:
            local_x = button["x"] - ox
            local_y = button["y"] - oy
            crop_left = max(0, local_x - 420)
            crop_top = max(0, local_y - 90)
            crop_right = min(screen_w, local_x + max(40, button["w"] // 2))
            crop_bottom = min(screen_h, local_y + button["h"] + 90)
            if crop_right <= crop_left or crop_bottom <= crop_top:
                continue

            crop = screen.crop((crop_left, crop_top, crop_right, crop_bottom))
            items = self._safe_ocr(
                crop,
                purpose="按钮邻域候选人OCR",
                max_side=900,
                timeout_sec=10,
            )
            if not items:
                dedup_key = f"button:{button['x']}:{button['y']}"
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)
                click_x = max(ox + 80, button["x"] - max(180, int(button["w"] * 2.6)))
                click_y = button["y"] + button["h"] // 2
                resumes.append(
                    {
                        "name": "未知候选人",
                        "raw_text": "",
                        "dedup_key": dedup_key,
                        "click_point": (click_x, click_y),
                        "greet_button_point": (
                            int(button["x"] + button["w"] / 2),
                            int(button["y"] + button["h"] / 2),
                        ),
                        "greet_button_box": (
                            int(button["x"]),
                            int(button["y"]),
                            int(button["w"]),
                            int(button["h"]),
                        ),
                        "requires_detail": True,
                    }
                )
                continue

            adjusted_items = []
            for item in items:
                adjusted_items.append(
                    {
                        "text": item["text"],
                        "x": item["x"] + ox + crop_left,
                        "y": item["y"] + oy + crop_top,
                        "w": item["w"],
                        "h": item["h"],
                    }
                )

            adjusted_items.sort(key=lambda item: (item["y"], item["x"]))
            line_text = " ".join(item["text"] for item in adjusted_items if item["text"].strip())
            if not line_text:
                continue

            name = self._candidate_name_from_line(adjusted_items)
            click_x = max(ox + 80, button["x"] - max(180, int(button["w"] * 2.6)))
            click_y = button["y"] + button["h"] // 2
            dedup_key = line_text[:80]
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            resumes.append(
                {
                    "name": name,
                    "raw_text": line_text,
                    "dedup_key": dedup_key,
                    "click_point": (click_x, click_y),
                    "greet_button_point": (
                        int(button["x"] + button["w"] / 2),
                        int(button["y"] + button["h"] / 2),
                    ),
                    "greet_button_box": (
                        int(button["x"]),
                        int(button["y"]),
                        int(button["w"]),
                        int(button["h"]),
                    ),
                    "requires_detail": False,
                }
            )

        return resumes

    def open_resume(self, resume: dict) -> bool:
        """点击打开当前候选人，便于读取详情和直接打招呼"""
        try:
            click_point = resume.get("click_point")
            if not click_point:
                logger.warning(f"候选人缺少点击坐标: {resume.get('name', '?')}")
                return False

            self.current_resume = resume
            x, y = click_point
            self._click_point(x, y)
            self._wait(2)
            logger.info(f"已打开候选人: {resume.get('name', '?')}")
            return True
        except Exception as e:
            logger.error(f"打开候选人失败 {resume.get('name', '?')}: {e}")
            return False

    def capture_resume_card(self, resume: dict) -> Image.Image | None:
        """截取当前候选人卡片区域，用于视觉快筛。"""
        button_box = resume.get("greet_button_box")
        if not button_box:
            return None

        region = self._recommend_list_region()
        screen = self._screenshot(region=region)
        region_x, region_y, _, _ = region
        button_x, button_y, button_w, button_h = button_box

        local_x = button_x - region_x
        local_y = button_y - region_y
        if local_x < 0 or local_y < 0:
            return None

        screen_w, screen_h = screen.size
        crop_left = max(0, local_x - 460)
        crop_top = max(0, local_y - 110)
        crop_right = min(screen_w, local_x + button_w + 24)
        crop_bottom = min(screen_h, local_y + button_h + 110)
        if crop_right <= crop_left or crop_bottom <= crop_top:
            return None
        return screen.crop((crop_left, crop_top, crop_right, crop_bottom)).convert("RGB")

    def close_resume_detail(self) -> bool:
        """关闭当前候选人详情，返回推荐列表。"""
        if not self._is_candidate_detail_open():
            return False
        window = self._boss_window_info()
        if not window:
            return False
        close_x = window["x"] + window["width"] - 42
        close_y = window["y"] + 26
        self._click_point(close_x, close_y)
        self._wait(1.2)
        return not self._is_candidate_detail_open()

    def get_active_resume_detail(self) -> dict:
        """读取当前打开候选人的详情面板"""
        region = self._resume_detail_region()
        max_pages = int(self.rpa_config.get("resume_detail_scroll_pages", 4))
        detail_lines: list[str] = []
        seen_lines: set[str] = set()

        for page_index in range(max_pages):
            screen = self._screenshot(region=region)
            items = self._safe_ocr(
                screen,
                purpose="简历详情OCR",
                max_side=DETAIL_OCR_SIDE,
                timeout_sec=DETAIL_OCR_TIMEOUT_SEC,
            )
            for item in items:
                text = item["text"].strip()
                if not text or text in seen_lines:
                    continue
                seen_lines.add(text)
                detail_lines.append(text)

            if page_index < max_pages - 1:
                self._scroll_region(region, amount=-12)
                self._wait(0.8)

        detail_text = "\n".join(detail_lines)
        return {"detail_text": detail_text[:4000]}

    def _detect_greeting_limit_reached(self) -> bool:
        screen = self._screenshot(region=self._main_region())
        items = self._safe_ocr(
            screen,
            purpose="打招呼额度检测",
            max_side=FAST_LIST_OCR_SIDE,
            timeout_sec=FAST_LIST_OCR_TIMEOUT_SEC,
        )
        text = " ".join(item["text"] for item in items)
        limit_keywords = [
            "今日打招呼次数已用完",
            "今日沟通人数已达上限",
            "今日已达上限",
            "次数已用完",
            "明日再来",
            "上限",
        ]
        return any(keyword in text for keyword in limit_keywords)

    def _detect_operation_blocked(self) -> str | None:
        x, y, w, h = self._main_region()
        toast_region = (
            x + max(120, int(w * 0.18)),
            y,
            max(360, int(w * 0.56)),
            max(80, int(h * 0.12)),
        )
        screen = self._screenshot(region=toast_region)
        items = self._safe_ocr(
            screen,
            purpose="操作拦截提示检测",
            max_side=900,
            timeout_sec=10,
        )
        text = " ".join(item["text"] for item in items)
        blocked_keywords = [
            "账号状态异常",
            "暂不支持此操作",
            "请联系客服",
            "操作异常",
        ]
        if any(keyword in text for keyword in blocked_keywords):
            return text
        return None

    def _is_candidate_detail_open(self) -> bool:
        x, y, w, h = self._resume_detail_region()
        detail_region = (
            x,
            y,
            w,
            min(h, 420),
        )
        screen = self._screenshot(region=detail_region)
        items = self._safe_ocr(
            screen,
            purpose="候选人详情态检测",
            max_side=900,
            timeout_sec=10,
        )
        text = " ".join(item["text"] for item in items)
        detail_keywords = ["经历概览", "收藏", "不合适", "举报", "转发牛人"]
        return any(keyword in text for keyword in detail_keywords)

    def greet_current_candidate(self, candidate: dict | str) -> str:
        """在当前候选人上下文中直接点击打招呼按钮。

        返回:
          - sent: 已触发打招呼
          - limit_reached: 今日额度可能已用完
          - unavailable: 未找到可用按钮
        """
        name = candidate.get("name", "未知") if isinstance(candidate, dict) else str(candidate)
        if self.dry_run:
            logger.info(f"[dry-run] 模拟向 {name} 打招呼")
            return "sent"

        if isinstance(candidate, dict):
            button_point = candidate.get("greet_button_point")
            if button_point:
                try:
                    self._click_point(button_point[0], button_point[1])
                    self._wait(1.2)
                    blocked_message = self._detect_operation_blocked()
                    if blocked_message:
                        logger.warning(f"打招呼被拦截 {name}: {blocked_message}")
                        return "blocked"
                    if self._detect_greeting_limit_reached():
                        logger.warning(f"打招呼额度可能已用完: {name}")
                        return "limit_reached"
                    logger.info(f"已通过卡片按钮向 {name} 打招呼")
                    return "sent"
                except Exception as e:
                    logger.error(f"卡片按钮打招呼失败 {name}: {e}")

        if not self._is_candidate_detail_open():
            logger.warning(f"当前未处于候选人详情页，跳过打招呼: {name}")
            return "unavailable"

        try:
            for action_text in ("打招呼", "立即沟通", "立即开聊", "继续沟通"):
                if self._click_text(action_text):
                    self._wait(1.2)
                    blocked_message = self._detect_operation_blocked()
                    if blocked_message:
                        logger.warning(f"打招呼被拦截 {name}: {blocked_message}")
                        return "blocked"
                    if self._detect_greeting_limit_reached():
                        logger.warning(f"打招呼额度可能已用完: {name}")
                        return "limit_reached"
                    logger.info(f"已向 {name} 打招呼")
                    return "sent"
        except Exception as e:
            logger.error(f"打招呼失败 {name}: {e}")

        try:
            region = self._main_region()
            screen = self._screenshot(region=region)
            buttons = self._find_greet_buttons(screen, origin=(region[0], region[1]))
            if buttons:
                button = buttons[0]
                self._click_point(button["x"] + button["w"] // 2, button["y"] + button["h"] // 2)
                self._wait(1.2)
                blocked_message = self._detect_operation_blocked()
                if blocked_message:
                    logger.warning(f"打招呼被拦截 {name}: {blocked_message}")
                    return "blocked"
                if self._detect_greeting_limit_reached():
                    logger.warning(f"打招呼额度可能已用完: {name}")
                    return "limit_reached"
                logger.info(f"已通过按钮坐标向 {name} 打招呼")
                return "sent"
        except Exception as e:
            logger.error(f"按钮兜底打招呼失败 {name}: {e}")

        if self._detect_greeting_limit_reached():
            return "limit_reached"

        return "unavailable"

    def _scroll_region(self, region: tuple[int, int, int, int], amount: int = -5):
        """在指定区域内滚动，优先用于详情面板。"""
        if self.dry_run:
            logger.info(f"[dry-run] 跳过区域滚动: {region}")
            return

        x, y, w, h = region
        pyautogui.click(x + w // 2, y + h // 2)
        pyautogui.scroll(amount)

    def _scroll_down(self):
        """向下滚动页面"""
        if self.dry_run:
            logger.info("[dry-run] 跳过滚动")
            return
        # 在屏幕中间区域滚动
        screen_w, screen_h = pyautogui.size()
        pyautogui.click(screen_w // 2, screen_h // 2)
        pyautogui.scroll(-5)  # 向下滚5格

    def scroll_recommend_list(self, pages: int = 1):
        """在推荐列表区域内向下滚动若干次。"""
        if self.dry_run:
            logger.info(f"[dry-run] 跳过推荐列表滚动: {pages}")
            return
        region = self._recommend_list_region()
        x, y, w, h = region
        pyautogui.click(x + w // 2, y + min(h // 2, 260))
        for _ in range(max(1, pages)):
            pyautogui.scroll(-8)
            time.sleep(0.25)

    # ==================== 消息 ====================

    def get_unread_messages(self) -> list[dict]:
        """获取未读消息列表"""
        messages = []
        logger.info("检查未读消息...")

        # 点击「沟通」菜单
        if not self._click_text_in_region(
            "沟通",
            self._sidebar_region(),
            max_side=SIDEBAR_OCR_SIDE,
            timeout_sec=SIDEBAR_OCR_TIMEOUT_SEC,
        ):
            logger.warning("未找到沟通入口")
            return messages

        self._wait(3)

        # 截屏OCR
        screen = self._screenshot()
        items = self._safe_ocr(screen, purpose="消息列表OCR")

        # 查找带数字角标的（未读数）
        for item in items:
            text = item["text"].strip()
            if text.isdigit() and int(text) > 0:
                # 这个数字附近应该有候选人名字
                nearby = [i for i in items if abs(i["y"] - item["y"]) < 40]
                for nb in nearby:
                    if nb["text"] not in ["沟通", "全部", "新招呼", "沟通中", "已约面"]:
                        messages.append({
                            "name": nb["text"],
                            "message_text": nb["text"],
                        })

        logger.info(f"发现 {len(messages)} 条未读消息")
        return messages

    def send_message(self, name: str, message: str) -> bool:
        """向候选人发送消息"""
        try:
            if self.dry_run:
                logger.info(f"[dry-run] 模拟向 {name} 发送消息: {message[:60]}")
                return True

            # 主动找人场景优先在当前详情页直接沟通，不再依赖聊天列表按名字二次查找
            for action_text in ("立即沟通", "立即开聊", "打招呼", "沟通"):
                if self._click_text(action_text):
                    self._wait(1)
                    break

            input_x, input_y = self._message_input_point()
            self._click_point(input_x, input_y)
            self._wait(0.5)

            # 输入消息（使用剪贴板方式，支持中文）
            import pyperclip
            pyperclip.copy(message)
            pyautogui.hotkey("command", "v")
            self._wait(1)

            # 按回车发送
            pyautogui.press("enter")
            logger.info(f"消息已发送给 {name}")
            return True

        except Exception as e:
            logger.error(f"发送消息失败 {name}: {e}")
            return False

    # ==================== 关闭 ====================

    def stop(self):
        logger.info("RPA爬虫已停止")
