"""Boss直聘RPA爬虫 - 基于pyautogui+OCR，自动化PC客户端"""

import asyncio
import base64
import io
import time
from pathlib import Path

import pyautogui
from PIL import Image
from loguru import logger

# PaddleOCR 延迟导入（首次加载较慢）
_ocr_engine = None


def _get_ocr():
    global _ocr_engine
    if _ocr_engine is None:
        from paddleocr import PaddleOCR
        _ocr_engine = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        logger.info("OCR引擎已加载")
    return _ocr_engine


def ocr_image(image: Image.Image) -> list[dict]:
    """对图片做OCR，返回 [{text, x, y, w, h}, ...]"""
    ocr = _get_ocr()
    img_bytes = io.BytesIO()
    image.save(img_bytes, format="PNG")
    img_bytes.seek(0)

    import numpy as np
    img_array = np.array(image)

    results = ocr.ocr(img_array, cls=True)
    items = []
    if results and results[0]:
        for line in results[0]:
            box = line[0]  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
            text = line[1][0]
            confidence = line[1][1]
            if confidence > 0.5:
                x = int(box[0][0])
                y = int(box[0][1])
                w = int(box[2][0] - box[0][0])
                h = int(box[2][1] - box[0][1])
                items.append({"text": text, "x": x, "y": y, "w": w, "h": h})
    return items


class RPACrawler:
    """通过RPA操作Boss直聘PC客户端"""

    def __init__(self, config: dict):
        self.config = config
        pyautogui.PAUSE = 0.5  # 每次操作间隔0.5秒
        pyautogui.FAILSAFE = True  # 鼠标移到左上角可紧急停止

    def _screenshot(self, region=None) -> Image.Image:
        """截屏"""
        return pyautogui.screenshot(region=region)

    def _find_text_position(self, text: str, screenshot: Image.Image = None) -> dict | None:
        """在屏幕上查找指定文字的位置"""
        if screenshot is None:
            screenshot = self._screenshot()

        items = ocr_image(screenshot)
        for item in items:
            if text in item["text"]:
                return item
        return None

    def _find_all_text(self, text: str, screenshot: Image.Image = None) -> list[dict]:
        """查找屏幕上所有包含指定文字的位置"""
        if screenshot is None:
            screenshot = self._screenshot()

        items = ocr_image(screenshot)
        return [item for item in items if text in item["text"]]

    def _click_text(self, text: str, screenshot: Image.Image = None) -> bool:
        """点击屏幕上指定文字"""
        pos = self._find_text_position(text, screenshot)
        if pos:
            center_x = pos["x"] + pos["w"] // 2
            center_y = pos["y"] + pos["h"] // 2
            pyautogui.click(center_x, center_y)
            logger.info(f"点击: {text} @ ({center_x}, {center_y})")
            return True
        return False

    def _type_text(self, text: str):
        """模拟键盘输入"""
        pyautogui.typewrite(text, interval=0.05)

    def _press(self, key: str):
        """按键"""
        pyautogui.press(key)

    def _wait(self, seconds: float = 3):
        time.sleep(seconds)

    def _random_wait(self, min_s: float = 1.5, max_s: float = 4.0):
        import random
        time.sleep(random.uniform(min_s, max_s))

    # ==================== 主流程 ====================

    def start(self):
        """检查Boss直聘客户端是否已打开"""
        logger.info("请确保Boss直聘PC客户端已打开并登录")
        logger.info("紧急停止：将鼠标移到屏幕左上角")

        # 先做一次OCR检查，确认客户端窗口可见
        screen = self._screenshot()
        items = ocr_image(screen)
        texts = [item["text"] for item in items]

        # 检查是否有Boss直聘相关文字
        boss_keywords = ["推荐牛人", "职位管理", "沟通", "牛人管理"]
        found = [kw for kw in boss_keywords if any(kw in t for t in texts)]

        if found:
            logger.info(f"检测到Boss直聘客户端界面: {found}")
        else:
            logger.warning("未检测到Boss直聘界面，请确认客户端已打开并登录")
            logger.info("等待10秒后重试...")
            time.sleep(10)

    # ==================== 岗位管理 ====================

    def fetch_my_jobs(self) -> list[dict]:
        """从职位管理页面抓取岗位列表"""
        jobs = []
        logger.info("正在抓取岗位列表...")

        # 点击「职位管理」
        if not self._click_text("职位管理"):
            logger.warning("未找到「职位管理」，尝试其他方式...")
            self._click_text("职位")

        self._wait(5)

        # 截屏OCR读取岗位列表
        screen = self._screenshot()
        items = ocr_image(screen)

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

    def fetch_resumes(self, max_count: int = 20) -> list[dict]:
        """抓取推荐牛人列表"""
        resumes = []
        logger.info("开始抓取推荐牛人...")

        # 点击「推荐牛人」
        if not self._click_text("推荐牛人"):
            if not self._click_text("推荐"):
                logger.error("未找到推荐牛人入口")
                return resumes

        self._wait(5)

        # 循环截屏+OCR读取简历
        for i in range(max_count // 5 + 1):
            screen = self._screenshot()
            page_resumes = self._parse_resumes_from_screen(screen)

            new_count = 0
            for r in page_resumes:
                if r.get("name") and r["name"] not in [ex.get("name") for ex in resumes]:
                    resumes.append(r)
                    new_count += 1

            logger.info(f"本屏识别到 {new_count} 份新简历，累计 {len(resumes)}")

            if new_count == 0 or len(resumes) >= max_count:
                break

            # 向下滚动加载更多
            self._scroll_down()
            self._random_wait(2, 4)

        logger.info(f"共抓取 {len(resumes)} 份简历")
        return resumes

    def _parse_resumes_from_screen(self, screen: Image.Image) -> list[dict]:
        """从截屏中解析简历信息"""
        resumes = []
        items = ocr_image(screen)

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
                name = line[0]["text"].strip() if line else ""
                # 名字通常是2-3个中文字符
                if len(name) > 4:
                    name = name[:3]

                resumes.append({
                    "name": name,
                    "raw_text": line_text,
                    "position": line[0],  # 记录位置，后续可点击
                })

        return resumes

    def _scroll_down(self):
        """向下滚动页面"""
        # 在屏幕中间区域滚动
        screen_w, screen_h = pyautogui.size()
        pyautogui.click(screen_w // 2, screen_h // 2)
        pyautogui.scroll(-5)  # 向下滚5格

    # ==================== 消息 ====================

    def get_unread_messages(self) -> list[dict]:
        """获取未读消息列表"""
        messages = []
        logger.info("检查未读消息...")

        # 点击「沟通」菜单
        if not self._click_text("沟通"):
            logger.warning("未找到沟通入口")
            return messages

        self._wait(3)

        # 截屏OCR
        screen = self._screenshot()
        items = ocr_image(screen)

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
            # 先在聊天列表中找到该候选人并点击
            if not self._click_text(name):
                logger.warning(f"未找到候选人: {name}")
                return False

            self._wait(2)

            # 找到输入框并点击（输入框通常在页面底部）
            # 先截屏确认当前位置
            if not self._click_text("请输入"):
                # fallback: 点击屏幕底部中央区域（输入框通常在那里）
                screen_w, screen_h = pyautogui.size()
                pyautogui.click(screen_w // 2, screen_h - 100)

            self._wait(1)

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
