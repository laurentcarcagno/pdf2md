"""Data models and settings shared by every pass of the pipeline."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ElementKind = Literal["header", "footer", "toc", "list_of_figures", "list_of_tables"]
ALL_ELEMENTS: tuple[ElementKind, ...] = (
    "header",
    "footer",
    "toc",
    "list_of_figures",
    "list_of_tables",
)
Engine = Literal["auto", "geometric", "docling", "marker", "ocr"]
SourcePass = Literal[
    "pass2_pdfplumber",
    "ocr_tesseract",
    "ocr_rapidocr",
    "docling",
    "marker",
    "pass3_pymupdf",
    "pymupdf_image",
    "docx",
]


class Settings(BaseModel):
    """Tunable thresholds; every heuristic of the pipeline reads them from here."""

    model_config = ConfigDict(frozen=True)

    # Pass 1 - classification
    scanned_char_threshold: int = Field(100, description="Below this many chars + images => scan")
    scanned_image_ratio: float = Field(0.5, description="Image area / page area for a scan")
    column_min_words: int = Field(80, description="Minimum words to look for a column gutter")
    column_gutter_ratio: float = Field(0.03, description="Gutter width / page width")
    column_side_ratio: float = Field(0.25, description="Minimum share of words on each side")
    complex_page_ratio: float = Field(0.5, description="Share of non-native pages => full fallback")

    # Pass 2 - cleaning
    margin_band_ratio: float = Field(0.12, description="Image logo band / page height")
    margin_search_ratio: float = Field(0.25, description="Outer band searched for headers/footers")
    margin_tolerance: float = Field(3.0, description="Max vertical drift (pt) of a recurring line")
    recurrence_ratio: float = Field(0.4, description="Share of pages a margin line must hit")

    # Pass 2 - structure
    heading_size_delta: float = Field(0.4, description="pt above body size to be a heading")
    paragraph_break_ratio: float = Field(1.8, description="Line pitch / font size for a new para")
    toc_min_lines: int = Field(5, description="Min TOC-like lines on a page to flag a TOC")
    snippet_length: int = Field(80, description="Max characters of the mapping snippet")

    # Output filtering: elements listed here are kept, every other one is removed
    keep: frozenset[ElementKind] = Field(frozenset(), description="Elements to keep")

    pages: str | None = Field(None, description="Page selection such as '1-5,10-20,30-'")

    # Images
    image_dpi: int = Field(200, description="Resolution of the extracted PNG figures")
    image_min_area: float = Field(2500.0, description="Smaller images (pt^2) are decoration")
    tables_as_images: int | None = Field(
        None, description="Render tables of at least this many rows as images (None: never)"
    )
    tables_keep_text: bool = Field(False, description="Keep the text of tables rendered as images")

    # Routing and OCR
    engine: Engine = Field("auto", description="Force one engine instead of the fallback chain")
    ocr_enabled: bool = True
    ocr_language: str = Field("fra+eng", description="Tesseract language codes")
    ocr_dpi: int = Field(300, description="Rendering resolution for OCR")
    ocr_min_chars: int = Field(30, description="Fewer OCR chars on a page => engine failed")
    garbled_ratio: float = Field(0.2, description="Share of U+FFFD chars => text layer unusable")

    @field_validator("pages")
    @classmethod
    def _check_pages(cls, value: str | None) -> str | None:
        if value is not None:
            from pdf2md.pages import parse_page_spec

            parse_page_spec(value)
        return value


class PageKind(StrEnum):
    """Verdict of the classification pass for one page."""

    NATIVE = "native_text"
    EMPTY = "empty"
    SCANNED = "scanned"
    MULTI_COLUMN = "multi_column"
    GARBLED = "garbled"


class PageProfile(BaseModel):
    """Classification result of one page."""

    page_number: int
    kind: PageKind
    char_count: int
    image_ratio: float
    reason: str


class Classification(BaseModel):
    """Document level classification (pass 1)."""

    document_kind: Literal["native_text", "complex"]
    pages: list[PageProfile]

    def pages_of(self, *kinds: PageKind) -> list[int]:
        """Return the page numbers whose kind is one of ``kinds``."""
        return [p.page_number for p in self.pages if p.kind in kinds]


class Coordinates(BaseModel):
    """Bounding box in PDF points, origin at the top-left corner of the page."""

    x0: float
    x1: float
    top: float
    bottom: float

    def union(self, other: Coordinates) -> Coordinates:
        """Return the smallest box containing both boxes."""
        return Coordinates(
            x0=min(self.x0, other.x0),
            x1=max(self.x1, other.x1),
            top=min(self.top, other.top),
            bottom=max(self.bottom, other.bottom),
        )

    def rounded(self) -> Coordinates:
        """Return a copy rounded to 0.1 pt for stable JSON output."""
        return Coordinates(
            x0=round(self.x0, 1),
            x1=round(self.x1, 1),
            top=round(self.top, 1),
            bottom=round(self.bottom, 1),
        )


class Line(BaseModel):
    """One visual text line with its typography."""

    page_number: int
    text: str
    coords: Coordinates
    size: float
    is_mono: bool
    element: ElementKind | None = None


class Table(BaseModel):
    """A table detected by pdfplumber."""

    page_number: int
    coords: Coordinates
    rows: list[list[str]]
    element: ElementKind | None = None


class PageContent(BaseModel):
    """Raw geometric content of a page (output of the extraction pass)."""

    page_number: int
    width: float
    height: float
    lines: list[Line]
    tables: list[Table]


BlockKind = Literal["heading", "paragraph", "list", "code", "table", "toc", "image"]


class Block(BaseModel):
    """A logical block of the final document (one mapping entry)."""

    page_number: int
    kind: BlockKind
    text: str
    level: int = 0
    rows: list[list[str]] = Field(default_factory=list)
    coords: Coordinates | None = None
    source_pass: SourcePass
    element: ElementKind | None = None
    image_path: str | None = None


class MappingEntry(BaseModel):
    """One line of the traceability JSON."""

    paragraph_id: int
    page_number: int
    paragraph_number_in_page: int
    kind: BlockKind
    snippet: str
    coordinates: Coordinates | None
    source_pass: str
    element: ElementKind | None = None
    image_path: str | None = None
