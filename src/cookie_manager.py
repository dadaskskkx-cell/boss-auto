"""Cookie持久化管理"""

import json
from pathlib import Path
from loguru import logger


class CookieManager:
    def __init__(self, cookie_path: str):
        self.cookie_path = Path(cookie_path)
        self.cookie_path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, cookies: list[dict]):
        """保存cookies到文件"""
        with open(self.cookie_path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        logger.info(f"Cookies已保存: {self.cookie_path}")

    def load(self) -> list[dict] | None:
        """从文件加载cookies"""
        if not self.cookie_path.exists():
            return None
        try:
            with open(self.cookie_path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            if cookies:
                logger.info("Cookies加载成功")
                return cookies
        except Exception as e:
            logger.warning(f"Cookies加载失败: {e}")
        return None

    def clear(self):
        """清除已保存的cookies"""
        if self.cookie_path.exists():
            self.cookie_path.unlink()
            logger.info("Cookies已清除")
