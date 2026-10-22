"""Docling engine: layout analysis, table structure and OCR by models (optional dependency)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pdf2md.models import Block, Coordinates, ElementKind
from pdf2md.quiet import quiet_third_party_logs

logger = logging.getLogger(__name__)

_converters: dict[tuple[str, ...], Any] = {}

_PARAGRAPH_LABELS = {"text", "paragraph", "caption", "footnote", "reference", "formula"}


def contiguous_ranges(pages: Sequence[int]) -> list[tuple[int, int]]:
    """Group sorted 1-based page numbers into inclusive ``(first, last)`` ranges."""
    ranges: list[tuple[int, int]] = []
    for number in sorted(set(pages)):
        if ranges and number == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], number)
        else:
            ranges.append((number, number))
    return ranges


def _ocr_options(languages: tuple[str, ...]) -> Any:
    """Pick the OCR engine explicitly: Docling's "auto" mode probes every engine and logs noise.

    RapidOCR is used rather than Tesseract: Docling runs Tesseract on small crops, where its
    orientation detection fails and logs an ERROR per crop. Tesseract stays our own OCR engine
    for fully scanned pages (``ocr.py``).
    """
    from docling.datamodel.pipeline_options import RapidOcrOptions

    return RapidOcrOptions()


def _get_converter(languages: tuple[str, ...]) -> Any:
    """Build one converter per language set: loading the layout and table models is expensive."""
    if languages not in _converters:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        options = PdfPipelineOptions(
            do_ocr=True, do_table_structure=True, ocr_options=_ocr_options(languages)
        )
        _converters[languages] = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )
        quiet_third_party_logs()
    return _converters[languages]


def _coordinates(provenance: Any, page_height: float) -> Coordinates:
    box = provenance.bbox.to_top_left_origin(page_height)
    return Coordinates(x0=box.l, x1=box.r, top=box.t, bottom=box.b)


def _table_rows(item: Any) -> list[list[str]]:
    rows = [[" ".join(str(cell.text).split()) for cell in row] for row in item.data.grid]
    return [row for row in rows if any(row)]


def _block_from_item(item: Any, page_number: int, coords: Coordinates) -> Block | None:
    label = str(getattr(item.label, "value", item.label))
    text = str(getattr(item, "text", "") or "").strip()
    common: dict[str, Any] = {
        "page_number": page_number,
        "coords": coords,
        "source_pass": "docling",
    }
    if label == "page_header" or label == "page_footer":
        element: ElementKind = "header" if label == "page_header" else "footer"
        return Block(kind="paragraph", text=text, element=element, **common) if text else None
    if label == "document_index":
        rows = _table_rows(item) if hasattr(item, "data") else []
        lines = [" ".join(cell for cell in row if cell) for row in rows] or ([text] if text else [])
        return Block(kind="toc", text="\n".join(lines), **common) if lines else None
    if label == "table":
        rows = _table_rows(item)
        if not rows:
            return None
        return Block(kind="table", text=" | ".join(rows[0]), rows=rows, **common)
    if not text:
        return None
    if label in ("title", "section_header"):
        level = 1 if label == "title" else max(int(getattr(item, "level", 1)), 1)
        return Block(kind="heading", text=text, level=min(level, 6), **common)
    if label == "list_item":
        marker = str(getattr(item, "marker", "") or "-").strip() or "-"
        return Block(kind="list", text=f"{marker} {text}", **common)
    if label == "code":
        return Block(kind="code", text=text, **common)
    if label in _PARAGRAPH_LABELS:
        return Block(kind="paragraph", text=text, **common)
    return None


def _merge_lists(blocks: list[Block]) -> list[Block]:
    """Merge consecutive list-item blocks of one page into a single list block."""
    merged: list[Block] = []
    for block in blocks:
        last = merged[-1] if merged else None
        if (
            last is not None
            and block.kind == "list"
            and last.kind == "list"
            and last.page_number == block.page_number
        ):
            last.text += "\n" + block.text
            if last.coords and block.coords:
                last.coords = last.coords.union(block.coords)
        else:
            merged.append(block)
    return merged


def extract_docling(
    pdf_path: Path,
    pages: Sequence[int],
    ocr_language: str = "fra+eng",
    on_page: Callable[[int], None] | None = None,
) -> dict[int, list[Block]] | None:
    """Convert the given pages with Docling; return ``None`` if it is unavailable or fails."""
    try:
        converter = _get_converter(tuple(ocr_language.split("+")))
    except ImportError:
        logger.info("docling not installed, skipping")
        return None
    except Exception as exc:  # noqa: BLE001 - model loading can fail in many ways
        logger.warning("docling cannot start: %s", exc)
        return None
    result: dict[int, list[Block]] = {}
    for first, last in contiguous_ranges(pages):
        try:

            def report(event: Any, first: int = first, last: int = last) -> None:
                done = getattr(event, "completed_pages", None)
                if on_page and done:
                    on_page(min(first + int(done) - 1, last))

            document = converter.convert(
                pdf_path, page_range=(first, last), progress_callback=report
            ).document
            if on_page:
                for number in range(first, last + 1):
                    on_page(number)
            for item, _level in document.iterate_items():
                if not getattr(item, "prov", None):
                    continue
                provenance = item.prov[0]
                page_number = int(provenance.page_no)
                height = float(document.pages[page_number].size.height)
                block = _block_from_item(item, page_number, _coordinates(provenance, height))
                if block is not None:
                    result.setdefault(page_number, []).append(block)
        except Exception as exc:  # noqa: BLE001 - docling raises many unrelated types
            logger.warning("docling failed on pages %d-%d: %s", first, last, exc)
    return {number: _merge_lists(blocks) for number, blocks in result.items()} or None
