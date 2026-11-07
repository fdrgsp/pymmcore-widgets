"""NumPy image operations for well detection.

`canny` and `peak_local_max` match scikit-image's; `fftconvolve`, `bilinear` and
`least_squares` match SciPy's `fftconvolve(mode="same")`, `map_coordinates(order=1,
mode="nearest")` and `least_squares(loss="soft_l1")`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def _correlate1d(
    image: np.ndarray, weights: Sequence[float] | np.ndarray, axis: int, mode: str
) -> np.ndarray:
    """1D correlation along `axis` (`mode` as `np.pad`)."""
    r = len(weights) // 2
    pad = [(0, 0)] * image.ndim
    pad[axis] = (r, r)
    padded = np.pad(image.astype(np.float64), pad, mode=mode)  # type: ignore[call-overload]
    n = image.shape[axis]
    out = np.zeros(image.shape)
    for k, weight in enumerate(weights):
        if weight:
            out += weight * padded[(slice(None),) * axis + (slice(k, k + n),)]
    return out.astype(image.dtype)


def _gaussian(image: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian filter with zeros past the borders (kernel truncated at 4 sigma)."""
    x = np.arange(-int(4 * sigma + 0.5), int(4 * sigma + 0.5) + 1)
    kernel = np.exp(-0.5 * (x / sigma) ** 2)
    kernel /= kernel.sum()
    for axis in (0, 1):
        image = _correlate1d(image, kernel, axis, "constant")
    return image


def _sobel(image: np.ndarray, axis: int) -> np.ndarray:
    derivative = _correlate1d(image, (-1, 0, 1), axis, "symmetric")
    return _correlate1d(derivative, (1, 2, 1), 1 - axis, "symmetric")


def canny(
    image: np.ndarray,
    sigma: float,
    mask: np.ndarray | None = None,
    low: float = 0.1,
    high: float = 0.2,
) -> np.ndarray:
    """Canny edges of a [0, 1] image, as `skimage.feature.canny` with defaults."""
    dtype = image.dtype if image.dtype in (np.float32, np.float64) else np.float64
    image = image.astype(dtype, copy=False)
    if mask is None:
        mask = np.ones(image.shape, bool)
    # Smooth only measured pixels, then undo the bleed-over from masked ones.
    smooth = _gaussian(np.where(mask, image, 0).astype(dtype), sigma)
    smooth /= _gaussian(mask.astype(dtype), sigma) + np.finfo(dtype).eps
    padded = np.pad(mask, 1)
    h, w = mask.shape
    eroded = np.logical_and.reduce(
        [padded[i : i + h, j : j + w] for i in range(3) for j in range(3)]
    )
    isobel, jsobel = _sobel(smooth, 0), _sobel(smooth, 1)
    magnitude = np.hypot(isobel, jsobel)
    weak = _thin(isobel, jsobel, magnitude) & eroded & (magnitude >= low)
    return _hysteresis(weak, weak & (magnitude >= high))


def _hysteresis(weak: np.ndarray, strong: np.ndarray) -> np.ndarray:
    """Weak pixels 8-connected to a strong one, grown out from the strong pixels."""
    h, w = weak.shape
    # Flat indices into zero-padded images, so neighbors never leave the array.
    candidate, edges = np.pad(weak, 1).ravel(), np.pad(strong, 1).ravel()
    offsets = np.array([i * (w + 2) + j for i in (-1, 0, 1) for j in (-1, 0, 1)])
    front = np.flatnonzero(edges)
    while front.size:
        neighbors = (front[:, None] + offsets).ravel()
        front = np.unique(neighbors[candidate[neighbors] & ~edges[neighbors]])
        edges[front] = True
    return edges.reshape(h + 2, w + 2)[1:-1, 1:-1]


def _thin(isobel: np.ndarray, jsobel: np.ndarray, magnitude: np.ndarray) -> np.ndarray:
    """Non-maximum suppression, interpolating the magnitude along the gradient."""
    padded = np.pad(magnitude, 1)
    h, w = magnitude.shape

    def at(di: int, dj: int) -> np.ndarray:
        return padded[1 + di : 1 + di + h, 1 + dj : 1 + dj + w]

    ai, aj = np.abs(isobel), np.abs(jsobel)
    # Gradient along the main diagonal or the anti-diagonal.
    diagonal = ((isobel >= 0) & (jsobel >= 0)) | ((isobel <= 0) & (jsobel <= 0))
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio_i, ratio_j = aj / ai, ai / aj
    peak = np.zeros(magnitude.shape, bool)
    for select, weight, near, far in (
        (diagonal & (ai > aj), ratio_i, (1, 0), (1, 1)),
        (diagonal & (ai <= aj), ratio_j, (0, 1), (1, 1)),
        (~diagonal & (ai < aj), ratio_j, (0, 1), (-1, 1)),
        (~diagonal & (ai >= aj), ratio_i, (-1, 0), (-1, 1)),
    ):
        weight = np.where(select, weight, 0)
        ahead = at(*far) * weight + at(*near) * (1 - weight)
        behind = at(-far[0], -far[1]) * weight + at(-near[0], -near[1]) * (1 - weight)
        peak |= select & (ahead <= magnitude) & (behind <= magnitude)
    return peak


def peak_local_max(
    image: np.ndarray, min_distance: int, threshold: float, num_peaks: int
) -> np.ndarray:
    """(row, col) of the strongest local maxima at least `min_distance` apart."""
    size = 2 * min_distance + 1
    window = np.lib.stride_tricks.sliding_window_view
    # A square maximum filter is a row pass followed by a column pass.
    local_max = image
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (min_distance, min_distance)
        padded = np.pad(local_max, pad, mode="edge")
        local_max = window(padded, size, axis=axis).max(axis=-1)
    peaks = (image == local_max) & (image > threshold)
    coords = np.argwhere(peaks)[np.argsort(-image[peaks], kind="stable")]
    kept: list[np.ndarray] = []
    for coord in coords:
        if len(kept) == num_peaks:
            break
        if all(np.abs(coord - k).max() > min_distance for k in kept):
            kept.append(coord)
    return np.array(kept, dtype=int).reshape(-1, 2)


def fftconvolve(image: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Convolution cropped to the image (the "same" mode)."""
    shape = [a + b - 1 for a, b in zip(image.shape, kernel.shape, strict=True)]
    full = np.fft.irfft2(
        np.fft.rfft2(image, shape) * np.fft.rfft2(kernel, shape), shape
    )
    r0, c0 = ((n - 1) // 2 for n in kernel.shape)
    return full[r0 : r0 + image.shape[0], c0 : c0 + image.shape[1]]


def bilinear(image: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """Sample `image` at fractional coordinates, clamping to the nearest edge."""
    r = np.clip(rows, 0, image.shape[0] - 1)
    c = np.clip(cols, 0, image.shape[1] - 1)
    r0 = np.clip(np.floor(r).astype(int), 0, image.shape[0] - 2)
    c0 = np.clip(np.floor(c).astype(int), 0, image.shape[1] - 2)
    fr, fc = r - r0, c - c0
    top = image[r0, c0] * (1 - fc) + image[r0, c0 + 1] * fc
    bottom = image[r0 + 1, c0] * (1 - fc) + image[r0 + 1, c0 + 1] * fc
    return np.asarray(top * (1 - fr) + bottom * fr, dtype=image.dtype)


def least_squares(
    fun: Callable[[np.ndarray], np.ndarray],
    x0: Sequence[float] | np.ndarray,
    f_scale: float,
    bounds: tuple[Sequence[float], Sequence[float]] | None = None,
    max_iterations: int = 200,
) -> np.ndarray:
    """Minimize the soft-L1 cost of `fun` residuals (Levenberg-Marquardt).

    Outliers are down-weighted by `1 / sqrt(1 + (r / f_scale)**2)`; steps are
    clipped to `bounds`.
    """
    x: np.ndarray = np.asarray(x0, dtype=float).copy()
    lo, hi = (
        (np.full_like(x, -np.inf), np.full_like(x, np.inf))
        if bounds is None
        else (np.asarray(bounds[0], float), np.asarray(bounds[1], float))
    )
    x = np.clip(x, lo, hi)

    def cost(r: np.ndarray) -> float:
        return float(np.sum(np.sqrt(1 + (r / f_scale) ** 2) - 1))

    residuals = fun(x)
    current, damping = cost(residuals), 1e-3
    for _ in range(max_iterations):
        weights = 1 / np.sqrt(1 + (residuals / f_scale) ** 2)
        # Forward-difference Jacobian, stepping back from an upper bound.
        steps = np.sqrt(np.finfo(float).eps) * np.maximum(1, np.abs(x))
        steps = np.where(x + steps <= hi, steps, -steps)
        jacobian = np.column_stack(
            [
                (fun(x + step * e) - residuals) / step
                for step, e in zip(steps, np.eye(len(x)), strict=False)
            ]
        )
        normal = jacobian.T @ (weights[:, None] * jacobian)
        gradient = jacobian.T @ (weights * residuals)
        scale = np.diag(np.maximum(np.diag(normal), 1e-12))
        while True:
            trial = np.clip(
                x - np.linalg.solve(normal + damping * scale, gradient), lo, hi
            )
            trial_residuals = fun(trial)
            trial_cost = cost(trial_residuals)
            if trial_cost < current:
                damping = max(damping / 10, 1e-12)
                break
            damping *= 10
            if damping > 1e12:  # no downhill step left: converged
                return x
        converged = current - trial_cost <= 1e-8 * current or np.linalg.norm(
            trial - x
        ) <= 1e-8 * (1e-8 + np.linalg.norm(trial))
        x, residuals, current = trial, trial_residuals, trial_cost
        if converged:
            break
    return x
