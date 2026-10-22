"""Command line interface: ``python -m pdf2md input.pdf -o out/``."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from tqdm.contrib.logging import logging_redirect_tqdm

from pdf2md.errors import PdfOpenError
from pdf2md.models import ALL_ELEMENTS, ElementKind, Settings
from pdf2md.pages import parse_page_spec
from pdf2md.pipeline import assets_dir, convert_pdf, count_selected_pages, write_outputs
from pdf2md.plugin_cli import (
    PluginError,
    add_plugin_arguments,
    apply_plugins,
    load_selected_plugins,
    run_plugin_commands,
)
from pdf2md.progress import PageProgress

logger = logging.getLogger("pdf2md")

KEEP_ALIASES: dict[str, tuple[ElementKind, ...]] = {
    "header": ("header",),
    "footer": ("footer",),
    "toc": ("toc",),
    "lof": ("list_of_figures",),
    "lot": ("list_of_tables",),
    "all": ALL_ELEMENTS,
}


def page_spec(value: str) -> str:
    try:
        parse_page_spec(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def keep_set(values: list[str]) -> frozenset[ElementKind]:
    return frozenset(kind for value in values for kind in KEEP_ALIASES[value])


def main(argv: Sequence[str] | None = None) -> int:
    """Run the converter; return a process exit code (0 ok, 1 at least one file failed)."""
    parser = argparse.ArgumentParser(prog="pdf2md", description=__doc__)
    parser.add_argument("pdfs", nargs="*", type=Path, help="PDF files to convert")
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("out"))
    parser.add_argument("--anchors", action="store_true", help="add <!-- p:ID page:N --> markers")
    parser.add_argument(
        "--keep",
        nargs="+",
        default=[],
        choices=sorted(KEEP_ALIASES),
        metavar="ELEMENT",
        help="elements to keep (all are removed by default): header footer toc lof lot all",
    )
    parser.add_argument(
        "--engine",
        choices=["auto", "geometric", "docling", "marker", "ocr"],
        default="auto",
        help="force one engine instead of the automatic fallback chain",
    )
    parser.add_argument(
        "-p",
        "--pages",
        type=page_spec,
        metavar="RANGES",
        help="pages to convert, e.g. 10- (10 to end), -10 (1 to 10), 15-18, 1-5,10-20,30-",
    )
    parser.add_argument("--ocr-lang", default="fra+eng", help="Tesseract languages")
    parser.add_argument("--ocr-dpi", type=int, default=300)
    parser.add_argument("--no-ocr", action="store_true", help="never run OCR")
    parser.add_argument("--no-images", action="store_true", help="do not extract figures")
    parser.add_argument("--image-dpi", type=int, default=200, help="figure resolution (200)")
    parser.add_argument("--no-json", action="store_true", help="do not write the JSON mapping")
    parser.add_argument("--no-progress", action="store_true", help="hide the progress bar")
    parser.add_argument("-v", "--verbose", action="store_true")
    add_plugin_arguments(parser)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        handled = run_plugin_commands(args)
        if not args.pdfs:
            if handled:
                return 0
            parser.error("no input file given")
        plugins = load_selected_plugins(args)
    except PluginError as exc:
        logger.error("%s", exc)
        return 2
    settings = Settings(
        keep=keep_set(args.keep),
        pages=args.pages,
        engine=args.engine,
        ocr_language=args.ocr_lang,
        ocr_dpi=args.ocr_dpi,
        ocr_enabled=not args.no_ocr,
        image_dpi=args.image_dpi,
    )
    failures = 0
    with logging_redirect_tqdm():
        for index, pdf_path in enumerate(args.pdfs, start=1):
            label = f"[{index}/{len(args.pdfs)}] {pdf_path.name}"
            try:
                total = count_selected_pages(pdf_path, settings)
                logger.info("%s: converting %d pages", label, total)
                with PageProgress(total, label, enabled=False if args.no_progress else None) as bar:
                    images_dir = None if args.no_images else assets_dir(args.output_dir, pdf_path)
                    result = convert_pdf(
                        pdf_path,
                        settings,
                        anchors=args.anchors,
                        progress=bar,
                        images_dir=images_dir,
                    )
            except (PdfOpenError, ValueError) as exc:
                logger.error("%s: %s", pdf_path, exc)
                failures += 1
                continue
            md_path, json_path = write_outputs(
                result, pdf_path, args.output_dir, write_json=not args.no_json
            )
            md_path, json_path, plugins_ok = apply_plugins(plugins, pdf_path, md_path, json_path)
            written = f"{md_path} and {json_path}" if json_path else str(md_path)
            logger.info("wrote %s (%d blocks)", written, len(result.mapping))
            if not plugins_ok:
                failures += 1
    logger.info("done: %d/%d converted", len(args.pdfs) - failures, len(args.pdfs))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
