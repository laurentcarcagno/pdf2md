"""Word list numbering: turns ``numId``/``ilvl`` into the label Word would display."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": W_NS}
_PLACEHOLDER = re.compile(r"%(\d)")


def _attr(element: Any, tag: str, name: str = "val") -> str | None:
    child = element.find(f"w:{tag}", _NS)
    return None if child is None else child.get(f"{{{W_NS}}}{name}")


def _letters(number: int, upper: bool) -> str:
    text = ""
    while number > 0:
        number, rest = divmod(number - 1, 26)
        text = chr(ord("a") + rest) + text
    return text.upper() if upper else text


def _roman(number: int, upper: bool) -> str:
    text = ""
    for value, symbol in (
        (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
        (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
    ):  # fmt: skip
        while number >= value:
            text += symbol
            number -= value
    return text.upper() if upper else text


def format_number(number: int, num_format: str) -> str:
    """Format ``number`` as Word's ``numFmt`` would (decimal, letters, roman)."""
    if num_format == "lowerLetter":
        return _letters(number, upper=False)
    if num_format == "upperLetter":
        return _letters(number, upper=True)
    if num_format == "lowerRoman":
        return _roman(number, upper=False)
    if num_format == "upperRoman":
        return _roman(number, upper=True)
    if num_format == "decimalZero":
        return f"{number:02d}"
    return str(number)


@dataclass
class _Level:
    start: int = 1
    num_format: str = "decimal"
    text: str = "%1."
    style_id: str | None = None


@dataclass
class _Abstract:
    levels: dict[int, _Level] = field(default_factory=dict)
    counters: dict[int, int] = field(default_factory=dict)


class Numbering:
    """Counters and label formatting for every list of a document.

    Build it once from the ``w:numbering`` element and call :meth:`label` for each numbered
    paragraph in document order.
    """

    def __init__(self, numbering: Any | None) -> None:
        self._abstracts: dict[str, _Abstract] = {}
        self._num_to_abstract: dict[str, str] = {}
        if numbering is not None:
            self._load(numbering)

    def _load(self, numbering: Any) -> None:
        for abstract in numbering.findall("w:abstractNum", _NS):
            key = abstract.get(f"{{{W_NS}}}abstractNumId") or ""
            levels: dict[int, _Level] = {}
            for lvl in abstract.findall("w:lvl", _NS):
                index = int(lvl.get(f"{{{W_NS}}}ilvl") or 0)
                start = _attr(lvl, "start")
                levels[index] = _Level(
                    start=int(start) if start else 1,
                    num_format=_attr(lvl, "numFmt") or "decimal",
                    text=_attr(lvl, "lvlText") or "",
                    style_id=_attr(lvl, "pStyle"),
                )
            self._abstracts[key] = _Abstract(levels=levels)
        for num in numbering.findall("w:num", _NS):
            abstract_id = _attr(num, "abstractNumId")
            if abstract_id is not None:
                self._num_to_abstract[num.get(f"{{{W_NS}}}numId") or ""] = abstract_id

    def level_for_style(self, num_id: str, style_id: str) -> int:
        """Return the list level linked to a paragraph style (heading numbering), default 0."""
        abstract = self._abstracts.get(self._num_to_abstract.get(num_id, ""))
        if abstract:
            for index, level in abstract.levels.items():
                if level.style_id == style_id:
                    return index
        return 0

    def label(self, num_id: str, ilvl: int) -> str | None:
        """Advance the counter of ``(num_id, ilvl)`` and return its label ("1.", "a.", "-").

        Returns None for ``numId`` 0 or an unknown list (no numbering applies).
        """
        abstract = self._abstracts.get(self._num_to_abstract.get(num_id, ""))
        if abstract is None or num_id == "0":
            return None
        level = abstract.levels.get(ilvl, _Level())
        abstract.counters[ilvl] = abstract.counters.get(ilvl, level.start - 1) + 1
        for deeper in [k for k in abstract.counters if k > ilvl]:
            del abstract.counters[deeper]
        if level.num_format in ("bullet", "none") or not level.text:
            return "-" if level.num_format == "bullet" else ""

        def substitute(match: re.Match[str]) -> str:
            index = int(match.group(1)) - 1
            target = abstract.levels.get(index, _Level())
            return format_number(abstract.counters.get(index, target.start), target.num_format)

        return _PLACEHOLDER.sub(substitute, level.text)


def parse_numbering(xml: bytes) -> Numbering:
    """Build :class:`Numbering` from the raw XML of ``numbering.xml``."""
    return Numbering(etree.fromstring(xml))
