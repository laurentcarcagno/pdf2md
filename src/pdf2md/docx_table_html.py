"""Word tables as HTML: spans, cell shading and run formatting (bold, italic, colours...)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pdf2md.table_html import HtmlCell, Run, render_table_html

from lxml import etree  # isort: skip

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}

_HIGHLIGHTS = {
    "yellow": "#ffff00", "green": "#00ff00", "cyan": "#00ffff", "magenta": "#ff00ff",
    "blue": "#0000ff", "red": "#ff0000", "darkBlue": "#000080", "darkCyan": "#008080",
    "darkGreen": "#008000", "darkMagenta": "#800080", "darkRed": "#800000",
    "darkYellow": "#808000", "darkGray": "#808080", "lightGray": "#c0c0c0",
    "black": "#000000", "white": "#ffffff",
}  # fmt: skip
_OFF_VALUES = ("0", "false", "off", "none")


def _w(tag: str) -> str:
    return f"{{{W}}}{tag}"


def _hex(value: str | None) -> str | None:
    """Return ``#rrggbb`` for a 6-digit hex value, None for ``auto`` or anything else."""
    if value and len(value) == 6 and all(c in "0123456789abcdefABCDEF" for c in value):
        return f"#{value.lower()}"
    return None


def _toggle(rpr: Any, tag: str) -> bool | None:
    element = rpr.find(f"w:{tag}", NS)
    if element is None:
        return None
    return (element.get(_w("val")) or "true").lower() not in _OFF_VALUES


@dataclass
class _Props:
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    superscript: bool = False
    subscript: bool = False
    color: str | None = None
    background: str | None = None

    def apply(self, rpr: Any) -> None:
        """Override the properties set by a ``w:rPr`` element."""
        for attribute, tag in (("bold", "b"), ("italic", "i"), ("underline", "u")):
            value = _toggle(rpr, tag)
            if value is not None:
                setattr(self, attribute, value)
        for tag in ("strike", "dstrike"):
            value = _toggle(rpr, tag)
            if value is not None:
                self.strike = value
        align = rpr.find("w:vertAlign", NS)
        if align is not None:
            kind = align.get(_w("val"))
            self.superscript, self.subscript = kind == "superscript", kind == "subscript"
        color = rpr.find("w:color", NS)
        if color is not None:
            self.color = _hex(color.get(_w("val")))
        highlight = rpr.find("w:highlight", NS)
        shading = rpr.find("w:shd", NS)
        if highlight is not None:
            self.background = _HIGHLIGHTS.get(highlight.get(_w("val")) or "")
        elif shading is not None:
            self.background = _hex(shading.get(_w("fill")))


class StyleMap:
    """The ``w:style`` elements of a document by id, to resolve inherited formatting."""

    def __init__(self, document: Any) -> None:
        try:
            elements = document.styles.element.findall("w:style", NS)
        except AttributeError:
            elements = []
        self.styles = {s.get(_w("styleId")): s for s in elements}

    def run_properties(self, style_id: str | None) -> list[Any]:
        """Return the ``w:rPr`` of a style and its ancestors, base style first."""
        chain: list[Any] = []
        seen: set[str] = set()
        while style_id and style_id not in seen and style_id in self.styles:
            seen.add(style_id)
            style = self.styles[style_id]
            rpr = style.find("w:rPr", NS)
            if rpr is not None:
                chain.append(rpr)
            based = style.find("w:basedOn", NS)
            style_id = based.get(_w("val")) if based is not None else None
        return chain[::-1]


def _run_text(run_el: Any) -> str:
    parts: list[str] = []
    for child in run_el:
        if child.tag == _w("t"):
            parts.append(child.text or "")
        elif child.tag == _w("tab"):
            parts.append(" ")
        elif child.tag in (_w("br"), _w("cr")):
            parts.append("\n")
        elif child.tag == _w("noBreakHyphen"):
            parts.append("-")
    return "".join(parts)


def _paragraph_runs(paragraph_el: Any, styles: StyleMap) -> list[Run]:
    paragraph_style = paragraph_el.find("w:pPr/w:pStyle", NS)
    base = styles.run_properties(
        paragraph_style.get(_w("val")) if paragraph_style is not None else None
    )
    runs: list[Run] = []
    for run_el in paragraph_el.iter(_w("r")):
        if any(a.tag == _w("del") for a in run_el.iterancestors()):
            continue
        text = _run_text(run_el)
        if not text:
            continue
        props = _Props()
        direct = run_el.find("w:rPr", NS)
        run_style = direct.find("w:rStyle", NS) if direct is not None else None
        sources = base + styles.run_properties(
            run_style.get(_w("val")) if run_style is not None else None
        )
        if direct is not None:
            sources.append(direct)
        for rpr in sources:
            props.apply(rpr)
        runs.append(Run(text=text, **vars(props)))
    return runs


def _cell_fill(cell_el: Any) -> str | None:
    shading = cell_el.find("w:tcPr/w:shd", NS)
    return _hex(shading.get(_w("fill"))) if shading is not None else None


def _span(cell_el: Any) -> int:
    grid = cell_el.find("w:tcPr/w:gridSpan", NS)
    return max(int(grid.get(_w("val")) or 1), 1) if grid is not None else 1


def _vmerge(cell_el: Any) -> str | None:
    """Return ``restart``, ``continue`` or None (not vertically merged)."""
    merge = cell_el.find("w:tcPr/w:vMerge", NS)
    if merge is None:
        return None
    return "restart" if merge.get(_w("val")) == "restart" else "continue"


def table_element_to_html(table_el: etree._Element, styles: StyleMap) -> str:
    """Return the HTML of a ``w:tbl`` element."""
    grid: list[list[tuple[Any, int, int]]] = []  # per row: (cell, first column, colspan)
    for row_el in table_el.findall("w:tr", NS):
        column = 0
        cells = []
        for cell_el in row_el.findall("w:tc", NS):
            span = _span(cell_el)
            cells.append((cell_el, column, span))
            column += span
        grid.append(cells)
    rows: list[list[HtmlCell]] = []
    for index, cells in enumerate(grid):
        html_cells: list[HtmlCell] = []
        for cell_el, column, span in cells:
            merge = _vmerge(cell_el)
            if merge == "continue":
                continue
            rowspan = 1
            if merge == "restart":
                for below in grid[index + 1 :]:
                    follower = next((c for c, col, _ in below if col == column), None)
                    if follower is None or _vmerge(follower) != "continue":
                        break
                    rowspan += 1
            paragraphs = [_paragraph_runs(p, styles) for p in cell_el.iter(_w("p"))]
            html_cells.append(
                HtmlCell(
                    paragraphs=paragraphs,
                    fill=_cell_fill(cell_el),
                    colspan=span,
                    rowspan=rowspan,
                )
            )
        rows.append(html_cells)
    return render_table_html(rows)
