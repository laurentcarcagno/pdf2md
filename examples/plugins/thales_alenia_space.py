r"""Plugin "Thales Alénia Space": mark the requirements written as ``#ID Title ... ##``.

In the PDF a requirement starts with ``#`` immediately followed by its ID (``EUR-DCH-000010``)
and a title, then the attributes (Parent(s), Assignment, Verification) and the text, and it
ends with ``##``. pdf2md merges the lines of a paragraph, so this plugin reads the Markdown
paragraph by paragraph, from the one starting with ``#<ID>`` to the one ending with ``##``
(the end marker can also be a paragraph of its own), and rewrites it as:

    <a id="eur-dch-000010"></a>**EUR-DCH-000010 Interconnection wiring**

    - Parent(s): none
    - Assignment:
    - Verification: [R, I]

    The DC harness shall be manufactured ...

    <!-- end EUR-DCH-000010 -->

The page header/footer left by the conversion is dropped too: paragraphs made only of
``CONTROLLATO NON CLASSIFICATO THALES ALENIA SPACE ITALIA``, ``REFERENCE: <id>``, ``DATE:
<dd/mm/yyyy>``, ``ISSUE: <n>`` and ``Page: x/y`` (in any order, with or without their values).

A requirement is thus found by ``^<a id="[^"]+"></a>\*\*(\S+) (.*)\*\*$`` and ends at the
``<!-- end ID -->`` comment. A requirement never closed ends before the next requirement, the
next heading or the end of the file, and is logged as a warning.

Register and use it:

    pdf2md --register-plugin "Thales Alénia Space" examples/plugins/thales_alenia_space.py
    pdf2md doc.pdf -o out --post-process "Thales Alénia Space"
"""

from __future__ import annotations

import logging
import re

from pdf2md.plugins import PluginContext

logger = logging.getLogger("pdf2md.plugins.thales_alenia_space")

_START = re.compile(r"^#(?P<id>[A-Za-z0-9]+(?:-[A-Za-z0-9]+){2,})(?:[ \t]+(?P<rest>.*))?$", re.S)
_HEADING = re.compile(r"^#{1,6}\s")
_END = re.compile(r"\s*##\s*$")
_FURNITURE_LABELS = re.compile(
    r"CONTROLLATO\s+NON\s+CLASSIFICATO\s+THALES\s+ALENIA\s+SPACE\s+ITALIA"
    r"|REFERENCE:|DATE:|ISSUE:|Page:\s*\d+\s*/\s*\d+",
    re.IGNORECASE,
)
# What may follow the labels: the reference, the date, the issue number
_FURNITURE_VALUES = re.compile(r"[A-Z]{2,}(?:-[A-Z0-9]+)+|\d{1,2}/\d{1,2}/\d{4}|\d{1,3}", re.I)
_LABELS = ("Parent(s):", "Assignment:", "Verification:")
_VERIFICATION = re.compile(r"\[[^\]]*\]")


def _is_page_furniture(paragraph: str) -> bool:
    """Return True for a paragraph that is only the repeated page header/footer."""
    if not _FURNITURE_LABELS.search(paragraph):
        return False
    rest = _FURNITURE_VALUES.sub("", _FURNITURE_LABELS.sub("", paragraph))
    return not rest.strip()


def _split_attributes(rest: str) -> tuple[str, dict[str, str], str]:
    """Split what follows the ID into (title, attributes, body)."""
    first = rest.find(_LABELS[0])
    if first < 0:
        title, _, body = rest.partition("\n")
        return title.strip(), {}, body.strip()
    title, remainder = rest[:first].strip(), rest[first:]
    positions = [(remainder.find(label), label) for label in _LABELS if label in remainder]
    positions.sort()
    attributes: dict[str, str] = {}
    body = ""
    for index, (start, label) in enumerate(positions):
        value_start = start + len(label)
        if label == "Verification:":
            found = _VERIFICATION.search(remainder, value_start)
            end = found.end() if found else len(remainder)
            attributes[label] = " ".join(remainder[value_start:end].split())
            body = remainder[end:]
            break
        end = positions[index + 1][0] if index + 1 < len(positions) else len(remainder)
        attributes[label] = " ".join(remainder[value_start:end].split())
        if index + 1 == len(positions):
            break
    else:
        body = ""
    return title, attributes, body.strip()


def _render(identifier: str, title: str, attributes: dict[str, str], body: list[str]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", identifier.lower()).strip("-")
    parts = [f'<a id="{slug}"></a>**{" ".join([identifier, title]).strip()}**']
    if attributes:
        parts.append(
            "\n".join(f"- {label} {attributes.get(label, '')}".rstrip() for label in _LABELS)
        )
    parts.extend(part for part in body if part)
    parts.append(f"<!-- end {identifier} -->")
    return "\n\n".join(parts)


def process(markdown: str, context: PluginContext) -> str:
    """Return the Markdown with every ``#ID ... ##`` requirement marked."""
    output: list[str] = []
    current: tuple[str, str, dict[str, str], list[str]] | None = None
    count = 0

    def close(reason: str = "") -> None:
        nonlocal current, count
        if current is None:
            return
        identifier, title, attributes, body = current
        if reason:
            logger.warning("requirement %s: %s", identifier, reason)
        output.append(_render(identifier, title, attributes, body))
        count += 1
        current = None

    dropped = 0
    # a caption ending with the "##" marker also ends up in the alt text of its table image
    markdown = re.sub(r"[ \t]+##\](?=\()", "]", markdown)
    for paragraph in markdown.split("\n\n"):
        if _is_page_furniture(paragraph):
            dropped += 1
            continue
        start = _START.match(paragraph.strip())
        if start:
            close("no closing ## before the next requirement")
            title, attributes, body = _split_attributes(start["rest"] or "")
            current = (start["id"], title, attributes, [])
            paragraph = body
        elif current is None:
            output.append(paragraph)
            continue
        elif _HEADING.match(paragraph):
            close("no closing ## before the next heading")
            output.append(paragraph)
            continue
        assert current is not None  # noqa: S101 - set above
        ended = _END.search(paragraph) is not None
        text = _END.sub("", paragraph).strip()
        if text:
            current[3].append(text)
        if ended:
            close()
    close("no closing ## before the end of the file")
    logger.info(
        "%s: %d requirement(s) marked, %d header/footer paragraph(s) removed",
        context.plugin_name,
        count,
        dropped,
    )
    return "\n\n".join(output)
