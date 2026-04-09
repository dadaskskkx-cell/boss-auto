"""OCR子进程入口，隔离PaddleOCR崩溃风险。"""

import json
import sys

from PIL import Image

_OCR_ENGINE = None


def _get_ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from paddleocr import PaddleOCR

        _OCR_ENGINE = PaddleOCR(
            lang="ch",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
    return _OCR_ENGINE


def _extract_items(results: list) -> list[dict]:
    items = []
    if not results:
        return items

    first = results[0]
    if isinstance(first, dict):
        polys = first.get("dt_polys", [])
        texts = first.get("rec_texts", [])
        scores = first.get("rec_scores", [])
        for box, text, confidence in zip(polys, texts, scores):
            if confidence <= 0.5:
                continue
            x = int(box[0][0])
            y = int(box[0][1])
            w = int(box[2][0] - box[0][0])
            h = int(box[2][1] - box[0][1])
            items.append({"text": text, "x": x, "y": y, "w": w, "h": h})
        return items

    for line in first or []:
        box = line[0]
        text = line[1][0]
        confidence = line[1][1]
        if confidence > 0.5:
            x = int(box[0][0])
            y = int(box[0][1])
            w = int(box[2][0] - box[0][0])
            h = int(box[2][1] - box[0][1])
            items.append({"text": text, "x": x, "y": y, "w": w, "h": h})
    return items


def ocr_path(image_path: str) -> list[dict]:
    import numpy as np

    image = Image.open(image_path).convert("RGB")
    img_array = np.array(image)
    ocr = _get_ocr_engine()

    if hasattr(ocr, "predict"):
        results = ocr.predict(img_array)
    else:
        results = ocr.ocr(img_array)
    return _extract_items(results)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m src.ocr_worker <image_path>", file=sys.stderr)
        return 2

    items = ocr_path(argv[1])
    json.dump(items, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
