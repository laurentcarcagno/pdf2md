"""Example plugin: rename the produced files (``report.md`` -> ``converted_report.md``).

Shows the ``PluginResult`` return value. Renaming applies to the .md and the .json files; the
folder and ``assets/`` keep their names so image links stay valid.
"""

from __future__ import annotations

from pdf2md.plugins import PluginContext, PluginResult


def process(markdown: str, context: PluginContext) -> PluginResult:
    """Keep the text, add a ``converted_`` prefix to the file name."""
    stem = context.md_path.stem
    if stem.startswith("converted_"):
        return PluginResult(markdown=markdown)
    return PluginResult(markdown=markdown, stem=f"converted_{stem}")
