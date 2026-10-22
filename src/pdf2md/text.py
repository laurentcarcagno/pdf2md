"""Text repair applied to everything extracted from a PDF."""

from __future__ import annotations

import ftfy

from pdf2md.models import Block

_CONFIG = ftfy.TextFixerConfig(
    uncurl_quotes=False, fix_latin_ligatures=True, normalization="NFC", fix_line_breaks=False
)


def fix_text(text: str) -> str:
    """Repair mojibake and ligatures without touching quotes, spaces or line breaks."""
    return ftfy.fix_text(text, _CONFIG)


def fix_blocks(blocks: list[Block]) -> list[Block]:
    """Return copies of ``blocks`` with repaired text and table cells."""
    fixed = []
    for block in blocks:
        rows = [[fix_text(cell) for cell in row] for row in block.rows]
        fixed.append(block.model_copy(update={"text": fix_text(block.text), "rows": rows}))
    return fixed
