"""命令行脚本入口：直接驱动 Boss 客户端做筛选和打招呼。"""

from __future__ import annotations

import hashlib
import io
import time
from pathlib import Path

import yaml
from loguru import logger

from .llm_client import LLMClient
from .messenger import Messenger
from .resume_filter import ResumeFilter
from .rpa_crawler import RPACrawler


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class _NullLLMClient:
    def chat(self, _prompt: str, system: str = "你是一个专业的HR助手。") -> str:
        return ""

    def understand_image(self, _image_path: str, _prompt: str) -> str:
        return ""


class ScreeningScript:
    def __init__(
        self,
        config: dict,
        profile: dict,
        *,
        crawler: RPACrawler | None = None,
        resume_filter: ResumeFilter | None = None,
        messenger: Messenger | None = None,
    ):
        self.config = config
        self.profile = profile
        self.crawler = crawler or RPACrawler(config)
        self.llm_client = self._build_llm_client(config.get("llm", {}))
        self.resume_filter = resume_filter or ResumeFilter(profile, self.llm_client)
        self.messenger = messenger or Messenger(config, self.crawler)
        self.stats = {
            "screens": 0,
            "visible": 0,
            "scanned": 0,
            "matched": 0,
            "matched_sent": 0,
            "rejected": 0,
        }

    def _build_llm_client(self, llm_config: dict):
        required = ("base_url", "api_key", "model")
        if all(llm_config.get(key) for key in required):
            return LLMClient(llm_config)
        return _NullLLMClient()

    def _hash_card_image(self, card_image) -> str:
        if card_image is None:
            return ""

        try:
            if isinstance(card_image, Path):
                return hashlib.sha1(card_image.read_bytes()).hexdigest()[:16]
            if isinstance(card_image, str):
                image_path = Path(card_image)
                if image_path.exists():
                    return hashlib.sha1(image_path.read_bytes()).hexdigest()[:16]
                return hashlib.sha1(card_image.encode("utf-8")).hexdigest()[:16]

            image_bytes = io.BytesIO()
            card_image.save(image_bytes, format="PNG")
            return hashlib.sha1(image_bytes.getvalue()).hexdigest()[:16]
        except Exception as exc:
            logger.debug(f"卡片哈希失败，回退到文本去重: {exc}")
            return ""

    def _build_dedup_key(self, resume: dict) -> str:
        stable_parts: list[str] = []

        button_box = resume.get("greet_button_box")
        if isinstance(button_box, (tuple, list)) and len(button_box) == 4:
            x, y, width, height = (int(v) for v in button_box)
            stable_parts.append(f"box:{x}:{y}:{width}:{height}")

        button_point = resume.get("greet_button_point")
        if isinstance(button_point, (tuple, list)) and len(button_point) == 2:
            x, y = (int(v) for v in button_point)
            stable_parts.append(f"pt:{x}:{y}")

        image_hash = self._hash_card_image(resume.get("card_image"))
        if image_hash:
            stable_parts.append(f"img:{image_hash}")

        if stable_parts:
            return "|".join(stable_parts)

        fallback_parts: list[str] = []

        source_key = str(resume.get("dedup_key", "")).strip()
        if source_key:
            fallback_parts.append(f"src:{source_key[:120]}")

        raw_text = str(resume.get("raw_text", "")).strip()
        if raw_text:
            fallback_parts.append(f"txt:{raw_text[:120]}")

        name = str(resume.get("name", "")).strip()
        if name:
            fallback_parts.append(f"name:{name[:40]}")

        return "|".join(fallback_parts)

    def run(self) -> dict:
        seen_screen_keys: set[str] = set()
        seen_detail_keys: set[str] = set()
        screen_limit = int(self.config.get("workflow", {}).get("screen_scan_limit", 8))
        scroll_pages = int(self.config.get("workflow", {}).get("scroll_pages_per_turn", 3))
        detail_pause = float(self.config.get("workflow", {}).get("detail_pause_seconds", 1.2))
        max_empty_screens = int(self.config.get("workflow", {}).get("max_empty_screens", 5))
        consecutive_empty_screens = 0

        logger.info(f"开始脚本执行，岗位：{self.profile.get('job_title', '未命名岗位')}")
        self.crawler.start()
        try:
            screen_idx = 0
            while screen_limit <= 0 or screen_idx < screen_limit:
                resumes = self.crawler.get_visible_resumes()
                self.stats["screens"] += 1
                self.stats["visible"] += len(resumes)
                logger.info(f"第 {screen_idx + 1} 屏识别到 {len(resumes)} 个候选人")
                screen_idx += 1

                if resumes:
                    consecutive_empty_screens = 0
                else:
                    consecutive_empty_screens += 1

                for resume in resumes:
                    raw_text = resume.get("raw_text", "")
                    if "继续沟通" in raw_text:
                        continue

                    if hasattr(self.crawler, "capture_resume_card"):
                        try:
                            card_image = self.crawler.capture_resume_card(resume)
                            if card_image is not None:
                                resume = {**resume, "card_image": card_image}
                        except Exception as exc:
                            logger.warning(
                                f"候选人 {resume.get('name', '未知')} 卡片截图失败，继续用文本快筛: {exc}"
                            )

                    dedup_key = self._build_dedup_key(resume)
                    if not dedup_key or dedup_key in seen_screen_keys:
                        continue
                    seen_screen_keys.add(dedup_key)

                    if self.messenger.is_processed(dedup_key):
                        continue

                    self.stats["scanned"] += 1
                    quick = self.resume_filter.quick_filter_resume(resume)
                    logger.info(f"候选人 {resume.get('name', '未知')} 快筛结果：{quick['reason']}")
                    if quick.get("passed"):
                        self.stats["matched"] += 1
                        greet_result = self.crawler.greet_current_candidate(resume)
                        logger.info(f"候选人 {resume.get('name', '未知')} 打招呼结果：{greet_result}")
                        if greet_result == "sent":
                            self.stats["matched_sent"] += 1
                            self.messenger.mark_processed(dedup_key)
                        elif greet_result == "limit_reached":
                            logger.warning("今日打招呼额度可能已用完，提前结束")
                            return self.stats
                    else:
                        self.stats["rejected"] += 1
                        self.messenger.mark_processed(dedup_key)
                    time.sleep(detail_pause)

                if max_empty_screens > 0 and consecutive_empty_screens >= max_empty_screens:
                    logger.warning(f"连续 {consecutive_empty_screens} 屏未识别到候选人，结束本轮扫描")
                    break

                self.crawler.scroll_recommend_list(scroll_pages)
                time.sleep(detail_pause)

            return self.stats
        finally:
            self.crawler.stop()


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def main() -> int:
    config = load_yaml(PROJECT_ROOT / "config" / "config.yaml")
    profile = load_yaml(PROJECT_ROOT / "config" / "profile.yaml")
    runner = ScreeningScript(config, profile)
    result = runner.run()
    print(
        "RESULT "
        f"screens={result['screens']} "
        f"visible={result['visible']} "
        f"scanned={result['scanned']} "
        f"matched={result['matched']} "
        f"matched_sent={result['matched_sent']} "
        f"rejected={result['rejected']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
