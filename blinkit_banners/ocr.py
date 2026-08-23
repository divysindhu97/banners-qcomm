from __future__ import annotations

import functools
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TextBlock:
    text: str
    left: int
    top: int
    right: int
    bottom: int
    confidence: float
    skew: float = 0.0
    """Degrees the detected quad is rotated from horizontal."""

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        """Cap height, used as the proxy for font size."""
        return self.bottom - self.top

    @property
    def centre_y(self) -> float:
        return self.top + self.height / 2

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "box": [self.left, self.top, self.right, self.bottom],
            "confidence": round(self.confidence, 3),
        }


@functools.lru_cache(maxsize=1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def read(path: str | Path, min_confidence: float = 0.5) -> list[TextBlock]:
    """Run OCR and return text blocks sorted top to bottom."""
    result, _ = _engine()(str(path))
    blocks: list[TextBlock] = []
    for box, text, score in result or []:
        confidence = float(score)
        if confidence < min_confidence or not text.strip():
            continue
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        (x0, y0), (x1, y1) = box[0], box[1]
        blocks.append(
            TextBlock(
                text=text.strip(),
                left=int(min(xs)),
                top=int(min(ys)),
                right=int(max(xs)),
                bottom=int(max(ys)),
                confidence=confidence,
                skew=math.degrees(math.atan2(y1 - y0, x1 - x0)),
            )
        )
    return sorted(blocks, key=lambda b: (b.top, b.left))
