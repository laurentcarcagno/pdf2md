"""Exceptions raised by the conversion pipeline."""


class PdfOpenError(Exception):
    """Raised when a PDF cannot be opened (missing, corrupted, encrypted, not a PDF)."""


class DocxOpenError(Exception):
    """Raised when a Word file cannot be opened (missing, corrupted, legacy .doc, not a .docx)."""
