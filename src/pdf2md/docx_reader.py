"""Read a Word document (.docx) into the same blocks as the PDF pipeline produces."""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

from pdf2md.docx_numbering import Numbering, parse_numbering
from pdf2md.docx_table_html import StyleMap, table_element_to_html
from pdf2md.models import Block, BlockKind, ElementKind

logger = logging.getLogger(__name__)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {
    "w": W,
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "v": "urn:schemas-microsoft-com:vml",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}


def _w(tag: str) -> str:
    return f"{{{W}}}{tag}"


_SKIP_TEXT = {
    _w("del"), _w("instrText"), _w("delText"), _w("txbxContent"), _w("pPr"), _w("rPr"),
    f"{{{NS['mc']}}}Fallback",
}  # fmt: skip
_X_BLIPS = etree.XPath(".//a:blip/@r:embed | .//v:imagedata/@r:id", namespaces=NS)
_X_DOCPR = etree.XPath(".//wp:docPr/@descr", namespaces=NS)
_X_TEXTBOX_PARAS = etree.XPath(
    ".//mc:Choice//w:txbxContent/w:p | .//w:pict//w:txbxContent/w:p", namespaces=NS
)
_X_RENDERED_BREAKS = etree.XPath(".//w:lastRenderedPageBreak", namespaces=NS)

_HEADING_NAME = re.compile(r"^(?:heading|titre|überschrift|título)\s*(\d)", re.IGNORECASE)
_TOC_STYLE = re.compile(r"^(?:toc|table des matières|sommaire)\s*\d*$", re.IGNORECASE)
_FIGURES_STYLE = re.compile(r"^(?:table of figures|table des illustrations)$", re.IGNORECASE)
_CODE_STYLE = re.compile(r"code|preformatted|verbatim|macro text", re.IGNORECASE)
_MONO_FONT = re.compile(r"courier|consolas|mono|menlo", re.IGNORECASE)
_LETTER_LABEL = re.compile(r"^[a-z][.)]$")
_MIN_DECORATION_REPEATS = 3
_SUBITEM_SPACES = "   "
_IMAGE_EXTENSIONS = {
    "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/bmp": "bmp",
    "image/tiff": "tiff", "image/x-emf": "emf", "image/x-wmf": "wmf", "image/svg+xml": "svg",
}  # fmt: skip


@dataclass
class DocxContent:
    """Result of reading a Word file."""

    blocks: list[Block]
    images: dict[str, tuple[bytes, str]] = field(default_factory=dict)  # name -> (bytes, ext)
    page_count: int = 1


@dataclass
class _State:
    """Mutable parsing state shared by the helpers."""

    document: Any
    numbering: Numbering
    rendered_mode: bool
    decorations: set[str]
    blocks: list[Block] = field(default_factory=list)
    images: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    page: int = 1
    image_counter: dict[int, int] = field(default_factory=dict)
    field_stack: list[ElementKind | None] = field(default_factory=list)
    open_list: list[int] = field(default_factory=list)  # [base ilvl] of the list being built
    code_open: bool = False
    tables_html: bool = False
    styles: StyleMap | None = None


def _text_of(element: Any) -> str:
    parts: list[str] = []

    def walk(node: Any) -> None:
        for child in node:
            tag = child.tag
            if tag in _SKIP_TEXT:
                continue
            if tag == _w("t"):
                parts.append(child.text or "")
            elif tag in (_w("tab"), _w("br"), _w("cr")):
                parts.append(" ")
            elif tag == _w("noBreakHyphen"):
                parts.append("-")
            elif isinstance(tag, str):
                walk(child)

    walk(element)
    return " ".join("".join(parts).split())


def _style_chain(document: Any, paragraph_el: Any) -> list[Any]:
    """Return the ``w:style`` elements of a paragraph, from its own style to its ancestors."""
    style_id = paragraph_el.find("w:pPr/w:pStyle", NS)
    chain: list[Any] = []
    seen: set[str] = set()
    wanted = style_id.get(_w("val")) if style_id is not None else None
    styles = {s.get(_w("styleId")): s for s in document.styles.element.findall("w:style", NS)}
    if wanted is None:
        default = next(
            (
                s
                for s in styles.values()
                if s.get(_w("type")) == "paragraph" and s.get(_w("default")) == "1"
            ),
            None,
        )
        return [default] if default is not None else []
    while wanted and wanted not in seen and wanted in styles:
        seen.add(wanted)
        chain.append(styles[wanted])
        based = styles[wanted].find("w:basedOn", NS)
        wanted = based.get(_w("val")) if based is not None else None
    return chain


def _style_name(chain: list[Any]) -> str:
    if not chain:
        return ""
    name = chain[0].find("w:name", NS)
    return (name.get(_w("val")) if name is not None else "") or ""


def _heading_level(paragraph_el: Any, chain: list[Any]) -> int | None:
    for style in chain:
        name = style.find("w:name", NS)
        label = name.get(_w("val")) if name is not None else ""
        match = _HEADING_NAME.match(label or "")
        if match:
            return int(match.group(1))
        if (label or "").lower() == "title":
            return 1
    for source in [paragraph_el, *chain]:
        outline = source.find("w:pPr/w:outlineLvl", NS)
        if outline is not None:
            level = int(outline.get(_w("val")) or 9)
            if level < 9:
                return level + 1
    return None


def _numpr(paragraph_el: Any, chain: list[Any], numbering: Numbering) -> tuple[str, int] | None:
    """Return ``(numId, ilvl)`` of the paragraph, looking at its style chain too."""
    own = paragraph_el.find("w:pPr/w:numPr", NS)
    sources = [(own, None)] + [(s.find("w:pPr/w:numPr", NS), s) for s in chain]
    ilvl: int | None = None
    for numpr, style in sources:
        if numpr is None:
            continue
        num = numpr.find("w:numId", NS)
        level = numpr.find("w:ilvl", NS)
        if level is not None and ilvl is None:
            ilvl = int(level.get(_w("val")) or 0)
        if num is None:
            continue
        num_id = num.get(_w("val")) or "0"
        if num_id == "0":
            return None
        if ilvl is None and style is not None:
            ilvl = numbering.level_for_style(num_id, style.get(_w("styleId")) or "")
        return num_id, ilvl or 0
    return None


def _is_code(paragraph_el: Any, chain: list[Any], text: str) -> bool:
    """Monospace paragraphs: every text run uses a monospace font, or the style says "code".

    A style named "HTML Preformatted" whose runs override the font with Arial is prose.
    """
    runs = [r for r in paragraph_el.findall("w:r", NS) if "".join(r.itertext()).strip()]
    fonts = [
        font.get(_w("ascii")) or "" for run in runs for font in run.findall("w:rPr/w:rFonts", NS)
    ]
    if any(not _MONO_FONT.search(f) for f in fonts):
        return False
    if _CODE_STYLE.search(_style_name(chain)):
        return True
    return bool(text) and bool(runs) and len(fonts) == len(runs)


def _field_element(instruction: str) -> ElementKind | None:
    """Map a field instruction to the kind of index it builds (TOC, table of figures/tables)."""
    words = instruction.strip()
    if not words.upper().startswith("TOC"):
        return None
    match = re.search(r'\\c\s+"?([^"\\]+)"?', words)
    if match:
        label = match.group(1).strip().lower()
        if label.startswith(("tab",)):
            return "list_of_tables"
        return "list_of_figures"
    return "toc"


def _field_context(state: _State, paragraph_el: Any) -> ElementKind | None:
    """Track Word fields across paragraphs; return the index kind the paragraph is part of."""
    inside = next((kind for kind in reversed(state.field_stack) if kind), None)
    for node in paragraph_el.iter(_w("fldChar"), _w("instrText"), _w("fldSimple")):
        if node.tag == _w("fldChar"):
            kind = node.get(_w("fldCharType"))
            if kind == "begin":
                state.field_stack.append(None)
            elif kind == "end" and state.field_stack:
                state.field_stack.pop()
        elif node.tag == _w("instrText"):
            element = _field_element(node.text or "")
            if element and state.field_stack:
                state.field_stack[-1] = element
                inside = element
        else:
            element = _field_element(node.get(_w("instr")) or "")
            if element:
                inside = element
    return inside


def _page_events(paragraph_el: Any, rendered_mode: bool) -> tuple[int, int]:
    """Return ``(breaks before the first text, breaks after it)`` for a paragraph."""
    before = after = 0
    seen_text = False
    explicit_before = paragraph_el.find("w:pPr/w:pageBreakBefore", NS)
    if not rendered_mode and explicit_before is not None:
        before += 1
    for node in paragraph_el.iter(_w("t"), _w("lastRenderedPageBreak"), _w("br")):
        if node.tag == _w("t") and (node.text or "").strip():
            seen_text = True
        elif node.tag == _w("lastRenderedPageBreak") and rendered_mode:
            if seen_text:
                after += 1
            else:
                before += 1
        elif node.tag == _w("br") and not rendered_mode and node.get(_w("type")) == "page":
            if seen_text:
                after += 1
            else:
                before += 1
    section = paragraph_el.find("w:pPr/w:sectPr", NS)
    if section is not None and not rendered_mode:
        kind = section.find("w:type", NS)
        if kind is None or kind.get(_w("val")) != "continuous":
            after += 1
    return before, after


def _block(
    state: _State,
    kind: BlockKind,
    text: str,
    *,
    level: int = 0,
    element: ElementKind | None = None,
    rows: list[list[str]] | None = None,
    html: str | None = None,
) -> Block:
    return Block(
        page_number=state.page,
        kind=kind,
        text=text,
        level=level,
        rows=rows or [],
        element=element,
        source_pass="docx",
        html=html,
    )


def _close_list(state: _State) -> None:
    state.open_list.clear()


def _emit_images(state: _State, paragraph_el: Any) -> list[Block]:
    blocks: list[Block] = []
    description = (_X_DOCPR(paragraph_el) or [""])[0]
    for relationship in _X_BLIPS(paragraph_el):
        try:
            part = state.document.part.related_parts[relationship]
        except KeyError:
            logger.warning("page %d: image %s not found in the package", state.page, relationship)
            continue
        blob = part.blob
        if hashlib.sha1(blob).hexdigest() in state.decorations:  # noqa: S324 - dedupe only
            continue
        extension = _IMAGE_EXTENSIONS.get(part.content_type, Path(str(part.partname)).suffix[1:])
        if extension in ("emf", "wmf"):
            logger.warning(
                "page %d: %s image kept as-is, browsers cannot show it", state.page, extension
            )
        index = state.image_counter.get(state.page, 0) + 1
        state.image_counter[state.page] = index
        name = f"p{state.page:04d}_{index:02d}.{extension}"
        state.images[name] = (blob, extension)
        image = _block(state, "image", description.strip())
        image.image_path = f"assets/{name}"
        blocks.append(image)
    return blocks


def _paragraph(state: _State, paragraph_el: Any, forced: ElementKind | None) -> None:
    before, after = _page_events(paragraph_el, state.rendered_mode)
    state.page += before
    chain = _style_chain(state.document, paragraph_el)
    text = _text_of(paragraph_el)
    style_name = _style_name(chain)
    field_kind = _field_context(state, paragraph_el)
    images = _emit_images(state, paragraph_el)

    index_kind: ElementKind | None = forced or field_kind
    if index_kind is None and _FIGURES_STYLE.match(style_name):
        index_kind = (
            "list_of_tables" if re.match(r"^(table|tableau)\b", text, re.I) else "list_of_figures"
        )
    if index_kind is None and (_TOC_STYLE.match(style_name) or style_name.lower() == "toc heading"):
        index_kind = "toc"

    if text:
        _text_block(state, paragraph_el, chain, text, index_kind)
    for image in images:
        _close_list(state)
        state.blocks.append(image)
    for textbox in _X_TEXTBOX_PARAS(paragraph_el):
        extra = _text_of(textbox)
        if extra:
            _close_list(state)
            state.blocks.append(_block(state, "paragraph", extra))
    state.page += after


def _text_block(
    state: _State, paragraph_el: Any, chain: list[Any], text: str, index_kind: ElementKind | None
) -> None:
    if index_kind is not None:
        _close_list(state)
        state.blocks.append(_block(state, "toc", text, element=index_kind))
        return
    numbering = _numpr(paragraph_el, chain, state.numbering)
    label = state.numbering.label(*numbering) if numbering else None
    heading = _heading_level(paragraph_el, chain)
    if heading is not None:
        _close_list(state)
        full = f"{label} {text}" if label and label != "-" else text
        state.blocks.append(_block(state, "heading", full, level=min(heading, 6)))
        return
    if _is_code(paragraph_el, chain, text):
        _close_list(state)
        previous = state.blocks[-1] if state.blocks else None
        if state.code_open and previous is not None and previous.kind == "code":
            previous.text += "\n" + text
        else:
            state.blocks.append(_block(state, "code", text))
        state.code_open = True
        return
    state.code_open = False
    if numbering and label:
        _numbered(state, numbering[1], label, text)
        return
    _close_list(state)
    state.blocks.append(_block(state, "paragraph", text))


def _numbered(state: _State, ilvl: int, label: str, text: str) -> None:
    """Add a numbered/bulleted paragraph: lettered top-level items stay separate paragraphs."""
    if _LETTER_LABEL.match(label) and (not state.open_list or ilvl <= state.open_list[0]):
        _close_list(state)
        state.blocks.append(_block(state, "paragraph", f"{label} {text}"))
        return
    previous = state.blocks[-1] if state.blocks else None
    if state.open_list and previous is not None and previous.kind == "list":
        depth = max(ilvl - state.open_list[0], 0)
        previous.text += "\n" + _SUBITEM_SPACES * depth + f"{label} {text}"
        return
    state.open_list[:] = [ilvl]
    state.blocks.append(_block(state, "list", f"{label} {text}"))


def _table(state: _State, table_el: Any) -> None:
    breaks_inside = len(_X_RENDERED_BREAKS(table_el)) if state.rendered_mode else 0
    rows: list[list[str]] = []
    for row_el in table_el.findall("w:tr", NS):
        cells: list[str] = []
        for cell_el in row_el.findall("w:tc", NS):
            vertical = cell_el.find("w:tcPr/w:vMerge", NS)
            continued = vertical is not None and vertical.get(_w("val")) in (None, "continue")
            text = " ".join(t for t in (_text_of(p) for p in cell_el.iter(_w("p"))) if t)
            cells.append("" if continued else text)
        if any(cells):
            rows.append(cells)
    _close_list(state)
    state.code_open = False
    if not rows:
        return
    if max(len(r) for r in rows) < 2:
        for row in rows:
            state.blocks.append(_block(state, "paragraph", row[0]))
    else:
        html = None
        if state.tables_html and state.styles is not None:
            html = table_element_to_html(table_el, state.styles)
        state.blocks.append(_block(state, "table", " | ".join(rows[0]), rows=rows, html=html))
    state.page += breaks_inside


def _walk_body(
    state: _State, parent: Any, forced: ElementKind | None, tick: Callable[[], None]
) -> None:
    for child in parent:
        if child.tag == _w("p"):
            _paragraph(state, child, forced)
            tick()
        elif child.tag == _w("tbl"):
            _table(state, child)
            tick()
        elif child.tag == _w("sdt"):
            gallery = child.find("w:sdtPr/w:docPartObj/w:docPartGallery", NS)
            is_toc = (
                gallery is not None
                and (gallery.get(_w("val")) or "").lower() == "table of contents"
            )
            content = child.find("w:sdtContent", NS)
            if content is not None:
                _walk_body(state, content, "toc" if is_toc else forced, tick)


def _merge_index_blocks(blocks: list[Block]) -> list[Block]:
    """Merge consecutive index entries of the same kind into one block (like the PDF pass)."""
    merged: list[Block] = []
    for block in blocks:
        last = merged[-1] if merged else None
        if (
            last is not None
            and block.kind == "toc"
            and last.kind == "toc"
            and block.element == last.element
        ):
            last.text += "\n" + block.text
        else:
            merged.append(block)
    return merged


def _header_footer_blocks(document: Any, last_page: int) -> tuple[list[Block], list[Block]]:
    headers: list[Block] = []
    footers: list[Block] = []
    seen: set[tuple[str, str]] = set()
    for section in document.sections:
        for kind, parts, bucket, page in (
            (
                "header",
                (section.header, section.first_page_header, section.even_page_header),
                headers,
                1,
            ),
            (
                "footer",
                (section.footer, section.first_page_footer, section.even_page_footer),
                footers,
                last_page,
            ),
        ):
            for part in parts:
                if part.is_linked_to_previous:
                    continue
                for paragraph in part.paragraphs:
                    text = " ".join(_text_of(paragraph._p).split())
                    key = (kind, re.sub(r"\d+", "#", text.lower()))
                    if text and key not in seen:
                        seen.add(key)
                        bucket.append(
                            Block(
                                page_number=page,
                                kind="paragraph",
                                text=text,
                                element=kind,
                                source_pass="docx",
                            )
                        )
    return headers, footers


def read_docx(
    document: Any, tick: Callable[[], None] | None = None, tables_html: bool = False
) -> DocxContent:
    """Convert an opened ``python-docx`` document to blocks, in reading order.

    Page numbers are estimates: Word does not store pagination, so they come from the
    ``lastRenderedPageBreak`` marks of the last save (or, if absent, from explicit page and
    section breaks). With ``tables_html`` tables also carry their HTML (shading, spans and run
    formatting; formatting that only comes from a table style is not resolved).
    """
    body = document.element.body
    try:
        numbering_xml = document.part.numbering_part.blob
        numbering = parse_numbering(numbering_xml)
    except (KeyError, NotImplementedError, AttributeError):
        numbering = Numbering(None)

    hashes: Counter[str] = Counter()
    for relationship in _X_BLIPS(body):
        try:
            hashes[hashlib.sha1(document.part.related_parts[relationship].blob).hexdigest()] += 1  # noqa: S324
        except KeyError:
            continue
    state = _State(
        document=document,
        numbering=numbering,
        rendered_mode=bool(_X_RENDERED_BREAKS(body)),
        decorations={h for h, n in hashes.items() if n >= _MIN_DECORATION_REPEATS},
        tables_html=tables_html,
        styles=StyleMap(document) if tables_html else None,
    )
    _walk_body(state, body, None, tick or (lambda: None))
    headers, footers = _header_footer_blocks(document, state.page)
    blocks = headers + _merge_index_blocks(state.blocks) + footers
    return DocxContent(blocks=blocks, images=state.images, page_count=state.page)
