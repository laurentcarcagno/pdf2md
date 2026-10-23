"""Text repair applied to everything extracted from a PDF."""

from __future__ import annotations

import re

import ftfy

from pdf2md.models import Block

_CONFIG = ftfy.TextFixerConfig(
    uncurl_quotes=False, fix_latin_ligatures=True, normalization="NFC", fix_line_breaks=False
)


# Word bullets are Symbol/Wingdings glyphs stored in the Private Use Area (U+F0xx), which render
# as boxes or nothing outside the original font. Map the usual ones to real Unicode bullets.
_PUA_BULLETS = {
    chr(0xF0B7): "•",  # Symbol bullet
    chr(0xF0A7): "▪",  # Wingdings small square
    chr(0xF0D8): "➢",  # Wingdings arrowhead
    chr(0xF0FC): "✓",  # Wingdings check mark
    chr(0xF076): "❖",  # Wingdings diamond
    chr(0xF0A8): "◦",  # Wingdings hollow square
    chr(0xF06E): "■",  # Wingdings black square
    chr(0xF071): "❑",  # Wingdings box
}
_PUA_RANGE = f"{chr(0xE000)}-{chr(0xF8FF)}"
_PUA_CHAR = re.compile(f"[{_PUA_RANGE}]")
_LEADING_PUA = re.compile(r"(?m)^([ \t]*)[" + _PUA_RANGE + r"](?=\s)")


def _replace_pua_bullets(text: str) -> str:
    """Replace known PUA bullets; any other PUA label opening a line becomes a bullet."""
    for glyph, bullet in _PUA_BULLETS.items():
        text = text.replace(glyph, bullet)
    text = _LEADING_PUA.sub(lambda match: f"{match.group(1)}•", text)
    return _PUA_CHAR.sub("", text)


def fix_text(text: str) -> str:
    """Repair mojibake, ligatures and PUA bullets, leaving quotes, spaces and breaks alone."""
    return _replace_pua_bullets(ftfy.fix_text(text, _CONFIG))


def fix_blocks(blocks: list[Block]) -> list[Block]:
    """Return copies of ``blocks`` with repaired text and table cells."""
    fixed = []
    for block in blocks:
        rows = [[fix_text(cell) for cell in row] for row in block.rows]
        fixed.append(block.model_copy(update={"text": fix_text(block.text), "rows": rows}))
    return fixed
