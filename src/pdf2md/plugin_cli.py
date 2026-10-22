"""Command line options shared by ``pdf2md`` and ``docx2md`` for post-processing plugins."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from pdf2md.pipeline import ASSETS_DIR_NAME
from pdf2md.plugins import (
    Plugin,
    PluginError,
    default_plugin_dir,
    list_plugins,
    load_plugins,
    post_process_outputs,
    register_plugin,
)

logger = logging.getLogger(__name__)


def add_plugin_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ``--post-process``, ``--register-plugin``, ``--list-plugins`` and ``--plugin-dir``."""
    parser.add_argument(
        "--post-process",
        action="append",
        default=[],
        metavar="PLUGIN_NAME",
        help="apply a registered plugin to the produced Markdown; repeat to chain plugins "
        "(applied in the order given)",
    )
    parser.add_argument(
        "--register-plugin",
        nargs=2,
        action="append",
        default=[],
        metavar=("PLUGIN_NAME", "SCRIPT.py"),
        help="register SCRIPT.py (copied into the plugin folder) under PLUGIN_NAME, then exit "
        "unless input files are also given",
    )
    parser.add_argument("--list-plugins", action="store_true", help="list registered plugins")
    parser.add_argument(
        "--plugin-dir",
        type=Path,
        default=None,
        help=f"plugin folder (default: $PDF2MD_PLUGIN_DIR or {default_plugin_dir()})",
    )


def run_plugin_commands(args: argparse.Namespace) -> bool:
    """Execute ``--register-plugin`` / ``--list-plugins``; return True if any was requested.

    Raises:
        PluginError: a registration was refused.
    """
    handled = False
    for name, script in args.register_plugin:
        register_plugin(name, Path(script), args.plugin_dir)
        handled = True
    if args.list_plugins:
        plugins = list_plugins(args.plugin_dir)
        for name, path in plugins.items():
            logger.info("plugin %r -> %s", name, path)
        if not plugins:
            logger.info("no plugin registered in %s", args.plugin_dir or default_plugin_dir())
        handled = True
    return handled


def load_selected_plugins(args: argparse.Namespace) -> list[Plugin]:
    """Load the ``--post-process`` plugins before converting anything (fail fast).

    Raises:
        PluginError: a name is unknown or its script cannot be loaded.
    """
    return load_plugins(args.post_process, args.plugin_dir)


def apply_plugins(
    plugins: list[Plugin], source: Path, md_path: Path, json_path: Path | None
) -> tuple[Path, Path | None, bool]:
    """Post-process one converted file; return ``(md_path, json_path, ok)``.

    A failing plugin is logged and stops the chain; the files keep the content produced by the
    plugins that succeeded (and the paths they gave them), and ``ok`` is False.
    """
    if not plugins:
        return md_path, json_path, True
    try:
        md_path, json_path = post_process_outputs(
            plugins, source, md_path, json_path, md_path.parent / ASSETS_DIR_NAME
        )
    except PluginError as exc:
        logger.error("%s: %s", source.name, exc)
        return md_path, json_path, False
    return md_path, json_path, True


__all__ = [
    "PluginError",
    "add_plugin_arguments",
    "apply_plugins",
    "load_selected_plugins",
    "run_plugin_commands",
]
