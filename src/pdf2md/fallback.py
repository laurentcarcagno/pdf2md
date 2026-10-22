"""Last-resort extraction: plain PyMuPDF text blocks, no dependency beyond the core."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

import pymupdf

from pdf2md.models import Block, Coordinates

logger = logging.getLogger(__name__)


def extract_pymupdf_blocks(
    doc: pymupdf.Document, pages: Sequence[int], on_page: Callable[[int], None] | None = None
) -> dict[int, list[Block]]:
    """Return one paragraph block per PyMuPDF text block, with its bounding box."""
    result: dict[int, list[Block]] = {}
    for page_number in pages:
        blocks: list[Block] = []
        try:
            raw_blocks = doc[page_number - 1].get_text("blocks", sort=True)
        except (RuntimeError, ValueError) as exc:
            logger.warning("PyMuPDF cannot read page %d: %s", page_number, exc)
            raw_blocks = []
        for x0, y0, x1, y1, text, _no, block_type in raw_blocks:
            cleaned = " ".join(str(text).split())
            if block_type != 0 or not cleaned:
                continue
            blocks.append(
                Block(
                    page_number=page_number,
                    kind="paragraph",
                    text=cleaned,
                    coords=Coordinates(x0=x0, x1=x1, top=y0, bottom=y1),
                    source_pass="pass3_pymupdf",
                )
            )
        if not blocks:
            logger.warning("page %d: no extractable text", page_number)
        result[page_number] = blocks
        if on_page:
            on_page(page_number)
    return result
