"""Pass 2a: geometric extraction of lines and tables with pdfplumber."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pdfplumber
import pymupdf

from pdf2md.errors import PdfOpenError
from pdf2md.models import Coordinates, Line, PageContent, Table
from pdf2md.pdf_table_html import PageDrawings, table_to_html

logger = logging.getLogger(__name__)

# pdfminer warns once per font about missing FontBBox; the output is unaffected.
logging.getLogger("pdfminer").setLevel(logging.ERROR)

_MONO_MARKERS = ("mono", "courier", "consolas")
_LABEL = re.compile(r"^[A-Z][A-Z0-9_]*:")
_MIN_COLUMNS = 2


def _line_from_plumber(raw: dict[str, Any], page_number: int) -> Line | None:
    text = str(raw["text"]).strip()
    chars = raw.get("chars") or []
    if not text or not chars:
        return None
    sizes = sorted(float(c["size"]) for c in chars)
    flags = [any(m in str(c["fontname"]).lower() for m in _MONO_MARKERS) for c in chars]
    # Inline code inside prose must stay prose; a monospace "LABEL:" opening a line is code.
    is_mono = sum(flags) > len(flags) / 2 or (flags[0] and bool(_LABEL.match(text)))
    return Line(
        page_number=page_number,
        text=text,
        coords=Coordinates(
            x0=float(raw["x0"]),
            x1=float(raw["x1"]),
            top=float(raw["top"]),
            bottom=float(raw["bottom"]),
        ),
        size=round(sizes[len(sizes) // 2], 1),
        is_mono=is_mono,
    )


def _center_inside(line: Line, box: Coordinates) -> bool:
    cx = (line.coords.x0 + line.coords.x1) / 2
    cy = (line.coords.top + line.coords.bottom) / 2
    return box.x0 <= cx <= box.x1 and box.top <= cy <= box.bottom


def _clean_cell(cell: object) -> str:
    return " ".join(str(cell or "").split())


Styler = tuple[pymupdf.Page, PageDrawings]


def _table_html(found: Any, styler: Styler | None) -> tuple[str, list[float]] | None:
    """Return the styled HTML of a pdfplumber table, None when not requested or on failure."""
    if styler is None:
        return None
    page, drawings = styler
    boxes = [(c[0], c[1], c[2], c[3]) for row in found.rows for c in row.cells if c]
    try:
        return table_to_html(page, boxes, drawings)
    except (RuntimeError, ValueError, KeyError) as exc:
        logger.warning("page %d: cannot build the HTML table: %s", page.number + 1, exc)
        return None


_MAX_BORDER_THICKNESS = 2.0  # pt: thinner filled rectangles are drawn borders, not backgrounds


def _not_background(obj: dict[str, Any]) -> bool:
    """Keep everything but filled rectangles that are too thick to be a drawn border."""
    if obj["object_type"] != "rect":
        return True
    thickness = min(obj["x1"] - obj["x0"], obj["bottom"] - obj["top"])
    return bool(thickness <= _MAX_BORDER_THICKNESS)


def _wide_tables(tables: list[Any]) -> int:
    return sum(1 for t in tables if t.rows and len(t.rows[0].cells) >= _MIN_COLUMNS)


def _find_tables(page: Any, clean_grid: bool) -> list[Any]:
    """Find the tables of a page, optionally ignoring the background fills of the cells.

    Some producers (Word) shade a cell with one rectangle per text line; pdfplumber then takes
    their edges for cell borders and cuts a cell into one piece per line. Without the fills only
    the real borders remain. The result is used when it finds at least as many tables.
    """
    found = list(page.find_tables())
    if clean_grid:
        cleaned = list(page.filter(_not_background).find_tables())
        if _wide_tables(cleaned) >= _wide_tables(found):
            return cleaned
    return found


def _extract_page(page: Any, page_number: int, styler: Styler | None = None) -> PageContent:
    tables: list[Table] = []
    for found in _find_tables(page, clean_grid=styler is not None):
        rows = [[_clean_cell(c) for c in row] for row in found.extract()]
        rows = [row for row in rows if any(row)]
        if len(rows) >= 1 and rows and len(rows[0]) >= 2:
            x0, top, x1, bottom = found.bbox
            styled = _table_html(found, styler)
            tables.append(
                Table(
                    page_number=page_number,
                    coords=Coordinates(x0=x0, x1=x1, top=top, bottom=bottom),
                    rows=rows,
                    html=styled[0] if styled else None,
                    grid=styled[1] if styled else [],
                )
            )
    lines: list[Line] = []
    for raw in page.extract_text_lines():
        line = _line_from_plumber(raw, page_number)
        if line and not any(_center_inside(line, t.coords) for t in tables):
            lines.append(line)
    return PageContent(
        page_number=page_number,
        width=float(page.width),
        height=float(page.height),
        lines=lines,
        tables=tables,
    )


def extract_pages(
    pdf_path: Path,
    page_numbers: Iterable[int],
    on_page: Callable[[int], None] | None = None,
    tables_html: bool = False,
) -> dict[int, PageContent]:
    """Extract lines and tables of the requested 1-based pages.

    A page that pdfplumber cannot parse is logged and omitted so the caller can route it to
    pass 3 instead of failing the whole document. With ``tables_html`` each table also carries
    its HTML (cell fills and text styles read with PyMuPDF).
    """
    wanted = sorted(set(page_numbers))
    result: dict[int, PageContent] = {}
    styled_doc = pymupdf.open(pdf_path) if tables_html else None
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for number in wanted:
                try:
                    styler = None
                    if styled_doc is not None:
                        page = styled_doc[number - 1]
                        styler = (page, PageDrawings(page))
                    result[number] = _extract_page(pdf.pages[number - 1], number, styler)
                except Exception as exc:  # noqa: BLE001 - pdfminer raises many unrelated types
                    logger.warning("pdfplumber failed on page %d: %s", number, exc)
                if on_page:
                    on_page(number)
    except Exception as exc:  # noqa: BLE001
        raise PdfOpenError(f"pdfplumber cannot open {pdf_path}: {exc}") from exc
    finally:
        if styled_doc is not None:
            styled_doc.close()
    return result
