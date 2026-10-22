"""Example plugin: drop every "NOTE ..." paragraph from the Markdown.

Register and use it:

    pdf2md --register-plugin "remove notes" examples/plugins/remove_notes.py
    pdf2md doc.pdf -o out --post-process "remove notes"
"""

from __future__ import annotations

import re

from pdf2md.plugins import PluginContext

_NOTE = re.compile(r"^NOTE(?:S)?(?:\s+\d+)?\s")


def process(markdown: str, context: PluginContext) -> str:
    """Return the Markdown without the paragraphs that start with NOTE."""
    paragraphs = markdown.split("\n\n")
    kept = [p for p in paragraphs if not _NOTE.match(p.lstrip())]
    return "\n\n".join(kept)
