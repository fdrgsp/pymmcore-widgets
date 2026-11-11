"""Multiscale, chunked overview of stage-map tiles.

At low zoom, drawing chunks instead of individual tiles bounds VisPy's per-visual
overhead by the visible area. Levels are built lazily from block-averaged thumbnails
and updated as tiles arrive. Resampling respects the camera-to-stage affine.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from threading import Event
from time import perf_counter
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from vispy import scene
from vispy.scene.visuals import Image

if TYPE_CHECKING:
    from collections.abc import Hashable, Iterable

    from vispy.color import Colormap


# Side of one chunk, in overview pixels. Small enough that sparse, scattered
# tiles don't allocate much empty space; large enough that a full screen of
# the right level is only a few dozen chunks.
CHUNK_PX = 256
# The finest level holds one pixel per BASE_FACTOR x BASE_FACTOR source pixels.
BASE_FACTOR = 4
# Each further level halves the resolution of the previous one.
MAX_LEVELS = 6
MAX_OVERVIEW_BYTES = 1024**3
GUI_BATCH_ITEMS = 4
GUI_BATCH_SECONDS = 0.004

ChunkKey = tuple[int, int]  # (column, row) of the chunk in its level's grid
Bounds = tuple[float, float, float, float]  # world (x0, x1, y0, y1)


def downsampled(data: np.ndarray, factor: tuple[int, int]) -> np.ndarray:
    """Block-mean `data` by power-of-two `factor`, dropping any partial edge block.

    Averaging rather than striding keeps small bright features (beads, puncta)
    from vanishing or flickering between zoom levels. Blocks are summed by
    repeated pairwise halving, several times faster than a reshape-and-sum.
    """
    fy, fx = factor
    if fy == fx == 1:
        return data
    n = fy * fx
    h, w = data.shape[0] // fy, data.shape[1] // fx
    out = data[: h * fy, : w * fx]
    integer = np.issubdtype(data.dtype, np.integer)
    if integer:
        info = np.iinfo(data.dtype)
        unsigned_fits = info.min >= 0 and info.max * n < 2**32
        acc = np.dtype(np.uint32 if unsigned_fits else np.int64)
    else:
        acc = data.dtype
    while fy > 1 or fx > 1:
        if fy > 1:
            out = np.add(out[0::2], out[1::2], dtype=acc)
            fy //= 2
        if fx > 1:
            out = np.add(out[:, 0::2], out[:, 1::2], dtype=acc)
            fx //= 2
    return (out // n if integer else out / n).astype(data.dtype)


def scaled(
    matrix: np.ndarray, source_shape: tuple[int, ...], shown_shape: tuple[int, ...]
) -> np.ndarray:
    """Transform stretching a `shown_shape` image over the source's extent.

    Scaling by the shape ratio rather than the integer factor keeps the tile's
    world extent exact even when its size isn't a multiple of the factor.
    """
    sy = source_shape[0] / shown_shape[0]
    sx = source_shape[1] / shown_shape[1]
    if sx == sy == 1:
        return matrix
    return cast("np.ndarray", np.diag([sx, sy, 1.0, 1.0]) @ matrix)


def pixel_steps(matrix: np.ndarray) -> tuple[float, float]:
    """World length of one source pixel step along (rows, columns).

    `matrix` is a vispy (row-vector) transform: a step along x maps to row 0,
    a step along y to row 1.
    """
    return float(np.hypot(*matrix[1, :2])), float(np.hypot(*matrix[0, :2]))


def intersects(bounds: Bounds, rect: Any) -> bool:
    """Whether world `bounds` (x0, x1, y0, y1) overlap a vispy camera `rect`."""
    x0, x1, y0, y1 = bounds
    return not (x1 < rect.left or x0 > rect.right or y1 < rect.bottom or y0 > rect.top)


def pow2_at_most(ratio: float, size: int) -> int:
    """Largest power of two <= ratio (at least 1), never above `size`.

    Capped at a power of two too, so successive factors always divide evenly.
    """
    if ratio < 2 or size < 2:
        return 1
    return int(2 ** int(np.log2(min(ratio, size))))


class _LevelData:
    """Worker-owned pixels and geometry for one pyramid resolution."""

    def __init__(self, res: float) -> None:
        self.res = res
        self.built = False
        self.arrays: dict[ChunkKey, np.ndarray] = {}
        self.dirty: set[ChunkKey] = set()
        self.footprint: Counter[ChunkKey] = Counter()

    def chunk_keys(self, bounds: Bounds) -> list[ChunkKey]:
        """Chunks overlapped by world `bounds` (x0, x1, y0, y1)."""
        size = CHUNK_PX * self.res
        x0, x1, y0, y1 = (int(np.floor(v / size)) for v in bounds)
        return [(cx, cy) for cy in range(y0, y1 + 1) for cx in range(x0, x1 + 1)]

    def chunk_rect(self, key: ChunkKey) -> Bounds:
        """World (x0, x1, y0, y1) covered by a chunk."""
        size = CHUNK_PX * self.res
        return (key[0] * size, (key[0] + 1) * size, key[1] * size, (key[1] + 1) * size)

    def nbytes(self) -> int:
        return sum(arr.nbytes for arr in self.arrays.values())


@dataclass
class OverviewUpdate:
    """Immutable pixel buffers transferred from the worker to the GUI."""

    generation: int
    usable: bool
    levels: list[tuple[float, bool, Counter[ChunkKey], dict[ChunkKey, np.ndarray]]]
    nbytes: int


class OverviewData:
    """CPU-only pyramid. Accessed exclusively by one processing worker."""

    def __init__(self) -> None:
        self._levels: list[_LevelData] = []
        self._thumbs: dict[Hashable, tuple[np.ndarray, np.ndarray]] = {}
        self._bounds: dict[Hashable, Bounds] = {}
        self._matrices: dict[Hashable, np.ndarray] = {}
        self._dtype: np.dtype | None = None
        self._channels: tuple[int, ...] = ()
        self.usable = True
        self.generation = 0
        self.cancelled = Event()
        self.max_bytes = MAX_OVERVIEW_BYTES
        self._array_bytes = 0
        self._thumb_bytes = 0

    # ------------------------------------------------------------------ state

    @property
    def base_res(self) -> float | None:
        return self._levels[0].res if self._levels else None

    def nbytes(self) -> int:
        """Approximate memory held: thumbnails plus built levels."""
        return self._thumb_bytes + self._array_bytes

    def reset(self) -> None:
        """Drop everything; the next tile sets the pyramid's resolution again."""
        self._drop_levels()
        self._thumbs.clear()
        self._thumb_bytes = 0
        self._bounds.clear()
        self._matrices.clear()
        self._dtype = None
        self._channels = ()
        self.usable = True

    def snapshot(self) -> OverviewUpdate:
        """Freeze changed chunks; subsequent writes copy them first.

        Keep dirty keys until delivery is acknowledged, so rejecting a stale
        result cannot lose chunks needed by other locations.
        """
        levels = []
        for level in self._levels:
            arrays = {key: level.arrays[key] for key in level.dirty}
            for arr in arrays.values():
                arr.flags.writeable = False
            levels.append((level.res, level.built, level.footprint.copy(), arrays))
        return OverviewUpdate(self.generation, self.usable, levels, self.nbytes())

    # ------------------------------------------------------------- painting

    def set_tile(self, key: Hashable, data: np.ndarray, matrix: np.ndarray) -> None:
        """Add or refresh a tile (full-res `data`, row-vector `matrix`)."""
        if not self.usable:
            return
        rows, cols = pixel_steps(matrix)
        if rows == 0 or cols == 0:
            return
        if self._dtype is None:
            self._dtype = data.dtype
            self._channels = data.shape[2:]
        elif data.dtype != self._dtype or data.shape[2:] != self._channels:
            self.reset()
            self.usable = False
            return

        base = BASE_FACTOR * min(rows, cols)
        bounds = world_bounds(data.shape, matrix)
        old = self._bounds.pop(key, None)
        if self.base_res is None or base > 2 * self.base_res:
            # First tile, or one much coarser than the rest (e.g. after
            # switching to a low-magnification objective): base the levels on
            # it rather than upsampling it into many mostly-empty chunks.
            self._new_levels(base)
        elif old is not None and (
            _moved(old, bounds, self._levels[0].res)
            or not np.allclose(self._matrices[key][:2, :2], matrix[:2, :2])
        ):
            # A tile moved (e.g. a recalibrated pixel affine): built levels
            # still hold it at its old place, so start them over. Stage jitter
            # on a revisited location (under one overview pixel) is ignored.
            self._new_levels(self._levels[0].res)
        elif old is not None:
            for level in self._levels:
                level.footprint.subtract(level.chunk_keys(old))
        self._bounds[key] = bounds
        self._matrices[key] = matrix
        for level in self._levels:
            level.footprint.update(level.chunk_keys(bounds))
        thumb = _reduced(data, matrix, self._levels[0].res)

        previous = self._thumbs.pop(key, None)
        size = self._thumb_bytes + thumb[0].nbytes
        if previous is not None:
            size -= previous[0].nbytes
        if size + self._array_bytes > self.max_bytes:
            raise MemoryError("Overview cache budget exceeded")
        self._thumb_bytes = size
        self._thumbs[key] = thumb
        for level in self._levels:
            if level.built:
                self._paste(level, *_reduced(*thumb, level.res))

    def _build(self, level: _LevelData) -> None:
        """Paint every thumbnail into a level, oldest first."""
        for thumb, matrix in self._thumbs.values():
            if self.cancelled.is_set():
                return
            self._paste(level, *_reduced(thumb, matrix, level.res))
        level.built = True

    def _new_levels(self, base: float) -> None:
        """Replace the levels with unbuilt ones based at `base` world units/px.

        The thumbnails are kept: levels rebuild from them when next shown.
        """
        self._drop_levels()
        self._levels = [_LevelData(base * 2**i) for i in range(MAX_LEVELS)]
        for level in self._levels:
            for bounds in self._bounds.values():
                level.footprint.update(level.chunk_keys(bounds))

    def _drop_levels(self) -> None:
        self._levels = []
        self._array_bytes = 0
        self.generation += 1

    def _paste(self, level: _LevelData, small: np.ndarray, matrix: np.ndarray) -> None:
        """Resample `small` (placed by `matrix`) into the level's chunks.

        Nearest-neighbour sampling at each overview pixel's center: `small` is
        already block-averaged to about the level's resolution, so this only
        places it on the grid (rotating/shearing it if the transform does).
        """
        lin = matrix[:2, :2]  # world = [px, py] @ lin + off
        off = matrix[3, :2]
        try:
            inv = np.linalg.inv(lin)
        except np.linalg.LinAlgError:
            return
        h, w = small.shape[:2]
        corners = np.array([[0, 0], [w, 0], [0, h], [w, h]], dtype=float) @ lin + off
        res = level.res
        gx0, gy0 = np.floor(corners.min(axis=0) / res).astype(int)
        gx1, gy1 = np.ceil(corners.max(axis=0) / res).astype(int)
        if gx1 <= gx0 or gy1 <= gy0:
            return

        # Map every overview pixel center in the tile's bounding box back to
        # tile pixels; those that land inside the tile take its value.
        # Bound temporary affine grids as well as retained chunks. Extreme
        # shear/anisotropy must not allocate an unbounded bounding rectangle.
        if (gx1 - gx0) * (gy1 - gy0) * 64 > self.max_bytes:
            raise MemoryError("Overview resampling budget exceeded")
        wx = (np.arange(gx0, gx1) + 0.5) * res - off[0]
        wy = (np.arange(gy0, gy1) + 0.5) * res - off[1]
        px = wx[np.newaxis, :] * inv[0, 0] + wy[:, np.newaxis] * inv[1, 0]
        py = wx[np.newaxis, :] * inv[0, 1] + wy[:, np.newaxis] * inv[1, 1]
        col_idx = np.floor(px).astype(np.intp)
        row_idx = np.floor(py).astype(np.intp)
        inside = (col_idx >= 0) & (col_idx < w) & (row_idx >= 0) & (row_idx < h)

        for cy in range(gy0 // CHUNK_PX, (gy1 - 1) // CHUNK_PX + 1):
            if self.cancelled.is_set():
                return
            v0 = max(gy0, cy * CHUNK_PX)
            v1 = min(gy1, (cy + 1) * CHUNK_PX)
            for cx in range(gx0 // CHUNK_PX, (gx1 - 1) // CHUNK_PX + 1):
                u0 = max(gx0, cx * CHUNK_PX)
                u1 = min(gx1, (cx + 1) * CHUNK_PX)
                box = (slice(v0 - gy0, v1 - gy0), slice(u0 - gx0, u1 - gx0))
                mask = inside[box]
                if not mask.any():
                    continue
                key = (cx, cy)
                arr = self._chunk_array(level, key)
                dst = arr[
                    v0 - cy * CHUNK_PX : v1 - cy * CHUNK_PX,
                    u0 - cx * CHUNK_PX : u1 - cx * CHUNK_PX,
                ]
                dst[mask] = small[row_idx[box][mask], col_idx[box][mask]]
                level.dirty.add(key)

    def _chunk_array(self, level: _LevelData, key: ChunkKey) -> np.ndarray:
        arr = level.arrays.get(key)
        if arr is None:
            shape = (CHUNK_PX, CHUNK_PX, *self._channels)
            size = int(np.prod(shape)) * np.dtype(self._dtype).itemsize
            if self.nbytes() + size > self.max_bytes:
                raise MemoryError("Overview cache budget exceeded")
            arr = np.zeros(shape, dtype=self._dtype)
            self._array_bytes += size
            level.arrays[key] = arr
        elif not arr.flags.writeable:
            arr = arr.copy()
            level.arrays[key] = arr
        return arr

    def level_arrays(self, level_index: int) -> dict[ChunkKey, np.ndarray]:
        level = self._levels[level_index]
        if not level.built:
            self._build(level)
        return level.arrays


class _Level(_LevelData):
    def __init__(self, res: float, parent: scene.Node) -> None:
        super().__init__(res)
        self.node = scene.Node(parent=parent)
        self.node.visible = False
        self.images: dict[ChunkKey, Image] = {}


class OverviewPyramid:
    """GUI-only scene displaying immutable chunks prepared by OverviewData."""

    def __init__(self, parent: scene.Node, cmap: Colormap, clim: Any) -> None:
        self.node = scene.Node(parent=parent)
        self.node.order = -1
        self._cmap = cmap
        self._clim = clim
        self._levels: list[_Level] = []
        self._generation = -1
        self._nbytes = 0
        self.usable = True
        self.active_level: int | None = None

    @property
    def has_content(self) -> bool:
        return self.usable and bool(self._levels)

    def nbytes(self) -> int:
        # CPU pyramid, GUI snapshots, and approximate GL copies. Shared buffers
        # are deliberately counted twice, reserving room for copy-on-write.
        return self._nbytes + sum(
            level.nbytes() + sum(level.arrays[k].nbytes for k in level.images)
            for level in self._levels
        )

    def reset(self) -> None:
        for level in self._levels:
            level.node.parent = None
        self._levels.clear()
        self.active_level = None
        self._generation = -1
        self._nbytes = 0
        self.usable = True

    def accept(self, update: OverviewUpdate) -> None:
        """Adopt worker buffers on the GUI thread without resampling pixels."""
        if update.generation != self._generation:
            self.reset()
            self._generation = update.generation
            self._levels = [_Level(res, self.node) for res, *_ in update.levels]
        self.usable = update.usable
        self._nbytes = update.nbytes
        for level, (_, built, footprint, arrays) in zip(
            self._levels, update.levels, strict=True
        ):
            level.built = built
            level.footprint = footprint
            level.arrays.update(arrays)
            level.dirty.update(arrays)

    def set_clims(self, clim: Any) -> None:
        self._clim = clim
        for image in self._images():
            image.clim = clim

    def _new_image(self, level: _Level, key: ChunkKey, arr: np.ndarray) -> Image:
        image = Image(
            arr,
            cmap=self._cmap,
            clim=self._clim,
            parent=level.node,
            texture_format="auto",
        )
        x0, _, y0, _ = level.chunk_rect(key)
        image.transform = scene.STTransform(
            scale=(level.res, level.res, 1), translate=(x0, y0, 0)
        )
        return image

    # -------------------------------------------------------------- display

    def level_for(self, world_per_screen_px: float) -> int | None:
        """Coarsest level whose pixels are no larger than a screen pixel."""
        best = None
        for i, level in enumerate(self._levels):
            if level.res <= world_per_screen_px:
                best = i
        return best

    def chunks_in(self, level_index: int, rect: Any) -> int:
        """How many of a level's chunks would be drawn for camera `rect`."""
        level = self._levels[level_index]
        return sum(
            n > 0 and intersects(level.chunk_rect(key), rect)
            for key, n in level.footprint.items()
        )

    def level_arrays(self, level_index: int) -> dict[ChunkKey, np.ndarray]:
        """A level's prepared chunk arrays; never builds on the GUI thread."""
        return self._levels[level_index].arrays

    def show(
        self, level_index: int | None, rect: Any, limit: int = GUI_BATCH_ITEMS
    ) -> bool:
        """Show one level, drawing only its chunks that intersect `rect`."""
        self.active_level = level_index
        for i, level in enumerate(self._levels):
            visible = i == level_index
            if level.node.visible != visible:
                level.node.visible = visible
        if level_index is None:
            return False
        level = self._levels[level_index]
        started = perf_counter()
        remaining = False
        for key, arr in self.level_arrays(level_index).items():
            image = level.images.get(key)
            if not intersects(level.chunk_rect(key), rect):
                if image is not None and image.visible:
                    image.visible = False
                continue
            if (image is None or key in level.dirty) and (
                limit <= 0 or perf_counter() - started >= GUI_BATCH_SECONDS
            ):
                remaining = True
                continue
            if image is None:
                level.images[key] = self._new_image(level, key, arr)
                limit -= 1
            else:
                if key in level.dirty:
                    limit -= 1
                    # Keeps the array; the texture upload happens at the next draw.
                    image.set_data(arr)
                if not image.visible:
                    image.visible = True
            level.dirty.discard(key)
        return remaining

    def _images(self) -> Iterable[Image]:
        for level in self._levels:
            yield from level.images.values()


def world_bounds(shape: tuple[int, ...], matrix: np.ndarray) -> Bounds:
    h, w = shape[0], shape[1]
    corners = np.array([[0, 0], [w, 0], [0, h], [w, h]], dtype=float)
    world = corners @ matrix[:2, :2] + matrix[3, :2]
    (x0, y0), (x1, y1) = world.min(axis=0), world.max(axis=0)
    return float(x0), float(x1), float(y0), float(y1)


def _moved(old: Bounds, new: Bounds, tolerance: float) -> bool:
    return any(abs(a - b) > tolerance for a, b in zip(old, new, strict=True))


def _reduced(
    data: np.ndarray, matrix: np.ndarray, res: float
) -> tuple[np.ndarray, np.ndarray]:
    """`data` block-averaged to about `res` world units per pixel, and its transform.

    Per-axis powers of two, never coarser than `res`, so detail is kept along
    a more magnified axis of an anisotropic transform.
    """
    rows, cols = pixel_steps(matrix)
    factor = (
        pow2_at_most(res / rows, data.shape[0]),
        pow2_at_most(res / cols, data.shape[1]),
    )
    small = downsampled(data, factor)
    return small, scaled(matrix, data.shape, small.shape)
