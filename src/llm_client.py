"""LLM API客户端 - 支持 Anthropic 兼容接口（MiniMax等）"""

import anthropic
from loguru import logger


class LLMClient:
    def __init__(self, config: dict):
        self.client = anthropic.Anthropic(
            base_url=config["base_url"],
            api_key=config["api_key"],
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
