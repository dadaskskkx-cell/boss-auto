import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

import src.ocr_worker as ocr_worker


class OCRWorkerTests(unittest.TestCase):
    def tearDown(self):
        ocr_worker._OCR_ENGINE = None

    def test_create_ocr_engine_uses_safe_constructor_args(self):
        captured = {}

        class FakePaddleOCR:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        fake_module = SimpleNamespace(PaddleOCR=FakePaddleOCR)

        with patch.dict("sys.modules", {"paddleocr": fake_module}):
            engine = ocr_worker._get_ocr_engine()

        self.assertIsInstance(engine, FakePaddleOCR)
        self.assertEqual(captured["lang"], "ch")
        self.assertFalse(captured["use_doc_orientation_classify"])
        self.assertFalse(captured["use_doc_unwarping"])
        self.assertFalse(captured["use_textline_orientation"])

    def test_ocr_path_parses_dict_style_result(self):
        screen = Image.new("RGB", (20, 20), "white")

        class FakeOCR:
            def predict(self, _img_array):
                return [
                    {
                        "dt_polys": [[[1, 2], [11, 2], [11, 12], [1, 12]]],
                        "rec_texts": ["测试文本"],
                        "rec_scores": [0.99],
                    }
                ]

        with patch("src.ocr_worker._get_ocr_engine", return_value=FakeOCR()), patch(
            "src.ocr_worker.Image.open", return_value=screen
        ):
            items = ocr_worker.ocr_path("/tmp/fake.png")

        self.assertEqual(
            items,
            [{"text": "测试文本", "x": 1, "y": 2, "w": 10, "h": 10}],
        )


if __name__ == "__main__":
    unittest.main()
