"""Tests for hakaton Tesseract preprocessing."""

from __future__ import annotations

import numpy as np
from PIL import Image

from app.services.document_processing.kd.tesseract_preprocess import hakaton_preprocess_bgr


def test_hakaton_preprocess_returns_binary_single_channel() -> None:
    bgr = np.full((200, 150, 3), 180, dtype=np.uint8)
    bgr[50:150, 30:120] = 40
    result = hakaton_preprocess_bgr(bgr, apply_undistort=False)
    assert result.ndim == 2
    assert result.dtype == np.uint8
    assert set(np.unique(result)).issubset({0, 255})


def test_hakaton_preprocess_pil_roundtrip() -> None:
    from app.services.document_processing.kd.tesseract_preprocess import hakaton_preprocess

    image = Image.new("RGB", (100, 80), color=(200, 200, 200))
    out = hakaton_preprocess(image, apply_undistort=False)
    assert out.mode == "L"
    assert out.size == (100, 80)
