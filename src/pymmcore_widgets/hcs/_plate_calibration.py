from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import useq

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping


def min_calibration_wells(plate: useq.WellPlate) -> int:
    """Wells needed to calibrate: three, or every well of a smaller plate."""
    return min(3, plate.rows * plate.columns)


def supports_automated_calibration(plate: useq.WellPlate) -> bool:
    """Every plate except a single rectangular well (no lattice, no circle)."""
    return plate.circular_wells or plate.rows * plate.columns > 1


def valid_calibration_selection(
    indices: Collection[tuple[int, int]], min_wells: int = 3
) -> bool:
    """Accept enough distinct wells in one row/column or a non-collinear group."""
    if len(indices) < min_wells or len(set(indices)) != len(indices):
        return False
    if len({r for r, _ in indices}) == 1 or len({c for _, c in indices}) == 1:
        return True
    return bool(
        np.linalg.matrix_rank(np.column_stack([list(indices), np.ones(len(indices))]))
        == 3
    )


def fit_plate_calibration(
    centers: Mapping[tuple[int, int], tuple[float, float]],
    plate: useq.WellPlate | None = None,
) -> tuple[tuple[float, float], tuple[float, float], float]:
    """Fit indexed centers to A1, the measured well spacing, and rotation.

    With a plate, A1 and rotation are fitted at the plate's nominal spacing: the
    plan lays wells out at that spacing, so fitting it with a measured spacing
    would shift A1 and every well. The returned spacing is still measured (using
    the nominal perpendicular spacing for a single row/column), for reporting.
    """
    if plate is not None and len(centers) == 1:
        # A single well fixes the origin only: nominal spacing, no rotation.
        (row, col), (x, y) = next(iter(centers.items()))
        dx, dy = plate.well_spacing[0] * 1000, plate.well_spacing[1] * 1000
        return (round(x - col * dx, 4), round(y + row * dy, 4)), plate.well_spacing, 0.0
    rows, cols = {r for r, _ in centers}, {c for _, c in centers}
    if plate is not None and (len(rows) == 1 or len(cols) == 1):
        result = fit_collinear_calibration(plate, centers, len(rows) == 1)
        if result is None:
            raise ValueError("Well centers are coincident; cannot determine rotation.")
        spacing = result[1]
    else:
        a, b, ty, c, d, tx = well_coords_affine(centers)
        if plate is None:
            return (
                (round(float(tx), 4), round(float(ty), 4)),
                (float(np.hypot(b, d) / 1000), float(np.hypot(a, c) / 1000)),
                round(float(np.rad2deg(np.arctan2(-c, a))), 2),
            )
        spacing = (float(np.hypot(b, d) / 1000), float(np.hypot(a, c) / 1000))
    origin, rotation = fit_plate_pose(centers, plate.well_spacing)
    return origin, spacing, rotation


def fit_plate_pose(
    centers: Mapping[tuple[int, int], tuple[float, float]],
    well_spacing: tuple[float, float],
) -> tuple[tuple[float, float], float]:
    """Least-squares A1 center (µm) and rotation (degrees) at a fixed spacing (mm).

    Uses `useq.WellPlatePlan`'s model: well (row, col) lies at
    A1 + R(rotation) @ (col * spacing_x, -row * spacing_y).
    """
    indices = np.array(list(centers), dtype=float)
    measured = np.array(list(centers.values()), dtype=float)
    nominal = (
        np.column_stack(
            [indices[:, 1] * well_spacing[0], -indices[:, 0] * well_spacing[1]]
        )
        * 1000
    )
    u, q = nominal - nominal.mean(axis=0), measured - measured.mean(axis=0)
    if not np.any(u):
        raise ValueError("Well centers are coincident; cannot determine rotation.")
    # 2D Procrustes: the angle that best rotates the nominal onto measured offsets.
    cross = np.sum(u[:, 0] * q[:, 1] - u[:, 1] * q[:, 0])
    rotation = round(float(np.rad2deg(np.arctan2(cross, np.sum(u * q)))), 2)
    # Place A1 for the rounded angle the plan will actually use.
    theta = np.deg2rad(rotation)
    rot = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    tx, ty = measured.mean(axis=0) - rot @ nominal.mean(axis=0)
    return (round(float(tx), 4), round(float(ty), 4)), rotation


def validated_calibration_plan(
    plate: useq.WellPlate,
    centers: Mapping[tuple[int, int], tuple[float, float]],
    max_error_um: float,
) -> tuple[useq.WellPlatePlan, float]:
    """Validate centers against the actual nominal-pitch plan before committing."""
    if not valid_calibration_selection(centers.keys(), min_calibration_wells(plate)):
        raise ValueError(
            "Select three or more wells in one row/column, "
            "or a non-collinear group (a 2 x 2 block is recommended)."
        )
    # max_error_um=inf only measures the error (callers then warn instead).
    if np.isnan(max_error_um) or max_error_um <= 0:
        raise ValueError("Position tolerance must be positive.")
    for (row, col), xy in centers.items():
        if not (0 <= row < plate.rows and 0 <= col < plate.columns):
            raise ValueError("A well label is outside the selected plate.")
        if len(xy) != 2 or not np.isfinite(xy).all():
            raise ValueError("Well centers must contain finite XY coordinates.")
    origin, _, rotation = fit_plate_calibration(centers, plate)
    plan = useq.WellPlatePlan(plate=plate, a1_center_xy=origin, rotation=rotation)
    positions = list(plan.selected_well_positions)
    errors = []
    for (r, c), (x, y) in centers.items():
        position = positions[r * plate.columns + c]
        assert position.x is not None and position.y is not None
        errors.append(np.hypot(position.x - x, position.y - y))
    max_error = float(max(errors))
    if max_error > max_error_um:
        raise ValueError(
            f"Exported plate positions differ from detected centers by {max_error:.1f} "
            f"µm (limit {max_error_um:.1f} µm). Check labels, orientation, and pitch."
        )
    return plan, max_error


def well_coords_affine(
    index_coordinates: Mapping[tuple[int, int], tuple[float, float]],
) -> tuple[float, float, float, float, float, float]:
    """Return best-fit transformation that maps well plate indices to world coordinates.

    Parameters
    ----------
    index_coordinates : Mapping[tuple[int, int], tuple[float, float]]
        A mapping of grid indices to world coordinates.

    Returns
    -------
    np.ndarray: The affine transformation matrix of shape (3, 3).
    """
    A: list[list[int]] = []
    B: list[float] = []
    for (row, col), (x, y) in index_coordinates.items():
        # well plate indices go up in row as we go down in y
        # so we have to negate the row to get the correct transformation
        A.append([-row, col, 1, 0, 0, 0])
        A.append([0, 0, 0, -row, col, 1])
        # row corresponds to y, col corresponds to x
        B.extend((y, x))

    # Solve the least squares problem to find the affine transformation parameters
    params, _, rank, _ = np.linalg.lstsq(np.array(A), np.array(B), rcond=None)

    if rank != 6:
        raise ValueError("Underdetermined system of equations. Are points collinear?")

    return tuple(params)


def fit_collinear_calibration(
    plate: useq.WellPlate,
    index_coordinates: Mapping[tuple[int, int], tuple[float, float]],
    is_single_row: bool,
) -> tuple[tuple[float, float], tuple[float, float], float] | None:
    """Fit a row/column, deriving the perpendicular axis from nominal plate spacing."""
    indices = list(index_coordinates.keys())
    centers = np.array(list(index_coordinates.values()))

    # Build the varying-axis indices and identify the fixed-axis index
    if is_single_row:  # varying column, fixed row
        varying = np.array([c for _, c in indices], dtype=float)
        fixed_idx = indices[0][0]
        other_spacing = plate.well_spacing[1]
    else:  # varying row, fixed column
        varying = np.array([-r for r, _ in indices], dtype=float)
        fixed_idx = indices[0][1]
        other_spacing = plate.well_spacing[0]

    # Fit linear model: x = slope_x * v + tx,  y = slope_y * v + ty
    design = np.column_stack([varying, np.ones(len(varying))])
    coefficients, *_ = np.linalg.lstsq(design, centers, rcond=None)
    (slope_x, slope_y), (tx, ty) = coefficients

    # Measured spacing along the varying axis
    measured_spacing = np.hypot(slope_y, slope_x) / 1000  # mm
    if measured_spacing < 1e-6:
        return None  # wells are coincident

    # Compute rotation and assign spacings per axis
    if is_single_row:
        # Column axis direction is (slope_x, slope_y) in world (x, y)
        rot_rad = np.arctan2(slope_y, slope_x)
        unit_x, unit_y = measured_spacing, max(other_spacing, 0.0)

        # Extrapolate A1 center to row=0
        if fixed_idx != 0:
            other_um = other_spacing * 1000
            ty += other_um * np.cos(rot_rad) * fixed_idx
            tx -= other_um * np.sin(rot_rad) * fixed_idx
    else:
        # Row axis direction is (slope_x, slope_y) in world (x, y)
        rot_rad = np.arctan2(-slope_x, slope_y)
        unit_x, unit_y = max(other_spacing, 0.0), measured_spacing

        # Extrapolate A1 center to col=0
        if fixed_idx != 0:
            other_um = other_spacing * 1000
            tx -= other_um * np.cos(rot_rad) * fixed_idx
            ty -= other_um * np.sin(rot_rad) * fixed_idx

    rotation = round(float(np.rad2deg(rot_rad)), 2)

    return (round(float(tx), 4), round(float(ty), 4)), (unit_x, unit_y), rotation
