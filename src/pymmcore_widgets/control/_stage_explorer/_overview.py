"""Multiscale, chunked overview of the stage map's tiles.

Every tile in view is drawn by its own vispy `Image`, and vispy spends a fixed
slice of Python time per visual per frame (GL command building, uniform
updates) no matter how small its texture is. A zoomed-out map with hundreds of
tiles in view is therefore slow to pan/zoom even though each tile is
downsampled.

Like napari's multiscale rendering, the overview keeps a pyramid of reduced
copies of the map, each level cut into fixed-size chunks. When zoomed out, the
viewer shows the coarsest level whose pixels are still no larger than a screen
pixel (so nothing visible is lost) instead of the individual tiles. The number
of chunks on screen is then bounded by the canvas size, not by the number of
tiles on the map.

Each tile keeps a small, block-averaged thumbnail (1/BASE_FACTOR per axis).
A level is built from those thumbnails the first time it is shown, and kept up
to date from then on, so levels that are never looked at cost nothing -- which
matters for sparse maps, where a level's chunks are mostly empty space.
Painting resamples each thumbnail into the level's axis-aligned grid, so tiles
rotated or sheared by the pixel affine land in place too.
"""

from __future__ import annotations

from collections import Counter
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


class _Level:
    """One resolution of the pyramid: its pixel size and chunks."""

    __slots__ = ("arrays", "built", "dirty", "footprint", "images", "node", "res")

    def __init__(self, res: float, parent: scene.Node) -> None:
        self.res = res  # world units per overview pixel
        self.node = scene.Node(parent=parent)
        self.node.visible = False
        # False until first shown; only built levels are painted into.
        self.built = False
        self.arrays: dict[ChunkKey, np.ndarray] = {}
        # Scene nodes are created, and textures re-sent, only for chunks on
        # screen: each node costs a shader build on its first draw.
        self.images: dict[ChunkKey, Image] = {}
        self.dirty: set[ChunkKey] = set()
        # How many tiles' bounds touch each chunk, built or not: estimates a
        # level's draw count before paying to build it.
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
        """Chunk arrays plus the GL-side copies of the chunks drawn so far."""
        held = sum(arr.nbytes for arr in self.arrays.values())
        return held + sum(self.arrays[key].nbytes for key in self.images)


class OverviewPyramid:
    """Chunked multiscale copies of the map, shown in place of zoomed-out tiles."""

    def __init__(self, parent: scene.Node, cmap: Colormap, clim: Any) -> None:
        self.node = scene.Node(parent=parent)
        # Below the grid lines and overlays (order 0), like the tiles it replaces.
        self.node.order = -1
        self._cmap = cmap
        self._clim = clim
        self._levels: list[_Level] = []
        # Per tile: thumbnail and its (row-vector) transform, in paint order:
        # a refreshed tile moves to the end so it is painted on top.
        self._thumbs: dict[Hashable, tuple[np.ndarray, np.ndarray]] = {}
        self._bounds: dict[Hashable, Bounds] = {}
        self._dtype: np.dtype | None = None
        self._channels: tuple[int, ...] = ()
        # False once a tile that can't share the pyramid's dtype/channels shows
        # up; the viewer then keeps drawing tiles until the map is cleared.
        self.usable = True
        self.active_level: int | None = None

    # ------------------------------------------------------------------ state

    @property
    def base_res(self) -> float | None:
        return self._levels[0].res if self._levels else None

    @property
    def has_content(self) -> bool:
        return self.usable and bool(self._thumbs)

    def nbytes(self) -> int:
        """Approximate memory held: thumbnails plus built levels."""
        thumbs = sum(thumb.nbytes for thumb, _ in self._thumbs.values())
        return thumbs + sum(level.nbytes() for level in self._levels)

    def reset(self) -> None:
        """Drop everything; the next tile sets the pyramid's resolution again."""
        self._drop_levels()
        self._thumbs.clear()
        self._bounds.clear()
        self._dtype = None
        self._channels = ()
        self.usable = True

    def set_clims(self, clim: Any) -> None:
        self._clim = clim
        for image in self._images():
            image.clim = clim

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
        elif old is not None and _moved(old, bounds, self._levels[0].res):
            # A tile moved (e.g. a recalibrated pixel affine): built levels
            # still hold it at its old place, so start them over. Stage jitter
            # on a revisited location (under one overview pixel) is ignored.
            self._new_levels(self._levels[0].res)
        elif old is not None:
            for level in self._levels:
                level.footprint.subtract(level.chunk_keys(old))
        self._bounds[key] = bounds
        for level in self._levels:
            level.footprint.update(level.chunk_keys(bounds))
        thumb = _reduced(data, matrix, self._levels[0].res)

        self._thumbs.pop(key, None)
        self._thumbs[key] = thumb
        for level in self._levels:
            if level.built:
                self._paste(level, *_reduced(*thumb, level.res))

    def _build(self, level: _Level) -> None:
        """Paint every thumbnail into a level, oldest first."""
        for thumb, matrix in self._thumbs.values():
            self._paste(level, *_reduced(thumb, matrix, level.res))
        level.built = True

    def _new_levels(self, base: float) -> None:
        """Replace the levels with unbuilt ones based at `base` world units/px.

        The thumbnails are kept: levels rebuild from them when next shown.
        """
        self._drop_levels()
        self._levels = [_Level(base * 2**i, self.node) for i in range(MAX_LEVELS)]
        for level in self._levels:
            for bounds in self._bounds.values():
                level.footprint.update(level.chunk_keys(bounds))

    def _drop_levels(self) -> None:
        for level in self._levels:
            level.node.parent = None
        self._levels = []
        self.active_level = None

    def _paste(self, level: _Level, small: np.ndarray, matrix: np.ndarray) -> None:
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
        wx = (np.arange(gx0, gx1) + 0.5) * res - off[0]
        wy = (np.arange(gy0, gy1) + 0.5) * res - off[1]
        px = wx[np.newaxis, :] * inv[0, 0] + wy[:, np.newaxis] * inv[1, 0]
        py = wx[np.newaxis, :] * inv[0, 1] + wy[:, np.newaxis] * inv[1, 1]
        col_idx = np.floor(px).astype(np.intp)
        row_idx = np.floor(py).astype(np.intp)
        inside = (col_idx >= 0) & (col_idx < w) & (row_idx >= 0) & (row_idx < h)

        for cy in range(gy0 // CHUNK_PX, (gy1 - 1) // CHUNK_PX + 1):
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

    def _chunk_array(self, level: _Level, key: ChunkKey) -> np.ndarray:
        if (arr := level.arrays.get(key)) is None:
            arr = np.zeros((CHUNK_PX, CHUNK_PX, *self._channels), dtype=self._dtype)
            level.arrays[key] = arr
        return arr

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
        """A level's chunk arrays, building the level first if needed."""
        level = self._levels[level_index]
        if not level.built:
            self._build(level)
        return level.arrays

    def show(self, level_index: int | None, rect: Any) -> None:
        """Show one level, drawing only its chunks that intersect `rect`."""
        self.active_level = level_index
        for i, level in enumerate(self._levels):
            level.node.visible = i == level_index
        if level_index is None:
            return
        level = self._levels[level_index]
        for key, arr in self.level_arrays(level_index).items():
            image = level.images.get(key)
            if not intersects(level.chunk_rect(key), rect):
                if image is not None:
                    image.visible = False
                continue
            if image is None:
                level.images[key] = self._new_image(level, key, arr)
            else:
                if key in level.dirty:
                    # Keeps the array; the texture upload happens at the next draw.
                    image.set_data(arr)
                image.visible = True
            level.dirty.discard(key)

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
