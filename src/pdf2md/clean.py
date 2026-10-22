"""Pass 2b: dynamic detection of recurring headers and footers (refinedoc-like)."""

from __future__ import annotations

import logging
import math
import re
from collections import defaultdict

from pdf2md.models import ElementKind, Line, PageContent, Settings, Table

logger = logging.getLogger(__name__)

_DIGITS = re.compile(r"\d+")
_SPACES = re.compile(r"\s+")


def normalize_key(text: str) -> str:
    """Normalise a line so that page numbers and dates collapse to the same key."""
    return _SPACES.sub(" ", _DIGITS.sub("#", text.lower())).strip()


def _band(line: Line, page: PageContent, settings: Settings) -> tuple[ElementKind, bool] | None:
    """Return ``(header|footer, in_edge_band)`` for a line in an outer band, else None.

    Two bands: the narrow edge band (12 % of the page), where lines may differ by digits
    ("Page 3" / "Page 4"), and a wider search band (25 %) for tall headers, where the text
    must be identical on every page - a heading such as "2. Section 2" at the same height on
    each page must not be taken for a header.
    """
    edge = settings.margin_band_ratio * page.height
    search = settings.margin_search_ratio * page.height
    from_top, from_bottom = line.coords.bottom, page.height - line.coords.top
    if from_top <= edge:
        return "header", True
    if from_bottom <= edge:
        return "footer", True
    if from_top <= search:
        return "header", False
    if from_bottom <= search:
        return "footer", False
    return None


def _key(text: str, in_edge_band: bool) -> str:
    """Normalised text: digits collapse only in the edge band (page numbers, dates)."""
    return normalize_key(text) if in_edge_band else _SPACES.sub(" ", text.lower()).strip()


def _best_window(tops: list[tuple[int, float]], tolerance: float) -> tuple[float, int]:
    """Return ``(position, pages)`` of the densest group of tops within ``tolerance`` points."""
    ordered = sorted(tops, key=lambda item: item[1])
    best = (ordered[0][1], 0)
    for start, (_, first) in enumerate(ordered):
        group = {page for page, top in ordered[start:] if top - first <= 2 * tolerance}
        if len(group) > best[1]:
            best = (first + tolerance, len(group))
    return best


def find_recurring_margin_keys(
    pages: dict[int, PageContent], settings: Settings
) -> dict[tuple[ElementKind, str], float]:
    """Return ``(band, key) -> vertical position`` of the lines repeated at one position.

    A line is a header/footer if its normalised text appears on enough pages at the same
    height (within ``Settings.margin_tolerance``); body text repeated by chance (table
    headings, captions) moves from page to page and is left alone.
    """
    if len(pages) < 2:
        return {}
    hits: dict[tuple[ElementKind, str], list[tuple[int, float]]] = defaultdict(list)
    for number, page in pages.items():
        for line in page.lines:
            found = _band(line, page, settings)
            if found:
                hits[(found[0], _key(line.text, found[1]))].append((number, line.coords.top))
    needed = max(2, math.ceil(settings.recurrence_ratio * len(pages)))
    recurring: dict[tuple[ElementKind, str], float] = {}
    for key, tops in hits.items():
        position, count = _best_window(tops, settings.margin_tolerance)
        if count >= needed:
            recurring[key] = position
    return recurring


def _table_band(table: Table, page: PageContent, settings: Settings) -> ElementKind | None:
    """Return ``header``/``footer`` if the whole table lies in the outer search band."""
    search = settings.margin_search_ratio * page.height
    if table.coords.bottom <= search:
        return "header"
    if page.height - table.coords.top <= search:
        return "footer"
    return None


def _same_box(a: Table, b: Table, tolerance: float) -> bool:
    return all(
        abs(x - y) <= tolerance
        for x, y in (
            (a.coords.x0, b.coords.x0),
            (a.coords.x1, b.coords.x1),
            (a.coords.top, b.coords.top),
            (a.coords.bottom, b.coords.bottom),
        )
    )


def _mark_tables(pages: dict[int, PageContent], settings: Settings) -> dict[int, list[Table]]:
    """Tag the tables repeated at the same place of the outer bands (header/footer boxes).

    Their text is ignored: a box holding "Page 3" then "Page 4" is still the same box.
    """
    needed = max(2, math.ceil(settings.recurrence_ratio * len(pages)))
    banded = [
        (number, table, band)
        for number, page in pages.items()
        for table in page.tables
        if (band := _table_band(table, page, settings))
    ]
    tagged: dict[int, list[Table]] = {}
    for number, page in pages.items():
        tables = []
        for table in page.tables:
            band = _table_band(table, page, settings)
            if band:
                same = {
                    n
                    for n, other, other_band in banded
                    if other_band == band and _same_box(table, other, settings.margin_tolerance)
                }
                if len(same) >= needed:
                    table = table.model_copy(update={"element": band})
            tables.append(table)
        tagged[number] = tables
    return tagged


def mark_margins(pages: dict[int, PageContent], settings: Settings) -> dict[int, PageContent]:
    """Return copies of ``pages`` whose recurring header/footer lines carry ``element``.

    Lines are tagged rather than dropped so that the caller can keep them on request. Once a
    text is known to recur at a fixed position, its occurrences in the outer bands are tagged
    wherever they sit: the first page or an odd page often shifts the header by a few lines.
    """
    recurring = find_recurring_margin_keys(pages, settings)
    logger.info("detected %d recurring header/footer patterns", len(recurring))
    tables = _mark_tables(pages, settings) if len(pages) >= 2 else {}
    marked: dict[int, PageContent] = {}
    for number, page in pages.items():
        lines = []
        for line in page.lines:
            found = _band(line, page, settings)
            if found and (found[0], _key(line.text, found[1])) in recurring:
                line = line.model_copy(update={"element": found[0]})
            lines.append(line)
        marked[number] = page.model_copy(
            update={"lines": lines, "tables": tables.get(number, page.tables)}
        )
    return marked
