from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Iterable, Sequence

from pymmcore_plus import CMMCorePlus, DeviceProperty, DeviceType, Keyword
from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from pymmcore_widgets._device_property_table import DevicePropertyTable

if TYPE_CHECKING:
    from pymmcore_plus.model import Setting


class _PropertiesGroupBox(QGroupBox):
    valueChanged = Signal()

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        mmcore: CMMCorePlus | None = None,
    ) -> None:
        super().__init__(title, parent)
        self.setCheckable(True)
        self.setChecked(False)
        self.toggled.connect(self.valueChanged)
        self._mmc = mmcore or CMMCorePlus.instance()

        self.props = DevicePropertyTable(self, mmcore=self._mmc, connect_core=False)
        self.props.valueChanged.connect(self.valueChanged)
        self.props.setRowsCheckable(True)

    def value(self) -> Iterable[tuple[str, str, str]]:
        for d, p, v in self.props.value():
            with contextlib.suppress(IndexError):
                item = self.props.findItems(f"{d}-{p}", Qt.MatchFlag.MatchExactly)[0]
                row = item.row()
                if not self.props.isRowHidden(row):
                    yield d, p, v

    def setValue(self, values: Sequence[tuple[str, str, str] | Setting]) -> None:
        _to_add = []
        for d, p, v in values:
            with contextlib.suppress(IndexError):
                item = self.props.findItems(f"{d}-{p}", Qt.MatchFlag.MatchExactly)[0]
                row = item.row()
                if self.props.isRowHidden(row):
                    continue
                _to_add.append((d, p, v))

        self.props.setValue(_to_add)
        self.setChecked(bool(_to_add))


def light_path_predicate(prop: DeviceProperty) -> bool | None:
    devtype = prop.deviceType()
    if devtype == DeviceType.Shutter:
        return False
    if any(x in prop.device for x in prop.core.guessObjectiveDevices()):
        return False
    return None


class _LightPathGroupBox(_PropertiesGroupBox):
    def __init__(
        self, parent: QWidget | None = None, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__("Light Path", parent, mmcore)

        self.active_shutter = QComboBox(self)
        shutters = self._mmc.getLoadedDevicesOfType(DeviceType.Shutter)
        self.active_shutter.addItems(("", *shutters))
        self.active_shutter.currentIndexChanged.connect(self.valueChanged)

        self.props.filterDevices(
            exclude_devices=[
                DeviceType.Camera,
                DeviceType.Core,
                DeviceType.AutoFocus,
                DeviceType.Stage,
                DeviceType.XYStage,
            ],
            include_read_only=False,
            include_pre_init=False,
            predicate=light_path_predicate,
        )

        shutter_layout = QHBoxLayout()
        shutter_layout.setContentsMargins(2, 0, 0, 0)
        shutter_layout.addWidget(QLabel("Active Shutter:"), 0)
        shutter_layout.addWidget(self.active_shutter, 1)
        shutter_layout.addSpacerItem(QSpacerItem(40, 0))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        layout.addLayout(shutter_layout)
        layout.addWidget(self.props)

    def value(self) -> Iterable[tuple[str, str, str]]:
        yield from super().value()
        yield (
            Keyword.CoreDevice.value,
            Keyword.CoreShutter.value,
            self.active_shutter.currentText(),
        )

    def setValue(self, values: Sequence[tuple[str, str, str] | Setting]) -> None:
        enable: bool = False
        _to_add = []
        for d, p, v in values:
            if d == Keyword.CoreDevice.value and p == Keyword.CoreShutter.value:
                self.active_shutter.setCurrentText(v)
                enable = True
                continue

            with contextlib.suppress(IndexError):
                item = self.props.findItems(f"{d}-{p}", Qt.MatchFlag.MatchExactly)[0]
                row = item.row()
                if self.props.isRowHidden(row):
                    continue
                _to_add.append((d, p, v))

        self.props.setValue(_to_add)
        self.setChecked(bool(enable or _to_add))


class _CameraGroupBox(_PropertiesGroupBox):
    def __init__(
        self, parent: QWidget | None = None, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__("Camera", parent, mmcore)

        self.active_camera = QComboBox(self)
        cameras = self._mmc.getLoadedDevicesOfType(DeviceType.Camera)
        self.active_camera.addItems(("", *cameras))
        self.active_camera.currentIndexChanged.connect(self.valueChanged)

        self.props.filterDevices(
            include_devices=[DeviceType.Camera],
            include_read_only=False,
            include_pre_init=False,
        )

        camera_layout = QHBoxLayout()
        camera_layout.setContentsMargins(2, 0, 0, 0)
        camera_layout.addWidget(QLabel("Active Camera:"), 0)
        camera_layout.addWidget(self.active_camera, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        layout.addLayout(camera_layout)
        layout.addWidget(self.props)

    def value(self) -> Iterable[tuple[str, str, str]]:
        yield from super().value()
        yield (
            Keyword.CoreDevice.value,
            Keyword.CoreCamera.value,
            self.active_camera.currentText(),
        )

    def setValue(self, values: Sequence[tuple[str, str, str] | Setting]) -> None:
        enable: bool = False
        _to_add = []
        for d, p, v in values:
            if d == Keyword.CoreDevice.value and p == Keyword.CoreCamera.value:
                self.active_camera.setCurrentText(v)
                enable = True
                continue

            with contextlib.suppress(IndexError):
                item = self.props.findItems(f"{d}-{p}", Qt.MatchFlag.MatchExactly)[0]
                row = item.row()
                if self.props.isRowHidden(row):
                    continue
                _to_add.append((d, p, v))

        self.props.setValue(_to_add)
        self.setChecked(bool(enable or _to_add))


class _StageGroupBox(_PropertiesGroupBox):
    def __init__(
        self, parent: QWidget | None = None, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__("Stage", parent, mmcore)

        self.active_z_stage = QComboBox(self)
        stages = self._mmc.getLoadedDevicesOfType(DeviceType.Stage)
        self.active_z_stage.addItems(("", *stages))
        self.active_z_stage.currentIndexChanged.connect(self.valueChanged)

        self.active_xy_stage = QComboBox(self)
        xy_stages = self._mmc.getLoadedDevicesOfType(DeviceType.XYStage)
        self.active_xy_stage.addItems(("", *xy_stages))
        self.active_xy_stage.currentIndexChanged.connect(self.valueChanged)

        self.props.filterDevices(
            include_devices=[DeviceType.Stage, DeviceType.XYStage],
            include_read_only=False,
            include_pre_init=False,
        )

        stage_layout = QHBoxLayout()
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.addWidget(QLabel("Active XY Stage:"), 0)
        stage_layout.addWidget(self.active_xy_stage, 1)
        stage_layout.addWidget(QLabel("Active Z Stage:"), 0)
        stage_layout.addWidget(self.active_z_stage, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        layout.addLayout(stage_layout)
        layout.addWidget(self.props)

    def value(self) -> Iterable[tuple[str, str, str]]:
        yield from super().value()
        yield (
            Keyword.CoreDevice.value,
            Keyword.CoreXYStage.value,
            self.active_xy_stage.currentText(),
        )
        yield (
            Keyword.CoreDevice.value,
            Keyword.CoreFocus.value,
            self.active_z_stage.currentText(),
        )

    def setValue(self, values: Sequence[tuple[str, str, str] | Setting]) -> None:
        enable: bool = False
        _to_add = []
        for d, p, v in values:
            if d == Keyword.CoreDevice.value and p == Keyword.CoreXYStage.value:
                self.active_xy_stage.setCurrentText(v)
                enable = True
                continue
            if d == Keyword.CoreDevice.value and p == Keyword.CoreFocus.value:
                self.active_z_stage.setCurrentText(v)
                enable = True
                continue

            with contextlib.suppress(IndexError):
                item = self.props.findItems(f"{d}-{p}", Qt.MatchFlag.MatchExactly)[0]
                row = item.row()
                if self.props.isRowHidden(row):
                    continue
                _to_add.append((d, p, v))

        self.props.setValue(_to_add)
        self.setChecked(bool(enable or _to_add))


class _ObjectiveGroupBox(_PropertiesGroupBox):
    def __init__(
        self, parent: QWidget | None = None, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__("Objective", parent, mmcore)

        self.props.filterDevices(
            include_devices=[DeviceType.StateDevice],
            include_read_only=False,
            include_pre_init=False,
            predicate=self._objective_predicate,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        layout.addWidget(self.props)

    def _objective_predicate(self, prop: DeviceProperty) -> bool | None:
        devtype = prop.deviceType()
        if devtype == DeviceType.StateDevice:
            obj_devices = self._mmc.guessObjectiveDevices()
            if prop.device not in obj_devices:
                return False
        return None


def other_predicate(prop: DeviceProperty) -> bool | None:
    devtype = prop.deviceType()
    if devtype == DeviceType.CoreDevice and prop.name in (
        Keyword.CoreShutter.value,
        Keyword.CoreCamera.value,
        Keyword.CoreXYStage.value,
        Keyword.CoreFocus.value,
    ):
        return False
    return None


class _OtherGroupBox(_PropertiesGroupBox):
    def __init__(
        self, parent: QWidget | None = None, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__("Other", parent, mmcore)

        self.props.filterDevices(
            exclude_devices=[
                DeviceType.Camera,
                DeviceType.Stage,
                DeviceType.XYStage,
                DeviceType.StateDevice,
            ],
            include_read_only=False,
            include_pre_init=False,
            predicate=other_predicate,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        layout.addWidget(self.props)
