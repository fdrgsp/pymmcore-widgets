"""Thread ownership, bounded delivery, and cancellation of stage-map processing."""

from __future__ import annotations

from threading import Event, current_thread, get_ident
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import numpy as np
import pytest
import useq
from qtpy.QtCore import QEvent, QThread, QTimer

from pymmcore_widgets.control._stage_explorer import _image_processing
from pymmcore_widgets.control._stage_explorer._overview import (
    GUI_BATCH_ITEMS,
    OverviewData,
)
from pymmcore_widgets.control._stage_explorer._stage_explorer import (
    StageExplorer,
    _LatestFrameRelay,
)
from pymmcore_widgets.control._stage_explorer._stage_viewer import (
    SAMPLE_BATCH,
    StageViewer,
)

from ._utils import wait_for_stage_viewer

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pytestqt.qtbot import QtBot
    from qtpy.QtWidgets import QApplication

    from pymmcore_widgets.control._stage_explorer._image_processing import (
        PreparedImages,
    )


@pytest.fixture
def blocked_worker(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Event, Event]]:
    started, release = Event(), Event()
    original = _image_processing.prepare_images

    def blocked(*args: Any, **kwargs: Any) -> PreparedImages:
        started.set()
        assert release.wait(10), "test did not release the worker"
        return original(*args, **kwargs)

    monkeypatch.setattr(_image_processing, "prepare_images", blocked)
    try:
        yield started, release
    finally:
        release.set()


def test_processing_and_result_delivery_threads(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    blocked_worker: tuple[Event, Event],
) -> None:
    started, release = blocked_worker
    worker_threads, gui_threads = [], []
    original_prepare = _image_processing.prepare_images
    original_accept = StageViewer._accept_prepared

    def prepare(*args: Any, **kwargs: Any) -> PreparedImages:
        worker_threads.append(get_ident())
        return original_prepare(*args, **kwargs)

    def accept(self: StageViewer, *args: Any) -> None:
        gui_threads.append(QThread.currentThread())
        original_accept(self, *args)

    monkeypatch.setattr(_image_processing, "prepare_images", prepare)
    monkeypatch.setattr(StageViewer, "_accept_prepared", accept)
    viewer = StageViewer()
    qtbot.addWidget(viewer)
    viewer.show()
    viewer.view.camera.set_range(x=(0, 20000), y=(0, 20000))
    tile = viewer.add_image(np.ones((2048, 2048), np.uint16))
    qtbot.waitUntil(started.is_set)
    assert tile.image is not None and not tile.image.visible
    assert tile.data.shape == (2048, 2048)
    release.set()
    wait_for_stage_viewer(qtbot, viewer)
    assert worker_threads and all(t != get_ident() for t in worker_threads)
    assert gui_threads and all(t == viewer.thread() for t in gui_threads)
    assert tile.factor is not None and tile.factor != (1, 1)
    assert tile.prepared is not None
    assert tile.image.visible


def test_visible_tile_is_prepared_before_offscreen_backlog(
    qtbot: QtBot,
    blocked_worker: tuple[Event, Event],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, release = blocked_worker
    accepted = []
    original = StageViewer._accept_prepared

    def accept(self: StageViewer, samples: list, result: PreparedImages) -> None:
        accepted.append((result.overview is not None, [t for t, *_ in samples]))
        original(self, samples, result)

    monkeypatch.setattr(StageViewer, "_accept_prepared", accept)
    viewer = StageViewer()
    qtbot.addWidget(viewer)
    viewer.show()
    viewer.view.camera.set_range(x=(0, 20000), y=(0, 20000))
    source = np.ones((64, 64), np.uint16)
    matrix = np.eye(4)
    matrix[0, 3] = 100000
    viewer.add_image(source, matrix)
    viewer._process_images()
    assert started.wait(2)
    for _i in range(8):
        matrix[0, 3] += 3000
        viewer.add_image(source, matrix)
    visible = viewer.add_image(source)
    release.set()
    wait_for_stage_viewer(qtbot, viewer)

    assert accepted[:2] == [(True, []), (False, [visible])]
    assert visible.prepared is not None
    assert visible.image is not None and visible.image.visible
    assert len(viewer._processor.model._thumbs) == 10


@pytest.mark.parametrize("n_tiles", [1, SAMPLE_BATCH, 2 * SAMPLE_BATCH + 1])
def test_visible_tiles_are_downsampled_in_batches(qtbot: QtBot, n_tiles: int) -> None:
    viewer = StageViewer()
    qtbot.addWidget(viewer)
    viewer.show()
    viewer.view.camera.set_range(x=(0, 20000), y=(0, 20000))
    matrix = np.eye(4)
    tiles = []
    submit = viewer._processor.submit
    with (
        patch.object(viewer, "_downsample_factor", return_value=(2, 2)),
        patch.object(viewer._processor, "submit", wraps=submit) as spy,
    ):
        for i in range(n_tiles):
            matrix[0, 3] = i * 100
            tiles.append(viewer.add_image(np.full((64, 64), i, np.uint16), matrix))
        wait_for_stage_viewer(qtbot, viewer)
    batches = [
        len(call.kwargs["samples"])
        for call in spy.call_args_list
        if "samples" in call.kwargs
    ]
    full, tail = divmod(n_tiles, SAMPLE_BATCH)
    assert batches == [SAMPLE_BATCH] * full + ([tail] if tail else [])
    assert all(t.image is not None and t.image.visible for t in tiles)
    for i, tile in enumerate(tiles):
        assert tile.prepared is not None
        factor, shown = tile.prepared
        assert factor == (2, 2) and shown.shape == (32, 32)
        assert np.all(shown == i)
        assert not shown.flags.writeable


@pytest.mark.parametrize("zoom", [False, True])
def test_sample_batch_refresh(
    qtbot: QtBot, blocked_worker: tuple[Event, Event], zoom: bool
) -> None:
    started, release = blocked_worker
    viewer = StageViewer()
    qtbot.addWidget(viewer)
    viewer.show()
    viewer.view.camera.set_range(x=(0, 20000), y=(0, 20000))
    tiles = [viewer.add_image(np.ones((64, 64), np.uint16)) for _ in range(2)]
    baseline = viewer.overview_nbytes()
    with patch.object(
        viewer._processor, "submit", wraps=viewer._processor.submit
    ) as spy:
        viewer._process_images()
        assert started.wait(2)
        assert viewer.overview_nbytes() > baseline
        if zoom:
            viewer.view.camera.set_range(x=(0, 5000), y=(0, 5000))
        viewer.apply_downsampling()
        release.set()
        assert viewer._processor.future is not None
        viewer._processor.future.result(timeout=10)
        with patch.object(viewer, "cull_to_view", wraps=viewer.cull_to_view) as cull:
            viewer._process_images()
        cull.assert_not_called()
    assert sum("samples" in call.kwargs for call in spy.call_args_list) == 1 + zoom
    wait_for_stage_viewer(qtbot, viewer)
    assert all(tile.factor == viewer._downsample_factor(tile) for tile in tiles)


@pytest.mark.parametrize("clear", [False, True])
def test_sample_batch_rejects_replaced_tiles(
    qtbot: QtBot, blocked_worker: tuple[Event, Event], clear: bool
) -> None:
    started, release = blocked_worker
    viewer = StageViewer()
    qtbot.addWidget(viewer)
    viewer.show()
    viewer.view.camera.set_range(x=(0, 20000), y=(0, 20000))
    tiles = [viewer.add_image(np.full((64, 64), i, np.uint16)) for i in (1, 2)]
    viewer._process_images()
    assert started.wait(2)
    if clear:
        release.set()
        assert viewer._processor.future is not None
        viewer._processor.future.result(timeout=10)
        viewer.clear()
        tile = viewer.add_image(np.full((64, 64), 9, np.uint16))
    else:
        tile = tiles[0]
        viewer.update_image(tile, np.full((64, 64), 9, np.uint16))
    release.set()
    wait_for_stage_viewer(qtbot, viewer)
    assert tile.prepared is not None
    assert np.all(tile.prepared[1] == 9)
    assert tile.image is not None and tile.image.visible
    if clear:
        assert viewer._tiles == [tile]
        assert all(t.prepared is None for t in tiles)
    else:
        assert tiles[1].prepared is not None
        assert np.all(tiles[1].prepared[1] == 2)


def test_clear_rejects_result_and_reused_tile_index(
    qtbot: QtBot, blocked_worker: tuple[Event, Event]
) -> None:
    started, release = blocked_worker
    viewer = StageViewer()
    qtbot.addWidget(viewer)
    viewer.add_image(np.full((64, 64), 99, np.uint16))
    viewer._process_images()
    assert started.wait(2)
    viewer.clear()
    tile = viewer.add_image(np.full((64, 64), 7, np.uint16))
    release.set()
    wait_for_stage_viewer(qtbot, viewer)
    assert viewer._tiles == [tile]
    assert viewer._overview._nbytes == 16 * 16 * 2
    assert viewer._processor.model._thumbs[0][0].max() == 7


def test_geometry_change_rejects_result_and_preserves_other_locations(
    qtbot: QtBot,
    blocked_worker: tuple[Event, Event],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, release = blocked_worker
    accepted = []
    original = StageViewer._accept_prepared

    def accept(self: StageViewer, samples: list, result: PreparedImages) -> None:
        accepted.append(result.overview is not None)
        original(self, samples, result)

    monkeypatch.setattr(StageViewer, "_accept_prepared", accept)
    viewer = StageViewer()
    qtbot.addWidget(viewer)
    tile = viewer.add_image(np.ones((64, 64), np.uint16))
    viewer._process_images()
    assert started.wait(2)
    matrix = np.eye(4)
    matrix[:2, 3] = (1000, 1000)
    viewer.add_image(np.full((64, 64), 3, np.uint16))
    viewer.update_image(tile, np.full((64, 64), 9, np.uint16), matrix)
    release.set()
    wait_for_stage_viewer(qtbot, viewer)
    assert accepted == [False, True, True]
    model = viewer._processor.model
    assert model._bounds[tile.index] == (1000, 1064, 1000, 1064)
    assert len(model._thumbs) == 2


def test_owned_sources_and_copy_on_write_overview(qtbot: QtBot) -> None:
    viewer = StageViewer()
    qtbot.addWidget(viewer)
    source = np.full((64, 64), 7, np.uint16)
    matrix = np.eye(4)
    tile = viewer.add_image(source, matrix)
    source[:] = 99
    matrix[0, 0] = 99
    wait_for_stage_viewer(qtbot, viewer)
    assert tile.data.max() == 7 and tile.matrix[0, 0] == 1
    assert not tile.data.flags.writeable

    model = OverviewData()
    model.set_tile(0, tile.data, tile.matrix)
    model.level_arrays(0)
    first = model.snapshot().levels[0][3][(0, 0)]
    model.set_tile(0, source, tile.matrix)
    second = model.snapshot().levels[0][3][(0, 0)]
    assert not first.flags.writeable and not second.flags.writeable
    assert first[0, 0] == 7 and second[0, 0] == 99
    assert not np.shares_memory(first, second)


def test_spatial_relay_batches_preserve_latest_and_sequence_tail(qtbot: QtBot) -> None:
    explorer = StageExplorer()
    qtbot.addWidget(explorer)
    explorer.show()
    explorer.poll_stage_position = False
    sequence = useq.MDASequence()
    for value in (1, 9):
        for i in range(21):
            event = useq.MDAEvent(
                sequence=sequence, index={"p": i}, x_pos=i * 5000, y_pos=0
            )
            explorer._frame_relay.submit(np.full((16, 16), value, np.uint16), event, {})
    explorer._on_sequence_finished()
    assert len(explorer._tiles) <= GUI_BATCH_ITEMS
    mouse_tick = []
    QTimer.singleShot(0, lambda: mouse_tick.append(len(explorer._tiles)))
    qtbot.waitUntil(lambda: len(explorer._tiles) == 21)
    assert mouse_tick and mouse_tick[0] < 21
    assert all(tile.data[0, 0] == 9 for tile in explorer._tiles.values())
    assert explorer._frame_relay.nbytes() == 0


def test_relay_capacity_is_explicit_and_never_evicts_spatial_updates() -> None:
    relay = _LatestFrameRelay()
    relay.set_enabled(True)
    image = np.ones((8, 8), np.uint16)
    relay.set_limit(2 * image.nbytes)
    rejected = []
    relay.capacityExceeded.connect(lambda: rejected.append(True))
    for x in (0, 1, 2, 3):
        relay.submit(image, useq.MDAEvent(x_pos=x, y_pos=0), {})
    image[:] = 99  # acquisition reused its buffer
    relay.submit(image, useq.MDAEvent(x_pos=0, y_pos=0), {})
    assert rejected == [True]
    assert relay.nbytes() == 2 * image.nbytes
    batch = relay.take_or_disarm()
    assert [(event.x_pos, int(data[0, 0])) for data, event in batch] == [
        (1, 1),
        (0, 99),
    ]


def test_pan_and_accounting_do_not_prepare_pixels(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    viewer = StageViewer()
    qtbot.addWidget(viewer)
    viewer.add_image(np.ones((64, 64), np.uint16))
    wait_for_stage_viewer(qtbot, viewer)

    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("panning or accounting submitted pixel work")

    monkeypatch.setattr(viewer._processor, "submit", unexpected)
    for x in range(10):
        viewer.view.camera.set_range(x=(x, x + 100), y=(0, 100), margin=0)
        viewer.overview_nbytes()
    wait_for_stage_viewer(qtbot, viewer)

    with patch.object(viewer.canvas, "update") as repaint:
        viewer.cull_to_view()
    repaint.assert_not_called()


@pytest.mark.parametrize("close", [True, False])
def test_close_and_destruction_join_worker(
    qtbot: QtBot,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    close: bool,
) -> None:
    threads = []
    original = _image_processing.prepare_images

    def prepare(*args: Any, **kwargs: Any) -> PreparedImages:
        threads.append(current_thread())
        return original(*args, **kwargs)

    monkeypatch.setattr(_image_processing, "prepare_images", prepare)
    viewer = StageViewer()
    qtbot.addWidget(viewer)
    viewer.add_image(np.ones((2048, 2048), np.uint16))
    viewer._process_images()
    processor = viewer._processor
    qtbot.waitUntil(lambda: bool(threads))
    if close:
        viewer.close()
    else:
        viewer.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    assert processor.closed and processor.future is None
    assert all(not thread.is_alive() for thread in threads)


def test_overview_budget_falls_back_without_losing_full_resolution(
    qtbot: QtBot,
) -> None:
    viewer = StageViewer()
    qtbot.addWidget(viewer)
    viewer._overview_limit = 1
    tile = viewer.add_image(np.ones((64, 64), np.uint16))
    wait_for_stage_viewer(qtbot, viewer)
    assert not viewer._overview.usable
    assert viewer._overview.nbytes() == 0
    assert tile.data.shape == (64, 64)


def test_close_then_show_rebuilds_worker_cache(qtbot: QtBot) -> None:
    viewer = StageViewer()
    qtbot.addWidget(viewer)
    viewer.show()
    tile = viewer.add_image(np.ones((64, 64), np.uint16))
    wait_for_stage_viewer(qtbot, viewer)
    previous = viewer._processor
    viewer.close()
    assert previous.closed
    viewer.show()
    wait_for_stage_viewer(qtbot, viewer)
    assert viewer._processor is not previous
    assert viewer._tiles == [tile]
    assert viewer._overview.has_content


def test_rejected_build_delta_is_retained_for_other_locations() -> None:
    model = OverviewData()
    model.set_tile(0, np.full((64, 64), 7, np.uint16), np.eye(4))
    shifted = np.eye(4)
    shifted[3, 0] = 1024
    model.set_tile(1, np.full((64, 64), 9, np.uint16), shifted)
    model.level_arrays(0)
    rejected = model.snapshot()
    model.set_tile(0, np.full((64, 64), 11, np.uint16), np.eye(4))
    accepted = model.snapshot()
    assert accepted.levels[0][3].keys() == rejected.levels[0][3].keys()
    assert accepted.levels[0][3][(1, 0)][0, 0] == 9


def test_affine_change_with_equal_bounds_repaints_overview() -> None:
    model = OverviewData()
    image = np.arange(64 * 64, dtype=np.uint16).reshape(64, 64)
    model.set_tile(0, image, np.eye(4))
    model.level_arrays(0)
    generation = model.generation
    mirror = np.eye(4)
    mirror[0, 0] = -1
    mirror[3, 0] = 64
    model.set_tile(0, image, mirror)
    assert model.generation == generation
    result = model.level_arrays(0)[(0, 0)]
    assert result[0, 0] > result[0, 15]


def test_worker_coalesces_per_location_in_update_order(
    qtbot: QtBot, blocked_worker: tuple[Event, Event]
) -> None:
    started, release = blocked_worker
    viewer = StageViewer()
    qtbot.addWidget(viewer)
    tiles = [viewer.add_image(np.ones((64, 64), np.uint16))]
    viewer._process_images()
    assert started.wait(2)
    tiles.extend(viewer.add_image(np.ones((64, 64), np.uint16)) for _ in range(15))
    for i in reversed(range(16)):
        viewer.update_image(tiles[i], np.full((64, 64), i + 100, np.uint16))
    release.set()
    wait_for_stage_viewer(qtbot, viewer)
    thumbs = viewer._processor.model._thumbs
    assert list(thumbs) == list(reversed(range(16)))
    assert all(thumb[0][0, 0] == index + 100 for index, thumb in thumbs.items())
