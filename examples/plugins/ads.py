r"""Plugin ADS: turn the small text-strip images of requirements into a marked line.

pdf2md extracts text printed as a thin image on the same line as a requirement title and
gives it the OCR text as alt text. This plugin replaces such an image, alone in its
paragraph, by that text in bold behind an invisible anchor named after the requirement ID
(the text before " / "), and deletes the image file when nothing else references it:

    ![HAR SSOW-1 / Inputs](assets/p0015_01.png)
        ->   <a id="har-ssow-1"></a>**HAR SSOW-1 / Inputs**

Markdown has no "requirement" notion; bold plus the anchor renders everywhere, is not a
heading, and is matched by the regex ``^<a id="[^"]+"></a>\*\*(.+)\*\*$``.

Register and use it:

    pdf2md --register-plugin "ADS" examples/plugins/ads.py
    pdf2md doc.pdf -o out --post-process "ADS"
"""

from __future__ import annotations

import logging
import re
import struct
from pathlib import Path

from pdf2md.plugins import PluginContext

logger = logging.getLogger("pdf2md.plugins.ads")

MAX_STRIP_HEIGHT_PX = 60
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_GENERIC_ALT = re.compile(r"^Image \(page \d+\)$")
_ALONE = re.compile(r"^!\[(?P<alt>[^\]\n]*)\]\((?P<target>[^)\s]+)\)$")
_ANY_IMAGE = re.compile(r"!\[[^\]\n]*\]\((?P<target>[^)\s]+)\)")


def _png_height(path: Path) -> int | None:
    """Return the pixel height of a PNG file, None if it is not a readable PNG."""
    try:
        with path.open("rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    if len(header) < 24 or not header.startswith(_PNG_SIGNATURE):
        return None
    return int(struct.unpack(">I", header[20:24])[0])


def _marker(text: str) -> str:
    """Bold requirement line preceded by an anchor derived from its ID."""
    text = text.strip()
    identifier = re.sub(r"[^a-z0-9]+", "-", text.split(" / ")[0].lower()).strip("-")
    anchor = f'<a id="{identifier}"></a>' if identifier else ""
    return f"{anchor}**{text}**"


def _is_title_strip(alt: str, image: Path) -> bool:
    height = _png_height(image)
    return (
        bool(alt.strip())
        and not _GENERIC_ALT.match(alt.strip())
        and height is not None
        and height <= MAX_STRIP_HEIGHT_PX
    )


def process(markdown: str, context: PluginContext) -> str:
    """Return the Markdown with the title strips replaced by their text."""
    base = context.md_path.parent
    replaced: list[Path] = []
    paragraphs = markdown.split("\n\n")
    for index, paragraph in enumerate(paragraphs):
        match = _ALONE.match(paragraph.strip())
        if match is None:
            continue
        image = base / match["target"]
        if _is_title_strip(match["alt"], image):
            paragraphs[index] = _marker(match["alt"])
            replaced.append(image)
    result = "\n\n".join(paragraphs)
    still_used = {base / found["target"] for found in _ANY_IMAGE.finditer(result)}
    for image in set(replaced) - still_used:
        try:
            image.unlink()
        except OSError as exc:
            logger.warning("cannot delete %s: %s", image, exc)
    logger.info("%s: %d requirement image(s) converted", context.plugin_name, len(replaced))
    return result
