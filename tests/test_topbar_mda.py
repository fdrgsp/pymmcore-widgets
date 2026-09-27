from __future__ import annotations

from typing import TYPE_CHECKING

import useq
from qtpy.QtGui import QIcon
from qtpy.QtWidgets import QWidget

from pymmcore_widgets import MDAWidget, MDAWidgetTopbar
from pymmcore_widgets.mda import TopbarMDATabs

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot

MDA = useq.MDASequence(
    time_plan=useq.TIntervalLoops(interval=0.01, loops=2),
    stage_positions=[
        useq.AbsolutePosition(
            x=0,
            y=1,
            z=2,
            name="P1",
            sequence=useq.MDASequence(
                autofocus_plan=useq.AxesBasedAF(
                    autofocus_motor_offset=25.0, axes=("p",)
                )
            ),
        ),
        useq.Position(x=42, y=0, z=3),
    ],
    channels=[
        {"config": "DAPI", "exposure": 1, "acquire_every": 2, "z_offset": 1.5},
        {"config": "FITC", "exposure": 2},
    ],
    z_plan=useq.ZRangeAround(range=1, step=0.3),
    grid_plan=useq.GridRowsColumns(rows=2, columns=1),
    axis_order="tpgzc",
    keep_shutter_open_across=("z",),
)


def test_topbar_is_mda_widget(qtbot: QtBot) -> None:
    wdg = MDAWidgetTopbar()
    qtbot.addWidget(wdg)
    # It IS an MDAWidget: same behavior, different presentation.
    assert isinstance(wdg, MDAWidget)
    assert isinstance(wdg.tab_wdg, TopbarMDATabs)
    assert wdg.tabs is wdg.tab_wdg

    tabs = wdg.tabs
    # The five axes, in this app's order, are followed by Saving. Global
    # settings are not a tab at all (see below). Each tab's
    # native icon/text are both empty; the visible checkbox, icon and name
    # label are a custom button widget instead (see _AxisTabButton), with the
    # name also available as a tooltip on hover.
    tab_bar = tabs.tabBar()
    assert tab_bar is not None
    assert [tabs.tabText(i) for i in range(tabs.count())] == [""] * tabs.count()
    assert [tab_bar.tabToolTip(i) for i in range(tabs.count())] == [
        "Channels",
        "Positions",
        "Grid/Tiles",
        "Z Stack",
        "Time Series",
        "Saving",
    ]
    # Every tab has an icon.
    for i in range(tabs.count()):
        assert not tabs.tabIcon(i).isNull()
        assert isinstance(tabs.tab_icon(i), QIcon)

    # Every tab is checkable (an on/off inclusion state).
    checkable = [tabs.isChecked(i) is not None for i in range(tabs.count())]
    assert checkable == [True] * tabs.count()

    # Global settings always apply, so they are a card below the tabs rather
    # than a tab of their own.
    assert wdg._settings_widget not in tabs._page_for
    assert wdg._settings_box.isAncestorOf(wdg.axis_order)
    assert wdg._settings_box.isAncestorOf(wdg.keep_shutter_open)
    assert wdg._settings_box.isAncestorOf(wdg.af_axis)

    # Saving is a tab, checkable like an axis: its tab checkbox and
    # SaveGroupBox's own checkable state drive each other, and the native
    # title is hidden so only one of the two toggles shows.
    assert tabs.indexOf(wdg.save_info) == tabs.count() - 1
    assert wdg.save_info.isCheckable()
    assert wdg.save_info.title() == ""
    assert not tabs.isChecked(wdg.save_info)
    tabs.setChecked(wdg.save_info, True)
    assert wdg.save_info.isChecked()
    wdg.save_info.setChecked(False)
    assert not tabs.isChecked(wdg.save_info)


def test_topbar_selects_channels_by_default(qtbot: QtBot) -> None:
    wdg = MDAWidgetTopbar()
    qtbot.addWidget(wdg)
    assert wdg.tabs.currentWidget() is wdg.channels


def test_topbar_every_tab_shows_its_editor(qtbot: QtBot) -> None:
    """Selecting a tab must actually reveal its editor.

    Regression test: `__init__` rebuilds the five axis tabs, and
    `QTabWidget.removeTab` hides each page as it drops it. Only the
    card-wrapped editors were shown again afterwards, so Channels,
    Positions, Time Series and Saving came up blank.
    """
    wdg = MDAWidgetTopbar()
    qtbot.addWidget(wdg)
    wdg.show()
    qtbot.waitExposed(wdg)

    editors = [
        wdg.channels,
        wdg.stage_positions,
        wdg.grid_plan,
        wdg.z_plan,
        wdg.time_plan,
        wdg.save_info,
    ]
    assert len(editors) == wdg.tabs.count()
    for editor in editors:
        wdg.tabs.setCurrentWidget(editor)
        assert wdg.tabs.currentWidget() is editor
        assert editor.isVisible(), f"{type(editor).__name__} tab is blank"


def test_topbar_checking_a_tab_switches_to_it(qtbot: QtBot) -> None:
    """Native `CheckableTabWidget` behaviour: checking a tab selects it too.

    Unlike the collapsible presentation's dedicated section widgets, which
    deliberately decouple "included in the acquisition" from "currently
    shown", a real tab's checkbox uses upstream's own default
    (`change_tab_on_check=True`) -- the same behaviour plain `MDAWidget`
    already has for its own tabs.
    """
    wdg = MDAWidgetTopbar()
    qtbot.addWidget(wdg)
    tabs = wdg.tabs
    assert tabs.currentWidget() is wdg.channels  # starts on Channels

    tabs.setChecked(wdg.stage_positions, True)
    assert tabs.isChecked(wdg.stage_positions)
    assert tabs.currentWidget() is wdg.stage_positions


def test_topbar_disabled_axis_stays_selectable(qtbot: QtBot) -> None:
    """A disabled dimension is dimmed but its editor remains viewable."""
    wdg = MDAWidgetTopbar()
    qtbot.addWidget(wdg)
    tabs = wdg.tabs
    assert not tabs.isChecked(wdg.stage_positions)
    tabs.setCurrentWidget(wdg.stage_positions)
    assert tabs.currentWidget() is wdg.stage_positions
    assert not wdg.stage_positions.isEnabled()


def test_topbar_value_parity_with_mda_widget(qtbot: QtBot) -> None:
    """The top-tabs presentation must build the identical sequence."""
    ref = MDAWidget()
    qtbot.addWidget(ref)
    wdg = MDAWidgetTopbar()
    qtbot.addWidget(wdg)

    ref.setValue(MDA)
    wdg.setValue(MDA)

    topbar_value = wdg.value()
    reference_value = ref.value()
    assert topbar_value.replace(metadata={}) == reference_value.replace(metadata={})
    # per-axis inclusion mirrors the reference
    for axis in "cpgzt":
        assert wdg.tabs.isAxisUsed(axis) == ref.tab_wdg.isAxisUsed(axis)
    # per-position autofocus offset preserved
    restored = wdg.value().stage_positions
    assert restored[0].sequence is not None
    assert restored[0].sequence.autofocus_plan is not None
    assert restored[0].sequence.autofocus_plan.autofocus_motor_offset == 25.0


def test_topbar_settings_sit_between_the_tabs_and_the_footer(qtbot: QtBot) -> None:
    """Saving is a tab; global settings are a card of their own below them.

    The settings card is outside both the tab widget (it always applies, so
    it stays visible whichever tab is showing) and the footer (which holds
    only the always-reachable actions).
    """
    wdg = MDAWidgetTopbar()
    qtbot.addWidget(wdg)
    wdg.resize(800, 700)
    wdg.show()
    qtbot.waitExposed(wdg)

    footer = wdg.findChild(QWidget, "mdaExecutionFooter")
    assert footer is not None
    assert not footer.isAncestorOf(wdg._settings_box)
    assert not wdg.tabs.isAncestorOf(wdg._settings_box)
    assert not footer.isAncestorOf(wdg.save_info)
    assert wdg.tabs.isAncestorOf(wdg.save_info)

    def top_of(w: QWidget) -> int:
        return int(w.mapTo(wdg, w.rect().topLeft()).y())

    def bottom_of(w: QWidget) -> int:
        return int(w.mapTo(wdg, w.rect().bottomLeft()).y())

    # tabs above, then the settings card, then the footer
    assert bottom_of(wdg.tabs) <= top_of(wdg._settings_box)
    assert bottom_of(wdg._settings_box) <= top_of(footer)
    for button in (wdg._save_button, wdg._load_button):
        assert footer.isAncestorOf(button)
    assert footer.isAncestorOf(wdg.control_btns)

    qtbot.wait(1)
    save_center = wdg._save_button.mapTo(footer, wdg._save_button.rect().center())
    load_center = wdg._load_button.mapTo(footer, wdg._load_button.rect().center())
    run = wdg.control_btns.run_btn
    run_center = run.mapTo(footer, run.rect().center())
    assert abs(save_center.y() - run_center.y()) <= 2
    assert abs(load_center.y() - run_center.y()) <= 2
    assert save_center.x() < load_center.x() < run_center.x()


def test_topbar_disables_editors_during_run(qtbot: QtBot) -> None:
    wdg = MDAWidgetTopbar()
    qtbot.addWidget(wdg)
    tabs = wdg.tabs
    axis_widgets = {
        "p": wdg.stage_positions,
        "g": wdg.grid_plan,
        "z": wdg.z_plan,
        "t": wdg.time_plan,
    }
    for widget in axis_widgets.values():
        tabs.setChecked(widget, True)

    wdg._enable_widgets(False)
    for widget in (
        wdg.channels,
        wdg.stage_positions,
        wdg.grid_plan,
        wdg.z_plan,
        wdg.time_plan,
    ):
        assert not widget.isEnabled()
    for cbox in tabs._cboxes:
        assert not cbox.isEnabled()
    assert not wdg._settings_box.isEnabled()
    assert not wdg.save_info.isEnabled()
    assert not wdg._save_button.isEnabled()
    assert not wdg._load_button.isEnabled()

    wdg._enable_widgets(True)
    assert wdg.channels.isEnabled()
    assert wdg._settings_box.isEnabled()
    assert wdg.save_info.isEnabled()
    assert wdg._save_button.isEnabled()
    assert wdg._load_button.isEnabled()


def test_topbar_run_preserves_axis_order(qtbot: QtBot) -> None:
    wdg = MDAWidgetTopbar()
    qtbot.addWidget(wdg)
    wdg.setValue(MDA)

    combo = wdg.axis_order
    before = combo.currentText()
    assert before == "tpgzc"

    with qtbot.waitSignal(wdg._mmc.mda.events.sequenceFinished):
        wdg.control_btns.run_btn.click()

    assert combo.currentText() == before
    wdg.control_btns._disconnect()
    wdg._disconnect()


def test_topbar_runs_acquisition(qtbot: QtBot) -> None:
    wdg = MDAWidgetTopbar()
    qtbot.addWidget(wdg)
    wdg.setValue(MDA)

    with qtbot.waitSignal(wdg._mmc.mda.events.sequenceFinished):
        wdg.control_btns.run_btn.click()

    assert wdg.control_btns.run_btn.isEnabled()
    wdg.control_btns._disconnect()
    wdg._disconnect()


def test_topbar_card_tab_enables_content_when_checked(qtbot: QtBot) -> None:
    """Checking a card-wrapped axis tab (grid, z) enables its inner widget."""
    wdg = MDAWidgetTopbar()
    qtbot.addWidget(wdg)
    tabs = wdg.tabs
    for widget in (wdg.grid_plan, wdg.z_plan):
        assert not widget.isEnabled()
        tabs.setChecked(widget, True)
        assert widget.isEnabled()
        tabs.setChecked(widget, False)
        assert not widget.isEnabled()


def test_topbar_duration_label_in_footer(qtbot: QtBot) -> None:
    """Duration/time-estimate label must live in the footer, not float over tabs."""
    wdg = MDAWidgetTopbar()
    qtbot.addWidget(wdg)
    footer = wdg.findChild(QWidget, "mdaExecutionFooter")
    assert footer is not None
    assert footer.isAncestorOf(wdg._duration_label)
    assert footer.isAncestorOf(wdg._time_warning)
