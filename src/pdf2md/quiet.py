"""Silence the INFO chatter of heavy third-party libraries (model probes, downloads, HTTP)."""

from __future__ import annotations

import logging

_NOISY = ("docling", "RapidOCR", "httpx", "huggingface_hub", "surya", "marker")


class _MinWarning(logging.Filter):
    """Drop records below WARNING.

    A filter survives libraries that call ``setLevel`` again when they initialise (RapidOCR does).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if "CUDAExecutionProvider is not in available providers" in record.getMessage():
            return False  # Docling asks RapidOCR for CUDA; on AMD it falls back to CPU, harmlessly
        return record.levelno >= logging.WARNING


def quiet_third_party_logs() -> None:
    """Hide INFO/DEBUG records of noisy libraries; safe to call several times."""
    for name in _NOISY:
        logger = logging.getLogger(name)
        logger.setLevel(logging.WARNING)
        if not any(isinstance(f, _MinWarning) for f in logger.filters):
            logger.addFilter(_MinWarning())
