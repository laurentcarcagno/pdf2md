"""Pass 2c: turn marked lines and tables into headings, paragraphs, lists, code and tables."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import cast

from pdf2md.models import (
    Block,
    BlockKind,
    Coordinates,
    Line,
    PageContent,
    Settings,
    SourcePass,
    Table,
)
from pdf2md.toc import is_entry_line

_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+\S")
_LIST_MARKER = re.compile(r"^(?:\d{1,3}[.)]|\([a-z0-9]{1,3}\)|[-•–▪●◦])\s+\S")
# Labels that always open a new paragraph: requirement letters ("a. ...") and notes.
_PARAGRAPH_LABEL = re.compile(r"^(?:[a-z][.)]|(?:NOTES?|EXAMPLES?)(?:\s+\d+)?)\s+\S")
_SUBITEM_INDENT_PT = 10.0
_SUBITEM_SPACES = "   "
_PIPE_ROW = re.compile(r"\S\s\|\s\S")
_MAX_LEVEL = 6
_LIST_ITEM_GAP_RATIO = 2.6


def body_font_size(pages: dict[int, PageContent]) -> float:
    """Return the most frequent font size (weighted by characters), ignoring monospace."""
    counter: Counter[float] = Counter()
    for page in pages.values():
        for line in page.lines:
            if not line.is_mono and line.element is None:
                counter[line.size] += len(line.text)
    return counter.most_common(1)[0][0] if counter else 0.0


def heading_sizes(
    pages: dict[int, PageContent], body_size: float, settings: Settings
) -> list[float]:
    """Return the distinct font sizes larger than the body text, largest first."""
    sizes = {
        line.size
        for page in pages.values()
        for line in page.lines
        if not line.is_mono
        and line.element is None
        and line.size > body_size + settings.heading_size_delta
    }
    return sorted(sizes, reverse=True)


def _is_heading(line: Line, body_size: float, settings: Settings) -> bool:
    return not line.is_mono and line.size > body_size + settings.heading_size_delta


def _heading_level(line: Line, sizes: list[float]) -> int:
    match = _NUMBERED.match(line.text)
    if match:
        return min(match.group(1).count(".") + 1, _MAX_LEVEL)
    return min(sizes.index(line.size) + 1, _MAX_LEVEL) if line.size in sizes else 1


def join_lines(lines: list[str]) -> str:
    """Join visual lines into one paragraph.

    A line ending with a hyphen is glued to the next one *keeping* the hyphen: renderers wrap
    compounds ("pas-à-pas") at hyphens, and dropping it would corrupt them.
    """
    text = ""
    for fragment in lines:
        if not text:
            text = fragment
        elif text.endswith("-"):
            text += fragment
        else:
            text = f"{text} {fragment}"
    return text


def _union(lines: list[Line]) -> Coordinates:
    box = lines[0].coords
    for line in lines[1:]:
        box = box.union(line.coords)
    return box


def _mark_toc(lines: list[Line], settings: Settings) -> set[int]:
    """Return indexes of entry-shaped lines when the page has enough of them to be an index."""
    hits = [i for i, line in enumerate(lines) if is_entry_line(line.text) and line.element is None]
    return set(hits) if len(hits) >= settings.toc_min_lines else set()


def _list_items(group: list[Line]) -> list[str]:
    """Split a list block into items; items indented under the first one get a 3-space prefix.

    The prefix is kept in the block text so the renderer can nest them (Markdown needs the
    indentation of the parent marker).
    """
    items: list[tuple[int, list[str]]] = []
    base = group[0].coords.x0
    for line in group:
        if _LIST_MARKER.match(line.text) or not items:
            depth = 1 if line.coords.x0 > base + _SUBITEM_INDENT_PT else 0
            items.append((depth, [line.text]))
        else:
            items[-1][1].append(line.text)
    return [_SUBITEM_SPACES * depth + join_lines(parts) for depth, parts in items]


def _flush(group: list[Line], kind: str, level: int, source: SourcePass) -> Block:
    if kind in ("code", "toc", "table"):
        text = "\n".join(line.text for line in group)
    elif kind == "list":
        text = "\n".join(_list_items(group))
    else:
        text = join_lines([line.text for line in group])
    rows = [[c.strip() for c in ln.text.split("|")] for ln in group] if kind == "table" else []
    return Block(
        page_number=group[0].page_number,
        kind=cast(BlockKind, kind),
        text=text,
        level=level,
        rows=rows,
        coords=_union(group),
        source_pass=source,
        element=group[0].element,
    )


def _table_block(page_number: int, table: Table) -> Block:
    return Block(
        page_number=page_number,
        kind="table",
        text=" | ".join(table.rows[0]),
        rows=table.rows,
        coords=table.coords,
        source_pass="pass2_pdfplumber",
        element=table.element,
        html=table.html,
    )


@dataclass
class _GroupState:
    kind: BlockKind | str = ""
    level: int = 0


def build_blocks(
    page: PageContent,
    body_size: float,
    sizes: list[float],
    settings: Settings,
    source: SourcePass = "pass2_pdfplumber",
) -> list[Block]:
    """Group the lines of one page into blocks, ordered top to bottom."""
    lines = sorted(page.lines, key=lambda ln: (ln.coords.top, ln.coords.x0))
    toc_lines = _mark_toc(lines, settings)
    blocks: list[Block] = []
    group: list[Line] = []
    state = _GroupState()
    previous: Line | None = None

    def close() -> None:
        if group:
            blocks.append(_flush(group, state.kind, state.level, source))
        group.clear()
        state.kind = ""

    def line_kind(index: int, line: Line) -> str:
        if line.element is not None:
            return "paragraph"
        if index in toc_lines:
            return "toc"
        if line.is_mono:
            return "code"
        if _PIPE_ROW.search(line.text):
            return "table"
        if _is_heading(line, body_size, settings):
            return "heading"
        return "list" if _LIST_MARKER.match(line.text) else "paragraph"

    tables = sorted(page.tables, key=lambda t: t.coords.top)
    table_index = 0
    for index, line in enumerate(lines):
        while table_index < len(tables) and tables[table_index].coords.top <= line.coords.top:
            close()
            table = tables[table_index]
            blocks.append(_table_block(page.page_number, table))
            table_index += 1
            previous = None
        kind = line_kind(index, line)
        level = _heading_level(line, sizes) if kind == "heading" else 0
        pitch_ratio = (
            (line.coords.top - previous.coords.top) / max(line.size, previous.size, 1.0)
            if previous
            else 0.0
        )
        same_element = bool(group) and group[0].element == line.element
        if not group:
            continues = False
        elif _PARAGRAPH_LABEL.match(line.text) and line.element is None:
            continues = False
        elif state.kind == "list":
            is_item = kind == "list"
            is_continuation = kind == "paragraph" and line.coords.x0 > group[0].coords.x0 + 1
            continues = same_element and (
                (is_item and pitch_ratio <= _LIST_ITEM_GAP_RATIO)
                or (is_continuation and pitch_ratio <= settings.paragraph_break_ratio)
            )
        else:
            continues = (
                same_element
                and kind == state.kind
                and level == state.level
                and (
                    kind in ("toc", "code", "table")
                    or pitch_ratio <= settings.paragraph_break_ratio
                )
            )
        if not continues:
            close()
            state.kind, state.level = kind, level
        group.append(line)
        previous = line
    close()
    for table in tables[table_index:]:
        blocks.append(_table_block(page.page_number, table))
    return blocks
