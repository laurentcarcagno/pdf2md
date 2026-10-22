"""Orchestration: classification, per-page routing with fallback chains, post-processing."""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from pdf2md.classify import classify_document, open_document
from pdf2md.clean import mark_margins
from pdf2md.docling_engine import extract_docling
from pdf2md.extract import extract_pages
from pdf2md.fallback import extract_pymupdf_blocks
from pdf2md.filters import apply_keep
from pdf2md.images import extract_images, insert_images
from pdf2md.marker_engine import extract_marker
from pdf2md.models import (
    Block,
    Classification,
    MappingEntry,
    PageContent,
    PageKind,
    Settings,
    SourcePass,
)
from pdf2md.ocr import ocr_pages
from pdf2md.pages import resolve_pages
from pdf2md.progress import PageProgress
from pdf2md.render import build_mapping, render_markdown, write_mapping
from pdf2md.structure import body_font_size, build_blocks, heading_sizes
from pdf2md.table_images import tables_to_images
from pdf2md.text import fix_blocks
from pdf2md.toc import mark_contents

logger = logging.getLogger(__name__)

_OCR_HEADING_DELTA = 2.5  # OCR line heights are noisy: demand a clear size gap for a heading

PageBlocks = dict[int, list[Block]]


@dataclass(frozen=True)
class ConversionResult:
    """Outputs shared by every input format."""

    blocks: list[Block]
    markdown: str
    mapping: list[MappingEntry]


@dataclass(frozen=True)
class PdfConversionResult(ConversionResult):
    """Everything produced for one PDF, with the page classification of pass 1."""

    classification: Classification


@dataclass(frozen=True)
class _Context:
    pdf_path: Path
    doc: pymupdf.Document
    settings: Settings
    progress: PageProgress | None = None

    def tick(self, page_number: int) -> None:
        """Report that ``page_number`` has been processed."""
        if self.progress:
            self.progress.tick(page_number)


Step = Callable[[_Context, list[int]], PageBlocks]


def _blocks_from_pages(
    pages: dict[int, PageContent], settings: Settings, source: dict[int, SourcePass] | None = None
) -> PageBlocks:
    """Run the cleaning and structuring passes on extracted lines."""
    marked = mark_margins(pages, settings)
    body = body_font_size(marked)
    sizes = heading_sizes(marked, body, settings)
    return {
        number: build_blocks(
            page, body, sizes, settings, (source or {}).get(number, "pass2_pdfplumber")
        )
        for number, page in marked.items()
    }


def _step_geometric(ctx: _Context, pages: list[int]) -> PageBlocks:
    return _blocks_from_pages(extract_pages(ctx.pdf_path, pages, ctx.tick), ctx.settings)


def _step_ocr(ctx: _Context, pages: list[int]) -> PageBlocks:
    if not ctx.settings.ocr_enabled:
        return {}
    contents, used = ocr_pages(ctx.pdf_path, ctx.doc, pages, ctx.settings, on_page=ctx.tick)
    ocr_settings = ctx.settings.model_copy(update={"heading_size_delta": _OCR_HEADING_DELTA})
    return _blocks_from_pages(contents, ocr_settings, used)


def _step_docling(ctx: _Context, pages: list[int]) -> PageBlocks:
    return extract_docling(ctx.pdf_path, pages, ctx.settings.ocr_language, ctx.tick) or {}


def _step_marker(ctx: _Context, pages: list[int]) -> PageBlocks:
    return extract_marker(ctx.pdf_path, pages, ctx.tick) or {}


def _step_pymupdf(ctx: _Context, pages: list[int]) -> PageBlocks:
    return extract_pymupdf_blocks(ctx.doc, pages, ctx.tick)


_STEPS: dict[str, Step] = {
    "geometric": _step_geometric,
    "ocr": _step_ocr,
    "docling": _step_docling,
    "marker": _step_marker,
    "pymupdf": _step_pymupdf,
}

# Fallback chain per page kind (first engine that returns blocks for a page wins).
_CHAINS: dict[PageKind, tuple[str, ...]] = {
    PageKind.NATIVE: ("geometric", "docling", "marker", "pymupdf"),
    PageKind.MULTI_COLUMN: ("docling", "marker", "geometric", "pymupdf"),
    PageKind.SCANNED: ("ocr", "docling", "marker", "pymupdf"),
    PageKind.GARBLED: ("ocr", "docling", "marker", "pymupdf"),
}


def run_chain(ctx: _Context, pages: list[int], chain: tuple[str, ...]) -> PageBlocks:
    """Run ``chain`` on ``pages``; each engine only receives the pages still unresolved."""
    done: PageBlocks = {}
    remaining = list(pages)
    for name in chain:
        if not remaining:
            break
        if ctx.progress:
            ctx.progress.stage(name)
        started = time.perf_counter()
        try:
            produced = _STEPS[name](ctx, remaining)
        except Exception as exc:  # noqa: BLE001 - one engine failing must not stop the document
            logger.warning("engine %s failed on %d pages: %s", name, len(remaining), exc)
            continue
        solved = [n for n in remaining if produced.get(n)]
        logger.info(
            "engine %s resolved %d/%d pages in %.1fs",
            name,
            len(solved),
            len(remaining),
            time.perf_counter() - started,
        )
        done.update({n: produced[n] for n in solved})
        remaining = [n for n in remaining if n not in done]
    for number in remaining:
        logger.warning("page %d: no engine produced content", number)
    return done


def _plan(classification: Classification, settings: Settings) -> dict[tuple[str, ...], list[int]]:
    """Group the non-empty pages by the fallback chain they must follow."""
    groups: dict[tuple[str, ...], list[int]] = {}
    for profile in classification.pages:
        if profile.kind is PageKind.EMPTY:
            logger.info("page %d is empty, skipped", profile.page_number)
            continue
        if settings.engine != "auto":
            chain: tuple[str, ...] = (settings.engine, "pymupdf")
        elif classification.document_kind == "complex" and profile.kind is PageKind.NATIVE:
            chain = _CHAINS[PageKind.MULTI_COLUMN]
        else:
            chain = _CHAINS[profile.kind]
        groups.setdefault(chain, []).append(profile.page_number)
    return groups


def convert_pdf(
    pdf_path: Path,
    settings: Settings | None = None,
    anchors: bool = False,
    progress: PageProgress | None = None,
    images_dir: Path | None = None,
) -> PdfConversionResult:
    """Convert ``pdf_path" to Markdown and a paragraph mapping.

    Figures are saved as PNG in ``images_dir`` (created if needed) and referenced from the
    Markdown; pass ``None`` to skip image extraction.

    ``progress`` receives one tick per page processed (see :class:`PageProgress`); its total
    should be the number of selected pages, see :func:`count_selected_pages`.

    Raises:
        PdfOpenError: the file is missing, corrupted, encrypted or not a PDF.
        ValueError: ``settings.pages`` selects no page of the document.
    """
    settings = settings or Settings()
    started = time.perf_counter()
    doc = open_document(pdf_path)
    try:
        selected = resolve_pages(settings.pages, doc.page_count) if settings.pages else None
        classification = classify_document(doc, settings, selected)
        logger.info(
            "classified %s as %s (%d pages)",
            pdf_path.name,
            classification.document_kind,
            len(classification.pages),
        )
        ctx = _Context(pdf_path, doc, settings, progress)
        blocks_by_page: PageBlocks = {}
        for chain, pages in _plan(classification, settings).items():
            logger.info("pages %s follow chain %s", _summary(pages), " > ".join(chain))
            blocks_by_page.update(run_chain(ctx, pages, chain))
        if images_dir is not None:
            blocks_by_page = _add_images(doc, classification, blocks_by_page, images_dir, settings)
        ordered = [b for number in sorted(blocks_by_page) for b in blocks_by_page[number]]
        tagged = fix_blocks(mark_contents(ordered))
        if images_dir is not None and settings.tables_as_images is not None:
            tagged = tables_to_images(doc, tagged, images_dir, settings)
    finally:
        doc.close()

    blocks = apply_keep(tagged, settings.keep)
    _log_summary(pdf_path, classification, tagged, blocks, time.perf_counter() - started)
    return PdfConversionResult(
        classification=classification,
        blocks=blocks,
        markdown=render_markdown(blocks, anchors=anchors),
        mapping=build_mapping(blocks, settings),
    )


def _add_images(
    doc: pymupdf.Document,
    classification: Classification,
    blocks_by_page: PageBlocks,
    images_dir: Path,
    settings: Settings,
) -> PageBlocks:
    started = time.perf_counter()
    pages = [p.page_number for p in classification.pages if p.kind is not PageKind.EMPTY]
    scanned = frozenset(classification.pages_of(PageKind.SCANNED, PageKind.GARBLED))
    images = extract_images(doc, pages, images_dir, settings, scanned)
    logger.info(
        "extracted %d images from %d pages into %s in %.1fs",
        sum(len(v) for v in images.values()),
        len(images),
        images_dir,
        time.perf_counter() - started,
    )
    merged = insert_images(blocks_by_page, images, images_dir.parent)
    if images_dir.is_dir() and not any(images_dir.iterdir()):
        images_dir.rmdir()  # every candidate was rejected: leave no empty folder
    return merged


def count_selected_pages(pdf_path: Path, settings: Settings) -> int:
    """Return how many pages ``convert_pdf`` will process (for the progress bar total).

    Raises:
        PdfOpenError: the file cannot be opened.
        ValueError: ``settings.pages`` selects no page of the document.
    """
    doc = open_document(pdf_path)
    try:
        if settings.pages:
            return len(resolve_pages(settings.pages, doc.page_count))
        return int(doc.page_count)
    finally:
        doc.close()


def _log_summary(
    pdf_path: Path,
    classification: Classification,
    tagged: list[Block],
    kept: list[Block],
    elapsed: float,
) -> None:
    """Log what was produced and what was removed, one line each."""
    kinds = Counter(p.kind.value for p in classification.pages)
    logger.info("%s: page kinds %s", pdf_path.name, dict(kinds))
    logger.info(
        "%s: %d blocks by source %s",
        pdf_path.name,
        len(kept),
        dict(Counter(b.source_pass for b in kept)),
    )
    kept_ids = {id(b) for b in kept}
    removed = Counter(b.element for b in tagged if id(b) not in kept_ids)
    if removed:
        logger.info("%s: removed %s (use --keep to retain)", pdf_path.name, dict(removed))
    logger.info("%s: converted in %.1fs", pdf_path.name, elapsed)


def _summary(pages: list[int]) -> str:
    return f"{pages[0]}-{pages[-1]} ({len(pages)})" if len(pages) > 3 else str(pages)


ASSETS_DIR_NAME = "assets"


def document_dir(output_dir: Path, pdf_path: Path) -> Path:
    """Return the folder holding every output of ``pdf_path``: ``<output_dir>/<pdf stem>``."""
    return output_dir / pdf_path.stem


def assets_dir(output_dir: Path, pdf_path: Path) -> Path:
    """Return the folder for extracted images; it is created at the first image saved."""
    return document_dir(output_dir, pdf_path) / ASSETS_DIR_NAME


def write_outputs(
    result: ConversionResult, pdf_path: Path, output_dir: Path, write_json: bool = True
) -> tuple[Path, Path | None]:
    """Write ``<output_dir>/<stem>/<stem>.md`` (and ``.json`` unless ``write_json`` is False).

    The document folder also holds the ``assets`` folder written during conversion, so the
    Markdown can reference images as ``assets/<file>``. Returns the written paths; the JSON path
    is None when the mapping was not written.
    """
    doc_dir = document_dir(output_dir, pdf_path)
    doc_dir.mkdir(parents=True, exist_ok=True)
    md_path = doc_dir / f"{pdf_path.stem}.md"
    md_path.write_text(result.markdown, encoding="utf-8")
    if not write_json:
        return md_path, None
    json_path = doc_dir / f"{pdf_path.stem}.json"
    write_mapping(result.mapping, json_path)
    return md_path, json_path
