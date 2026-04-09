"""LLM API客户端 - 支持 Anthropic 兼容接口（MiniMax等）"""

import asyncio
import base64
import os
import threading
import time
from urllib.parse import urlsplit

import anthropic
from loguru import logger
from openai import OpenAI


class LLMClient:
    def __init__(self, config: dict):
        self.config = config
        self.api_key = config["api_key"]
        self.client = anthropic.Anthropic(
            base_url=config["base_url"],
            api_key=self.api_key,
        )
        self.model = config["model"]

    def chat(self, prompt: str, system: str = "你是一个专业的HR助手。") -> str:
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=500,
                system=system,
                messages=[
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
            return resp.content[0].text.strip()
        except Exception as e:
            logger.error(f"LLM API调用失败: {e}")
            return ""

    def understand_image(self, image_path: str, prompt: str) -> str:
        try:
            if self._vision_provider() == "zhipu_openai":
                return self._understand_image_via_zhipu_openai(image_path, prompt)
            return self._run_async(self._understand_image_async(image_path, prompt))
        except Exception as e:
            logger.error(f"视觉调用失败: {e}")
            return ""

    def _run_async(self, coroutine):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)

        result_box: dict[str, str] = {}
        error_box: dict[str, Exception] = {}

        def worker():
            try:
                result_box["result"] = asyncio.run(coroutine)
            except Exception as exc:
                error_box["error"] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join()
        if error_box:
            raise error_box["error"]
        return result_box.get("result", "")

    async def _understand_image_async(self, image_path: str, prompt: str) -> str:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise RuntimeError("缺少 mcp 依赖，请先安装 requirements.txt") from exc

        env = self._build_mcp_env()
        command = self.config.get("mcp_command", "uvx")
        args = self.config.get("mcp_args") or ["minimax-coding-plan-mcp"]
        timeout_sec = float(self.config.get("vision_timeout_seconds", 35))

        server = StdioServerParameters(
            command=command,
            args=args,
            env=env,
        )

        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await asyncio.wait_for(
                    session.call_tool(
                        "understand_image",
                        {
                            "image_source": image_path,
                            "prompt": prompt,
                        },
                    ),
                    timeout=timeout_sec,
                )
        return self._extract_mcp_text(result)

    def _understand_image_via_zhipu_openai(self, image_path: str, prompt: str) -> str:
        client = OpenAI(
            api_key=self.config.get("vision_api_key") or self.api_key,
            base_url=self.config.get("vision_base_url") or self._derive_zhipu_vision_base_url(),
        )
        model = self.config.get("vision_model") or "glm-4.6v"
        image_data_url = self._image_path_to_data_url(image_path)
        retry_count = int(self.config.get("vision_retry_count", 2))
        retry_delay = float(self.config.get("vision_retry_delay_seconds", 2))

        for attempt in range(1, retry_count + 2):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": image_data_url}},
                            ],
                        }
                    ],
                    temperature=0.1,
                )
                return (resp.choices[0].message.content or "").strip()
            except Exception as exc:
                if attempt > retry_count or not self._is_retryable_vision_error(exc):
                    raise
                time.sleep(retry_delay)

        return ""

    def _extract_mcp_text(self, result) -> str:
        parts: list[str] = []
        content = getattr(result, "content", None)
        if content is None and isinstance(result, dict):
            content = result.get("content")

        if isinstance(content, list):
            for item in content:
                text = getattr(item, "text", None)
                if text:
                    parts.append(str(text))
                    continue
                if isinstance(item, dict) and item.get("text"):
                    parts.append(str(item["text"]))

        if parts:
            return "\n".join(parts).strip()
        return str(result).strip()

    def _build_mcp_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("MINIMAX_API_KEY", self.api_key)

        host = self.config.get("mcp_api_host") or self._derive_mcp_api_host()
        if host:
            env.setdefault("MINIMAX_API_HOST", host)
        return env

    def _vision_provider(self) -> str:
        return str(self.config.get("vision_provider") or "minimax_mcp").strip()

    def _derive_mcp_api_host(self) -> str:
        base_url = (self.config.get("base_url") or "").strip()
        if not base_url:
            return ""
        parsed = urlsplit(base_url)
        if not parsed.scheme or not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}"

    def _derive_zhipu_vision_base_url(self) -> str:
        base_url = (self.config.get("base_url") or "").strip()
        if "open.bigmodel.cn" in base_url:
            return "https://open.bigmodel.cn/api/paas/v4/"
        return self.config.get("vision_base_url") or ""

    def _image_path_to_data_url(self, image_path: str) -> str:
        with open(image_path, "rb") as fh:
            raw = fh.read()
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    def _is_retryable_vision_error(self, exc: Exception) -> bool:
        text = str(exc)
        return "1305" in text or "访问量过大" in text or "rate limit" in text.lower()
