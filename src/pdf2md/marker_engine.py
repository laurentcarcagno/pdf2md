"""Marker engine (optional dependency): deep-learning conversion to Markdown, page by page."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pdf2md.models import Block
from pdf2md.quiet import quiet_third_party_logs

logger = logging.getLogger(__name__)

_BLANK_LINES = re.compile(r"\n\s*\n")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.DOTALL)
_PAGE_ANCHOR = re.compile(r"<span id=\"page-\d+-\d+\"></span>")

_models: Any = None


def _split_markdown(markdown: str, page_number: int) -> list[Block]:
    blocks = []
    for chunk in _BLANK_LINES.split(_PAGE_ANCHOR.sub("", markdown)):
        text = chunk.strip()
        if not text:
            continue
        heading = _HEADING.match(text)
        if heading:
            blocks.append(
                Block(
                    page_number=page_number,
                    kind="heading",
                    text=heading.group(2).strip(),
                    level=len(heading.group(1)),
                    source_pass="marker",
                )
            )
        elif text.startswith("|") and "\n" in text:
            rows = [
                [cell.strip() for cell in line.strip().strip("|").split("|")]
                for line in text.splitlines()
                if not set(line.replace("|", "").strip()) <= {"-", ":", " "}
            ]
            blocks.append(
                Block(
                    page_number=page_number,
                    kind="table",
                    text=" | ".join(rows[0]),
                    rows=rows,
                    source_pass="marker",
                )
            )
        else:
            blocks.append(
                Block(page_number=page_number, kind="paragraph", text=text, source_pass="marker")
            )
    return blocks


def extract_marker(
    pdf_path: Path, pages: Sequence[int], on_page: Callable[[int], None] | None = None
) -> dict[int, list[Block]] | None:
    """Convert pages with Marker; coordinates are not exposed, so blocks carry none."""
    global _models  # noqa: PLW0603 - deliberate cache of the loaded models
    try:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
    except ImportError:
        logger.info("marker not installed, skipping")
        return None
    result: dict[int, list[Block]] = {}
    try:
        if _models is None:
            _models = create_model_dict()
            quiet_third_party_logs()
        for number in pages:
            converter = PdfConverter(
                artifact_dict=_models,
                config={"page_range": [number - 1], "output_format": "markdown"},
            )
            rendered = converter(str(pdf_path))
            result[number] = _split_markdown(rendered.markdown, number)
            if on_page:
                on_page(number)
    except Exception as exc:  # noqa: BLE001 - marker raises many unrelated types
        logger.warning("marker failed: %s", exc)
        return result or None
    return result
