"""Keep or remove document elements (headers, footers, tables of contents, ...)."""

from __future__ import annotations

from collections.abc import Collection

from pdf2md.models import Block, ElementKind


def apply_keep(blocks: list[Block], keep: Collection[ElementKind]) -> list[Block]:
    """Drop every block tagged with an element that is not in ``keep``."""
    return [b for b in blocks if b.element is None or b.element in keep]
