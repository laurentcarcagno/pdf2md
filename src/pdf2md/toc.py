"""Detection of tables of contents, of figures and of tables, on any engine's blocks."""

from __future__ import annotations

import re

from pdf2md.models import Block, ElementKind

_LEADERS = re.compile(r"\s*(?:\.{3,}|…+|(?:\.\s){3,})\s*")
_TRAILING_PAGE = re.compile(r"(?:\s|\.)(\d{1,4}|[ivxlcdm]{1,6})$", re.IGNORECASE)
_SECTION_PREFIX = re.compile(r"^\d+(?:\.\d+)*\.?\s+\S")
_CAPTION_PREFIX = re.compile(r"^(?:figure|fig\.|table|tableau|annex|annexe)\b", re.IGNORECASE)
_EMPTY_NOTICE = re.compile(
    r"^no (?:table of )?(?:figures|tables|entries)\b.*found|^aucune entrée", re.I
)
_NUMBERING = re.compile(r"^(?:\d+(?:\.\d+)*\.?|[A-Z]\.)\s+")

_TITLES: dict[ElementKind, re.Pattern[str]] = {
    "toc": re.compile(
        r"^(?:table of contents?|contents?|table des mati[èe]res|sommaire)$", re.IGNORECASE
    ),
    "list_of_figures": re.compile(
        r"^(?:list of figures|table of figures|liste des figures|table des figures"
        r"|table des illustrations)$",
        re.IGNORECASE,
    ),
    "list_of_tables": re.compile(
        r"^(?:list of tables|table of tables|liste des tableaux|table des tableaux)$",
        re.IGNORECASE,
    ),
}
_MAX_TITLE_LENGTH = 60


def strip_leaders(text: str) -> str:
    """Replace dot leaders (``....``) by a single space."""
    return _LEADERS.sub(" ", text).strip()


def is_entry_line(text: str) -> bool:
    """Return True for a line shaped like ``<label> ..... <page>``.

    A trailing page number alone is not enough (body text can end with a number): the line
    must also have dot leaders, a section number prefix or a Figure/Table caption prefix.
    """
    text = text.strip()
    if not _TRAILING_PAGE.search(text):
        return False
    return bool(_LEADERS.search(text) or _SECTION_PREFIX.match(text) or _CAPTION_PREFIX.match(text))


def _is_entry_block(block: Block) -> bool:
    if block.kind == "toc":
        return True
    if block.kind not in ("paragraph", "list", "heading"):
        return False
    lines = [ln for ln in block.text.splitlines() if ln.strip()]
    return bool(lines) and (
        all(is_entry_line(ln) for ln in lines) or bool(_EMPTY_NOTICE.match(block.text.strip()))
    )


def _title_kind(block: Block) -> ElementKind | None:
    if block.kind not in ("heading", "paragraph") or len(block.text) > _MAX_TITLE_LENGTH:
        return None
    bare = _NUMBERING.sub("", block.text.strip()).strip(" :")
    for kind, pattern in _TITLES.items():
        if pattern.match(bare):
            return kind
    return None


def mark_contents(blocks: list[Block]) -> list[Block]:
    """Return blocks with ``element`` set on titles and entries of TOC/LOF/LOT sections.

    A section starts at a title block ("Table of contents", "Liste des figures", ...) and runs
    over the following blocks, across pages, while they look like index entries. Untitled
    runs of entry lines found by the geometric pass (``kind == "toc"``) are marked as TOC.
    """
    marked = [block.model_copy() for block in blocks]
    current: ElementKind | None = None
    for block in marked:
        if block.element in ("header", "footer"):
            continue
        if block.element is not None:  # already tagged by the reader (Word TOC styles/fields)
            current = block.element
            continue
        title = _title_kind(block)
        if title is not None:
            current = title
            block.element = title
        elif current is not None and _is_entry_block(block):
            block.element = current
        else:
            current = None
            if block.kind == "toc":
                block.element = "toc"
    return marked
