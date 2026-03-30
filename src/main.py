"""Boss直聘自动化系统 - 主入口"""

import asyncio
import random
import signal
import sys
from pathlib import Path

import yaml
from loguru import logger

from .crawler import BossCrawler
from .cookie_manager import CookieManager
from .llm_client import LLMClient
from .messenger import Messenger
from .resume_filter import ResumeFilter
from .scheduler import TaskScheduler


def load_config() -> dict:
    """加载配置文件"""
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    if not config_path.exists():
        logger.error(f"配置文件不存在: {config_path}")
        logger.info("请复制 config.yaml.example 为 config.yaml 并填写配置")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_profile() -> dict:
    """加载候选人画像"""
    profile_path = Path(__file__).parent.parent / "config" / "profile.yaml"
    if not profile_path.exists():
        logger.error(f"画像文件不存在: {profile_path}")
        logger.info("请复制 profile.yaml.example 为 profile.yaml 并填写画像")
        sys.exit(1)

    with open(profile_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(config: dict):
    """配置日志"""
    log_config = config.get("logging", {})
    level = log_config.get("level", "INFO")
    log_file = log_config.get("file")

    logger.remove()  # 移除默认handler
    logger.add(sys.stderr, level=level, format="{time:HH:mm:ss} | {level:<7} | {message}")

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        logger.add(log_file, level=level, rotation="10 MB", encoding="utf-8",
                    format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {message}")


class Application:
    def __init__(self):
        self.config = load_config()
        self.profile = load_profile()

        # 相对路径转绝对路径
        project_root = Path(__file__).parent.parent
        for key in ("cookie_path",):
            if key in self.config.get("boss", {}):
                p = Path(self.config["boss"][key])
                if not p.is_absolute():
                    self.config["boss"][key] = str(project_root / p)
        for key in ("processed_ids_file",):
            if key in self.config.get("messaging", {}):
                p = Path(self.config["messaging"][key])
                if not p.is_absolute():
                    self.config["messaging"][key] = str(project_root / p)

        setup_logging(self.config)

        self.cookie_mgr = CookieManager(self.config["boss"]["cookie_path"])
        self.llm_client = LLMClient(self.config["llm"])
        self.crawler = BossCrawler(self.config, self.cookie_mgr)
        self.messenger = Messenger(self.config, self.crawler)
        self.scheduler = TaskScheduler(self.config)
        self.filter = None  # 登录后根据实际JD初始化

    async def run(self):
        """启动应用"""
        logger.info("Boss直聘自动化系统启动中...")

        # 启动浏览器并登录
        await self.crawler.start()

        # 自动抓取岗位JD，构建筛选画像
        await self._load_job_profile()

        # 注册定时任务
        self.scheduler.set_resume_scan_func(self._scan_resumes)
        self.scheduler.set_message_check_func(self._check_messages)
        self.scheduler.start()

        # 首次立即执行一次
        logger.info("执行首次扫描...")
        await self._scan_resumes()
        await self._check_messages()

        # 保持运行
        logger.info("系统已就绪，进入24h运行模式。按 Ctrl+C 退出。")
        try:
            stop_event = asyncio.Event()
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop_event.set)
            await stop_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            await self.shutdown()

    async def _load_job_profile(self):
        """从Boss直聘抓取岗位JD，自动构建筛选画像"""
        jobs = await self.crawler.fetch_my_jobs()

        if not jobs:
            logger.warning("未抓取到岗位JD，使用配置文件中的默认画像")
            self.filter = ResumeFilter(self.profile, self.llm_client)
            return

        # 用第一个岗位的JD覆盖profile中的JD
        job = jobs[0]
        self.profile["job_title"] = job.get("title", self.profile.get("job_title", ""))

        # 优先使用详情页抓到的JD，否则用摘要
        jd_text = job.get("description") or job.get("summary", "")
        if jd_text:
            self.profile["job_description"] = jd_text
            logger.info(f"已自动加载岗位JD: {job.get('title')}")
            logger.debug(f"JD内容: {jd_text[:200]}...")

        # 如果有多个岗位，记录下来
        if len(jobs) > 1:
            job_names = [j.get("title", "未知") for j in jobs]
            logger.info(f"共有 {len(jobs)} 个在线岗位: {job_names}")
            logger.info(f"当前使用第一个岗位 [{job_names[0]}] 的JD进行筛选")

        self.filter = ResumeFilter(self.profile, self.llm_client)

    async def _scan_resumes(self):
        """扫描并处理推荐简历"""
        try:
            resumes = await self.crawler.fetch_resumes(max_pages=3)
            questions = self.profile.get("questions", [])

            for resume in resumes:
                geek_id = resume.get("geek_id")
                if not geek_id:
                    logger.warning(f"简历缺少geek_id，跳过: {resume.get('name', 'unknown')}")
                    continue

                if self.messenger.is_processed(geek_id):
                    continue

                # 获取详细简历（可选，提升筛选准确度）
                detail = await self.crawler.get_resume_detail(geek_id)
                if detail:
                    resume.update(detail)

                # 筛选
                result = self.filter.filter_resume(resume)
                logger.info(
                    f"简历筛选结果 - {resume.get('name', geek_id)}: "
                    f"{result['status']} (评分: {result.get('score', 'N/A')}, "
                    f"理由: {result.get('reason', '')})"
                )

                # 发送消息
                await self.messenger.handle_filter_result(geek_id, result, questions)

                # 操作间隔
                await asyncio.sleep(random.uniform(3, 6))

        except Exception as e:
            logger.error(f"扫描简历异常: {e}")

    async def _check_messages(self):
        """检查并处理未读消息"""
        try:
            messages = await self.crawler.get_unread_messages()
            # 未读消息暂时只记录，不做自动回复（避免误操作）
            for msg in messages:
                geek_id = msg.get("geek_id", "unknown")
                logger.info(f"未读消息 - {geek_id}: {msg.get('message_text', '')[:100]}")
        except Exception as e:
            logger.error(f"检查消息异常: {e}")

    async def shutdown(self):
        """优雅关闭"""
        logger.info("正在关闭系统...")
        self.scheduler.stop()
        await self.crawler.stop()
        logger.info("系统已关闭")


def main():
    app = Application()
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
