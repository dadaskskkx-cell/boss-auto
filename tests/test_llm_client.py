import unittest
from unittest.mock import MagicMock, patch

from src.llm_client import LLMClient


class LLMClientTests(unittest.TestCase):
    def test_build_mcp_env_derives_host_from_anthropic_base_url(self):
        client = LLMClient(
            {
                "base_url": "https://api.minimaxi.com/anthropic",
                "api_key": "test-key",
                "model": "MiniMax-M2.7",
            }
        )

        env = client._build_mcp_env()

        self.assertEqual(env["MINIMAX_API_KEY"], "test-key")
        self.assertEqual(env["MINIMAX_API_HOST"], "https://api.minimaxi.com")

    def test_understand_image_uses_zhipu_openai_compatible_client(self):
        client = LLMClient(
            {
                "base_url": "https://open.bigmodel.cn/api/anthropic",
                "api_key": "glm-key",
                "model": "glm-5",
                "vision_provider": "zhipu_openai",
                "vision_base_url": "https://open.bigmodel.cn/api/paas/v4/",
                "vision_model": "glm-4.6v",
            }
        )
        fake_response = MagicMock()
        fake_response.choices = [MagicMock(message=MagicMock(content='{"passed": true, "reason": "匹配"}'))]
        fake_sdk = MagicMock()
        fake_sdk.chat.completions.create.return_value = fake_response

        with patch("src.llm_client.OpenAI", return_value=fake_sdk) as openai_cls, patch.object(
            client,
            "_image_path_to_data_url",
            return_value="data:image/png;base64,ZmFrZQ==",
        ):
            result = client.understand_image("/tmp/candidate.png", "请判断是否匹配")

        self.assertEqual(result, '{"passed": true, "reason": "匹配"}')
        openai_cls.assert_called_once_with(
            api_key="glm-key",
            base_url="https://open.bigmodel.cn/api/paas/v4/",
        )
        _, kwargs = fake_sdk.chat.completions.create.call_args
        self.assertEqual(kwargs["model"], "glm-4.6v")
        self.assertEqual(kwargs["messages"][0]["content"][0]["type"], "text")
        self.assertEqual(kwargs["messages"][0]["content"][1]["type"], "image_url")

    def test_derive_zhipu_vision_base_url_from_anthropic_url(self):
        client = LLMClient(
            {
                "base_url": "https://open.bigmodel.cn/api/anthropic",
                "api_key": "glm-key",
                "model": "glm-5",
                "vision_provider": "zhipu_openai",
            }
        )

        self.assertEqual(
            client._derive_zhipu_vision_base_url(),
            "https://open.bigmodel.cn/api/paas/v4/",
        )

    def test_understand_image_retries_when_zhipu_flash_is_busy(self):
        client = LLMClient(
            {
                "base_url": "https://open.bigmodel.cn/api/anthropic",
                "api_key": "glm-key",
                "model": "glm-5",
                "vision_provider": "zhipu_openai",
                "vision_base_url": "https://open.bigmodel.cn/api/paas/v4/",
                "vision_model": "glm-4.6v-flash",
                "vision_retry_count": 2,
                "vision_retry_delay_seconds": 0,
            }
        )
        success_response = MagicMock()
        success_response.choices = [MagicMock(message=MagicMock(content='{"passed": false, "reason": "不匹配"}'))]
        fake_sdk = MagicMock()
        fake_sdk.chat.completions.create.side_effect = [
            Exception("Error code: 429 - {'error': {'code': '1305', 'message': '该模型当前访问量过大，请您稍后再试'}}"),
            success_response,
        ]

        with patch("src.llm_client.OpenAI", return_value=fake_sdk), patch.object(
            client,
            "_image_path_to_data_url",
            return_value="data:image/png;base64,ZmFrZQ==",
        ):
            result = client.understand_image("/tmp/candidate.png", "请判断是否匹配")

        self.assertEqual(result, '{"passed": false, "reason": "不匹配"}')
        self.assertEqual(fake_sdk.chat.completions.create.call_count, 2)


if __name__ == "__main__":
    unittest.main()
