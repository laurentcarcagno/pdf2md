"""Command line interface: ``docx2md input.docx -o out/``."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from tqdm.contrib.logging import logging_redirect_tqdm

from pdf2md.cli import KEEP_ALIASES, keep_set, page_spec
from pdf2md.docx_convert import convert_docx, count_blocks
from pdf2md.errors import DocxOpenError
from pdf2md.models import Settings
from pdf2md.pipeline import assets_dir, write_outputs
from pdf2md.plugin_cli import (
    PluginError,
    add_plugin_arguments,
    apply_plugins,
    load_selected_plugins,
    run_plugin_commands,
)
from pdf2md.progress import PageProgress
from pdf2md.settings_cli import add_setting_arguments, format_settings, parse_overrides

logger = logging.getLogger("docx2md")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the converter; return a process exit code (0 ok, 1 at least one file failed)."""
    parser = argparse.ArgumentParser(prog="docx2md", description=__doc__)
    parser.add_argument("docs", nargs="*", type=Path, help="Word files (.docx) to convert")
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
        "-p",
        "--pages",
        type=page_spec,
        metavar="RANGES",
        help="estimated pages to keep, e.g. 10- , -10 , 15-18 , 1-5,10-20,30-",
    )
    parser.add_argument("--no-images", action="store_true", help="do not extract images")
    parser.add_argument(
        "--tables-html",
        action="store_true",
        help="insert tables as HTML keeping cell colours and text styles (bold, italic, ...)",
    )
    parser.add_argument("--no-json", action="store_true", help="do not write the JSON mapping")
    parser.add_argument("--no-progress", action="store_true", help="hide the progress bar")
    parser.add_argument("-v", "--verbose", action="store_true")
    add_plugin_arguments(parser)
    add_setting_arguments(parser)
    args = parser.parse_args(argv)
    if args.list_settings:
        print(format_settings())  # noqa: T201 - the command's output
        return 0
    try:
        overrides = parse_overrides(args.settings)
    except ValueError as exc:
        parser.error(str(exc))

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        handled = run_plugin_commands(args)
        if not args.docs:
            if handled:
                return 0
            parser.error("no input file given")
        plugins = load_selected_plugins(args)
    except PluginError as exc:
        logger.error("%s", exc)
        return 2
    settings = Settings(
        **{
            "keep": keep_set(args.keep),
            "pages": args.pages,
            "tables_html": args.tables_html,
            **overrides,
        }
    )
    failures = 0
    with logging_redirect_tqdm():
        for index, path in enumerate(args.docs, start=1):
            label = f"[{index}/{len(args.docs)}] {path.name}"
            try:
                total = count_blocks(path)
                logger.info("%s: converting %d body elements", label, total)
                with PageProgress(
                    total, label, enabled=False if args.no_progress else None, unit="block"
                ) as bar:
                    images_dir = None if args.no_images else assets_dir(args.output_dir, path)
                    result = convert_docx(
                        path, settings, anchors=args.anchors, progress=bar, images_dir=images_dir
                    )
            except (DocxOpenError, ValueError) as exc:
                logger.error("%s: %s", path, exc)
                failures += 1
                continue
            md_path, json_path = write_outputs(
                result, path, args.output_dir, write_json=not args.no_json
            )
            md_path, json_path, plugins_ok = apply_plugins(plugins, path, md_path, json_path)
            written = f"{md_path} and {json_path}" if json_path else str(md_path)
            logger.info("wrote %s (%d blocks)", written, len(result.mapping))
            if not plugins_ok:
                failures += 1
    logger.info("done: %d/%d converted", len(args.docs) - failures, len(args.docs))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
