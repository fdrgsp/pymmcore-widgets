from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from qtpy.QtWidgets import (
    QProxyStyle,
    QStyle,
    QStyleOption,
    QTableView,
    QTableWidget,
    QWidget,
)

from pymmcore_widgets import _util
from pymmcore_widgets._install_widget import _InstallTable
from pymmcore_widgets._util import (
    QWIDGETSIZE_MAX,
    combo_box_height,
    use_combo_row_height,
)
from pymmcore_widgets.config_presets._group_preset_widget._group_preset_table_widget import (  # noqa: E501
    _MainTable,
)
from pymmcore_widgets.config_presets._pixel_configuration_widget import (
    _PixelTable,
    _PropertyValueTableView,
)
from pymmcore_widgets.config_presets._views._config_presets_table import (
    ConfigPresetsTableView,
)
from pymmcore_widgets.device_properties._device_property_table import (
    DevicePropertyTable,
)
from pymmcore_widgets.hcs._well_calibration_widget import _CalibrationTable

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytestqt.qtbot import QtBot
    from qtpy.QtCore import QSize


def test_qwidgetsize_max_matches_qt(qtbot: QtBot) -> None:
    # defined by hand because PySide does not expose Qt's QWIDGETSIZE_MAX
    wdg = QWidget()
    qtbot.addWidget(wdg)
    assert wdg.maximumWidth() == wdg.maximumHeight() == QWIDGETSIZE_MAX


def test_use_combo_row_height(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    table = QTableWidget(3, 1)
    qtbot.addWidget(table)
    table.setRowHidden(1, True)
    use_combo_row_height(table)

    row_height = combo_box_height(table) + 1  # the grid line
    assert table.rowHeight(0) == table.rowHeight(2) == row_height
    table.insertRow(3)
    assert table.rowHeight(3) == row_height
    assert table.isRowHidden(1)

    # a font or style change, which is how the rows follow the zoom of an app
    monkeypatch.setattr(_util, "combo_box_height", lambda _: 49)
    font = table.font()
    font.setPointSizeF(font.pointSizeF() + 1)
    table.setFont(font)
    assert {table.rowHeight(r) for r in (0, 2, 3)} == {50}
    table.insertRow(4)
    assert table.rowHeight(4) == 50

    # a hidden row stays hidden, and comes back at the current height
    assert table.isRowHidden(1)
    table.setRowHidden(1, False)
    assert table.rowHeight(1) == 50


@pytest.mark.parametrize(
    "make_table",
    [
        pytest.param(_InstallTable, id="install"),
        pytest.param(DevicePropertyTable, id="device_properties"),
        pytest.param(_CalibrationTable, id="well_calibration"),
        pytest.param(_MainTable, id="group_presets"),
        pytest.param(ConfigPresetsTableView, id="config_presets"),
        pytest.param(_PropertyValueTableView, id="property_values"),
    ],
)
def test_tables_use_the_combo_row_height(
    make_table: Callable[[], QTableView], qtbot: QtBot
) -> None:
    table = make_table()
    qtbot.addWidget(table)
    v_header = table.verticalHeader()
    assert v_header.defaultSectionSize() == combo_box_height(table) + 1


def test_pixel_table_uses_the_combo_row_height(qtbot: QtBot) -> None:
    wdg = _PixelTable()
    qtbot.addWidget(wdg)
    table = wdg.table()
    assert table.verticalHeader().defaultSectionSize() == combo_box_height(table) + 1


def test_combo_row_height_uses_the_tables_style(qtbot: QtBot) -> None:
    class TallComboStyle(QProxyStyle):
        def sizeFromContents(
            self,
            ct: QStyle.ContentsType,
            opt: QStyleOption | None,
            contentsSize: QSize,
            widget: QWidget | None = None,
        ) -> QSize:
            size = super().sizeFromContents(ct, opt, contentsSize, widget)
            if ct == QStyle.ContentsType.CT_ComboBox:
                size.setHeight(65)
            return size

    table = QTableWidget(1, 1)
    qtbot.addWidget(table)
    style = TallComboStyle("Fusion")
    style.setParent(table)
    table.setStyle(style)
    use_combo_row_height(table)
    assert table.rowHeight(0) == 66
