"""Предобработка изображений для Tesseract OCR (подход model_for_hakaton).

Источник: https://github.com/MaxJalo/model_for_hakaton (first.py)
Шаги: undistort → grayscale → kernel blur → adaptive Gaussian threshold → resize.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:
    from PIL import Image

_KERNEL = np.array(
    [[0.1, 0.15, 0.1], [0.15, 1.0, 0.15], [0.1, 0.15, 0.1]],
    dtype=np.float32,
)
_KERNEL /= np.sum(_KERNEL)


def _pil_to_bgr(image: Image.Image) -> np.ndarray:
    from PIL import Image

    rgb = np.asarray(image.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _remove_distortion(image: np.ndarray) -> np.ndarray:
    focal_length = float(image.shape[1])
    center = (image.shape[1] / 2.0, image.shape[0] / 2.0)
    camera_matrix = np.array(
        [[focal_length, 0, center[0]], [0, focal_length, center[1]], [0, 0, 1]],
        dtype=np.float32,
    )
    distortion_coeffs = np.array([0.1, 0, 0, 0], dtype=np.float32)
    return cv2.undistort(image, camera_matrix, distortion_coeffs)


def hakaton_preprocess_bgr(
    image_bgr: np.ndarray,
    *,
    target_size: tuple[int, int] | None = None,
    apply_undistort: bool = True,
) -> np.ndarray:
    """Binarize scan using hakaton pipeline; returns single-channel uint8 image."""
    working = _remove_distortion(image_bgr) if apply_undistort else image_bgr
    gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)
    filtered = cv2.filter2D(gray, ddepth=-1, kernel=_KERNEL)
    binary = cv2.adaptiveThreshold(
        filtered,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        11,
        2,
    )
    if target_size is not None:
        binary = cv2.resize(binary, target_size)
    return binary


def hakaton_preprocess(
    image: Image.Image,
    *,
    target_size: tuple[int, int] | None = None,
    apply_undistort: bool = True,
) -> Image.Image:
    """Apply hakaton preprocessing to a PIL image; returns L-mode binary image."""
    from PIL import Image

    binary = hakaton_preprocess_bgr(
        _pil_to_bgr(image),
        target_size=target_size,
        apply_undistort=apply_undistort,
    )
    return Image.fromarray(binary, mode="L")


HAKATON_TESSERACT_CONFIG = "--psm 6 --oem 1"
HAKATON_TESSERACT_LANG = "rus"
