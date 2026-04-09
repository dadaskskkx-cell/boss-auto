"""消息发送管理"""

import inspect
import json
from pathlib import Path
from typing import Any

from loguru import logger


class Messenger:
    def __init__(self, config: dict, crawler: Any):
        self.config = config
        self.crawler = crawler
        self.matched_template = config["messaging"]["matched_template"]
        self.rejected_template = config["messaging"]["rejected_template"]
        self.processed_ids_file = Path(config["messaging"]["processed_ids_file"])
        self._processed_ids: set[str] = set()
        self._load_processed_ids()

    async def _send_message(self, geek_id: str, message: str) -> bool:
        result = self.crawler.send_message(geek_id, message)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

    def _load_processed_ids(self):
        """加载已处理的候选人ID"""
        if self.processed_ids_file.exists():
            try:
                with open(self.processed_ids_file, "r", encoding="utf-8") as f:
                    ids = json.load(f)
                    self._processed_ids = set(ids)
                logger.info(f"已加载 {len(self._processed_ids)} 个已处理ID")
            except Exception as e:
                logger.warning(f"加载已处理ID失败: {e}")
                self._processed_ids = set()

    def _save_processed_ids(self):
        """持久化已处理的候选人ID"""
        self.processed_ids_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.processed_ids_file, "w", encoding="utf-8") as f:
            json.dump(list(self._processed_ids), f, ensure_ascii=False, indent=2)

    def is_processed(self, geek_id: str) -> bool:
        """检查候选人是否已处理过"""
        return geek_id in self._processed_ids

    def mark_processed(self, geek_id: str):
        """标记候选人为已处理并持久化"""
        self._processed_ids.add(geek_id)
        self._save_processed_ids()

    async def send_matched_message(self, geek_id: str, questions: list[str] | None = None):
        """向匹配的候选人发送标准问题"""
        if self.is_processed(geek_id):
            logger.debug(f"跳过已处理候选人: {geek_id}")
            return

        message = self.matched_template.strip()
        if questions:
            # 将问题列表拼接
            q_text = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
            message = f"{message}\n{q_text}" if "{questions}" not in message else message.replace("{questions}", q_text)

        success = await self._send_message(geek_id, message)
        if success:
            self._processed_ids.add(geek_id)
            self._save_processed_ids()
            logger.info(f"已向匹配候选人发送消息: {geek_id}")

    async def send_rejected_message(self, geek_id: str):
        """向不合适的候选人发送婉拒消息"""
        if self.is_processed(geek_id):
            return

        message = self.rejected_template.strip()
        success = await self._send_message(geek_id, message)
        if success:
            self._processed_ids.add(geek_id)
            self._save_processed_ids()
            logger.info(f"已向不合适候选人发送婉拒: {geek_id}")

    async def handle_filter_result(self, geek_id: str, filter_result: dict, questions: list[str] | None = None):
        """根据筛选结果发送对应消息"""
        if self.is_processed(geek_id):
            return

        if filter_result["status"] == "matched":
            await self.send_matched_message(geek_id, questions)
        else:
            await self.send_rejected_message(geek_id)
