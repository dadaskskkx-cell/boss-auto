"""后台爬虫管理器 - 支持多岗位并行筛选"""

import asyncio
import random
import threading
import time
from pathlib import Path

import yaml
from loguru import logger

from .crawler import BossCrawler
from .cookie_manager import CookieManager
from .llm_client import LLMClient
from .messenger import Messenger
from .resume_filter import ResumeFilter


class JobSlot:
    """单个岗位的筛选上下文"""

    def __init__(self, job_info: dict, profile: dict, llm_client: LLMClient):
        self.job = job_info  # {title, summary, description}
        # 每个岗位继承基础profile规则，但用自己的JD
        self.profile = dict(profile)
        self.profile["job_title"] = job_info.get("title", "")
        jd = job_info.get("description") or job_info.get("summary", "")
        if jd:
            self.profile["job_description"] = jd
        self.filter = ResumeFilter(self.profile, llm_client)
        self.enabled = True
        self.questions = profile.get("questions", [])
        self.stats = {"scanned": 0, "matched": 0, "rejected": 0, "sent": 0}


class CrawlerManager:
    """管理爬虫的生命周期，支持多岗位并行"""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event = threading.Event()
        self._paused = threading.Event()
        self._paused.set()

        # 共享状态
        self.status = "stopped"
        self.logs: list[str] = []
        self.max_logs = 200
        self.stats = {
            "total_scanned": 0,
            "matched": 0,
            "rejected": 0,
            "messages_sent": 0,
            "last_scan_time": "",
        }

        # 组件
        self.config: dict = {}
        self.profile: dict = {}
        self.crawler: BossCrawler | None = None
        self.messenger: Messenger | None = None
        self.llm_client: LLMClient | None = None
        self.cookie_mgr: CookieManager | None = None

        # 多岗位
        self.jobs: list[dict] = []
        self.job_slots: list[JobSlot] = []

    def load_config(self):
        config_path = Path(__file__).parent.parent / "config" / "config.yaml"
        profile_path = Path(__file__).parent.parent / "config" / "profile.yaml"
        project_root = Path(__file__).parent.parent

        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                self.config = yaml.safe_load(f) or {}
        if profile_path.exists():
            with open(profile_path, "r", encoding="utf-8") as f:
                self.profile = yaml.safe_load(f) or {}

        # 将相对路径转为基于项目根目录的绝对路径
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

        log_file = self.config.get("logging", {}).get("file")
        if log_file:
            p = Path(log_file)
            if not p.is_absolute():
                self.config["logging"]["file"] = str(project_root / p)

        self.cookie_mgr = CookieManager(self.config.get("boss", {}).get("cookie_path", str(project_root / "data" / "cookies" / "boss_cookies.json")))
        self.llm_client = LLMClient(self.config.get("llm", {}))
        self.crawler = BossCrawler(self.config, self.cookie_mgr)
        self.messenger = Messenger(self.config, self.crawler)

    def save_profile(self):
        profile_path = Path(__file__).parent.parent / "config" / "profile.yaml"
        with open(profile_path, "w", encoding="utf-8") as f:
            yaml.dump(self.profile, f, allow_unicode=True, default_flow_style=False)

    def add_log(self, msg: str):
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {msg}"
        self.logs.append(entry)
        if len(self.logs) > self.max_logs:
            self.logs = self.logs[-self.max_logs:]

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._paused.clear()
        self.status = "running"
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def pause(self):
        self._paused.set()
        self.status = "paused"
        self.add_log("爬虫已暂停")

    def resume(self):
        self._paused.clear()
        self.status = "running"
        self.add_log("爬虫已恢复")

    def stop(self):
        self._stop_event.set()
        self._paused.clear()
        self.status = "stopped"
        self.add_log("正在停止爬虫...")

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main())
        except Exception as e:
            self.status = "error"
            self.add_log(f"爬虫异常退出: {e}")
        finally:
            self._loop.close()

    async def _async_main(self):
        try:
            self.add_log("正在启动浏览器...")
            await self.crawler.start()
            self.add_log("登录成功")

            # 抓取所有岗位JD
            await self._fetch_jobs()

            # 为每个岗位创建独立的筛选器
            self._init_job_slots()

            # 主循环
            scan_interval = self.config.get("scheduler", {}).get("resume_scan_interval", 10) * 60
            msg_interval = self.config.get("scheduler", {}).get("message_check_interval", 5) * 60
            last_scan = time.time()
            last_msg_check = time.time()

            active_jobs = [s.job.get("title", "?") for s in self.job_slots if s.enabled]
            self.add_log(f"已加载 {len(self.job_slots)} 个岗位: {active_jobs}")
            self.add_log(f"定时任务就绪：简历扫描 {scan_interval//60}分钟，消息检查 {msg_interval//60}分钟")

            while not self._stop_event.is_set():
                while self._paused.is_set():
                    if self._stop_event.is_set():
                        return
                    await asyncio.sleep(1)

                now = time.time()

                if now - last_scan >= scan_interval:
                    last_scan = now
                    await self._scan_resumes()

                if now - last_msg_check >= msg_interval:
                    last_msg_check = now
                    await self._check_messages()

                await asyncio.sleep(10)

        except Exception as e:
            self.status = "error"
            self.add_log(f"运行错误: {e}")
        finally:
            await self.crawler.stop()
            self.add_log("爬虫已关闭")

    async def _fetch_jobs(self):
        self.add_log("正在从Boss直聘抓取所有在招岗位...")
        self.jobs = await self.crawler.fetch_my_jobs()
        if self.jobs:
            titles = [j.get("title", "?") for j in self.jobs]
            self.add_log(f"抓取到 {len(self.jobs)} 个岗位: {titles}")
        else:
            self.add_log("未抓取到岗位，使用配置文件默认JD")

    def _init_job_slots(self):
        """为每个岗位创建独立的筛选上下文"""
        self.job_slots = []
        if self.jobs:
            for job in self.jobs:
                slot = JobSlot(job, self.profile, self.llm_client)
                self.job_slots.append(slot)
        else:
            # fallback: 用配置文件的JD
            slot = JobSlot(
                {"title": self.profile.get("job_title", "默认岗位"), "description": self.profile.get("job_description", "")},
                self.profile,
                self.llm_client,
            )
            self.job_slots.append(slot)

    def toggle_job(self, index: int, enabled: bool):
        """启用/禁用某个岗位的筛选"""
        if 0 <= index < len(self.job_slots):
            self.job_slots[index].enabled = enabled
            state = "启用" if enabled else "禁用"
            self.add_log(f"已{state}岗位: {self.job_slots[index].job.get('title', '?')}")

    def update_job_profile(self, index: int, profile_updates: dict):
        """更新某个岗位的筛选规则"""
        if 0 <= index < len(self.job_slots):
            slot = self.job_slots[index]
            slot.profile.update(profile_updates)
            slot.filter = ResumeFilter(slot.profile, self.llm_client)
            if "questions" in profile_updates:
                slot.questions = profile_updates["questions"]

    async def _scan_resumes(self):
        """扫描简历，对每个启用的岗位分别筛选"""
        active_slots = [s for s in self.job_slots if s.enabled]
        if not active_slots:
            self.add_log("没有启用的岗位，跳过扫描")
            return

        self.add_log(f"=== 开始扫描简历（{len(active_slots)}个岗位） ===")
        try:
            resumes = await self.crawler.fetch_resumes(max_pages=3)

            for resume in resumes:
                if self._stop_event.is_set() or self._paused.is_set():
                    break

                geek_id = resume.get("geek_id")
                if not geek_id:
                    continue
                if self.messenger.is_processed(geek_id):
                    continue

                # 获取详细简历
                detail = await self.crawler.get_resume_detail(geek_id)
                if detail:
                    resume.update(detail)

                name = resume.get("name", geek_id)

                # 对每个启用的岗位分别筛选
                best_match = None
                best_score = -1
                best_slot = None

                for slot in active_slots:
                    result = slot.filter.filter_resume(resume)
                    slot.stats["scanned"] += 1

                    if result["status"] == "matched" and result.get("score", 0) > best_score:
                        best_match = result
                        best_score = result.get("score", 0)
                        best_slot = slot

                # 汇总结果
                self.stats["total_scanned"] += 1

                if best_match and best_slot:
                    # 匹配到某个岗位
                    best_slot.stats["matched"] += 1
                    self.stats["matched"] += 1
                    self.add_log(
                        f"✅ {name} → 匹配 [{best_slot.job.get('title')}] "
                        f"(评分:{best_score} {best_match.get('reason', '')})"
                    )
                    await self.messenger.handle_filter_result(geek_id, best_match, best_slot.questions)
                    self.stats["messages_sent"] += 1
                    best_slot.stats["sent"] += 1
                else:
                    # 所有岗位都不匹配
                    for slot in active_slots:
                        slot.stats["rejected"] += 1
                    self.stats["rejected"] += 1
                    self.add_log(f"❌ {name} → 所有岗位均不匹配")
                    # 发送婉拒
                    await self.messenger.handle_filter_result(
                        geek_id, {"status": "rejected", "reason": "不匹配", "score": 0}, []
                    )
                    self.stats["messages_sent"] += 1

                await asyncio.sleep(random.uniform(3, 6))

            self.stats["last_scan_time"] = time.strftime("%H:%M:%S")
            self.add_log(f"=== 扫描完成，本次处理 {len(resumes)} 份简历 ===")
        except Exception as e:
            self.add_log(f"扫描异常: {e}")

    async def _check_messages(self):
        self.add_log("检查未读消息...")
        try:
            messages = await self.crawler.get_unread_messages()
            for msg in messages:
                geek_id = msg.get("geek_id", "?")
                text = msg.get("message_text", "")[:80]
                self.add_log(f"未读消息 [{geek_id}]: {text}")
        except Exception as e:
            self.add_log(f"消息检查异常: {e}")


# 全局单例
manager = CrawlerManager()
