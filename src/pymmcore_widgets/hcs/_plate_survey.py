from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from ._image_ops import bilinear

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event

    from pymmcore_plus import CMMCorePlus

# Sanity bound on scan time; memory is bounded separately by the user's limit.
MAX_TILES = 2500
MAX_BYTES = 512 * 1024**2
# Stored tiles keep ~2000 pixels across a well: detection needs ~1000.
PIXELS_PER_WELL = 2000


def storage_binning(pixel_um: float, well_um: float) -> int:
    """Camera-pixel block size averaged per stored pixel for this well size."""
    return max(1, int(well_um / PIXELS_PER_WELL / pixel_um))


def bin_image(image: np.ndarray, binning: int) -> np.ndarray:
    """Average `binning` x `binning` blocks, cropping the remainder symmetrically."""
    if binning == 1:
        return image
    h, w = (n // binning * binning for n in image.shape)
    y0, x0 = (image.shape[0] - h) // 2, (image.shape[1] - w) // 2
    blocks = image[y0 : y0 + h, x0 : x0 + w].reshape(
        h // binning, binning, w // binning, binning
    )
    return np.asarray(blocks.mean(axis=(1, 3)).astype(image.dtype))


@dataclass(frozen=True)
class SurveyGeometry:
    """Immutable physical survey bounds and verified pixel-to-stage mapping.

    `shape` and `pixel_to_stage` describe the stored tiles: camera frames are
    averaged in `binning` x `binning` blocks during acquisition.
    """

    bounds: tuple[float, float, float, float]
    shape: tuple[int, int]
    pixel_to_stage: tuple[float, float, float, float]
    overlap: float = 0.2
    binning: int = 1

    def matrix(self) -> np.ndarray:
        """Return the linear map from raw (column, row) pixels to stage µm."""
        matrix = np.asarray(self.pixel_to_stage).reshape(2, 2)
        if not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-12:
            raise ValueError("A valid camera-to-stage affine calibration is required.")
        return matrix

    def grid(self) -> tuple[int, int]:
        """Return the (columns, rows) of overlapping frames covering the bounds."""
        x0, y0, x1, y1 = self.bounds
        if not np.isfinite(self.bounds).all() or x1 <= x0 or y1 <= y0:
            raise ValueError("The scan area needs a positive width and height.")
        h, w = self.shape
        if min(h, w) <= 0 or not 0 <= self.overlap < 1:
            raise ValueError("Invalid image size or overlap.")
        corners = np.array([(x0, y0), (x0, y1), (x1, y0), (x1, y1)])
        span = np.ptp(corners @ np.linalg.inv(self.matrix()).T, axis=0)
        steps = np.array([w, h]) * (1 - self.overlap)
        counts = np.maximum(1, np.ceil(np.maximum(0, span - [w, h]) / steps) + 1)
        nx, ny = (int(x) for x in counts)
        return nx, ny

    def positions(self) -> list[tuple[float, float]]:
        """Cover the stage rectangle with overlapping, possibly rotated frames."""
        nx, ny = self.grid()
        if nx * ny > MAX_TILES:
            raise ValueError(
                f"The scan needs {nx} x {ny} = {nx * ny} tiles; the limit is "
                f"{MAX_TILES}. Use a lower magnification or select fewer wells."
            )
        x0, y0, x1, y1 = self.bounds
        h, w = self.shape
        matrix = self.matrix()
        center = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
        steps = np.array([w, h]) * (1 - self.overlap)
        result = []
        for row in range(ny):
            for col in range(nx) if row % 2 == 0 else reversed(range(nx)):
                offset = (
                    (col - (nx - 1) / 2) * steps[0],
                    (row - (ny - 1) / 2) * steps[1],
                )
                x, y = center + matrix @ offset
                result.append((float(x), float(y)))
        return result


@dataclass(frozen=True)
class SurveyTile:
    """An owned raw frame and its settled stage position."""

    image: np.ndarray
    xy: tuple[float, float]


def acquire_survey(
    core: CMMCorePlus,
    geometry: SurveyGeometry,
    cancel: Event,
    progress: Callable[[int, int], None],
    on_tile: Callable[[SurveyTile], None] | None = None,
    max_bytes: float = MAX_BYTES,
) -> tuple[SurveyTile, ...]:
    """Acquire at current brightfield settings, without changing objective or Z."""
    if core.mda.is_running() or core.isSequenceRunning():
        raise ValueError("Stop live view or the running acquisition before surveying.")
    positions = geometry.positions()
    if len(positions) * np.prod(geometry.shape) * core.getBytesPerPixel() > max_bytes:
        raise ValueError(
            f"The scan exceeds the {max_bytes / 1024**3:.1f} GB memory limit. "
            "Raise the limit or select fewer wells."
        )

    # Settings that define tile geometry and intensity. Focus (Z) is deliberately
    # excluded: drive readouts jitter and hardware autofocus moves it, while the
    # XY calibration does not depend on it.
    def camera_state() -> dict[str, object]:
        return {
            "camera": core.getCameraDevice(),
            "pixel size configuration": core.getCurrentPixelSizeConfig(),
            "camera ROI": core.getROI(),
            "pixel size": core.getPixelSizeUm(),
            "exposure": core.getExposure(),
        }

    def changed_settings() -> str:
        current = camera_state()
        return ", ".join(k for k, v in initial.items() if current[k] != v)

    initial = camera_state()
    shutter = core.getShutterDevice()
    was_open = core.getShutterOpen() if shutter else False
    tiles = []
    used_bytes = 0
    try:
        for index, xy in enumerate(positions):
            if cancel.is_set():
                raise InterruptedError(
                    "Survey cancelled; previous calibration retained."
                )
            if core.mda.is_running() or core.isSequenceRunning():
                raise ValueError("Another acquisition started during the scan.")
            if changed := changed_settings():
                raise ValueError(f"Changed during the scan: {changed}. Scan again.")
            core.setXYPosition(*xy)
            core.waitForSystem()
            x, y = core.getXYPosition()
            image = np.array(core.snap(), copy=True)
            if changed := changed_settings():
                raise ValueError(f"Changed during exposure: {changed}. Scan again.")
            if not np.allclose(core.getXYPosition(), (x, y), rtol=0, atol=1):
                raise ValueError("The stage moved during exposure. Repeat the survey.")
            if image.ndim == 2:
                image = bin_image(image, geometry.binning)
            if image.ndim != 2 or image.shape != geometry.shape:
                raise ValueError(
                    "Use a monochrome camera with unchanged frame dimensions."
                )
            used_bytes += image.nbytes
            if used_bytes > max_bytes:
                raise ValueError("The scan exceeded its memory limit.")
            tile = SurveyTile(image, (x, y))
            tiles.append(tile)
            if on_tile is not None:
                on_tile(tile)
            progress(index + 1, len(positions))
    finally:
        # Preserve manually opened shutters too, including on snap failure.
        if shutter:
            core.setShutterOpen(shutter, was_open)
    if cancel.is_set():
        raise InterruptedError("Survey cancelled; previous calibration retained.")
    return tuple(tiles)


def survey_mosaic(
    tiles: tuple[SurveyTile, ...],
    geometry: SurveyGeometry,
    diameter_um: float,
    max_side: int = 1024,
) -> tuple[np.ndarray, np.ndarray, tuple[float, float], float]:
    """Create a stage-registered overview of everything imaged, plus a valid mask.

    The overview spans the tiles' footprints, not just the scan corners: frames
    extend past the corners, and wells there are fully imaged.
    """
    h, w = geometry.shape
    footprint = np.array(
        [(-w / 2, -h / 2), (-w / 2, h / 2), (w / 2, -h / 2), (w / 2, h / 2)]
    )
    all_corners = [footprint @ geometry.matrix().T + tile.xy for tile in tiles]
    x0, y0 = np.min(all_corners, axis=(0, 1))
    x1, y1 = np.max(all_corners, axis=(0, 1))
    pixel_um = max(np.linalg.svd(geometry.matrix(), compute_uv=False))
    scale = max(pixel_um, diameter_um / 200, (x1 - x0) / max_side, (y1 - y0) / max_side)
    shape = (int(np.ceil((y1 - y0) / scale)) + 1, int(np.ceil((x1 - x0) / scale)) + 1)
    image = np.zeros(shape, dtype=np.float32)
    weights = np.zeros(shape, dtype=np.uint16)
    inverse = np.linalg.inv(geometry.matrix())
    for tile, corners in zip(tiles, all_corners, strict=True):
        lo = np.maximum(0, np.floor((corners.min(axis=0) - [x0, y0]) / scale)).astype(
            int
        )
        hi = np.minimum(
            shape[::-1], np.ceil((corners.max(axis=0) - [x0, y0]) / scale) + 1
        ).astype(int)
        xx, yy = np.meshgrid(np.arange(lo[0], hi[0]), np.arange(lo[1], hi[1]))
        stage = np.stack([xx * scale + x0 - tile.xy[0], yy * scale + y0 - tile.xy[1]])
        uv = (
            np.einsum("ij,jkl->ikl", inverse, stage)
            + np.array([w / 2, h / 2])[:, None, None]
        )
        valid = (uv[0] >= 0) & (uv[0] < w - 1) & (uv[1] >= 0) & (uv[1] < h - 1)
        data = bilinear(tile.image.astype(np.float32), uv[1], uv[0])
        slc = np.s_[lo[1] : hi[1], lo[0] : hi[0]]
        image[slc] += data * valid
        weights[slc] += valid
    np.divide(image, weights, out=image, where=weights > 0)
    return image, weights > 0, (x0, y0), float(scale)
