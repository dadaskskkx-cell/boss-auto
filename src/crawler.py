"""Boss直聘爬虫 - Playwright浏览器自动化"""

import asyncio
import random
from typing import Callable

from playwright.async_api import async_playwright, Page, BrowserContext
from loguru import logger

from .cookie_manager import CookieManager


class BossCrawler:
    def __init__(self, config: dict, cookie_manager: CookieManager):
        self.config = config
        self.cookie_mgr = cookie_manager
        self.base_url = config["boss"]["base_url"]
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    async def start(self):
        """启动浏览器，加载cookies"""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=False)

        cookies = self.cookie_mgr.load()
        self._context = await self._browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )

        if cookies:
            await self._context.add_cookies(cookies)

        self._page = await self._context.new_page()

        # 验证登录状态
        await self._page.goto(f"{self.base_url}/web/user/recommend")
        await asyncio.sleep(2)

        if not await self._is_logged_in():
            logger.info("Cookie已失效或未登录，请扫码登录...")
            await self._page.goto(f"{self.base_url}/?ka=header-login")
            await self._wait_for_login()
            # 登录成功后保存cookies
            cookies = await self._context.cookies()
            self.cookie_mgr.save(cookies)
            logger.info("登录成功，Cookie已保存")

        logger.info("浏览器启动完成，登录状态正常")

    async def _is_logged_in(self) -> bool:
        """检查是否已登录"""
        try:
            # 访问推荐页，如果被重定向到登录页则未登录
            current_url = self._page.url
            if "login" in current_url:
                return False
            # 检查页面上是否有用户信息
            user_info = await self._page.query_selector(".user-info, .nav-figure, .boss-info")
            return user_info is not None
        except Exception:
            return False

    async def _wait_for_login(self):
        """等待用户扫码登录，最多等待5分钟"""
        for _ in range(300):  # 5分钟超时
            await asyncio.sleep(1)
            if await self._is_logged_in():
                return
            # 也检查是否跳转到了主页
            current_url = self._page.url
            if "zhipin.com/web" in current_url and "login" not in current_url:
                await asyncio.sleep(2)
                if await self._is_logged_in():
                    return
        raise TimeoutError("登录超时，请重新运行程序")

    async def _random_delay(self, min_s: float = 2.0, max_s: float = 5.0):
        """随机延迟，模拟人类操作"""
        delay = random.uniform(min_s, max_s)
        await asyncio.sleep(delay)

    async def fetch_my_jobs(self) -> list[dict]:
        """从「我的职位」页面抓取已发布的岗位列表和JD"""
        jobs = []
        logger.info("正在抓取已发布的岗位JD...")

        try:
            await self._page.goto(f"{self.base_url}/web/boss/job")
            await self._random_delay(2, 4)

            # 等待职位列表加载
            await self._page.wait_for_selector(
                ".job-item, .job-list-item, .job-card, .my-job-item",
                timeout=10000,
            )
            await asyncio.sleep(1)

            # 查找所有职位卡片
            job_cards = await self._page.query_selector_all(
                ".job-item, .job-list-item, .job-card, .my-job-item, .job-info"
            )

            for card in job_cards:
                try:
                    job = {}
                    # 提取职位名称
                    name_el = await card.query_selector(
                        ".job-name, .job-title, .name, h3, a"
                    )
                    if name_el:
                        job["title"] = (await name_el.inner_text()).strip()

                    # 提取职位详情链接
                    link_el = await card.query_selector("a[href*='job']")
                    if link_el:
                        href = await link_el.get_attribute("href")
                        if href:
                            job["detail_url"] = href if href.startswith("http") else f"{self.base_url}{href}"

                    # 提取卡片上的摘要信息（薪资、地点、要求等）
                    text = await card.inner_text()
                    job["summary"] = text.strip()

                    if job.get("title"):
                        jobs.append(job)
                except Exception:
                    continue

            # 对每个岗位，进入详情页抓取完整JD
            for job in jobs:
                if job.get("detail_url"):
                    try:
                        await self._page.goto(job["detail_url"])
                        await self._random_delay(1.5, 3)

                        # 抓取JD详情
                        detail_el = await self._page.query_selector(
                            ".job-detail, .job-desc, .detail-content, .job-description, .job-sec"
                        )
                        if detail_el:
                            job["description"] = (await detail_el.inner_text()).strip()
                        else:
                            # fallback: 抓取页面主体
                            body_el = await self._page.query_selector("main, .page-content, .detail-wrap")
                            if body_el:
                                job["description"] = (await body_el.inner_text()).strip()
                    except Exception as e:
                        logger.warning(f"抓取岗位JD详情失败: {e}")

            logger.info(f"成功抓取 {len(jobs)} 个岗位的JD")

        except Exception as e:
            logger.error(f"抓取岗位列表失败: {e}")

        return jobs

    async def fetch_resumes(self, max_pages: int = 5) -> list[dict]:
        """抓取推荐简历列表"""
        resumes = []
        logger.info("开始抓取推荐简历...")

        await self._page.goto(f"{self.base_url}/web/user/recommend")
        await self._random_delay(2, 4)

        for page_num in range(max_pages):
            logger.info(f"正在抓取第 {page_num + 1} 页...")

            # 等待简历卡片加载
            await self._page.wait_for_selector(".card-inner, .recommend-card, .geek-card", timeout=10000)
            await asyncio.sleep(1)

            # 提取当前页的简历信息
            page_resumes = await self._extract_resume_list()
            resumes.extend(page_resumes)

            if page_num < max_pages - 1:
                # 尝试翻页
                has_next = await self._go_next_page()
                if not has_next:
                    logger.info("没有更多简历了")
                    break
                await self._random_delay(2, 5)

        logger.info(f"共抓取 {len(resumes)} 份简历")
        return resumes

    async def _extract_resume_list(self) -> list[dict]:
        """从当前页面提取简历列表"""
        resumes = []

        # 查找所有简历卡片
        cards = await self._page.query_selector_all(
            ".card-inner, .recommend-card, .geek-card, .candidate-card"
        )

        for card in cards:
            try:
                resume = await self._extract_card_info(card)
                if resume:
                    resumes.append(resume)
            except Exception as e:
                logger.warning(f"提取简历信息失败: {e}")
                continue

        return resumes

    async def _extract_card_info(self, card) -> dict | None:
        """从单个卡片提取简历信息"""
        text_content = await card.inner_text()
        if not text_content or len(text_content.strip()) < 5:
            return None

        resume = {"raw_text": text_content}

        # 尝试提取候选人ID（用于后续操作）
        link = await card.query_selector("a[href*='geek']")
        if link:
            href = await link.get_attribute("href")
            if href:
                resume["geek_id"] = href.split("/")[-1].split("?")[0]

        # 提取姓名
        name_el = await card.query_selector(
            ".name, .geek-name, .candidate-name, h3"
        )
        if name_el:
            resume["name"] = (await name_el.inner_text()).strip()

        return resume

    async def _go_next_page(self) -> bool:
        """翻到下一页"""
        try:
            next_btn = await self._page.query_selector(
                ".next, .page-next, a[class*='next'], li.next > a"
            )
            if next_btn:
                await next_btn.click()
                await asyncio.sleep(2)
                return True
        except Exception:
            pass
        return False

    async def get_resume_detail(self, geek_id: str) -> dict | None:
        """获取候选人简历详情"""
        try:
            url = f"{self.base_url}/web/geek/{geek_id}"
            await self._page.goto(url)
            await self._random_delay(2, 4)

            # 提取详情页内容
            detail_el = await self._page.query_selector(
                ".resume-detail, .geek-detail, .detail-content, .resume-box"
            )
            if not detail_el:
                # fallback: 获取整个页面主体文本
                detail_el = await self._page.query_selector("main, .page-content, #main")

            if detail_el:
                text = await detail_el.inner_text()
                return {"geek_id": geek_id, "detail_text": text}

        except Exception as e:
            logger.warning(f"获取简历详情失败 {geek_id}: {e}")
        return None

    async def get_unread_messages(self) -> list[dict]:
        """获取未读消息列表"""
        messages = []
        logger.info("检查未读消息...")

        await self._page.goto(f"{self.base_url}/web/chat")
        await self._random_delay(2, 4)

        try:
            # 查找未读消息标记
            unread_items = await self._page.query_selector_all(
                ".chat-item.unread, .msg-item.has-new, li[class*='unread']"
            )

            # 如果没有专门的未读标记，则获取所有聊天项
            if not unread_items:
                unread_items = await self._page.query_selector_all(
                    ".chat-item, .msg-item, li[class*='chat']"
                )

            for item in unread_items[:20]:  # 限制处理数量
                try:
                    text = await item.inner_text()
                    geek_id = None

                    # 尝试提取geek_id
                    link = await item.query_selector("a[href*='geek'], a[href*='chat']")
                    if link:
                        href = await link.get_attribute("href")
                        if href:
                            geek_id = href.split("/")[-1].split("?")[0]

                    messages.append({
                        "geek_id": geek_id,
                        "message_text": text.strip(),
                    })
                except Exception:
                    continue

        except Exception as e:
            logger.warning(f"获取未读消息失败: {e}")

        logger.info(f"发现 {len(messages)} 条未读消息")
        return messages

    async def send_message(self, geek_id: str, message: str) -> bool:
        """向候选人发送消息"""
        try:
            url = f"{self.base_url}/web/chat/{geek_id}"
            await self._page.goto(url)
            await self._random_delay(2, 3)

            # 找到输入框
            input_box = await self._page.query_selector(
                ".chat-input, textarea, [contenteditable='true'], .editarea"
            )
            if not input_box:
                logger.warning(f"未找到消息输入框: {geek_id}")
                return False

            # 输入消息
            await input_box.click()
            await asyncio.sleep(0.5)
            await input_box.fill(message)
            await asyncio.sleep(0.5)

            # 点击发送
            send_btn = await self._page.query_selector(
                ".send-btn, button[class*='send'], .btn-send"
            )
            if send_btn:
                await send_btn.click()
            else:
                # 尝试按Enter发送
                await input_box.press("Enter")

            await asyncio.sleep(1)
            logger.info(f"消息已发送给 {geek_id}")
            return True

        except Exception as e:
            logger.error(f"发送消息失败 {geek_id}: {e}")
            return False

    async def stop(self):
        """关闭浏览器"""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("浏览器已关闭")
