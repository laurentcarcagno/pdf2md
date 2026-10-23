"""Merge the tables that continue on the next page (HTML tables only)."""

from __future__ import annotations

import html
import logging
import re

from pdf2md.models import Block, Settings

logger = logging.getLogger(__name__)

_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")


def _split(table_html: str) -> tuple[str, list[str], str]:
    """Return the opening tag, the ``<tr>`` lines and the closing tag of an HTML table."""
    lines = table_html.split("\n")
    return lines[0], lines[1:-1], lines[-1]


def _row_text(row: str) -> str:
    """Return the normalised text of a ``<tr>`` (tags removed), to compare rows."""
    return _SPACES.sub(" ", html.unescape(_TAGS.sub(" ", row))).strip().casefold()


def _repeated_header_rows(first: list[str], second: list[str]) -> int:
    """Return how many rows at the start of ``second`` repeat the start of ``first``.

    Blank rows never count as a title: a repeated header must carry some text.
    """
    count = 0
    for head, candidate in zip(first, second, strict=False):
        text = _row_text(candidate)
        if not text or text != _row_text(head):
            break
        count += 1
    return count


def _same_grid(first: Block, second: Block, tolerance: float) -> bool:
    return len(first.grid) == len(second.grid) > 1 and all(
        abs(a - b) <= tolerance for a, b in zip(first.grid, second.grid, strict=True)
    )


def _is_html_table(block: Block) -> bool:
    return block.kind == "table" and bool(block.html) and block.element is None


def merge_split_tables(blocks: list[Block], settings: Settings) -> list[Block]:
    """Return ``blocks`` where tables continued on the next page are one table.

    Table B continues table A when the only blocks between them are headers/footers, B is on
    the page after the last page of A, and both have the same column borders (within
    ``Settings.table_merge_tolerance``). The title rows B repeats from the top of A are dropped,
    so a title appears once, at the start. The merged block keeps the page and coordinates of
    its first part.
    """
    merged: list[Block] = []
    last_page: dict[int, int] = {}  # id of a merged table block -> page of its last part
    for block in blocks:
        if _is_html_table(block):
            previous = next((b for b in reversed(merged) if b.element is None), None)
            if (
                previous is not None
                and _is_html_table(previous)
                and block.page_number == last_page.get(id(previous), previous.page_number) + 1
                and _same_grid(previous, block, settings.table_merge_tolerance)
            ):
                _append(previous, block)
                last_page[id(previous)] = block.page_number
                continue
        merged.append(block.model_copy())
    count = len(blocks) - len(merged)
    if count:
        logger.info("merged %d table part(s) split across pages", count)
    return merged


def _append(first: Block, second: Block) -> None:
    """Add the rows of ``second`` to ``first`` (in place), without the repeated title rows."""
    opening, rows, closing = _split(first.html or "")
    _, more_rows, _ = _split(second.html or "")
    skipped = _repeated_header_rows(rows, more_rows)
    first.html = "\n".join([opening, *rows, *more_rows[skipped:], closing])
    text_skipped = 0
    for head, candidate in zip(first.rows, second.rows, strict=False):
        if candidate != head:
            break
        text_skipped += 1
    first.rows = [*first.rows, *second.rows[text_skipped:]]
    logger.debug("table of page %d continues on page %d", first.page_number, second.page_number)
