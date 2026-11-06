from __future__ import annotations

import itertools
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import numpy as np

from ._plate_calibration import (
    min_calibration_wells,
    supports_automated_calibration,
    valid_calibration_selection,
)
from ._plate_survey import SurveyGeometry, SurveyTile, survey_mosaic

if TYPE_CHECKING:
    from collections.abc import Collection
    from threading import Event

    import useq


# Plate rotations searched when proposing non-circular wells.
ROTATION_SEARCH_DEG = np.arange(-15, 16, 5)


@dataclass(frozen=True)
class DetectionSettings:
    """Parameters for the well-rim detector."""

    edge_sigma: float = 1.5
    hough_threshold: float = 0.25
    min_coverage: float = 0.75

    def __post_init__(self) -> None:
        if not np.isfinite(
            [self.edge_sigma, self.hough_threshold, self.min_coverage]
        ).all():
            raise ValueError("Detection settings must be finite.")
        if not (
            0 < self.edge_sigma <= 10
            and 0 < self.hough_threshold <= 1
            and 0.5 <= self.min_coverage <= 1
        ):
            raise ValueError(
                "Invalid edge smoothing, detection threshold, or edge coverage."
            )


@dataclass(frozen=True)
class DetectedWell:
    """Measured well rim in stage coordinates, with geometric diagnostics.

    Circles only set `radius_um`; ellipses and rectangles also set their half
    width/height (`half_size_um`) and in-plane rotation.
    """

    xy: tuple[float, float]
    radius_um: float
    residual_um: float
    coverage: float
    half_size_um: tuple[float, float] | None = None
    rotation_deg: float = 0.0
    rectangular: bool = False

    def outline(self, n: int = 129) -> np.ndarray:
        """Return closed rim points in stage coordinates."""
        a, b = self.half_size_um or (self.radius_um, self.radius_um)
        t = np.linspace(0, 2 * np.pi, n)
        if self.rectangular:
            local = np.array([(-a, -b), (a, -b), (a, b), (-a, b), (-a, -b)])
        else:
            local = np.column_stack([a * np.cos(t), b * np.sin(t)])
        theta = np.deg2rad(self.rotation_deg)
        rot = np.array(
            [[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]]
        )
        return np.asarray(local @ rot.T + self.xy)


@dataclass(frozen=True)
class DetectionResult:
    """Overview for review and measured centers; contains no applied calibration."""

    image: np.ndarray
    origin: tuple[float, float]
    pixel_um: float
    wells: tuple[DetectedWell, ...]


def _intensity_range(values: np.ndarray) -> tuple[float, float]:
    """Return the 1-99 percentile range used to scale every image to [0, 1]."""
    lo, hi = np.percentile(values, (1, 99))
    if not np.isfinite([lo, hi]).all() or hi <= lo:
        raise ValueError(
            "Insufficient image contrast. "
            "Adjust brightfield and focus on the well edges."
        )
    return float(lo), float(hi)


def _normalize(image: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return np.asarray(np.clip((image.astype(float) - lo) / (hi - lo), 0, 1))


def _robust_circle(
    points: np.ndarray, radius: float, tolerance: float
) -> tuple[np.ndarray, np.ndarray, float] | None:
    """Radius-constrained RANSAC followed by geometric-distance refinement."""
    from scipy.optimize import least_squares

    best: np.ndarray | None = None
    params: np.ndarray | None = None
    rng = np.random.default_rng(0)
    for _ in range(200):
        sample = points[rng.choice(len(points), 3, replace=False)]
        # Translate before solving to avoid cancellation at large stage offsets.
        origin = sample[0]
        delta = sample[1:] - origin
        if abs(np.linalg.det(delta)) < radius**2 * 1e-6:
            continue
        center = np.linalg.solve(2 * delta, np.sum(delta**2, axis=1))
        proposed_radius = np.linalg.norm(center)
        if not radius * 0.85 <= proposed_radius <= radius * 1.15:
            continue
        center += origin
        distances = np.linalg.norm(points - center, axis=1)
        inliers = np.abs(distances - proposed_radius) < tolerance
        if best is None or inliers.sum() > best.sum():
            best, params = inliers, np.array([*center, proposed_radius])
    if best is None or params is None or best.sum() < 36:
        return None
    # Recenter for stable optimization even with very large absolute XY values.
    origin = params[:2].copy()
    centered = points[best] - origin
    result = least_squares(
        lambda p: np.linalg.norm(centered - p[:2], axis=1) - p[2],
        [0, 0, params[2]],
        loss="soft_l1",
        f_scale=tolerance,
    )
    if not result.success:
        return None
    params = result.x
    params[:2] += origin
    residuals = np.abs(np.linalg.norm(points - params[:2], axis=1) - params[2])
    inliers = residuals < tolerance
    if inliers.sum() < 36:
        return None
    return params, inliers, float(np.sqrt(np.mean(residuals[inliers] ** 2)))


def _shape_distance(
    points: np.ndarray, params: np.ndarray, rectangular: bool
) -> np.ndarray:
    """Signed distance (approximate for ellipses) from points to a rotated rim."""
    cx, cy, a, b, theta = params
    c, s = np.cos(theta), np.sin(theta)
    dx, dy = points[:, 0] - cx, points[:, 1] - cy
    u, v = dx * c + dy * s, -dx * s + dy * c
    if rectangular:
        qx, qy = np.abs(u) - a, np.abs(v) - b
        outside = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0))
        return np.asarray(outside + np.minimum(np.maximum(qx, qy), 0))
    r = np.maximum(np.hypot(u / a, v / b), 1e-9)
    return np.asarray((r - 1) * np.hypot(u, v) / r)


def _fit_shape(
    points: np.ndarray,
    center: np.ndarray,
    half: tuple[float, float],
    rectangular: bool,
    tolerance: float,
) -> tuple[np.ndarray, np.ndarray, float] | None:
    """Robust least-squares fit of a rotated ellipse or rectangle near a proposal.

    Returns the fitted (x, y, half width, half height, rotation), the inlier rim
    points, and their RMS residual.
    """
    from scipy.optimize import least_squares

    a, b = half
    params = np.array([0, 0, a, b, 0.0])
    # Recenter for stable optimization even with very large absolute XY values.
    local = points - center
    # Reselect the rim band around each fit so the ends of rotated wells count.
    for i in range(4):
        distance = _shape_distance(local, params, rectangular)
        band = np.abs(distance) < min(half) * 0.2
        if i == 3:
            # A dark rim yields two parallel edges; fit the better-sampled one
            # (as circle RANSAC does) rather than the gap between them.
            inner = band & (distance < 0)
            band = inner if inner.sum() > (band & ~inner).sum() else band & ~inner
        rim = local[band]
        if len(rim) < 36:
            return None
        result = least_squares(
            lambda p, rim=rim: _shape_distance(rim, p, rectangular),
            params,
            bounds=(
                [-a, -b, a * 0.85, b * 0.85, -np.pi / 4],
                [a, b, a * 1.15, b * 1.15, np.pi / 4],
            ),
            loss="soft_l1",
            f_scale=tolerance,
        )
        if not result.success:
            return None
        params = result.x
    residuals = np.abs(_shape_distance(rim, params, rectangular))
    inliers = residuals < tolerance
    if inliers.sum() < 36:
        return None
    params[:2] += center
    rms = float(np.sqrt(np.mean(residuals[inliers] ** 2)))
    return params, rim[inliers] + center, rms


def _outline_template(
    a: float, b: float, rotation_deg: float, rectangular: bool
) -> np.ndarray:
    """Rasterize a 1-px rotated rim outline, centered in an odd-sized array."""
    corners = DetectedWell((0, 0), min(a, b), 0, 0, (a, b), rotation_deg, rectangular)
    vertices = corners.outline(int(4 * (a + b)) + 8)
    # Densify each segment so straight rectangle sides have no gaps.
    points = np.concatenate(
        [
            np.linspace(p, q, int(np.ceil(np.hypot(*(q - p)))) + 1)
            for p, q in itertools.pairwise(vertices)
        ]
    )
    r = int(np.ceil(np.hypot(a, b))) + 1
    template = np.zeros((2 * r + 1, 2 * r + 1))
    template[
        np.rint(points[:, 1] + r).astype(int), np.rint(points[:, 0] + r).astype(int)
    ] = 1
    return np.asarray(template / template.sum())


def _imaged(
    points: np.ndarray, tiles: tuple[SurveyTile, ...], geometry: SurveyGeometry
) -> bool:
    """Whether every stage point lies inside at least one tile's footprint."""
    inverse = np.linalg.inv(geometry.matrix())
    half = np.array(geometry.shape[::-1]) / 2
    covered = np.zeros(len(points), dtype=bool)
    for tile in tiles:
        uv = (points - tile.xy) @ inverse.T
        covered |= np.all(np.abs(uv) <= half, axis=1)
    return bool(covered.all())


def _propose_shapes(
    edges: np.ndarray,
    half_px: tuple[float, float],
    rectangular: bool,
    threshold: float,
    num_peaks: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Correlate edges with rim templates; peaks are the fraction of rim on edges.

    Templates span 85-115% of the nominal size and +/-15 degrees of plate rotation.
    """
    from scipy.signal import fftconvolve
    from skimage.feature import peak_local_max

    accumulator = np.zeros(edges.shape)
    for scale in np.linspace(0.85, 1.15, 7):
        for angle in ROTATION_SEARCH_DEG:
            a, b = (h * scale for h in half_px)
            template = _outline_template(a, b, float(angle), rectangular)
            response = fftconvolve(edges.astype(float), template, "same")
            np.maximum(accumulator, response, out=accumulator)
    peaks = peak_local_max(
        accumulator,
        min_distance=max(1, int(min(half_px))),
        threshold_abs=threshold,
        num_peaks=num_peaks,
        exclude_border=False,
    )
    return peaks[:, 1], peaks[:, 0]


def detect_wells(
    tiles: tuple[SurveyTile, ...],
    geometry: SurveyGeometry,
    plate: useq.WellPlate,
    cancel: Event,
    settings: DetectionSettings | None = None,
) -> DetectionResult:
    """Propose rims on an overview and refine using original tile edge evidence."""
    settings = settings or DetectionSettings()
    if not tiles:
        raise ValueError("No survey images were acquired.")
    if not supports_automated_calibration(plate):
        raise ValueError(
            "A single rectangular well cannot be calibrated automatically. "
            "Use Manual calibration."
        )
    from skimage.feature import canny
    from skimage.transform import hough_circle, hough_circle_peaks

    half_um = (plate.well_size[0] * 500, plate.well_size[1] * 500)
    circular = plate.circular_wells and np.isclose(*half_um)
    rectangular = not plate.circular_wells
    diameter = 2 * max(half_um)
    image, valid, origin, scale = survey_mosaic(tiles, geometry, diameter)
    # Normalize only measured pixels; missing mosaic data is never a dark rim.
    # Tiles share this range: stretching each tile alone would turn the noise of
    # background-only tiles into edges.
    lo, hi = _intensity_range(image[valid])
    overview = np.zeros_like(image)
    overview[valid] = _normalize(image[valid], lo, hi)
    edges = canny(overview, sigma=settings.edge_sigma, mask=valid)
    half_px = (half_um[0] / scale, half_um[1] / scale)
    if min(half_px) * 0.85 < 5:
        raise ValueError("Overview resolution is insufficient for well-edge detection.")
    if cancel.is_set():
        raise InterruptedError("Detection cancelled.")
    num_peaks = min(64, plate.rows * plate.columns)
    if circular:
        radius_px = half_px[0]
        radii = np.unique(
            np.linspace(radius_px * 0.85, radius_px * 1.15, 17).astype(int)
        )
        hough = hough_circle(edges, radii)
        _, cx, cy, _ = hough_circle_peaks(
            hough,
            radii,
            min_xdistance=max(1, int(radius_px)),
            min_ydistance=max(1, int(radius_px)),
            threshold=settings.hough_threshold,
            total_num_peaks=num_peaks,
            normalize=True,
        )
        del hough
    else:
        cx, cy = _propose_shapes(
            edges, half_px, rectangular, settings.hough_threshold, num_peaks
        )
    pixel_um = float(np.linalg.norm(geometry.matrix(), axis=0).max())
    # Find rim edges at ~1/1000 of the well size: finer pixels only add cost and
    # spread soft rims too thinly for the fixed-scale edge detector.
    f = max(1, round(diameter / 1000 / pixel_um))
    raw_edges = []
    for tile in tiles:
        if cancel.is_set():
            raise InterruptedError("Detection cancelled.")
        frame = tile.image
        if f > 1:
            h, w = (n - n % f for n in frame.shape)
            frame = frame[:h, :w].reshape(h // f, f, w // f, f).mean(axis=(1, 3))
        rows, cols = np.nonzero(canny(_normalize(frame, lo, hi), sigma=2))
        # Bound clutter cost while retaining angular evidence across every frame.
        per_tile = min(100_000, max(1_000, 2_000_000 // len(tiles)))
        stride = max(1, int(np.ceil(len(rows) / per_tile)))
        # Binned pixel centers back in original camera pixel coordinates.
        uv = np.column_stack([cols[::stride], rows[::stride]]) * f + (f - 1) / 2
        raw_edges.append(
            (uv - np.array(geometry.shape[::-1]) / 2) @ geometry.matrix().T + tile.xy
        )
    points = np.concatenate(raw_edges) if raw_edges else np.empty((0, 2))
    radius_um = min(half_um)
    residual_limit = max(pixel_um * f * 2, diameter * 0.002)
    wells: list[DetectedWell] = []
    for x, y in zip(cx, cy, strict=True):
        if cancel.is_set():
            raise InterruptedError("Detection cancelled.")
        proposal = np.array(origin) + np.array([x, y]) * scale
        if circular:
            distance = np.linalg.norm(points - proposal, axis=1) - radius_um
            rim = points[np.abs(distance) < radius_um * 0.2]
            if len(rim) < 36:
                continue
            fitted = _robust_circle(rim, radius_um, residual_limit)
            if fitted is None:
                continue
            (xc, yc, radius), inliers, rms = fitted
            if not radius_um * 0.85 <= radius <= radius_um * 1.15:
                continue
            rim = rim[inliers]
            well = DetectedWell((float(xc), float(yc)), float(radius), rms, 0)
        else:
            shape = _fit_shape(points, proposal, half_um, rectangular, residual_limit)
            if shape is None:
                continue
            (xc, yc, a, b, theta), rim, rms = shape
            well = DetectedWell(
                (float(xc), float(yc)),
                float(min(a, b)),
                rms,
                0,
                (float(a), float(b)),
                float(np.rad2deg(theta)),
                rectangular,
            )
        angles = np.arctan2(rim[:, 1] - yc, rim[:, 0] - xc)
        sectors = ((angles + np.pi) * 36 / (2 * np.pi)).astype(int) % 36
        coverage = len(np.unique(sectors)) / 36
        if coverage < settings.min_coverage:
            continue
        # The whole rim must have been imaged. Frames extend past the scan corners,
        # so test against the tiles' footprints, not the corner rectangle.
        if not _imaged(well.outline(), tiles, geometry):
            continue
        if any(np.hypot(xc - w.xy[0], yc - w.xy[1]) < radius_um for w in wells):
            continue
        wells.append(replace(well, coverage=coverage))
    if not wells:
        raise ValueError(
            "Found no complete wells. "
            "Enlarge the scan or improve well-edge focus/brightfield contrast."
        )
    return DetectionResult(overview, origin, scale, tuple(wells))


def assign_well_indices(
    wells: tuple[DetectedWell, ...],
    plate: useq.WellPlate,
    anchor: tuple[int, int],
    rotation: float | None = None,
) -> dict[tuple[int, int], DetectedWell]:
    """Suggest lattice labels from an explicit minimum-row/column anchor and pose."""
    if len(wells) == 1:
        return {anchor: wells[0]}
    if rotation is None:
        # Adjacent row and column vectors give the same angle modulo 90 degrees.
        # Choose the normal holder orientation (columns closest to +stage X).
        centers = np.array([well.xy for well in wells])
        differences = centers[:, None] - centers[None, :]
        distances = np.linalg.norm(differences, axis=-1)
        pitch = np.array(plate.well_spacing) * 1000
        pitch = pitch[pitch > 0]
        if not len(pitch):
            raise ValueError("The plate must have a positive well spacing.")
        adjacent = np.any(np.abs(distances[..., None] / pitch - 1) < 0.15, axis=-1)
        vectors = differences[adjacent]
        if not len(vectors):
            raise ValueError("Cannot find neighboring wells. Check the detections.")
        angles = np.arctan2(vectors[:, 1], vectors[:, 0]) * 4
        angle = np.arctan2(np.sin(angles).mean(), np.cos(angles).mean()) / 4
    else:
        angle = np.deg2rad(rotation)
    axes = np.array([[np.cos(angle), np.sin(angle)], [np.sin(angle), -np.cos(angle)]])
    xy = np.array([well.xy for well in wells]) @ axes.T
    pitch = np.array(plate.well_spacing) * 1000
    active = np.array([plate.columns > 1, plate.rows > 1])
    if np.any(~np.isfinite(pitch[active]) | (pitch[active] <= 0)):
        raise ValueError("The plate must have positive finite spacing between wells.")
    grid = np.zeros_like(xy)
    grid[:, active] = (xy[:, active] - xy[:, active].min(axis=0)) / pitch[active]
    rounded = np.rint(grid)
    if np.any(np.abs(grid - rounded) > 0.15):
        raise ValueError(
            "Centers do not match the expected pitch/orientation. Edit labels."
        )
    assigned = {}
    for (col, row), well in zip(rounded.astype(int), wells, strict=True):
        index = (int(row) + anchor[0], int(col) + anchor[1])
        if (
            index in assigned
            or not 0 <= index[0] < plate.rows
            or not 0 <= index[1] < plate.columns
        ):
            raise ValueError("Duplicate or out-of-plate well labels. Edit the labels.")
        assigned[index] = well
    return assigned


def _grid_shape(indices: Collection[tuple[int, int]]) -> str:
    """Return ' (rows x columns)' when indices fill a rectangular block."""
    rows, cols = {r for r, _ in indices}, {c for _, c in indices}
    n_rows, n_cols = max(rows) - min(rows) + 1, max(cols) - min(cols) + 1
    return f" ({n_rows} x {n_cols})" if n_rows * n_cols == len(indices) else ""


def match_selected_wells(
    wells: tuple[DetectedWell, ...],
    plate: useq.WellPlate,
    selected: tuple[tuple[int, int], ...],
) -> dict[tuple[int, int], DetectedWell]:
    """Match a measured local grid to the wells selected before scanning."""
    if len(wells) != len(selected):
        detected = ""
        with suppress(ValueError):  # the detections may not form a clean grid
            detected = _grid_shape(assign_well_indices(wells, plate, (0, 0)))
        raise ValueError(
            f"Detected {len(wells)} wells{detected}, but {len(selected)} wells "
            f"are selected{_grid_shape(selected)}. Select every well whose "
            "entire edge is in the scan area, or adjust detection settings."
        )
    if not valid_calibration_selection(selected, min_calibration_wells(plate)):
        raise ValueError(
            "Select 3+ wells in one row/column or a non-collinear group, "
            "such as a 2 x 2 block."
        )
    anchor = min(r for r, _ in selected), min(c for _, c in selected)
    assigned = assign_well_indices(wells, plate, anchor)
    if set(assigned) != set(selected):
        raise ValueError(
            "The detected well arrangement does not match the selected wells. "
            "Check the selection and detection settings."
        )
    return assigned
