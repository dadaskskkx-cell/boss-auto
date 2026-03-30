"""Boss直聘爬虫 - Playwright浏览器自动化"""

import asyncio
import random

from playwright.async_api import async_playwright
from loguru import logger


class BossCrawler:
    def __init__(self, config: dict):
        self.config = config
        self.base_url = "https://www.zhipin.com"
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    async def start(self):
        """启动浏览器，扫码登录"""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=False)
        self._context = await self._browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        self._page = await self._context.new_page()

        # 打开登录页
        await self._page.goto(f"{self.base_url}")
        await asyncio.sleep(2)

        # 点击「我要招聘」
        recruit_btn = await self._page.query_selector("text=我要招聘")
        if recruit_btn:
            await recruit_btn.click()
            await asyncio.sleep(2)
            logger.info("已切换到招聘端登录")

        logger.info("请用Boss直聘APP扫码登录...")
        await self._wait_for_login()
        logger.info("登录成功！")

    async def _wait_for_login(self):
        """等待用户扫码登录"""
        for _ in range(300):
            await asyncio.sleep(1)
            url = self._page.url
            # 登录成功后会跳转到 /web/chat/index
            if "/web/chat" in url:
                return
            # 也检查其他web页面
            if "/web/" in url and "login" not in url and "ka=" not in url:
                return
        raise TimeoutError("登录超时（5分钟），请重新运行")

    async def _is_on_chat_page(self) -> bool:
        return "/web/chat" in self._page.url

    async def _navigate_to(self, menu_selector: str, page_name: str):
        """通过侧边栏菜单导航（Boss直聘不支持直接URL跳转）"""
        try:
            # 先回到聊天页（确定在招聘端）
            if not self._is_on_chat_page():
                await self._page.goto(f"{self.base_url}/web/chat/index")
                await asyncio.sleep(5)

            menu = await self._page.query_selector(menu_selector)
            if menu:
                await menu.click()
                await asyncio.sleep(6)
                logger.info(f"已导航到: {page_name}")
                return True
            else:
                logger.warning(f"未找到菜单项: {menu_selector}")
                return False
        except Exception as e:
            logger.error(f"导航失败 {page_name}: {e}")
            return False

    async def _random_delay(self, min_s: float = 2.0, max_s: float = 5.0):
        await asyncio.sleep(random.uniform(min_s, max_s))

    # ==================== 岗位管理 ====================

    async def fetch_my_jobs(self) -> list[dict]:
        """从「职位管理」页面抓取已发布的岗位列表"""
        jobs = []
        logger.info("正在抓取岗位列表...")

        try:
            # 点击「职位管理」菜单
            if not await self._navigate_to("text=职位管理", "职位管理"):
                return jobs

            await asyncio.sleep(5)
            body_text = await self._page.inner_text("body")
            logger.info(f"职位管理页内容前200字: {body_text[:200]}")

            # 尝试多种选择器找职位卡片
            selectors_to_try = [
                ".job-card-wrapper",
                ".job-item",
                "[class*='job-card']",
                "[class*='job-item']",
                "[class*='position']",
            ]

            job_cards = []
            for sel in selectors_to_try:
                job_cards = await self._page.query_selector_all(sel)
                if job_cards:
                    logger.info(f"用选择器 {sel} 找到 {len(job_cards)} 个职位")
                    break

            if not job_cards:
                # fallback: 用页面文本解析
                logger.info("未找到职位卡片元素，尝试从页面文本提取")
                jobs = self._parse_jobs_from_text(body_text)
                return jobs

            for card in job_cards:
                try:
                    text = await card.inner_text()
                    job = {"summary": text.strip()}

                    name_el = await card.query_selector(
                        ".job-name, .job-title, .name, [class*='name'], a, h3"
                    )
                    if name_el:
                        job["title"] = (await name_el.inner_text()).strip()

                    if not job.get("title"):
                        # 从文本中提取第一行作为标题
                        first_line = text.strip().split("\n")[0].strip()
                        if first_line:
                            job["title"] = first_line

                    if job.get("title"):
                        jobs.append(job)
                except Exception:
                    continue

            logger.info(f"抓取到 {len(jobs)} 个岗位")

        except Exception as e:
            logger.error(f"抓取岗位列表失败: {e}")

        return jobs

    def _parse_jobs_from_text(self, text: str) -> list[dict]:
        """从页面文本中解析岗位列表"""
        jobs = []
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for line in lines[:20]:
            # 跳过导航、菜单等无关文本
            if any(skip in line for skip in ["职位管理", "推荐牛人", "搜索", "沟通",
                                              "互动", "牛人管理", "道具", "工具箱",
                                              "更多", "招聘规范", "客服", "面试",
                                              "招聘数据", "账号", "升级", "加载"]):
                continue
            if len(line) > 3 and len(line) < 50:
                jobs.append({"title": line, "summary": line})
        return jobs

    # ==================== 推荐牛人 ====================

    async def fetch_resumes(self, max_pages: int = 3) -> list[dict]:
        """抓取推荐牛人列表"""
        resumes = []
        logger.info("开始抓取推荐牛人...")

        # 点击「推荐牛人」菜单
        if not await self._navigate_to(".menu-recommend", "推荐牛人"):
            # fallback: 尝试文本选择器
            if not await self._navigate_to("text=推荐牛人", "推荐牛人"):
                logger.error("无法导航到推荐牛人页")
                return resumes

        await asyncio.sleep(8)

        for page_num in range(max_pages):
            logger.info(f"正在抓取第 {page_num + 1} 页...")

            # 等待内容加载
            await asyncio.sleep(3)

            # 获取页面文本看看有什么
            body_text = await self._page.inner_text("body")
            if page_num == 0:
                logger.info(f"推荐页内容前300字: {body_text[:300]}")

            # 提取简历
            page_resumes = await self._extract_resume_list()
            resumes.extend(page_resumes)
            logger.info(f"第 {page_num + 1} 页抓到 {len(page_resumes)} 份简历")

            if page_num < max_pages - 1:
                has_next = await self._go_next_page()
                if not has_next:
                    logger.info("没有更多了")
                    break
                await self._random_delay(2, 5)

        logger.info(f"共抓取 {len(resumes)} 份简历")
        return resumes

    async def _extract_resume_list(self) -> list[dict]:
        """从推荐页提取简历列表"""
        resumes = []

        # 尝试多种选择器
        selectors = [
            ".recommend-card",
            ".card-inner",
            ".geek-card",
            "[class*='recommend'] [class*='card']",
            "[class*='geek']",
            "[class*='candidate']",
        ]

        cards = []
        for sel in selectors:
            cards = await self._page.query_selector_all(sel)
            if cards and len(cards) > 1:
                break

        if not cards or len(cards) <= 1:
            # 最后尝试：获取页面中所有看起来像简历卡片的元素
            logger.info("标准选择器未匹配，尝试通用方式提取...")
            all_divs = await self._page.query_selector_all("div[class]")
            for div in all_divs:
                cls = await div.get_attribute("class") or ""
                text = await div.inner_text()
                # 判断是否像简历卡片（包含姓名+学历+经验等信息）
                if 30 < len(text) < 500 and any(kw in text for kw in ["年", "岁", "本科", "硕士", "大专"]):
                    cards.append(div)

            if not cards:
                # 最终fallback: 从整个页面文本解析
                body_text = await self._page.inner_text("body")
                return self._parse_resumes_from_text(body_text)

        for card in cards[:30]:
            try:
                text = await card.inner_text()
                if not text or len(text.strip()) < 10:
                    continue

                resume = {"raw_text": text.strip()}

                # 提取姓名
                name_el = await card.query_selector(
                    ".name, .geek-name, .candidate-name, [class*='name']"
                )
                if name_el:
                    resume["name"] = (await name_el.inner_text()).strip()
                else:
                    # 取第一行作为姓名
                    first_line = text.strip().split("\n")[0].strip()
                    if len(first_line) < 10:
                        resume["name"] = first_line

                resumes.append(resume)
            except Exception:
                continue

        return resumes

    def _parse_resumes_from_text(self, text: str) -> list[dict]:
        """从页面文本解析简历（最后fallback）"""
        resumes = []
        # 按换行分割，找出看起来像简历的行
        blocks = text.split("\n\n")
        for block in blocks:
            block = block.strip()
            if 20 < len(block) < 500 and any(kw in block for kw in ["年", "岁", "本科", "硕士", "大专"]):
                name = block.split("\n")[0].strip()[:10]
                resumes.append({"raw_text": block, "name": name})
        return resumes[:20]

    # ==================== 翻页 ====================

    async def _go_next_page(self) -> bool:
        try:
            next_btn = await self._page.query_selector(
                ".next, .page-next, [class*='next'], a[class*='next']"
            )
            if next_btn:
                await next_btn.click()
                await asyncio.sleep(3)
                return True
        except Exception:
            pass
        return False

    # ==================== 消息 ====================

    async def get_unread_messages(self) -> list[dict]:
        """获取聊天列表中的未读消息"""
        messages = []
        logger.info("检查未读消息...")

        # 回到聊天页
        if not self._is_on_chat_page():
            await self._page.goto(f"{self.base_url}/web/chat/index")
            await self._random_delay(3, 5)

        try:
            # 查找聊天列表项
            chat_items = await self._page.query_selector_all(".geek-item-wrap, .geek-item")
            if not chat_items:
                chat_items = await self._page.query_selector_all("[class*='geek']")

            for item in chat_items[:20]:
                try:
                    text = await item.inner_text()
                    if not text.strip():
                        continue

                    name = ""
                    name_el = await item.query_selector(".geek-name, [class*='name']")
                    if name_el:
                        name = (await name_el.inner_text()).strip()

                    messages.append({
                        "geek_id": name,
                        "message_text": text.strip(),
                        "name": name,
                    })
                except Exception:
                    continue

        except Exception as e:
            logger.warning(f"获取消息失败: {e}")

        logger.info(f"发现 {len(messages)} 条聊天")
        return messages

    async def send_message(self, geek_name: str, message: str) -> bool:
        """向候选人发送消息（通过聊天列表点击进入对话）"""
        try:
            # 确保在聊天页
            if not self._is_on_chat_page():
                await self._page.goto(f"{self.base_url}/web/chat/index")
                await self._random_delay(3, 5)

            # 在聊天列表中找到该候选人并点击
            target = await self._page.query_selector(f"text={geek_name}")
            if not target:
                logger.warning(f"聊天列表中未找到: {geek_name}")
                return False

            await target.click()
            await asyncio.sleep(2)

            # 找到输入框
            input_box = await self._page.query_selector(
                ".chat-input, [contenteditable='true'], textarea, .editarea, [class*='input']"
            )
            if not input_box:
                logger.warning(f"未找到输入框: {geek_name}")
                return False

            # 输入并发送
            await input_box.click()
            await asyncio.sleep(0.5)
            await input_box.fill(message)
            await asyncio.sleep(0.5)

            # 发送
            send_btn = await self._page.query_selector(
                "[class*='send'], button[class*='send']"
            )
            if send_btn:
                await send_btn.click()
            else:
                await input_box.press("Enter")

            await asyncio.sleep(1)
            logger.info(f"消息已发送给 {geek_name}")
            return True

        except Exception as e:
            logger.error(f"发送消息失败 {geek_name}: {e}")
            return False

    # ==================== 关闭 ====================

    async def stop(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("浏览器已关闭")
