from __future__ import annotations

from threading import Event, Thread, get_ident
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock, patch

import pytest
import useq
from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import QObject, QTimer

from pymmcore_widgets.mda import CollapsibleCoreMDATabs, TopbarMDATabs
from pymmcore_widgets.mda._core_mda import CoreMDATabs
from pymmcore_widgets.mda._core_z import CoreConnectedZPlanWidget

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from pytestqt.qtbot import QtBot


@pytest.fixture
def live_z(
    global_mmcore: CMMCorePlus, qtbot: QtBot
) -> Iterator[CoreConnectedZPlanWidget]:
    widget = CoreConnectedZPlanWidget(global_mmcore)
    qtbot.addWidget(widget)
    yield widget
    widget.poll_z.setChecked(False)
    qtbot.waitUntil(lambda: not widget._poll_pending)


def test_polling_is_opt_in_and_pauses_when_hidden(
    live_z: CoreConnectedZPlanWidget, global_mmcore: CMMCorePlus, qtbot: QtBot
) -> None:
    gui_thread = get_ident()
    read_threads = []

    def read_position(device: str) -> float:
        read_threads.append(get_ident())
        return 100

    with patch.object(global_mmcore, "getPosition", side_effect=read_position) as read:
        live_z.show()
        assert not live_z.poll_z.isChecked()
        qtbot.wait(150)
        read.assert_not_called()

        live_z.hide()
        live_z.poll_z.setChecked(True)
        qtbot.wait(150)
        read.assert_not_called()
        live_z.show()
        qtbot.waitUntil(lambda: live_z._live_z == 100)
        assert all(thread != gui_thread for thread in read_threads)

        live_z.hide()
        qtbot.waitUntil(lambda: not live_z._poll_pending)
        count = read.call_count
        qtbot.wait(150)
        assert read.call_count == count
        assert live_z._viz._stage_position is None

        live_z.show()
        qtbot.waitUntil(lambda: live_z._live_z == 100)
        live_z.poll_z.setChecked(False)
        qtbot.waitUntil(lambda: not live_z._poll_pending)
        count = read.call_count
        qtbot.wait(150)
        assert read.call_count == count
        assert live_z._viz._stage_position is None


def test_slow_poll_does_not_block_overlap_or_update_a_hidden_widget(
    live_z: CoreConnectedZPlanWidget, global_mmcore: CMMCorePlus, qtbot: QtBot
) -> None:
    started, release = Event(), Event()

    def slow_read(device: str) -> float:
        started.set()
        release.wait(5)
        return 100

    with patch.object(global_mmcore, "getPosition", side_effect=slow_read) as read:
        try:
            live_z.show()
            live_z.poll_z.setChecked(True)
            qtbot.waitUntil(started.is_set)
            tick = Mock()
            QTimer.singleShot(0, tick)
            qtbot.waitUntil(lambda: tick.called)
            qtbot.wait(250)
            assert read.call_count == 1
            live_z.hide()
        finally:
            release.set()
            qtbot.waitUntil(lambda: not live_z._poll_pending)
        assert live_z._live_z is None
        assert live_z._viz._stage_position is None


@pytest.mark.parametrize("mode", list(CoreConnectedZPlanWidget.Mode))
def test_live_coordinates_and_fixed_reference(
    live_z: CoreConnectedZPlanWidget,
    global_mmcore: CMMCorePlus,
    qtbot: QtBot,
    mode: CoreConnectedZPlanWidget.Mode,
) -> None:
    live_z.setMode(mode)
    live_z.top.setValue(112)
    live_z.bottom.setValue(84)
    live_z.range.setValue(24)
    live_z.above.setValue(8)
    live_z.below.setValue(16)
    global_mmcore.setZPosition(100)
    global_mmcore.waitForSystem()
    live_z.show()
    live_z.poll_z.setChecked(True)
    qtbot.waitUntil(lambda: live_z._live_z == 100)
    absolute = mode is live_z.Mode.TOP_BOTTOM
    assert live_z._viz._stage_position == (100 if absolute else 0)

    with qtbot.assertNotEmitted(live_z.valueChanged):
        global_mmcore.setZPosition(108)
        global_mmcore.waitForSystem()
        qtbot.waitUntil(lambda: live_z._live_z == 108)
    assert live_z._viz._stage_position == (108 if absolute else 8)
    if not absolute:
        assert live_z._reference_z == 100
        live_z.bottom_btn.visit.click()
        global_mmcore.waitForSystem()
        target = 88 if mode is live_z.Mode.RANGE_AROUND else 84
        qtbot.waitUntil(lambda: live_z._live_z == target)
        assert live_z._reference_z == 100
        if mode is live_z.Mode.ABOVE_BELOW:
            live_z.reference_btn.click()
            global_mmcore.waitForSystem()
            qtbot.waitUntil(lambda: live_z._live_z == 100)
            assert live_z._viz._stage_position == 0


def test_poll_recovers_from_errors_and_focus_device_changes(
    live_z: CoreConnectedZPlanWidget, global_mmcore: CMMCorePlus, qtbot: QtBot
) -> None:
    with patch.object(
        global_mmcore, "getPosition", side_effect=RuntimeError("offline")
    ) as read:
        live_z.show()
        live_z.poll_z.setChecked(True)
        qtbot.waitUntil(lambda: "offline" in live_z._viz.toolTip())
        assert live_z._viz._stage_position is None
        read.side_effect = None
        read.return_value = 100
        qtbot.waitUntil(lambda: live_z._live_z == 100)
        assert live_z._reference_z == 100

        with patch.object(global_mmcore, "getFocusDevice", return_value=""):
            qtbot.waitUntil(lambda: live_z._live_z is None)
            assert live_z._viz._stage_position is None

        with patch.object(global_mmcore, "getFocusDevice", return_value="OtherZ"):
            read.return_value = 200
            qtbot.waitUntil(lambda: live_z._live_z == 200)
            assert live_z._reference_z == 200
            assert live_z._viz._stage_position == 0


def test_polling_pauses_during_acquisition(
    live_z: CoreConnectedZPlanWidget, global_mmcore: CMMCorePlus, qtbot: QtBot
) -> None:
    sequence = useq.MDASequence()
    with patch.object(global_mmcore, "getPosition", return_value=100) as read:
        live_z.show()
        live_z.poll_z.setChecked(True)
        qtbot.waitUntil(lambda: live_z._live_z == 100)

        global_mmcore.mda.events.sequenceStarted.emit(sequence, {})
        assert not live_z._polling
        assert live_z._viz._stage_position is None
        qtbot.waitUntil(lambda: not live_z._poll_pending)
        count = read.call_count
        qtbot.wait(300)
        assert read.call_count == count

        global_mmcore.mda.events.sequenceFinished.emit(sequence)
        assert live_z._polling
        qtbot.waitUntil(lambda: live_z._live_z == 100)


def test_core_position_events_supersede_reads_in_flight(
    live_z: CoreConnectedZPlanWidget, global_mmcore: CMMCorePlus, qtbot: QtBot
) -> None:
    started, release = Event(), Event()

    def slow_read(device: str) -> float:
        started.set()
        release.wait(5)
        raise RuntimeError("offline")

    focus = global_mmcore.getFocusDevice()
    with patch.object(global_mmcore, "getPosition", side_effect=slow_read):
        try:
            live_z.show()
            live_z.poll_z.setChecked(True)
            qtbot.waitUntil(started.is_set)

            global_mmcore.events.stagePositionChanged.emit("Other", 50.0)
            assert live_z._live_z is None
            global_mmcore.events.stagePositionChanged.emit(focus, 108.0)
            assert live_z._live_z == 108
        finally:
            release.set()
            qtbot.waitUntil(lambda: not live_z._poll_pending)
        # the read that was in flight is stale: its error must not clear the event
        assert live_z._live_z == 108
        assert "offline" not in live_z._viz.toolTip()


def test_events_from_other_threads_are_handled_on_the_gui_thread(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    # psygnal (unlike Qt signals) calls back in the emitting thread
    monkeypatch.setenv("PYMM_SIGNALS_BACKEND", "psygnal")
    core = CMMCorePlus()
    core.loadSystemConfiguration()
    assert not isinstance(core.events, QObject)
    assert not isinstance(core.mda.events, QObject)

    widget = CoreConnectedZPlanWidget(core)
    qtbot.addWidget(widget)
    gui_thread = get_ident()
    handler_threads: list[int] = []

    def record(method: Callable[[], None]) -> Callable[[], None]:
        def wrapper() -> None:
            handler_threads.append(get_ident())
            method()

        return wrapper

    monkeypatch.setattr(widget, "_update_polling", record(widget._update_polling))
    monkeypatch.setattr(
        widget, "_update_live_marker", record(widget._update_live_marker)
    )

    def emit_in_thread(signal: Any, *args: Any) -> None:
        thread = Thread(target=signal.emit, args=args)
        thread.start()
        thread.join()

    started, release = Event(), Event()

    def slow_read(device: str) -> float:
        started.set()
        release.wait(5)
        return 100

    sequence = useq.MDASequence()
    with patch.object(core, "getPosition", side_effect=slow_read):
        try:
            widget.show()
            widget.poll_z.setChecked(True)
            qtbot.waitUntil(started.is_set)

            emit_in_thread(
                core.events.stagePositionChanged, core.getFocusDevice(), 108.0
            )
            qtbot.waitUntil(lambda: widget._live_z == 108)
            emit_in_thread(core.mda.events.sequenceStarted, sequence, {})
            qtbot.waitUntil(lambda: not widget._polling)
        finally:
            release.set()
            qtbot.waitUntil(lambda: not widget._poll_pending)
        assert widget._live_z is None

        emit_in_thread(core.mda.events.sequenceFinished, sequence)
        qtbot.waitUntil(lambda: widget._live_z == 100)
        widget.poll_z.setChecked(False)
        qtbot.waitUntil(lambda: not widget._poll_pending)

    assert handler_threads
    assert all(thread == gui_thread for thread in handler_threads)


@pytest.mark.parametrize(
    "tabs_type", [CoreMDATabs, CollapsibleCoreMDATabs, TopbarMDATabs]
)
def test_polling_requires_checked_and_visible_z_axis(
    global_mmcore: CMMCorePlus, qtbot: QtBot, tabs_type: type[CoreMDATabs]
) -> None:
    tabs = tabs_type(core=global_mmcore)
    qtbot.addWidget(tabs)
    widget = tabs.z_plan
    tabs.show()
    widget.poll_z.setChecked(True)
    try:
        if isinstance(tabs, CollapsibleCoreMDATabs):
            tabs.section(widget).set_expanded(True)
        else:
            tabs.setCurrentWidget(widget)
        assert widget.isVisible()
        assert not widget._polling

        tabs.setChecked(widget, True)
        assert widget._polling
        qtbot.waitUntil(lambda: widget._live_z is not None)
        # Acquisition disables editors; it must not masquerade as unchecking Z.
        widget.setEnabled(False)
        assert widget._polling
        widget.setEnabled(True)

        if isinstance(tabs, CollapsibleCoreMDATabs):
            tabs.section(widget).set_expanded(False)
        else:
            tabs.setCurrentWidget(tabs.channels)
        assert not widget._polling
        if isinstance(tabs, CollapsibleCoreMDATabs):
            tabs.section(widget).set_expanded(True)
        else:
            tabs.setCurrentWidget(widget)
        assert widget._polling

        tabs.setChecked(widget, False)
        assert not widget._polling
        assert widget._viz._stage_position is None
    finally:
        widget.poll_z.setChecked(False)
        qtbot.waitUntil(lambda: not widget._poll_pending)


@pytest.mark.parametrize(("top", "bottom"), [(10, 0), (0, 10), (0, 0)])
def test_live_marker_is_yellow_and_can_be_hidden(
    qtbot: QtBot, top: float, bottom: float
) -> None:
    from pymmcore_widgets.useq_widgets._z import ZStackViz

    viz = ZStackViz()
    qtbot.addWidget(viz)
    viz.setPlan(top=top, bottom=bottom, center_fraction=0.5, slices=5)
    viz.show()

    def yellow_pixels() -> int:
        image = viz.grab().toImage()
        return sum(
            image.pixelColor(x, y).name() == "#ffd600"
            for y in range(image.height())
            for x in range(image.width())
        )

    # Include a zero span and positions beyond either end of the displayed range.
    for position in (0, -100, 100):
        viz.setStagePosition(position)
        assert yellow_pixels() > 0
    viz.setStagePosition(None)
    assert yellow_pixels() == 0
