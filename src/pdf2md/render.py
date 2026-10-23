"""Rendering of blocks into Markdown and into the traceability mapping."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pdf2md.models import Block, MappingEntry, Settings
from pdf2md.toc import strip_leaders


def _escape_cell(cell: str) -> str:
    return cell.replace("|", "\\|")


def render_table(rows: list[list[str]]) -> str:
    """Render rows as a GitHub-flavoured Markdown table (first row is the header)."""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(_escape_cell(c) for c in padded[0]) + " |"]
    lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
    lines.extend("| " + " | ".join(_escape_cell(c) for c in row) + " |" for row in padded[1:])
    return "\n".join(lines)


_MD_MARKER = re.compile(r"^(?:\d{1,3}[.)]|[-*+])\s")


def _list_line(line: str) -> str:
    """Return a Markdown list line: numbers and ``-`` stay, other labels become ``- label``."""
    stripped = line.lstrip(" ")
    indent = line[: len(line) - len(stripped)]
    stripped = re.sub(r"^[•–▪●◦]\s+", "- ", stripped)
    if not _MD_MARKER.match(stripped):
        stripped = f"- {stripped}"  # e.g. "(a) text"
    return indent + stripped


def render_block(block: Block) -> str:
    """Return the Markdown of one block."""
    if block.kind == "image" and block.image_path:
        alt = re.sub(r"[\[\]\n]", " ", block.text).strip()
        image = f"![{alt}]({block.image_path})"
        if block.rows:  # table rendered as a picture whose text is kept
            table = render_table(block.rows)
            return f"{image}\n\n<details><summary>Table text</summary>\n\n{table}\n\n</details>"
        return image
    if block.kind == "heading":
        return f"{'#' * max(block.level, 1)} {block.text}"
    if block.kind == "code":
        return f"```\n{block.text}\n```"
    if block.kind == "toc":
        return "\n".join(f"- {strip_leaders(line)}" for line in block.text.splitlines())
    if block.kind == "list":
        return "\n".join(_list_line(line) for line in block.text.splitlines())
    if block.kind == "table" and block.html:
        return block.html
    if block.kind == "table" and block.rows:
        return render_table(block.rows)
    return block.text


def number_blocks(blocks: list[Block]) -> list[tuple[int, int, Block]]:
    """Return ``(paragraph_id, paragraph_number_in_page, block)`` in document order."""
    numbered = []
    per_page: dict[int, int] = {}
    for paragraph_id, block in enumerate(blocks, start=1):
        per_page[block.page_number] = per_page.get(block.page_number, 0) + 1
        numbered.append((paragraph_id, per_page[block.page_number], block))
    return numbered


def render_markdown(blocks: list[Block], anchors: bool = False) -> str:
    """Render the document; ``anchors`` adds ``<!-- p:ID page:N -->`` before each block."""
    parts = []
    for paragraph_id, _, block in number_blocks(blocks):
        body = render_block(block)
        if anchors:
            body = f"<!-- p:{paragraph_id} page:{block.page_number} -->\n{body}"
        parts.append(body)
    return "\n\n".join(parts) + "\n" if parts else ""


def _snippet(block: Block, length: int) -> str:
    text = " ".join(block.text.split())
    return text if len(text) <= length else text[: length - 3].rstrip() + "..."


def build_mapping(blocks: list[Block], settings: Settings) -> list[MappingEntry]:
    """Build one mapping entry per block, in the order of the Markdown file."""
    return [
        MappingEntry(
            paragraph_id=paragraph_id,
            page_number=block.page_number,
            paragraph_number_in_page=number,
            kind=block.kind,
            snippet=_snippet(block, settings.snippet_length),
            coordinates=block.coords.rounded() if block.coords else None,
            source_pass=block.source_pass,
            element=block.element,
            image_path=block.image_path,
        )
        for paragraph_id, number, block in number_blocks(blocks)
    ]


def write_mapping(mapping: list[MappingEntry], path: Path) -> None:
    """Write the mapping as UTF-8 JSON."""
    payload = [entry.model_dump(mode="json") for entry in mapping]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
