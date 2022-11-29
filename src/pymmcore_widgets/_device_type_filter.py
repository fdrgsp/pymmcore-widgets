from __future__ import annotations

from typing import cast

from pymmcore_plus import DeviceType
from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

DevTypeLabels: dict[str, tuple[DeviceType, ...]] = {
    "cameras": (DeviceType.CameraDevice,),
    "shutters": (DeviceType.ShutterDevice,),
    "stages": (DeviceType.StageDevice,),
    "wheels, turrets, etc.": (DeviceType.StateDevice,),
}
_d: set[DeviceType] = set.union(*(set(i) for i in DevTypeLabels.values()))
DevTypeLabels["other devices"] = tuple(set(DeviceType) - _d)


class DeviceTypeFilters(QWidget):
    filtersChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent=parent)
        self._show_read_only: bool = True
        self._filters: set[DeviceType] = set()

        all_btn = QPushButton("All")
        all_btn.clicked.connect(self._check_all)
        none_btn = QPushButton("None")
        none_btn.clicked.connect(self._check_none)

        grid = QGridLayout()
        grid.setSpacing(6)
        grid.addWidget(all_btn, 0, 0, 1, 1)
        grid.addWidget(none_btn, 0, 1, 1, 1)
        for i, (label, devtypes) in enumerate(DevTypeLabels.items()):
            cb = QCheckBox(label)
            cb.setChecked(devtypes[0] not in self._filters)
            cb.toggled.connect(self._toggle_filter)
            grid.addWidget(cb, i + 1, 0, 1, 2)

        self._dev_gb = QGroupBox("Device Type")
        self._dev_gb.setLayout(grid)

        for x in self._dev_gb.findChildren(QWidget):
            cast(QWidget, x).setFocusPolicy(Qt.FocusPolicy.NoFocus)

        ro = QCheckBox("Show read-only")
        ro.setChecked(self._show_read_only)
        ro.toggled.connect(self._set_show_read_only)
        ro.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        layout = QVBoxLayout()
        layout.addWidget(self._dev_gb)
        layout.addWidget(ro)
        layout.addStretch()
        self.setLayout(layout)

    def _check_all(self) -> None:
        for cxbx in self._dev_gb.findChildren(QCheckBox):
            cast(QCheckBox, cxbx).setChecked(True)

    def _check_none(self) -> None:
        for cxbx in self._dev_gb.findChildren(QCheckBox):
            cast(QCheckBox, cxbx).setChecked(False)

    def _toggle_filter(self, toggled: bool) -> None:
        label = cast(QCheckBox, self.sender()).text()
        self._filters.symmetric_difference_update(DevTypeLabels[label])
        self.filtersChanged.emit()

    def _set_show_read_only(self, state: bool) -> None:
        self._show_read_only = state
        self.filtersChanged.emit()

    def filters(self) -> set[DeviceType]:
        return self._filters

    def showReadOnly(self) -> bool:
        return self._show_read_only