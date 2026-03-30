"""定时任务调度器"""

import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger


class TaskScheduler:
    def __init__(self, config: dict):
        self.config = config
        self.scheduler = AsyncIOScheduler()
        self._resume_scan_func = None
        self._message_check_func = None

    def set_resume_scan_func(self, func):
        """设置简历扫描回调"""
        self._resume_scan_func = func

    def set_message_check_func(self, func):
        """设置消息检查回调"""
        self._message_check_func = func

    def start(self):
        """启动调度器"""
        sched_config = self.config.get("scheduler", {})

        # 简历扫描任务
        resume_interval = sched_config.get("resume_scan_interval", 10)
        if self._resume_scan_func:
            self.scheduler.add_job(
                self._run_resume_scan,
                "interval",
                minutes=resume_interval,
                id="resume_scan",
                name="简历扫描",
                max_instances=1,
            )
            logger.info(f"简历扫描任务已注册，间隔 {resume_interval} 分钟")

        # 消息检查任务
        msg_interval = sched_config.get("message_check_interval", 5)
        if self._message_check_func:
            self.scheduler.add_job(
                self._run_message_check,
                "interval",
                minutes=msg_interval,
                id="message_check",
                name="消息检查",
                max_instances=1,
            )
            logger.info(f"消息检查任务已注册，间隔 {msg_interval} 分钟")

        self.scheduler.start()
        logger.info("调度器已启动")

    async def _run_resume_scan(self):
        """执行简历扫描"""
        try:
            logger.info("=== 开始定时简历扫描 ===")
            if self._resume_scan_func:
                await self._resume_scan_func()
            logger.info("=== 简历扫描完成 ===")
        except Exception as e:
            logger.error(f"简历扫描异常: {e}")

    async def _run_message_check(self):
        """执行消息检查"""
        try:
            logger.info("=== 开始定时消息检查 ===")
            if self._message_check_func:
                await self._message_check_func()
            logger.info("=== 消息检查完成 ===")
        except Exception as e:
            logger.error(f"消息检查异常: {e}")

    def stop(self):
        """停止调度器"""
        self.scheduler.shutdown(wait=False)
        logger.info("调度器已停止")
