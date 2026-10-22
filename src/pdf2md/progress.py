"""Page progress reporting (tqdm bar on stderr), shared by every engine."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from tqdm import tqdm


class PageProgress:
    """Progress bar counting pages; ``tick`` is idempotent per page.

    A page may be attempted by several engines of a fallback chain, but it is counted once.
    With ``enabled=None`` the bar is shown only when stderr is a terminal.
    """

    def __init__(
        self, total: int, description: str = "", enabled: bool | None = None, unit: str = "page"
    ) -> None:
        self._seen: set[int] = set()
        self._bar = tqdm(
            total=total,
            desc=description,
            unit=unit,
            disable=(not enabled) if enabled is not None else None,
            dynamic_ncols=True,
        )

    @property
    def completed(self) -> int:
        """Number of distinct pages counted so far."""
        return len(self._seen)

    def tick(self, page_number: int) -> None:
        """Count ``page_number`` as processed (no-op if already counted)."""
        if page_number not in self._seen:
            self._seen.add(page_number)
            self._bar.update(1)

    def stage(self, text: str) -> None:
        """Show what is currently running (engine name) at the end of the bar."""
        self._bar.set_postfix_str(text, refresh=True)

    def close(self) -> None:
        """Finish and remove the bar."""
        self._bar.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
