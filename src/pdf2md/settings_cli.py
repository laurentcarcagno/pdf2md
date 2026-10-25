"""Every tunable of :class:`~pdf2md.models.Settings` on the command line (``--set NAME=VALUE``).

The dedicated options (``--keep``, ``--engine``, ``--pages``...) stay the way to set the common
ones; ``--set`` reaches all the others, the detection thresholds of headers and footers, columns,
scans, headings and tables, and any threshold added to ``Settings`` later, without a new option.
"""

from __future__ import annotations

import argparse
import types
from dataclasses import dataclass
from typing import Any, Union, get_args, get_origin

from pydantic import ValidationError
from pydantic.fields import FieldInfo

from pdf2md.models import Settings

SCALARS: tuple[type, ...] = (bool, int, float, str)
# Settings that have their own command line option, and are set there
DEDICATED = frozenset(
    {
        "pages",
        "engine",
        "ocr_enabled",
        "ocr_language",
        "image_dpi",
        "tables_as_images",
        "tables_html",
        "tables_keep_text",
    }
)


@dataclass(frozen=True)
class SettingInfo:
    """One tunable: its name, type, default, bounds and meaning."""

    name: str
    kind: type
    default: Any
    description: str
    minimum: float | None
    maximum: float | None
    dedicated: bool


def _scalar_kind(annotation: Any) -> type | None:
    """``int`` for ``int`` or ``int | None``; None when the type is not a plain scalar."""
    if get_origin(annotation) in (Union, types.UnionType):
        inner = [a for a in get_args(annotation) if a is not type(None)]
        annotation = inner[0] if len(inner) == 1 else None
    return annotation if annotation in SCALARS else None


def _bound(field: FieldInfo, attribute: str) -> float | None:
    for constraint in field.metadata:
        value = getattr(constraint, attribute, None)
        if value is not None:
            return float(value)
    return None


def tunable_settings() -> list[SettingInfo]:
    """The scalar settings (numbers, flags, text), in the order of ``Settings``."""
    found = []
    for name, field in Settings.model_fields.items():
        kind = _scalar_kind(field.annotation)
        if kind is None:
            continue
        found.append(
            SettingInfo(
                name=name,
                kind=kind,
                default=field.default,
                description=field.description or "",
                minimum=_bound(field, "ge"),
                maximum=_bound(field, "le"),
                dedicated=name in DEDICATED,
            )
        )
    return found


def parse_overrides(items: list[str]) -> dict[str, str]:
    """Read ``NAME=VALUE`` items; raise ``ValueError`` for an unknown name or a bad value."""
    known = {info.name for info in tunable_settings()}
    overrides: dict[str, str] = {}
    for item in items:
        name, separator, value = item.partition("=")
        name = name.strip().lower()
        if not separator:
            raise ValueError(f"--set {item!r}: expected NAME=VALUE")
        if name not in known:
            raise ValueError(f"--set: unknown setting {name!r} (see --list-settings)")
        overrides[name] = value.strip()
    try:
        Settings(**overrides)  # type and bounds, now rather than in the middle of a conversion
    except ValidationError as exc:
        first = exc.errors()[0]
        raise ValueError(f"--set {first['loc'][0]}: {first['msg']}") from exc
    return overrides


def add_setting_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ``--set`` and ``--list-settings``."""
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        dest="settings",
        metavar="NAME=VALUE",
        help="set any other setting, e.g. --set recurrence_ratio=0.3 (repeatable; --list-settings)",
    )
    parser.add_argument(
        "--list-settings",
        action="store_true",
        help="print the settings that --set accepts, with their default and meaning, and exit",
    )


def format_settings() -> str:
    """The text printed by ``--list-settings``."""
    lines = []
    for info in tunable_settings():
        bounds = ""
        if info.minimum is not None or info.maximum is not None:
            bounds = f" [{info.minimum:g}..{info.maximum:g}]"
        note = " (own option)" if info.dedicated else ""
        lines.append(
            f"{info.name:<24} {info.kind.__name__:<5} default {info.default!s:<8}{bounds}{note}"
            f"\n    {info.description}"
        )
    return "\n".join(lines)
