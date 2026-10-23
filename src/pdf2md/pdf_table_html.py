"""Tables of a PDF page as HTML: cell spans, background fills and styled text.

pdfplumber provides the grid (cell boxes); PyMuPDF provides what pdfplumber does not give:
the font style and colour of each span and the vector drawings, from which fills (filled
rectangles behind a cell) and underlines/strikethroughs (thin horizontal lines on the text) are
read.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pymupdf

from pdf2md.table_html import HtmlCell, Run, hex_color, render_table_html
from pdf2md.text import fix_text

logger = logging.getLogger(__name__)

Box = tuple[float, float, float, float]  # x0, top, x1, bottom

_GRID_TOLERANCE = 2.0
_INSET = 1.0  # shrink the cell clip so that neighbouring text is not picked up
_BOLD_WORDS = ("bold", "black", "heavy", "semibold", "demi")
_ITALIC_WORDS = ("italic", "oblique")
_FLAG_SUPERSCRIPT = 1
_FLAG_ITALIC = 2
_FLAG_BOLD = 16
_MAX_LINE_THICKNESS = 1.5
_WHITE = 0.97
_MIN_FILL_COVERAGE = 0.5
_LINE_GAP_RATIO = 0.6  # gap between two lines / font size above which a paragraph starts


@dataclass(frozen=True)
class _Segment:
    """A thin horizontal stroke: underline or strikethrough candidate."""

    x0: float
    x1: float
    y: float


@dataclass(frozen=True)
class _Fill:
    box: Box
    color: str


class PageDrawings:
    """Fills and horizontal strokes of one page, read once from PyMuPDF."""

    def __init__(self, page: pymupdf.Page) -> None:
        self.fills: list[_Fill] = []
        self.segments: list[_Segment] = []
        try:
            drawings = page.get_drawings()
        except (RuntimeError, ValueError) as exc:
            logger.warning("page %d: cannot read drawings: %s", page.number + 1, exc)
            return
        for drawing in drawings:
            self._read(drawing)

    def _read(self, drawing: dict[str, Any]) -> None:
        fill = drawing.get("fill")
        rect = drawing.get("rect")
        if fill is not None and rect is not None and (drawing.get("fill_opacity") or 1) > 0:
            if len(fill) == 3 and min(fill) < _WHITE:
                self.fills.append(
                    _Fill((rect.x0, rect.y0, rect.x1, rect.y1), hex_color(*fill)),
                )
        for item in drawing.get("items", []):
            if item[0] == "l":
                start, end = item[1], item[2]
                if abs(start.y - end.y) <= 0.8 and abs(start.x - end.x) > 2:
                    self.segments.append(
                        _Segment(min(start.x, end.x), max(start.x, end.x), (start.y + end.y) / 2)
                    )
            elif item[0] == "re":
                rect_item = item[1]
                if rect_item.height <= _MAX_LINE_THICKNESS and rect_item.width > 2:
                    self.segments.append(
                        _Segment(rect_item.x0, rect_item.x1, (rect_item.y0 + rect_item.y1) / 2)
                    )

    def fill_of(self, box: Box) -> str | None:
        """Return the colour of the smallest fill covering the cell centre and half its area."""
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        cell_area = max((box[2] - box[0]) * (box[3] - box[1]), 1.0)
        best: tuple[float, str] | None = None
        for fill in self.fills:
            x0, top, x1, bottom = fill.box
            if not (x0 <= cx <= x1 and top <= cy <= bottom):
                continue
            area = (x1 - x0) * (bottom - top)
            if area < _MIN_FILL_COVERAGE * cell_area:
                continue
            if best is None or area <= best[0]:
                best = (area, fill.color)  # later drawings are on top: keep the last smallest
        return best[1] if best else None

    def _crosses(self, span_box: Box, y_low: float, y_high: float, cell: Box) -> bool:
        """Return True if a stroke covers the span horizontally between two heights.

        A stroke as wide as the whole cell is a table border, not an underline.
        """
        width = span_box[2] - span_box[0]
        cell_width = cell[2] - cell[0]
        for segment in self.segments:
            length = segment.x1 - segment.x0
            if not (y_low <= segment.y <= y_high) or length >= cell_width - _GRID_TOLERANCE:
                continue
            overlap = min(segment.x1, span_box[2]) - max(segment.x0, span_box[0])
            if overlap >= 0.6 * width and length <= width + 4:
                return True
        return False

    def underlined(self, span: dict[str, Any], cell: Box) -> bool:
        """Return True if a thin line runs just under the baseline of the span."""
        baseline, size = span["origin"][1], span["size"]
        return self._crosses(
            tuple(span["bbox"]), baseline + 0.05 * size, baseline + 0.3 * size, cell
        )

    def struck(self, span: dict[str, Any], cell: Box) -> bool:
        """Return True if a thin line runs through the middle of the lower-case letters."""
        baseline, size = span["origin"][1], span["size"]
        return self._crosses(
            tuple(span["bbox"]), baseline - 0.45 * size, baseline - 0.15 * size, cell
        )


def _text_color(value: int) -> str | None:
    return None if value == 0 else f"#{value & 0xFFFFFF:06x}"


def _span_run(span: dict[str, Any], drawings: PageDrawings, cell: Box) -> Run:
    font = str(span["font"]).lower()
    flags = int(span["flags"])
    return Run(
        text=fix_text(str(span["text"])),
        bold=bool(flags & _FLAG_BOLD) or any(word in font for word in _BOLD_WORDS),
        italic=bool(flags & _FLAG_ITALIC) or any(word in font for word in _ITALIC_WORDS),
        underline=drawings.underlined(span, cell),
        strike=drawings.struck(span, cell),
        superscript=bool(flags & _FLAG_SUPERSCRIPT),
        color=_text_color(int(span["color"])),
    )


def cell_paragraphs(page: pymupdf.Page, box: Box, drawings: PageDrawings) -> list[list[Run]]:
    """Return the styled text of a cell: one list of runs per paragraph."""
    clip = pymupdf.Rect(box[0] + _INSET, box[1] + _INSET, box[2] - _INSET, box[3] - _INSET)
    if clip.is_empty:
        return []
    data = page.get_text("dict", clip=clip, flags=pymupdf.TEXTFLAGS_TEXT)
    paragraphs: list[list[Run]] = []
    previous_bottom: float | None = None
    for block in data["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"]]
            if not spans:
                continue
            size = max(float(s["size"]) for s in spans)
            top, bottom = line["bbox"][1], line["bbox"][3]
            same_paragraph = (
                previous_bottom is not None
                and paragraphs
                and top - previous_bottom <= _LINE_GAP_RATIO * size
            )
            runs = [_span_run(s, drawings, box) for s in spans]
            if same_paragraph:
                paragraphs[-1].append(Run(" "))
                paragraphs[-1].extend(runs)
            else:
                paragraphs.append(runs)
            previous_bottom = bottom
    return paragraphs


def _grid_lines(values: list[float]) -> list[float]:
    """Cluster coordinates closer than the tolerance into grid lines."""
    lines: list[float] = []
    for value in sorted(values):
        if not lines or value - lines[-1] > _GRID_TOLERANCE:
            lines.append(value)
    return lines


def _index(lines: list[float], value: float) -> int:
    return min(range(len(lines)), key=lambda i: abs(lines[i] - value))


def table_to_html(page: pymupdf.Page, cell_boxes: list[Box], drawings: PageDrawings) -> str | None:
    """Return the HTML of the table made of ``cell_boxes`` (None if there is no cell)."""
    if not cell_boxes:
        return None
    xs = _grid_lines([v for box in cell_boxes for v in (box[0], box[2])])
    ys = _grid_lines([v for box in cell_boxes for v in (box[1], box[3])])
    placed: dict[tuple[int, int], tuple[Box, int, int]] = {}
    covered: set[tuple[int, int]] = set()
    for box in cell_boxes:
        col0, col1 = _index(xs, box[0]), _index(xs, box[2])
        row0, row1 = _index(ys, box[1]), _index(ys, box[3])
        if col1 <= col0 or row1 <= row0 or (row0, col0) in placed:
            continue
        placed[(row0, col0)] = (box, col1 - col0, row1 - row0)
        covered.update((r, c) for r in range(row0, row1) for c in range(col0, col1))
    rows: list[list[HtmlCell]] = []
    for row in range(len(ys) - 1):
        cells: list[HtmlCell] = []
        for col in range(len(xs) - 1):
            if (row, col) in placed:
                box, colspan, rowspan = placed[(row, col)]
                cells.append(
                    HtmlCell(
                        paragraphs=cell_paragraphs(page, box, drawings),
                        fill=drawings.fill_of(box),
                        colspan=colspan,
                        rowspan=rowspan,
                    )
                )
            elif (row, col) not in covered:
                cells.append(HtmlCell())
        rows.append(cells)
    return render_table_html(rows)
