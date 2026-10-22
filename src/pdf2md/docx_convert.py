"""Word (.docx) to Markdown: same outputs, options and layout as the PDF pipeline."""

from __future__ import annotations

import logging
import time
import zipfile
from pathlib import Path

import docx
from docx.opc.exceptions import PackageNotFoundError

from pdf2md.docx_reader import DocxContent, read_docx
from pdf2md.errors import DocxOpenError
from pdf2md.filters import apply_keep
from pdf2md.images import caption_alt_text
from pdf2md.models import Block, Settings
from pdf2md.pages import resolve_pages
from pdf2md.pipeline import ConversionResult
from pdf2md.progress import PageProgress
from pdf2md.render import build_mapping, render_markdown
from pdf2md.text import fix_blocks
from pdf2md.toc import mark_contents

logger = logging.getLogger(__name__)


def open_docx(path: Path) -> docx.document.Document:
    """Open a ``.docx`` with python-docx, mapping every failure to :class:`DocxOpenError`."""
    if path.suffix.lower() == ".doc":
        raise DocxOpenError(f"{path}: legacy .doc is not supported, save it as .docx first")
    try:
        return docx.Document(str(path))
    except (PackageNotFoundError, zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
        raise DocxOpenError(f"Cannot open Word file {path}: {exc}") from exc


def count_blocks(path: Path) -> int:
    """Return the number of body elements (paragraphs and tables), the progress bar total."""
    document = open_docx(path)
    return len(document.element.body.xpath("./w:p | ./w:tbl | ./w:sdt"))


def _select_pages(blocks: list[Block], settings: Settings, page_count: int) -> list[Block]:
    if not settings.pages:
        return blocks
    wanted = set(resolve_pages(settings.pages, page_count))
    return [b for b in blocks if b.page_number in wanted]


def _caption_images(blocks: list[Block]) -> None:
    """Give images without a Word description the neighbouring "Figure N: ..." caption."""
    for index, block in enumerate(blocks):
        if block.kind == "image" and not block.text:
            others = blocks[:index] + blocks[index + 1 :]
            block.text = caption_alt_text(others, index, block.page_number)


def _save_images(content: DocxContent, blocks: list[Block], images_dir: Path | None) -> None:
    """Write the images still referenced by ``blocks``; the folder appears with the first one."""
    if images_dir is None:
        return
    for block in blocks:
        if block.kind != "image" or not block.image_path:
            continue
        name = Path(block.image_path).name
        blob, _extension = content.images[name]
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / name).write_bytes(blob)


def convert_docx(
    docx_path: Path,
    settings: Settings | None = None,
    anchors: bool = False,
    progress: PageProgress | None = None,
    images_dir: Path | None = None,
) -> ConversionResult:
    """Convert a Word file to Markdown and a paragraph mapping.

    Page numbers are estimates (see :func:`pdf2md.docx_reader.read_docx`) and there are no
    coordinates. Images go to ``images_dir`` (created at the first one); ``None`` skips them.

    Raises:
        DocxOpenError: the file is missing, corrupted, a legacy ``.doc`` or not a ``.docx``.
        ValueError: ``settings.pages`` selects no page of the document.
    """
    settings = settings or Settings()
    started = time.perf_counter()
    document = open_docx(docx_path)
    counter = {"done": 0}

    def tick() -> None:
        counter["done"] += 1
        if progress:
            progress.tick(counter["done"])

    content = read_docx(document, tick)
    tagged = fix_blocks(mark_contents(content.blocks))
    selected = _select_pages(tagged, settings, content.page_count)
    kept = apply_keep(selected, settings.keep)
    if images_dir is None:
        kept = [b for b in kept if b.kind != "image"]
    _caption_images(kept)
    _save_images(content, kept, images_dir)

    removed = len(selected) - len(kept)
    logger.info(
        "%s: %d blocks (~%d pages), %d images, %d removed (use --keep to retain), %.1fs",
        docx_path.name,
        len(kept),
        content.page_count,
        sum(1 for b in kept if b.kind == "image"),
        removed,
        time.perf_counter() - started,
    )
    return ConversionResult(
        blocks=kept,
        markdown=render_markdown(kept, anchors=anchors),
        mapping=build_mapping(kept, settings),
    )
