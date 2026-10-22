"""Image extraction: raster pictures and vector figures, saved as PNG and inserted in order."""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pymupdf

from pdf2md.models import Block, Coordinates, Settings

logger = logging.getLogger(__name__)

_CAPTION = re.compile(r"^(?:figure|fig\.|image|illustration|schéma|diagram)\b", re.IGNORECASE)
_MAX_ASPECT = 15.0
_FULL_PAGE_RATIO = 0.85
_DECORATION_PAGE_RATIO = 0.4
_MIN_DECORATION_PAGES = 3
_MIN_VECTOR_WIDTH = 100.0
_MIN_VECTOR_HEIGHT = 60.0
_MIN_DIAGONALS = 3
_OVERLAP_RATIO = 0.5
_ALT_LENGTH = 100
_MIN_TEXT_HEIGHT = 5.0
_MAX_TEXT_HEIGHT = 25.0
_MIN_TEXT_WIDTH = 20.0
_MIN_TEXT_ASPECT = 2.0
_MAX_PIECE_GAP = 6.0
_TEXT_IMAGE_DPI = 300


def _diagonal_items(drawings: list[dict[str, Any]], area: pymupdf.Rect) -> int:
    """Count curves and non-axis-aligned segments inside ``area``.

    Table borders and boxes are made of horizontal and vertical segments only; a figure
    (arrows, curves, plots) contains others, which is how the two are told apart.
    """
    count = 0
    for drawing in drawings:
        rect = pymupdf.Rect(drawing["rect"])
        if not area.contains(rect):
            continue
        for item in drawing["items"]:
            if item[0] == "c":
                count += 1
            elif (
                item[0] == "l" and abs(item[1].x - item[2].x) > 1 and abs(item[1].y - item[2].y) > 1
            ):
                count += 1
    return count


def _overlap(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    """Return the share of the smaller rectangle covered by the intersection."""
    inter = (a & b).get_area()
    smaller = min(a.get_area(), b.get_area())
    return inter / smaller if smaller > 0 else 0.0


def _decoration_digests(doc: pymupdf.Document, pages: Sequence[int]) -> set[str]:
    """Return content hashes of images repeated on many pages (logos, watermarks).

    The hash, not the xref, identifies an image: producers often embed the same picture under
    several xrefs.
    """
    counts: Counter[str] = Counter()
    for number in pages:
        digests = {info["digest"] for info in doc[number - 1].get_image_info(hashes=True)}
        counts.update(d for d in digests if d)
    needed = max(_MIN_DECORATION_PAGES, int(_DECORATION_PAGE_RATIO * len(pages)))
    return {digest for digest, seen in counts.items() if seen >= needed}


def _is_text_like(rect: pymupdf.Rect) -> bool:
    """A short, wide strip: a piece of a line of text drawn as a picture (labels, IDs)."""
    return bool(
        _MIN_TEXT_HEIGHT <= rect.height <= _MAX_TEXT_HEIGHT
        and rect.width >= _MIN_TEXT_WIDTH
        and rect.width >= _MIN_TEXT_ASPECT * rect.height
    )


def _in_margin(rect: pymupdf.Rect, page: pymupdf.Page, settings: Settings) -> bool:
    band = settings.margin_band_ratio * page.rect.height
    return bool(rect.y1 <= band or rect.y0 >= page.rect.height - band)


def _merge_text_line_pieces(
    rects: list[pymupdf.Rect], words: list[tuple[float, float, float, float]]
) -> list[pymupdf.Rect]:
    """Join images side by side on one line ("HAR SSOW-" + "10 /" + "Routing drawings").

    Two pieces are joined when they overlap vertically, are at most a few points apart and no
    real text word sits in the gap (otherwise the text would be duplicated in the picture).
    """
    merged: list[pymupdf.Rect] = []
    for rect in sorted(rects, key=lambda r: r.x0):
        for index, line in enumerate(merged):
            if _same_line(line, rect) and not _text_between(line, rect, words):
                merged[index] = line | rect
                break
        else:
            merged.append(pymupdf.Rect(rect))
    return merged


def _same_line(left: pymupdf.Rect, right: pymupdf.Rect) -> bool:
    overlap = min(left.y1, right.y1) - max(left.y0, right.y0)
    smaller = min(left.height, right.height)
    gap = right.x0 - left.x1
    return bool(smaller > 0 and overlap >= 0.5 * smaller and -1.0 <= gap <= _MAX_PIECE_GAP)


def _text_between(
    left: pymupdf.Rect, right: pymupdf.Rect, words: list[tuple[float, float, float, float]]
) -> bool:
    gap = pymupdf.Rect(left.x1, min(left.y0, right.y0), right.x0, max(left.y1, right.y1))
    return any(bool(pymupdf.Rect(w).intersects(gap)) for w in words)


def _candidate_rects(
    page: pymupdf.Page, decorations: set[str], skip_full_page: bool, settings: Settings
) -> list[tuple[pymupdf.Rect, bool]]:
    """Return ``(rect, text_like)`` for every figure of the page, top to bottom."""
    page_area = page.rect.get_area()
    words = [(w[0], w[1], w[2], w[3]) for w in page.get_text("words")]
    pieces: list[pymupdf.Rect] = []
    for info in page.get_image_info(hashes=True):
        rect = pymupdf.Rect(info["bbox"]) & page.rect
        if rect.is_empty:
            continue
        repeated = info["digest"] in decorations
        # A repeated strip in the body is a recurring text label (requirement ids), not a logo.
        if repeated and (_in_margin(rect, page, settings) or not _is_text_like(rect)):
            continue
        pieces.append(rect)

    rasters: list[tuple[pymupdf.Rect, bool]] = []
    for rect in _merge_text_line_pieces(pieces, words):
        text_like = _is_text_like(rect)
        if not text_like:
            if rect.get_area() < settings.image_min_area:
                continue
            aspect = max(rect.width / max(rect.height, 1), rect.height / max(rect.width, 1))
            if aspect > _MAX_ASPECT:
                continue
        if skip_full_page and rect.get_area() >= _FULL_PAGE_RATIO * page_area:
            continue  # the scan itself, not a figure
        rasters.append((rect, text_like))
    figures = _keep_text_flags(rasters)

    drawings = page.get_drawings()
    for rect in page.cluster_drawings(drawings=drawings):
        if rect.width < _MIN_VECTOR_WIDTH or rect.height < _MIN_VECTOR_HEIGHT:
            continue
        if rect.get_area() >= _FULL_PAGE_RATIO * page_area:
            continue
        if any(_overlap(rect, other) > _OVERLAP_RATIO for other, _ in figures):
            continue
        if _diagonal_items(drawings, rect) >= _MIN_DIAGONALS:
            figures.append((rect, False))
    return sorted(figures, key=lambda item: (item[0].y0, item[0].x0))


def _keep_text_flags(items: list[tuple[pymupdf.Rect, bool]]) -> list[tuple[pymupdf.Rect, bool]]:
    """Merge overlapping rectangles; a merged one is text-like only if all its parts were."""
    merged: list[tuple[pymupdf.Rect, bool]] = []
    for rect, flag in sorted(items, key=lambda item: (item[0].y0, item[0].x0)):
        for index, (other, other_flag) in enumerate(merged):
            if _overlap(rect, other) > _OVERLAP_RATIO:
                merged[index] = (other | rect, other_flag and flag)
                break
        else:
            merged.append((rect, flag))
    return merged


def _ocr_alt(pixmap: pymupdf.Pixmap, settings: Settings) -> str:
    """Read the text of a text-like picture (best effort, empty if OCR is unavailable)."""
    if not settings.ocr_enabled:
        return ""
    from pdf2md.ocr import ocr_line_image

    return ocr_line_image(pixmap, settings.ocr_language)


def extract_images(
    doc: pymupdf.Document,
    pages: Sequence[int],
    images_dir: Path,
    settings: Settings,
    scanned_pages: frozenset[int] = frozenset(),
) -> dict[int, list[Block]]:
    """Render every figure of the given pages to ``images_dir`` and return image blocks.

    Figures are rendered from the page (``clip``), so masks, CMYK and vector content come out
    right. Images side by side on one line are merged, and when such a strip is a line of text
    drawn as a picture it is rendered at 300 dpi and read with OCR to give its alt text.
    Repeated images (logos), tiny or extreme-aspect images, and the full-page picture of a
    scanned page are skipped. The directory is created only if at least one image is saved.
    """
    decorations = _decoration_digests(doc, pages)
    result: dict[int, list[Block]] = {}
    for number in pages:
        page = doc[number - 1]
        try:
            rects = _candidate_rects(page, decorations, number in scanned_pages, settings)
        except (RuntimeError, ValueError) as exc:
            logger.warning("page %d: cannot list images: %s", number, exc)
            continue
        for index, (rect, text_like) in enumerate(rects, start=1):
            try:
                dpi = max(settings.image_dpi, _TEXT_IMAGE_DPI) if text_like else settings.image_dpi
                pixmap = page.get_pixmap(clip=rect, dpi=dpi, alpha=False)
                if pixmap.is_unicolor:
                    continue
                images_dir.mkdir(parents=True, exist_ok=True)
                name = f"p{number:04d}_{index:02d}.png"
                pixmap.save(images_dir / name)
            except (RuntimeError, ValueError, OSError) as exc:
                logger.warning("page %d: cannot save image %d: %s", number, index, exc)
                continue
            result.setdefault(number, []).append(
                Block(
                    page_number=number,
                    kind="image",
                    text=_ocr_alt(pixmap, settings) if text_like else "",
                    coords=Coordinates(x0=rect.x0, x1=rect.x1, top=rect.y0, bottom=rect.y1),
                    source_pass="pymupdf_image",
                    image_path=f"{images_dir.name}/{name}",
                )
            )
    return result


def _area(box: Coordinates) -> float:
    return max(box.x1 - box.x0, 0.0) * max(box.bottom - box.top, 0.0)


def _duplicates_table(image: Coordinates, blocks: list[Block]) -> bool:
    """Return True if the "figure" is just the drawing of a table already in the text.

    Table borders and scrollbars can look like a vector figure; such a cluster covers most of
    a table block, while a picture inside a table cell is only a small part of it.
    """
    for block in blocks:
        if block.kind != "table" or block.coords is None:
            continue
        box = block.coords
        inter = Coordinates(
            x0=max(image.x0, box.x0),
            x1=min(image.x1, box.x1),
            top=max(image.top, box.top),
            bottom=min(image.bottom, box.bottom),
        )
        if _area(inter) > _OVERLAP_RATIO * _area(image) and _area(image) >= _OVERLAP_RATIO * _area(
            box
        ):
            return True
    return False


def caption_alt_text(blocks: list[Block], position: int, page_number: int) -> str:
    """Use the caption next to the image ("Figure 3: ...") as alt text, else a generic one."""
    for neighbour in blocks[position : position + 1] + blocks[max(position - 1, 0) : position]:
        if neighbour.kind == "paragraph" and _CAPTION.match(neighbour.text):
            return " ".join(neighbour.text.split())[:_ALT_LENGTH]
    return f"Image (page {page_number})"


def insert_images(
    blocks_by_page: dict[int, list[Block]],
    images_by_page: dict[int, list[Block]],
    base_dir: Path | None = None,
) -> dict[int, list[Block]]:
    """Return ``blocks_by_page`` with image blocks inserted at their vertical position.

    An image goes before the first block that starts below its top edge. Blocks without
    coordinates (Marker) cannot be compared, so images of such pages go at the end.
    Rejected images are deleted from disk when ``base_dir`` (parent of the image folder) is given.
    """
    merged = {number: list(blocks) for number, blocks in blocks_by_page.items()}
    for number, images in images_by_page.items():
        blocks = merged.setdefault(number, [])
        for image in images:
            assert image.coords is not None  # noqa: S101 - set by extract_images
            if _duplicates_table(image.coords, blocks):
                if base_dir is not None and image.image_path:
                    (base_dir / image.image_path).unlink(missing_ok=True)
                continue
            position = len(blocks)
            for index, block in enumerate(blocks):
                if block.coords is not None and block.coords.top > image.coords.top:
                    position = index
                    break
            if not image.text:
                image.text = caption_alt_text(blocks, position, number)
            blocks.insert(position, image)
    return merged
