"""Pass 1: fast page and document classification with PyMuPDF."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

import pymupdf

from pdf2md.errors import PdfOpenError
from pdf2md.models import Classification, PageKind, PageProfile, Settings

logger = logging.getLogger(__name__)

_GUTTER_STEP_PT = 2.0


def open_document(pdf_path: str | Path) -> pymupdf.Document:
    """Open a PDF with PyMuPDF, mapping every failure to :class:`PdfOpenError`."""
    try:
        doc = pymupdf.open(pdf_path)
    except (pymupdf.FileNotFoundError, pymupdf.FileDataError, RuntimeError, ValueError) as exc:
        raise PdfOpenError(f"Cannot open PDF {pdf_path}: {exc}") from exc
    if not doc.is_pdf:
        doc.close()
        raise PdfOpenError(f"{pdf_path} is not a PDF document")
    if doc.needs_pass:
        doc.close()
        raise PdfOpenError(f"{pdf_path} is encrypted and requires a password")
    return doc


def has_column_gutter(
    words: list[tuple[float, float, float, float]], page_width: float, settings: Settings
) -> bool:
    """Return True when a vertical empty strip separates two substantial groups of words.

    ``words`` are ``(x0, y0, x1, y1)`` boxes. A free strip around the page centre with enough
    words on both sides, some of them on the same horizontal band, means a multi-column layout.
    """
    if len(words) < settings.column_min_words:
        return False
    min_width = settings.column_gutter_ratio * page_width
    x = 0.3 * page_width
    run_start: float | None = None
    while x <= 0.7 * page_width:
        crossed = any(x0 < x < x1 for x0, _, x1, _ in words)
        if crossed:
            run_start = None
        else:
            run_start = x if run_start is None else run_start
            if x - run_start >= min_width:
                centre = (run_start + x) / 2
                left = [w for w in words if w[2] <= centre]
                right = [w for w in words if w[0] >= centre]
                share = settings.column_side_ratio * len(words)
                if len(left) >= share and len(right) >= share:
                    return _share_a_band(left, right)
        x += _GUTTER_STEP_PT
    return False


def _share_a_band(
    left: list[tuple[float, float, float, float]], right: list[tuple[float, float, float, float]]
) -> bool:
    """Return True if at least one left word and one right word overlap vertically."""
    right_bands = {round(w[1] / 4) for w in right}
    return any(round(w[1] / 4) in right_bands for w in left)


def classify_page(page: pymupdf.Page, page_number: int, settings: Settings) -> PageProfile:
    """Classify one page as native text, empty, scanned or multi-column."""
    char_count = len(page.get_text("text").strip())
    page_area = max(page.rect.width * page.rect.height, 1.0)
    image_area = sum(
        pymupdf.Rect(info["bbox"]).intersect(page.rect).get_area() for info in page.get_image_info()
    )
    image_ratio = min(image_area / page_area, 1.0)

    if char_count == 0 and image_ratio == 0.0:
        return PageProfile(
            page_number=page_number,
            kind=PageKind.EMPTY,
            char_count=0,
            image_ratio=0.0,
            reason="no text and no image",
        )
    if image_ratio >= settings.scanned_image_ratio and char_count < settings.scanned_char_threshold:
        return PageProfile(
            page_number=page_number,
            kind=PageKind.SCANNED,
            char_count=char_count,
            image_ratio=round(image_ratio, 2),
            reason=f"{image_ratio:.0%} of the page is image, only {char_count} chars",
        )
    text = page.get_text("text")
    unusable = text.count("\ufffd") + text.count("(cid:")
    if char_count and unusable / max(len(text), 1) >= settings.garbled_ratio:
        return PageProfile(
            page_number=page_number,
            kind=PageKind.GARBLED,
            char_count=char_count,
            image_ratio=round(image_ratio, 2),
            reason="text layer is mostly unmapped glyphs",
        )
    words = [(w[0], w[1], w[2], w[3]) for w in page.get_text("words")]
    if has_column_gutter(words, page.rect.width, settings):
        return PageProfile(
            page_number=page_number,
            kind=PageKind.MULTI_COLUMN,
            char_count=char_count,
            image_ratio=round(image_ratio, 2),
            reason="vertical gutter between two groups of words",
        )
    return PageProfile(
        page_number=page_number,
        kind=PageKind.NATIVE,
        char_count=char_count,
        image_ratio=round(image_ratio, 2),
        reason="native text layer",
    )


def classify_document(
    doc: pymupdf.Document, settings: Settings, pages: Sequence[int] | None = None
) -> Classification:
    """Classify the given 1-based pages (default: all) and decide the document level route."""
    profiles: list[PageProfile] = []
    for number in pages if pages is not None else range(1, doc.page_count + 1):
        index = number - 1
        try:
            profiles.append(classify_page(doc[index], index + 1, settings))
        except (RuntimeError, ValueError) as exc:
            logger.warning("page %d unreadable by PyMuPDF: %s", index + 1, exc)
            profiles.append(
                PageProfile(
                    page_number=index + 1,
                    kind=PageKind.SCANNED,
                    char_count=0,
                    image_ratio=0.0,
                    reason=f"page unreadable: {exc}",
                )
            )
    non_empty = [p for p in profiles if p.kind is not PageKind.EMPTY]
    complex_pages = [p for p in non_empty if p.kind is not PageKind.NATIVE]
    is_complex = (
        bool(non_empty) and len(complex_pages) / len(non_empty) > settings.complex_page_ratio
    )
    return Classification(document_kind="complex" if is_complex else "native_text", pages=profiles)
