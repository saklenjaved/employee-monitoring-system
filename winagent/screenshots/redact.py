"""Redaction (TAD 9). POC placeholder: solid-fill rectangles only. Real blur is an open decision (TAD 13).

Redaction runs before anything is written to disk, so unredacted policy regions never reach
the temp file or S3.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Sequence

from winagent.screenshots.policy import Rect


@dataclass(frozen=True)
class RedactedImage:
    png: bytes
    width: int
    height: int
    redaction_applied: bool


class SolidFillRedactor:
    def __init__(self, fill_rgb: tuple[int, int, int] = (0, 0, 0)) -> None:
        self._fill = fill_rgb

    def apply(self, png: bytes, regions: Sequence[Rect]) -> RedactedImage:
        from PIL import Image, ImageDraw

        image = Image.open(io.BytesIO(png))
        image.load()
        width, height = image.size
        if not regions:
            return RedactedImage(png=png, width=width, height=height, redaction_applied=False)
        canvas = image.convert("RGB")
        draw = ImageDraw.Draw(canvas)
        for r in regions:
            draw.rectangle([r.x, r.y, r.x + r.w - 1, r.y + r.h - 1], fill=self._fill)
        out = io.BytesIO()
        canvas.save(out, format="PNG", optimize=True)
        return RedactedImage(png=out.getvalue(), width=width, height=height, redaction_applied=True)
