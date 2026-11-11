"""Background image preparation for Stage Explorer."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._overview import MAX_OVERVIEW_BYTES, OverviewData, OverviewUpdate, downsampled

if TYPE_CHECKING:
    import numpy as np


@dataclass
class PreparedImages:
    """One completed job; pixel buffers are immutable after publication."""

    overview: OverviewUpdate | None = None
    shown: np.ndarray | None = None


def prepare_images(
    model: OverviewData,
    key: int | None,
    data: np.ndarray | None,
    matrix: np.ndarray | None,
    factor: tuple[int, int] | None,
    level: int | None,
    acknowledge: bool,
    max_bytes: int,
) -> PreparedImages:
    """Prepare a tile or a lazy overview level, with no GUI objects in scope."""
    if model.cancelled.is_set():
        return PreparedImages()
    model.max_bytes = max_bytes
    if acknowledge:
        for item in model._levels:
            item.dirty.clear()
    try:
        if key is not None:
            assert data is not None and matrix is not None
            model.set_tile(key, data, matrix)
        elif level is not None:
            model.level_arrays(level)
    except MemoryError:
        model.reset()
        model.usable = False
    overview = model.snapshot() if key is not None or level is not None else None
    shown = None
    if data is not None and factor is not None and not model.cancelled.is_set():
        shown = downsampled(data, factor)
        shown.flags.writeable = False
    return PreparedImages(overview, shown)


class ImageProcessor:
    """Serial CPU worker with one pending job and no references to the widget."""

    def __init__(self) -> None:
        self.model = OverviewData()
        self.future: Future[PreparedImages] | None = None
        self._executor: ThreadPoolExecutor | None = None
        self.closed = False
        self.accepted = True

    def submit(
        self,
        key: int | None = None,
        data: np.ndarray | None = None,
        matrix: np.ndarray | None = None,
        factor: tuple[int, int] | None = None,
        level: int | None = None,
        max_bytes: int = MAX_OVERVIEW_BYTES,
    ) -> None:
        assert self.future is None and not self.closed
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="stage-images"
            )
        self.future = self._executor.submit(
            prepare_images,
            self.model,
            key,
            data,
            matrix,
            factor,
            level,
            self.accepted,
            max_bytes,
        )

    def reset(self) -> None:
        self.model.cancelled.set()
        self.model = OverviewData()
        self.accepted = True

    def close(self) -> None:
        """Cancel a build and join the worker, including on QObject destruction."""
        self.closed = True
        self.model.cancelled.set()
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
        self.future = None
        self.model.reset()
