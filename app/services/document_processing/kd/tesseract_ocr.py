"""Tesseract OCR для КД с опциональной предобработкой hakaton."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from app.services.document_processing.kd.tesseract_preprocess import (
    HAKATON_TESSERACT_CONFIG,
    HAKATON_TESSERACT_LANG,
    hakaton_preprocess,
)

if TYPE_CHECKING:
    from PIL import Image

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_TESSERACT = _PROJECT_ROOT / "tools" / "tesseract" / "tesseract.exe"


def find_tesseract_cmd() -> str | None:
    if _DEFAULT_TESSERACT.is_file():
        return str(_DEFAULT_TESSERACT)
    which = shutil.which("tesseract")
    if which:
        return which
    for candidate in (
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _configure_tesseract(tesseract_cmd: str) -> None:
    import os

    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    tess_root = Path(tesseract_cmd).resolve().parent
    tessdata = tess_root / "tessdata"
    if tessdata.is_dir():
        # UB Mannheim / bundled layout: traineddata лежат прямо в tessdata/
        os.environ["TESSDATA_PREFIX"] = str(tessdata)


def tesseract_ocr_image(
    image: Image.Image,
    *,
    lang: str = "rus+eng",
    psm: int = 3,
    oem: int = 3,
    tesseract_cmd: str | None = None,
    preprocess: str | None = None,
    hakaton_target_size: tuple[int, int] | None = None,
    hakaton_apply_undistort: bool = True,
) -> str:
    """Run Tesseract on a PIL image.

    ``preprocess``:
    - ``None`` — без доп. обработки (как в compare_ufg_tesseract_ocr)
    - ``"hakaton"`` — undistort + blur + adaptive threshold (model_for_hakaton)
    """
    import pytesseract

    cmd = tesseract_cmd or find_tesseract_cmd()
    if not cmd:
        raise RuntimeError("Tesseract binary not found")

    _configure_tesseract(cmd)

    ocr_image = image
    config = f"--psm {psm} --oem {oem}"
    ocr_lang = lang

    if preprocess == "hakaton":
        ocr_image = hakaton_preprocess(
            image,
            target_size=hakaton_target_size,
            apply_undistort=hakaton_apply_undistort,
        )
        config = HAKATON_TESSERACT_CONFIG
        # rus+eng: смешанный текст КД; оригинал hakaton использовал только rus
        ocr_lang = "rus+eng" if lang in {"rus", "rus+eng"} else lang

    return pytesseract.image_to_string(ocr_image, lang=ocr_lang, config=config).strip()
