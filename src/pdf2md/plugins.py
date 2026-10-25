"""Post-processing plugins: user scripts that transform the Markdown a conversion produced.

A plugin is a Python file defining ``process(markdown, context)``. It is registered once under a
name (the script is copied into the plugin folder) and then selected by that name with
``--post-process``. Plugins run in the order they are given, each one receiving the output of
the previous one. They execute with the user's privileges: only register scripts you trust.
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import os
import re
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import RootModel, ValidationError

logger = logging.getLogger(__name__)

PLUGIN_DIR_ENV = "PDF2MD_PLUGIN_DIR"
REGISTRY_FILE = "registry.json"
ENTRY_POINT = "process"
_MAX_NAME_LENGTH = 64
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


class PluginError(Exception):
    """Raised for an invalid, unknown or failing plugin."""


@dataclass(frozen=True)
class PluginContext:
    """What a plugin may need to know about the conversion it post-processes."""

    plugin_name: str
    source: Path
    md_path: Path
    json_path: Path | None
    assets_dir: Path


@dataclass(frozen=True)
class PluginResult:
    """Return this from ``process`` to also rename the output files.

    ``stem`` is the new base name of the ``.md`` (and ``.json``) file, without extension or
    folder; ``None`` keeps the current name.
    """

    markdown: str
    stem: str | None = None


ProcessFunction = Callable[[str, PluginContext], "str | PluginResult"]


class _Registry(RootModel[dict[str, str]]):
    """``plugin name -> script file name`` stored in the plugin folder."""


@dataclass(frozen=True)
class Plugin:
    """A registered plugin, loaded and ready to run."""

    name: str
    path: Path
    process: ProcessFunction = field(repr=False)


def default_plugin_dir() -> Path:
    """Return ``$PDF2MD_PLUGIN_DIR`` or ``$XDG_CONFIG_HOME/pdf2md/plugins`` (``~/.config/...``)."""
    configured = os.environ.get(PLUGIN_DIR_ENV)
    if configured:
        return Path(configured).expanduser()
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_home / "pdf2md" / "plugins"


def _read_registry(plugin_dir: Path) -> dict[str, str]:
    path = plugin_dir / REGISTRY_FILE
    if not path.exists():
        return {}
    try:
        return _Registry.model_validate_json(path.read_text(encoding="utf-8")).root
    except (ValidationError, OSError) as exc:
        raise PluginError(f"plugin registry {path} is unreadable: {exc}") from exc


def _write_registry(plugin_dir: Path, registry: dict[str, str]) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    payload = _Registry(registry).model_dump_json(indent=2)
    (plugin_dir / REGISTRY_FILE).write_text(payload + "\n", encoding="utf-8")


def _validate_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > _MAX_NAME_LENGTH:
        raise PluginError(f"plugin name must be 1-{_MAX_NAME_LENGTH} characters, got {name!r}")
    return cleaned


def _check_script(script: Path) -> None:
    """Statically check that ``script`` is a Python file defining ``process`` (nothing runs)."""
    if script.suffix != ".py" or not script.is_file():
        raise PluginError(f"{script} is not a .py file")
    try:
        tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
    except (SyntaxError, UnicodeDecodeError, OSError) as exc:
        raise PluginError(f"{script} cannot be parsed: {exc}") from exc
    defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    if ENTRY_POINT not in defined:
        raise PluginError(f"{script} must define a function {ENTRY_POINT}(markdown, context)")


def _file_name_for(name: str, registry: dict[str, str]) -> str:
    """Choose the copied script's name: slug of the plugin name, unique in the registry."""
    slug = _UNSAFE.sub("_", name).strip("_") or "plugin"
    taken = {file for other, file in registry.items() if other != name}
    candidate, counter = f"{slug}.py", 2
    while candidate in taken or candidate == REGISTRY_FILE:
        candidate, counter = f"{slug}-{counter}.py", counter + 1
    return candidate


def register_plugin(name: str, script: Path, plugin_dir: Path | None = None) -> Path:
    """Associate ``name`` with ``script``, copying the script into the plugin folder.

    Registering an existing name replaces its script. Returns the path of the copy.

    Raises:
        PluginError: the name is invalid or the script is not a Python file defining ``process``.
    """
    plugin_dir = plugin_dir or default_plugin_dir()
    name = _validate_name(name)
    _check_script(script)
    registry = _read_registry(plugin_dir)
    file_name = registry.get(name) or _file_name_for(name, registry)
    plugin_dir.mkdir(parents=True, exist_ok=True)
    target = plugin_dir / file_name
    if script.resolve() != target.resolve():
        shutil.copy2(script, target)
    if name in registry:
        logger.info("plugin %r replaced (%s)", name, target)
    registry[name] = file_name
    _write_registry(plugin_dir, registry)
    logger.info("plugin %r registered: %s", name, target)
    return target


def unregister_plugin(name: str, plugin_dir: Path | None = None) -> None:
    """Forget the plugin ``name`` and delete its copied script.

    Raises:
        PluginError: no plugin is registered under that name.
    """
    plugin_dir = plugin_dir or default_plugin_dir()
    registry = _read_registry(plugin_dir)
    if name not in registry:
        known = ", ".join(repr(n) for n in registry) or "none registered"
        raise PluginError(f"unknown plugin {name!r} (known: {known})")
    file_name = registry.pop(name)
    _write_registry(plugin_dir, registry)
    if file_name not in registry.values():
        (plugin_dir / file_name).unlink(missing_ok=True)
    logger.info("plugin %r unregistered", name)


def list_plugins(plugin_dir: Path | None = None) -> dict[str, Path]:
    """Return ``name -> script path`` of every registered plugin."""
    plugin_dir = plugin_dir or default_plugin_dir()
    return {name: plugin_dir / file for name, file in sorted(_read_registry(plugin_dir).items())}


def load_plugin(name: str, plugin_dir: Path | None = None) -> Plugin:
    """Load the plugin registered as ``name``.

    Raises:
        PluginError: unknown name, missing script, or a script that cannot be imported.
    """
    available = list_plugins(plugin_dir)
    if name not in available:
        known = ", ".join(repr(n) for n in available) or "none registered"
        raise PluginError(f"unknown plugin {name!r} (known: {known})")
    path = available[name]
    if not path.is_file():
        raise PluginError(f"plugin {name!r}: script {path} is missing, register it again")
    spec = importlib.util.spec_from_file_location(f"pdf2md_plugin_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise PluginError(f"plugin {name!r}: cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # keep the plugin folder free of __pycache__
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - arbitrary user code
        raise PluginError(f"plugin {name!r}: importing {path} failed: {exc!r}") from exc
    finally:
        sys.dont_write_bytecode = previous
    function = getattr(module, ENTRY_POINT, None)
    if not callable(function):
        raise PluginError(f"plugin {name!r}: {path} has no callable {ENTRY_POINT}")
    return Plugin(name=name, path=path, process=function)


def load_plugins(names: Sequence[str], plugin_dir: Path | None = None) -> list[Plugin]:
    """Load several plugins, in the given order (a name may appear more than once)."""
    return [load_plugin(name, plugin_dir) for name in names]


def _rename_outputs(md_path: Path, json_path: Path | None, stem: str) -> tuple[Path, Path | None]:
    if Path(stem).name != stem or stem in ("", ".", ".."):
        raise PluginError(f"invalid output name {stem!r}: give a bare file name without folders")
    new_md = md_path.with_name(f"{stem}.md")
    new_json = json_path.with_name(f"{stem}.json") if json_path else None
    for old, new in ((md_path, new_md), (json_path, new_json)):
        if old is not None and new is not None and new != old and new.exists():
            raise PluginError(f"cannot rename to {stem!r}: {new} already exists")
    if new_md != md_path:
        md_path.rename(new_md)
    if json_path is not None and new_json is not None and new_json != json_path:
        json_path.rename(new_json)
    return new_md, new_json


def post_process_outputs(
    plugins: Sequence[Plugin],
    source: Path,
    md_path: Path,
    json_path: Path | None,
    assets_dir: Path,
) -> tuple[Path, Path | None]:
    """Run ``plugins`` in sequence on the Markdown file; return the final ``(md, json)`` paths.

    After each plugin the file is rewritten (and renamed if the plugin asked for it), so a
    failing plugin leaves the output as the previous plugin produced it. The JSON mapping is not
    edited: its snippets describe the text as converted, before post-processing.

    Raises:
        PluginError: a plugin raised, returned something other than ``str``/``PluginResult``,
            or asked for an invalid name.
    """
    for plugin in plugins:
        context = PluginContext(plugin.name, source, md_path, json_path, assets_dir)
        markdown = md_path.read_text(encoding="utf-8")
        try:
            result = plugin.process(markdown, context)
        except Exception as exc:  # noqa: BLE001 - arbitrary user code
            raise PluginError(f"plugin {plugin.name!r} failed on {md_path.name}: {exc!r}") from exc
        if isinstance(result, str):
            result = PluginResult(markdown=result)
        if not isinstance(result, PluginResult) or not isinstance(result.markdown, str):
            raise PluginError(
                f"plugin {plugin.name!r} must return a str or a PluginResult, "
                f"got {type(result).__name__}"
            )
        md_path.write_text(result.markdown, encoding="utf-8")
        if result.stem is not None:
            md_path, json_path = _rename_outputs(md_path, json_path, result.stem)
        logger.info(
            "plugin %r applied to %s (%d -> %d characters)",
            plugin.name,
            md_path.name,
            len(markdown),
            len(result.markdown),
        )
    return md_path, json_path
