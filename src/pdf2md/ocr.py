"""OCR for pages without a usable text layer.

Pipeline: render the page, clean it with OpenCV (grayscale, deskew, light denoise), recognise
text lines with Tesseract (CLI, TSV output) or RapidOCR, and return :class:`PageContent` objects
so the pages go through the same cleaning and structuring passes as native pages.
"""

from __future__ import annotations

import logging
import shutil
import statistics
import subprocess
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import pymupdf

from pdf2md.models import Coordinates, Line, PageContent, Settings, SourcePass
from pdf2md.quiet import quiet_third_party_logs

logger = logging.getLogger(__name__)

_TESSERACT_TIMEOUT_S = 180
_MAX_DESKEW_DEG = 15.0
_MIN_DESKEW_DEG = 0.3
_MIN_CONFIDENCE = 30.0


@dataclass(frozen=True)
class OcrLine:
    """A recognised text line; coordinates are in image pixels."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float


class OcrEngine(Protocol):
    """Anything able to turn a grayscale image into text lines."""

    name: SourcePass

    def recognize(self, image: Any) -> list[OcrLine]:
        """Return the text lines found in ``image`` (a 2-D uint8 numpy array)."""
        ...


class TesseractEngine:
    """Tesseract 5 through its command line, one process per page."""

    name: SourcePass = "ocr_tesseract"

    def __init__(self, language: str, dpi: int, psm: int = 3) -> None:
        self._binary = shutil.which("tesseract")
        if self._binary is None:
            raise RuntimeError("tesseract is not installed")
        self._language = language
        self._dpi = dpi
        self._psm = psm

    def recognize(self, image: Any) -> list[OcrLine]:
        """Run ``tesseract stdin stdout tsv`` and group words into lines."""
        import cv2

        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise RuntimeError("cannot encode the page image")
        completed = subprocess.run(  # noqa: S603 - fixed binary, no shell
            [
                str(self._binary),
                "stdin",
                "stdout",
                "-l",
                self._language,
                "--dpi",
                str(self._dpi),
                "--psm",
                "3",
                "tsv",
            ],
            input=encoded.tobytes(),
            capture_output=True,
            timeout=_TESSERACT_TIMEOUT_S,
            check=True,
        )
        return parse_tesseract_tsv(completed.stdout.decode("utf-8", errors="replace"))


def parse_tesseract_tsv(tsv: str) -> list[OcrLine]:
    """Group Tesseract TSV word rows into lines (block, paragraph, line numbers)."""
    words: dict[tuple[str, str, str, str], list[tuple[float, ...]]] = defaultdict(list)
    texts: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5" or not cols[11].strip():
            continue
        key = (cols[1], cols[2], cols[3], cols[4])
        left, top, width, height, conf = (float(c) for c in cols[6:11])
        words[key].append((left, top, left + width, top + height, conf))
        texts[key].append(cols[11].strip())
    lines = []
    for key, boxes in words.items():
        lines.append(
            OcrLine(
                text=" ".join(texts[key]),
                x0=min(b[0] for b in boxes),
                y0=min(b[1] for b in boxes),
                x1=max(b[2] for b in boxes),
                y1=max(b[3] for b in boxes),
                confidence=statistics.fmean(b[4] for b in boxes),
            )
        )
    return lines


class RapidOcrEngine:
    """RapidOCR (PaddleOCR models on onnxruntime); more robust on noisy scans."""

    name: SourcePass = "ocr_rapidocr"

    def __init__(self) -> None:
        from rapidocr import RapidOCR

        self._engine = RapidOCR()
        quiet_third_party_logs()

    def recognize(self, image: Any) -> list[OcrLine]:
        """Return one :class:`OcrLine` per detected text box."""
        result: Any = self._engine(image)
        if result.txts is None or result.boxes is None:
            return []
        lines = []
        for text, score, box in zip(result.txts, result.scores, result.boxes, strict=True):
            xs = [point[0] for point in box]
            ys = [point[1] for point in box]
            lines.append(OcrLine(str(text), min(xs), min(ys), max(xs), max(ys), float(score) * 100))
        return lines


def preprocess(image: Any) -> Any:
    """Deskew and lightly denoise a grayscale page image (OpenCV)."""
    import cv2
    import numpy as np

    inverted = cv2.bitwise_not(image)
    _, mask = cv2.threshold(inverted, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    points = cv2.findNonZero(mask)
    if points is not None and len(points) > 100:
        angle = cv2.minAreaRect(points)[-1]
        angle = angle - 90 if angle > 45 else angle
        if _MIN_DESKEW_DEG < abs(angle) < _MAX_DESKEW_DEG:
            height, width = image.shape[:2]
            matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
            image = cv2.warpAffine(
                image,
                matrix,
                (width, height),
                flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_REPLICATE,
            )
            logger.debug("deskewed page by %.2f degrees", angle)
    return cv2.medianBlur(np.ascontiguousarray(image), 3)


def render_page(page: pymupdf.Page, dpi: int) -> Any:
    """Render a PyMuPDF page to a grayscale numpy array."""
    import numpy as np

    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()


def lines_to_page(
    ocr_lines: list[OcrLine], page_number: int, width: float, height: float, dpi: int
) -> PageContent:
    """Convert pixel lines to a :class:`PageContent` in PDF points."""
    scale = 72.0 / dpi
    lines = []
    for item in ocr_lines:
        if item.confidence < _MIN_CONFIDENCE or not item.text.strip():
            continue
        box_height = (item.y1 - item.y0) * scale
        lines.append(
            Line(
                page_number=page_number,
                text=item.text.strip(),
                coords=Coordinates(
                    x0=item.x0 * scale,
                    x1=item.x1 * scale,
                    top=item.y0 * scale,
                    bottom=item.y1 * scale,
                ),
                size=round(box_height * 0.8 * 2) / 2,
                is_mono=False,
            )
        )
    return PageContent(page_number=page_number, width=width, height=height, lines=lines, tables=[])


def build_engines(settings: Settings) -> list[OcrEngine]:
    """Return the available OCR engines in order of preference (Tesseract, RapidOCR)."""
    engines: list[OcrEngine] = []
    try:
        engines.append(TesseractEngine(settings.ocr_language, settings.ocr_dpi))
    except RuntimeError as exc:
        logger.info("Tesseract unavailable: %s", exc)
    try:
        engines.append(RapidOcrEngine())
    except ImportError:
        logger.info("rapidocr not installed")
    return engines


def ocr_pages(
    pdf_path: Path,
    doc: pymupdf.Document,
    pages: list[int],
    settings: Settings,
    engines: list[OcrEngine] | None = None,
    on_page: Callable[[int], None] | None = None,
) -> tuple[dict[int, PageContent], dict[int, SourcePass]]:
    """OCR the given 1-based pages; return the lines per page and the engine that read each.

    For each page the engines are tried in order until one returns enough text
    (``Settings.ocr_min_chars``). Pages that no engine can read are omitted.
    """
    try:
        import cv2  # noqa: F401
    except ImportError:
        logger.warning("opencv-python-headless not installed: OCR disabled")
        return {}, {}
    engines = engines if engines is not None else build_engines(settings)
    if not engines:
        logger.warning("no OCR engine available for %s", pdf_path.name)
        return {}, {}
    contents: dict[int, PageContent] = {}
    used: dict[int, SourcePass] = {}
    for number in pages:
        if on_page:
            on_page(number)
        page = doc[number - 1]
        try:
            image = preprocess(render_page(page, settings.ocr_dpi))
        except (RuntimeError, ValueError) as exc:
            logger.warning("page %d cannot be rendered for OCR: %s", number, exc)
            continue
        for engine in engines:
            try:
                recognised = engine.recognize(image)
            except (RuntimeError, subprocess.SubprocessError, OSError, ValueError) as exc:
                logger.warning("page %d: %s failed: %s", number, engine.name, exc)
                continue
            content = lines_to_page(
                recognised, number, page.rect.width, page.rect.height, settings.ocr_dpi
            )
            if sum(len(line.text) for line in content.lines) >= settings.ocr_min_chars:
                contents[number] = content
                used[number] = engine.name
                break
            logger.info("page %d: %s returned too little text", number, engine.name)
    return contents, used


def ocr_line_image(pixmap: pymupdf.Pixmap, language: str) -> str:
    """Read a picture holding one line of text (a label drawn as an image).

    Best effort: returns "" when OpenCV or Tesseract is missing or reads nothing.
    """
    try:
        import cv2
        import numpy as np

        engine = TesseractEngine(language, dpi=300, psm=7)
        gray = (
            pixmap
            if pixmap.colorspace == pymupdf.csGRAY
            else pymupdf.Pixmap(pymupdf.csGRAY, pixmap)
        )
        image = np.frombuffer(gray.samples, dtype=np.uint8).reshape(gray.height, gray.width).copy()
        padded = cv2.copyMakeBorder(image, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
        lines = engine.recognize(padded)
    except (ImportError, RuntimeError, subprocess.SubprocessError, OSError, ValueError) as exc:
        logger.debug("cannot read text in image: %s", exc)
        return ""
    return " ".join(line.text for line in lines if line.confidence >= _MIN_CONFIDENCE).strip()
