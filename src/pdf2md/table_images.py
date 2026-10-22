"""Replace detected tables by a picture of their area in the PDF page."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pymupdf

from pdf2md.models import Block, Settings

logger = logging.getLogger(__name__)

_CAPTION = re.compile(r"^(?:table|tableau)\b", re.IGNORECASE)
_ALT_LENGTH = 100
_PADDING = 2.0  # pt added around the table so that its outer border is not clipped


def _alt_text(neighbours: list[Block], page_number: int) -> str:
    for neighbour in neighbours:
        if neighbour.kind == "paragraph" and _CAPTION.match(neighbour.text):
            return " ".join(neighbour.text.split())[:_ALT_LENGTH]
    return f"Table (page {page_number})"


def _convertible(block: Block, settings: Settings) -> bool:
    return (
        block.kind == "table"
        and block.element is None
        and block.coords is not None
        and settings.tables_as_images is not None
        and len(block.rows) >= settings.tables_as_images
    )


def tables_to_images(
    doc: pymupdf.Document, blocks: list[Block], images_dir: Path, settings: Settings
) -> list[Block]:
    """Return ``blocks`` with the tables of ``settings.tables_as_images`` rows or more as images.

    The picture is the page area of the table (``Block.coords``) rendered at ``image_dpi`` and
    saved as ``p<page>_t<n>.png``; the block keeps its coordinates and, with
    ``settings.tables_keep_text``, its rows (rendered under the image). A table without
    coordinates, tagged as a table of contents/figures, or that cannot be rendered stays text.
    """
    result = list(blocks)
    counters: dict[int, int] = {}
    for position, block in enumerate(blocks):
        if not _convertible(block, settings) or block.coords is None:
            continue
        page = doc[block.page_number - 1]
        box = block.coords
        clip = (
            pymupdf.Rect(
                box.x0 - _PADDING, box.top - _PADDING, box.x1 + _PADDING, box.bottom + _PADDING
            )
            & page.rect
        )
        index = counters.get(block.page_number, 0) + 1
        name = f"p{block.page_number:04d}_t{index:02d}.png"
        try:
            pixmap = page.get_pixmap(clip=clip, dpi=settings.image_dpi, alpha=False)
            images_dir.mkdir(parents=True, exist_ok=True)
            pixmap.save(images_dir / name)
        except (RuntimeError, ValueError, OSError) as exc:
            logger.warning("page %d: cannot render table %d: %s", block.page_number, index, exc)
            continue
        counters[block.page_number] = index
        around = [
            blocks[i]
            for i in (position - 1, position + 1)
            if 0 <= i < len(blocks) and blocks[i].page_number == block.page_number
        ]
        result[position] = block.model_copy(
            update={
                "kind": "image",
                "text": _alt_text(around, block.page_number),
                "rows": block.rows if settings.tables_keep_text else [],
                "image_path": f"{images_dir.name}/{name}",
            }
        )
    converted = sum(counters.values())
    if converted:
        logger.info("rendered %d tables as images in %s", converted, images_dir)
    return result
