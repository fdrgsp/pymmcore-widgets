from __future__ import annotations

import numpy as np
import pytest

from pymmcore_widgets.hcs._image_ops import (
    bilinear,
    canny,
    fftconvolve,
    least_squares,
    peak_local_max,
)


def _disk(radius: float = 20, size: int = 64) -> np.ndarray:
    yy, xx = np.mgrid[:size, :size] - size / 2
    return (np.hypot(xx, yy) < radius).astype(float)


def test_canny_finds_a_thin_closed_outline():
    edges = canny(_disk(), sigma=1.5)
    yy, xx = np.nonzero(edges)
    radii = np.hypot(xx - 32, yy - 32)
    # A closed ring one pixel wide: a double edge would have twice the pixels.
    assert 0.8 * 2 * np.pi * 20 < len(radii) < 1.3 * 2 * np.pi * 20
    assert np.abs(radii - 20).max() < 1.5


def test_canny_ignores_masked_pixels_and_their_border():
    image = _disk()
    mask = np.zeros(image.shape, bool)
    mask[:, :32] = True
    edges = canny(image, sigma=1.5, mask=mask)
    assert edges[:, 31:].sum() == 0  # no edge at, or past, the mask border
    assert edges[:, :31].sum() > np.pi * 20 * 0.9


def test_canny_keeps_weak_edges_only_when_connected_to_strong_ones():
    image = np.zeros((40, 40))
    image[:, 20:] = np.linspace(0.05, 1, 40)[:, None]  # weak on top, strong below
    image[2:6, 4:8] = 0.05  # a weak isolated square
    edges = canny(image, sigma=1)
    assert edges[3:37, 19:21].any(axis=1).all()  # the whole step edge
    assert not edges[:10, :10].any()


def test_peak_local_max_respects_distance_threshold_and_count():
    image = np.zeros((50, 50))
    image[10, 10], image[12, 12], image[40, 40], image[25, 5] = 1, 0.9, 0.8, 0.1
    assert peak_local_max(image, 5, 0.2, 10).tolist() == [[10, 10], [40, 40]]
    assert peak_local_max(image, 5, 0.2, 1).tolist() == [[10, 10]]
    assert peak_local_max(image, 1, 0.2, 10).tolist() == [[10, 10], [12, 12], [40, 40]]


def test_fftconvolve_matches_direct_convolution():
    rng = np.random.default_rng(0)
    image, kernel = rng.random((12, 9)), rng.random((5, 3))
    padded = np.pad(image, ((2, 2), (1, 1)))
    direct = np.array(
        [
            [
                np.sum(padded[i : i + 5, j : j + 3] * kernel[::-1, ::-1])
                for j in range(9)
            ]
            for i in range(12)
        ]
    )
    np.testing.assert_allclose(fftconvolve(image, kernel), direct, atol=1e-12)


def test_bilinear_interpolates_and_clamps():
    image = np.arange(12, dtype=np.float32).reshape(3, 4)
    rows, cols = np.array([0, 0.5, 1.25, -3, 9]), np.array([0, 0.5, 2.5, 1, 9])
    np.testing.assert_allclose(bilinear(image, rows, cols), [0, 2.5, 7.5, 1, 11])


def test_least_squares_robust_circle_fit():
    rng = np.random.default_rng(0)
    angles = rng.uniform(0, 2 * np.pi, 200)
    points = np.column_stack([3 + 10 * np.cos(angles), -2 + 10 * np.sin(angles)])
    points[:20] += rng.uniform(5, 20, (20, 2))  # outliers
    fit = least_squares(
        lambda p: np.linalg.norm(points - p[:2], axis=1) - p[2], [0, 0, 8], 0.5
    )
    np.testing.assert_allclose(fit, [3, -2, 10], atol=0.1)


@pytest.mark.parametrize("start", [0.5, 5.0])
def test_least_squares_stays_within_bounds(start):
    fit = least_squares(lambda p: p - 10, [start], 1.0, bounds=([0], [6]))
    assert fit == pytest.approx([6])
