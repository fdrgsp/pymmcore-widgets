from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from qtpy.QtGui import QColor, QPalette
from qtpy.QtWidgets import QWidget
from useq import MDASequence, WellPlate, WellPlatePlan

from pymmcore_widgets._busy import BusyOverlay, busy, progress
from pymmcore_widgets.mda._core_positions import CoreConnectedPositionTable
from pymmcore_widgets.useq_widgets import MDASequenceWidget

if TYPE_CHECKING:
    from pathlib import Path

    from pymmcore_plus import CMMCorePlus
    from pytestqt.qtbot import QtBot


@pytest.mark.parametrize(
    "background,foreground", [("#202020", "white"), ("#eeeeee", "black")]
)
def test_busy_overlay_uses_parent_palette(
    qtbot: QtBot, background: str, foreground: str
) -> None:
    parent = QWidget()
    parent.resize(240, 100)
    palette = parent.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(background))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(foreground))
    parent.setPalette(palette)
    qtbot.addWidget(parent)
    overlay = BusyOverlay(parent)
    parent.show()

    with busy(overlay, "Working…"):
        assert overlay.isVisible()
        assert overlay.geometry() == parent.rect()
        image = overlay.grab().toImage()
        assert image.pixelColor(0, 0).name() == background
        assert any(
            image.pixelColor(x, y).name() == QColor(foreground).name()
            for y in range(image.height())
            for x in range(image.width())
        )
    assert overlay.isHidden()


def test_busy_overlay_shows_reported_progress(qtbot: QtBot) -> None:
    parent = QWidget()
    parent.resize(300, 120)
    qtbot.addWidget(parent)
    overlay = BusyOverlay(parent)
    parent.show()
    highlight = parent.palette().color(QPalette.ColorRole.Highlight).name()

    def bar_pixels() -> int:
        image = overlay.grab().toImage()
        return sum(
            image.pixelColor(x, y).name() == highlight
            for y in range(image.height())
            for x in range(image.width())
        )

    progress(1, 2)  # outside a busy block: nothing to report to
    with busy(overlay, "Working…"):
        assert overlay._fraction is None
        progress(1, 4)
        assert overlay._fraction == 0.25
        quarter = bar_pixels()
        progress(3, 4)
        assert bar_pixels() > quarter > 0
    with busy(overlay, "Working…"):
        assert overlay._fraction is None
        assert bar_pixels() == 0


def test_position_import_reports_progress(
    qtbot: QtBot, global_mmcore: CMMCorePlus, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor = CoreConnectedPositionTable(mmcore=global_mmcore)
    qtbot.addWidget(editor)
    editor.show()
    plan = WellPlatePlan(
        plate=WellPlate.from_str("96-well"),
        a1_center_xy=(1000, 2000),
        selected_wells=((0, 0, 1), (0, 1, 1)),
    )
    reported: list[tuple[int, int]] = []
    monkeypatch.setattr(
        BusyOverlay, "setProgress", lambda _, *step: reported.append(step)
    )
    editor._update_table_positions(plan)
    assert reported == [(1, 3), (2, 3), (3, 3)]


@pytest.mark.parametrize("hidden", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_position_import_feedback_clears_after_success_or_error(
    qtbot: QtBot,
    global_mmcore: CMMCorePlus,
    monkeypatch: pytest.MonkeyPatch,
    hidden: bool,
    fail: bool,
) -> None:
    editor = CoreConnectedPositionTable(mmcore=global_mmcore)
    qtbot.addWidget(editor)
    if not hidden:
        editor.show()
    plan = WellPlatePlan(
        plate=WellPlate.from_str("96-well"),
        a1_center_xy=(1000, 2000),
        selected_wells=((0,), (0,)),
    )
    populate = editor.setValue
    called = False

    def populate_with_feedback(value: WellPlatePlan) -> None:
        nonlocal called
        called = True
        assert editor._import_overlay.isVisible() is not hidden
        if not hidden:
            assert editor._import_overlay._message == "Creating position list…"
        if fail:
            raise RuntimeError("Position creation failed")
        populate(value)

    monkeypatch.setattr(editor, "setValue", populate_with_feedback)
    if fail:
        with pytest.raises(RuntimeError, match="Position creation failed"):
            editor._update_table_positions(plan)
    else:
        editor._update_table_positions(plan)
        assert editor.value() == plan
        assert editor.table().rowCount() == 1
    assert called
    assert editor._import_overlay.isHidden()
    assert editor.table().isVisible() is not hidden


def test_mda_load_feedback(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wdg = MDASequenceWidget()
    qtbot.addWidget(wdg)
    wdg.show()
    seq = MDASequence(time_plan={"interval": 1, "loops": 2})
    file = tmp_path / "seq.json"
    file.write_text(seq.model_dump_json())
    populate = MDASequenceWidget.setValue

    def populate_with_feedback(self: MDASequenceWidget, value: MDASequence) -> None:
        assert self._load_overlay.isVisible()
        assert self._load_overlay._message == "Loading MDA settings…"
        populate(self, value)

    monkeypatch.setattr(MDASequenceWidget, "setValue", populate_with_feedback)
    wdg.load(file)
    assert wdg._load_overlay.isHidden()
    assert wdg.value().time_plan == seq.time_plan
