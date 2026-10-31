from __future__ import annotations

from typing import TYPE_CHECKING

from qtpy.QtWidgets import QWidget

from pymmcore_widgets._util import QWIDGETSIZE_MAX

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


def test_qwidgetsize_max_matches_qt(qtbot: QtBot) -> None:
    # defined by hand because PySide does not expose Qt's QWIDGETSIZE_MAX
    wdg = QWidget()
    qtbot.addWidget(wdg)
    assert wdg.maximumWidth() == wdg.maximumHeight() == QWIDGETSIZE_MAX
