"""Page selection: parse specs such as ``1-5,10-20,30-`` into page numbers."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

PageRange = tuple[int, int | None]

_PART = re.compile(r"^(\d*)\s*(-)?\s*(\d*)$")


def parse_page_spec(spec: str) -> list[PageRange]:
    """Parse a 1-based page selection into ``(first, last)`` ranges (``last`` None = to the end).

    Accepted forms, optionally wrapped in brackets and mixed with commas: ``7`` (one page),
    ``10-`` (page 10 to the end), ``-10`` (start to page 10), ``15-18`` (inclusive range).

    Raises:
        ValueError: the spec is empty, malformed, or a range is reversed or starts at 0.
    """
    cleaned = spec.strip().strip("[]").strip()
    if not cleaned:
        raise ValueError("empty page selection")
    ranges: list[PageRange] = []
    for part in cleaned.split(","):
        match = _PART.match(part.strip())
        if not match or not part.strip():
            raise ValueError(f"invalid page range {part.strip()!r}")
        first_text, dash, last_text = match.groups()
        last: int | None
        if not dash:
            if not first_text:
                raise ValueError(f"invalid page range {part.strip()!r}")
            first, last = int(first_text), int(first_text)
        else:
            if not first_text and not last_text:
                raise ValueError(f"invalid page range {part.strip()!r}")
            first = int(first_text) if first_text else 1
            last = int(last_text) if last_text else None
        if first < 1 or (last is not None and last < first):
            raise ValueError(
                f"invalid page range {part.strip()!r}: pages start at 1, first <= last"
            )
        ranges.append((first, last))
    return ranges


def resolve_pages(spec: str, page_count: int) -> list[int]:
    """Return the sorted, de-duplicated page numbers selected by ``spec`` in a document.

    Pages beyond the end of the document are ignored with a warning.

    Raises:
        ValueError: the spec is invalid or selects no page of this document.
    """
    selected: set[int] = set()
    for first, last in parse_page_spec(spec):
        if first > page_count:
            logger.warning("page range starting at %d is beyond the %d pages", first, page_count)
            continue
        selected.update(range(first, min(last or page_count, page_count) + 1))
    if not selected:
        raise ValueError(f"page selection {spec!r} matches no page of this {page_count}-page PDF")
    return sorted(selected)
