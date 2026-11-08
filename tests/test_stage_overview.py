"""Multiscale overview drawn instead of many zoomed-out stage-map tiles."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from pymmcore_widgets.control._stage_explorer import _stage_viewer
from pymmcore_widgets.control._stage_explorer._overview import (
    BASE_FACTOR,
    CHUNK_PX,
    downsampled,
)
from pymmcore_widgets.control._stage_explorer._stage_viewer import (
    OVERVIEW_MIN_TILES,
    StageViewer,
    Tile,
)

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot

TILE = 64


def _translation(x: float, y: float) -> np.ndarray:
    matrix = np.eye(4)
    matrix[:2, 3] = (x, y)
    return matrix


def _viewer(qtbot: QtBot) -> StageViewer:
    viewer = StageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(400, 400)
    viewer.show()
    qtbot.waitExposed(viewer)
    return viewer


def _add_grid(viewer: StageViewer, n: int) -> None:
    side = int(np.ceil(np.sqrt(n)))
    for i in range(n):
        row, col = divmod(i, side)
        img = np.full((TILE, TILE), i + 1, dtype=np.uint16)
        viewer.add_image(img, _translation(col * TILE, row * TILE))


def _drawn(viewer: StageViewer) -> list[Tile]:
    """The tiles drawn individually, i.e. with an image of their own."""
    return [tile for tile in viewer._tiles if tile in viewer._drawn]


def _drawn_chunks(viewer: StageViewer) -> int:
    return sum(
        image.visible
        for level in viewer._overview._levels
        if level.node.visible
        for image in level.images.values()
    )


def _set_view(viewer: StageViewer, size: float) -> None:
    viewer.view.camera.set_range(x=(0, size), y=(0, size), margin=0)
    viewer.cull_to_view()


def test_many_zoomed_out_tiles_draw_the_overview(qtbot: QtBot) -> None:
    viewer = _viewer(qtbot)
    _add_grid(viewer, 100)

    _set_view(viewer, 20_000)
    assert viewer._overview.active_level is not None
    assert not _drawn(viewer)
    assert 0 < _drawn_chunks(viewer) < 10

    # zoomed back in, the tiles themselves are drawn again
    _set_view(viewer, 2 * TILE)
    assert viewer._overview.active_level is None
    assert _drawn_chunks(viewer) == 0
    assert _drawn(viewer)


def test_few_tiles_are_always_drawn_individually(qtbot: QtBot) -> None:
    viewer = _viewer(qtbot)
    _add_grid(viewer, OVERVIEW_MIN_TILES)

    _set_view(viewer, 20_000)
    assert viewer._overview.active_level is None
    assert len(_drawn(viewer)) == len(viewer._tiles)


def test_overview_does_not_affect_zoom_to_fit(qtbot: QtBot) -> None:
    viewer = _viewer(qtbot)
    _add_grid(viewer, 100)
    _set_view(viewer, 20_000)

    viewer.zoom_to_fit(margin=0)
    rect = viewer.view.camera.rect
    # the tiles span 10 x 10 tiles; a chunk (CHUNK_PX * BASE_FACTOR) is larger
    assert rect.right == pytest.approx(10 * TILE, abs=1)
    assert rect.top == pytest.approx(10 * TILE, abs=1)


def test_overview_holds_block_means_in_place(qtbot: QtBot) -> None:
    viewer = _viewer(qtbot)
    img = np.arange(TILE * TILE, dtype=np.uint16).reshape(TILE, TILE)
    viewer.add_image(img, _translation(2 * BASE_FACTOR, 0))
    viewer.cull_to_view()

    chunk = viewer._overview.level_arrays(0)[(0, 0)]
    n = TILE // BASE_FACTOR
    blocks = img.reshape(n, BASE_FACTOR, n, BASE_FACTOR).astype(np.int64)
    expected = (blocks.sum(axis=(1, 3)) // BASE_FACTOR**2).astype(img.dtype)
    np.testing.assert_array_equal(chunk[:n, 2 : 2 + n], expected)
    assert not chunk[:, :2].any()  # left of the tile stays empty
    assert not chunk[n:].any()


def test_overview_follows_a_rotated_affine(qtbot: QtBot) -> None:
    viewer = _viewer(qtbot)
    img = np.tile(np.arange(TILE, dtype=np.uint16), (TILE, 1))  # value = column
    rotate90 = np.array(
        [[0, -1, 0, 2 * TILE], [1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], float
    )
    viewer.add_image(img, rotate90)  # tile lands at x in [TILE, 2 TILE]
    viewer.cull_to_view()

    n = TILE // BASE_FACTOR
    region = viewer._overview.level_arrays(0)[(0, 0)][:n, n : 2 * n].astype(int)
    # image columns now run along world y (overview rows)
    assert (np.diff(region, axis=0) > 0).all()
    assert (np.diff(region, axis=1) == 0).all()


def test_refreshed_tile_is_on_top_in_the_overview(qtbot: QtBot) -> None:
    viewer = _viewer(qtbot)
    old = viewer.add_image(np.full((TILE, TILE), 1, np.uint16), _translation(0, 0))
    viewer.add_image(np.full((TILE, TILE), 2, np.uint16), _translation(0, 0))
    viewer.cull_to_view()
    chunk = viewer._overview.level_arrays(0)[(0, 0)]
    assert chunk[0, 0] == 2

    viewer.update_image(old, np.full((TILE, TILE), 3, np.uint16))
    viewer.cull_to_view()
    assert chunk[0, 0] == 3


def test_coarser_tile_rebuilds_the_overview(qtbot: QtBot) -> None:
    viewer = _viewer(qtbot)
    viewer.add_image(np.ones((TILE, TILE), np.uint16), _translation(0, 0))
    viewer.cull_to_view()
    fine = viewer._overview.base_res

    coarse = np.diag([16.0, 16.0, 1.0, 1.0])  # e.g. a low-magnification objective
    viewer.add_image(np.ones((TILE, TILE), np.uint16), coarse)
    viewer.cull_to_view()
    assert fine is not None
    assert viewer._overview.base_res == pytest.approx(16 * fine)
    # both tiles are still painted after the rebuild
    arrays = viewer._overview.level_arrays(0)
    assert arrays[(0, 0)].any()
    assert len(arrays) <= 4  # not hundreds of fine chunks for the coarse one
    assert CHUNK_PX * viewer._overview._levels[0].res >= TILE * 16


def test_clear_and_disabled_downsampling_drop_the_overview(qtbot: QtBot) -> None:
    viewer = _viewer(qtbot)
    _add_grid(viewer, 100)
    _set_view(viewer, 20_000)
    assert viewer._overview.active_level is not None

    viewer.set_downsample(False)  # full resolution requested: draw the tiles
    assert viewer._overview.active_level is None
    assert _drawn(viewer)

    viewer.set_downsample(True)
    viewer.clear()
    viewer.cull_to_view()
    assert not viewer._overview.has_content
    assert viewer._overview.active_level is None


def test_mixed_dtypes_fall_back_to_tiles(qtbot: QtBot) -> None:
    viewer = _viewer(qtbot)
    _add_grid(viewer, 100)
    viewer.add_image(np.ones((TILE, TILE), np.float32), _translation(0, 0))
    _set_view(viewer, 20_000)
    assert viewer._overview.active_level is None
    assert _drawn(viewer)


def test_levels_are_built_only_when_shown(qtbot: QtBot) -> None:
    viewer = _viewer(qtbot)
    _add_grid(viewer, 100)
    viewer.cull_to_view()
    thumbs_only = viewer.overview_nbytes()
    assert not any(level.arrays for level in viewer._overview._levels)

    _set_view(viewer, 20_000)
    shown = viewer._overview.active_level
    assert shown is not None
    built = [bool(level.arrays) for level in viewer._overview._levels]
    assert built == [i == shown for i in range(len(built))]
    assert viewer.overview_nbytes() > thumbs_only

    # a built level keeps up with refreshed tiles while it is on screen
    tile = viewer._tiles[0]
    viewer.update_image(tile, np.full((TILE, TILE), 999, np.uint16))
    viewer.cull_to_view()
    level = viewer._overview._levels[shown]
    assert any((arr == 999).any() for arr in level.arrays.values())


def test_overview_memory_is_small_next_to_the_tiles(qtbot: QtBot) -> None:
    viewer = _viewer(qtbot)
    tile = np.ones((512, 512), np.uint16)
    for i in range(100):
        row, col = divmod(i, 10)
        viewer.add_image(tile, _translation(col * 512, row * 512))
    _set_view(viewer, 100_000)
    assert viewer._overview.active_level is not None
    assert viewer.overview_nbytes() < 0.15 * 100 * tile.nbytes


def test_overview_skipped_when_it_saves_no_draws(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    viewer = _viewer(qtbot)
    _add_grid(viewer, 100)
    # as if the tiles were so scattered that each needed a chunk of its own
    monkeypatch.setattr(_stage_viewer, "OVERVIEW_MAX_CHUNK_RATIO", 0.001)
    _set_view(viewer, 20_000)
    assert viewer._overview.active_level is None
    assert len(_drawn(viewer)) == len(viewer._tiles)
    assert not any(level.arrays for level in viewer._overview._levels)

    monkeypatch.setattr(_stage_viewer, "OVERVIEW_MAX_CHUNK_RATIO", 0.5)
    viewer.cull_to_view()
    assert viewer._overview.active_level is not None


def test_moved_tile_leaves_no_ghost_but_jitter_keeps_levels(qtbot: QtBot) -> None:
    viewer = _viewer(qtbot)
    _add_grid(viewer, 100)
    _set_view(viewer, 20_000)
    shown = viewer._overview.active_level
    assert shown is not None
    tile = viewer._tiles[0]
    marker = np.full((TILE, TILE), 999, np.uint16)

    # sub-pixel stage jitter on a revisit: levels stay built and are updated
    viewer.update_image(tile, marker, _translation(0.3, 0.2))
    viewer.cull_to_view()
    level = viewer._overview._levels[shown]
    assert level.built
    assert any((arr == 999).any() for arr in level.arrays.values())

    # a real move: rebuilt, with the tile only at its new place
    far = 50 * TILE
    viewer.update_image(tile, marker, _translation(far, far))
    viewer.cull_to_view()
    arrays = viewer._overview.level_arrays(shown)
    res = viewer._overview._levels[shown].res
    size = CHUNK_PX * res
    old_chunk = arrays[(0, 0)]
    assert not (old_chunk[: int(TILE / res) + 1, : int(TILE / res) + 1] == 999).any()
    new_key = (int(far // size), int(far // size))
    assert (arrays[new_key] == 999).any()


@pytest.mark.parametrize(
    "dtype", [np.uint8, np.uint16, np.uint32, np.int16, np.float32]
)
@pytest.mark.parametrize("factor", [(2, 2), (4, 1), (1, 8), (64, 64)])
def test_downsampled_is_the_block_mean(dtype: type, factor: tuple[int, int]) -> None:
    data = (np.random.default_rng(0).random((131, 257)) * 200).astype(dtype)
    fy, fx = factor
    h, w = 131 // fy, 257 // fx  # partial edge blocks are dropped
    mean = data[: h * fy, : w * fx].reshape(h, fy, w, fx).mean(axis=(1, 3))

    out = downsampled(data, factor)
    assert out.dtype == data.dtype
    if np.issubdtype(dtype, np.integer):
        np.testing.assert_array_equal(out, np.floor(mean))
    else:
        np.testing.assert_allclose(out, mean, rtol=1e-5)


def test_downsampled_keeps_rgb_channels() -> None:
    rgb = np.zeros((8, 8, 3), np.uint8)
    rgb[:4, :4] = (40, 80, 120)
    np.testing.assert_array_equal(downsampled(rgb, (4, 4))[0, 0], (40, 80, 120))
    assert downsampled(rgb, (4, 4)).shape == (2, 2, 3)
