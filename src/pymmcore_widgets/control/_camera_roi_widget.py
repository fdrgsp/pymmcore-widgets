from __future__ import annotations

from contextlib import ExitStack, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple, TypedDict

from pymmcore_plus import CMMCorePlus, DeviceType
from qtpy.QtCore import QSize, Qt, QTimer, Signal, Slot
from qtpy.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from superqt.iconify import QIconifyIcon
from superqt.utils import signals_blocked

if TYPE_CHECKING:
    from collections.abc import Mapping

fixed_sizepolicy = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
FULL = "Full Chip"
CUSTOM_ROI = "Custom ROI"


class ROI(NamedTuple):
    x: int
    y: int
    w: int
    h: int
    centered: bool


class CameraRoiValue(TypedDict):
    """Serializable ROI state for one camera."""

    camera: str
    x: int
    y: int
    width: int
    height: int


@dataclass
class CameraInfo:
    pixel_width: int
    pixel_height: int
    crop_mode: str
    roi: ROI

    def replace(self, **kwargs: Any) -> CameraInfo:
        return CameraInfo(
            kwargs.get("pixel_width", self.pixel_width),
            kwargs.get("pixel_height", self.pixel_height),
            kwargs.get("crop_mode", self.crop_mode),
            kwargs.get("roi", self.roi),
        )


class CameraRoiWidget(QWidget):
    """A Widget to control the camera device ROI.

    When the ROI changes, the `roiChanged` Signal is emitted with the current ROI
    (x, y, width, height, comboBoxText)

    Parameters
    ----------
    parent : QWidget | None
        Optional parent widget, by default None
    mmcore : CMMCorePlus | None
        Optional [`pymmcore_plus.CMMCorePlus`][] micromanager core.
        By default, None. If not specified, the widget will use the active
        (or create a new)
        [`CMMCorePlus.instance`][pymmcore_plus.core._mmcore_plus.CMMCorePlus.instance].
    show_live_selection : bool
        Show a button that emits :attr:`roiSelectionRequested`. The host application
        is responsible for opening and connecting a live viewer.
    show_auto_snap : bool
        Show the legacy Auto Snap option. Hosts with a live preview can hide it.
    """

    # (x, y, width, height, comboBoxText)
    roiChanged = Signal(int, int, int, int, str)
    roiSelectionRequested = Signal(bool)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        mmcore: CMMCorePlus | None = None,
        show_live_selection: bool = False,
        show_auto_snap: bool = True,
    ) -> None:
        super().__init__(parent=parent)

        self._mmc = mmcore or CMMCorePlus.instance()
        self._show_auto_snap = show_auto_snap
        self._show_roi_info = True
        self._live_restart_pending = False

        # this is use to store each camera information so that when the camera is
        # changed in the widget, the proper values can be updated.
        self._cameras: dict[str, CameraInfo] = {}

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(5)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # camera and mode selector groupbox ---------------------------------------
        self._selector_wdg = QGroupBox()
        _selector_layout = QGridLayout(self._selector_wdg)
        _selector_layout.setSpacing(5)
        _selector_layout.setContentsMargins(3, 3, 3, 3)

        _camera_lbl = QLabel("Camera:")
        _camera_lbl.setSizePolicy(fixed_sizepolicy)
        self.camera_combo = QComboBox()
        _selector_layout.addWidget(_camera_lbl, 0, 0)
        _selector_layout.addWidget(self.camera_combo, 0, 1)

        _crop_mode_lbl = QLabel("Mode:")
        _crop_mode_lbl.setSizePolicy(fixed_sizepolicy)
        self.camera_roi_combo = QComboBox()
        _selector_layout.addWidget(_crop_mode_lbl, 0, 2)
        _selector_layout.addWidget(self.camera_roi_combo, 0, 3)

        main_layout.addWidget(self._selector_wdg)

        # custom roi groupbox ---------------------------------------------------
        self._custom_roi_wdg = QGroupBox()
        layout = QGridLayout(self._custom_roi_wdg)
        layout.setSpacing(5)
        layout.setContentsMargins(3, 3, 3, 3)

        _roi_start_x_label = QLabel("Start x:")
        _roi_start_x_label.setSizePolicy(fixed_sizepolicy)
        self.start_x = QSpinBox()
        self.start_x.setMinimum(0)
        self.start_x.setMaximum(10000)
        self.start_x.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _roi_start_y_label = QLabel("Start y:")
        _roi_start_y_label.setSizePolicy(fixed_sizepolicy)
        self.start_y = QSpinBox()
        self.start_y.setMinimum(0)
        self.start_y.setMaximum(10000)
        self.start_y.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(_roi_start_x_label, 1, 0, 1, 1)
        layout.addWidget(self.start_x, 1, 1, 1, 1)
        layout.addWidget(_roi_start_y_label, 2, 0, 1, 1)
        layout.addWidget(self.start_y, 2, 1, 1, 1)

        _roi_size_label = QLabel("Width:")
        _roi_size_label.setSizePolicy(fixed_sizepolicy)
        self.roi_width = QSpinBox()
        self.roi_width.setObjectName("roi_width")
        self.roi_width.setMinimum(1)
        self.roi_width.setMaximum(10000)
        self.roi_width.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _roi_height_label = QLabel("Height:")
        _roi_height_label.setSizePolicy(fixed_sizepolicy)
        self.roi_height = QSpinBox()
        self.roi_height.setObjectName("roi_height")
        self.roi_height.setMinimum(1)
        self.roi_height.setMaximum(10000)
        self.roi_height.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(_roi_size_label, 1, 2, 1, 1)
        layout.addWidget(self.roi_width, 1, 3, 1, 1)
        layout.addWidget(_roi_height_label, 2, 2, 1, 1)
        layout.addWidget(self.roi_height, 2, 3, 1, 1)

        self.center_checkbox = QCheckBox(text="center ROI")
        layout.addWidget(self.center_checkbox, 3, 0, 1, 4)

        main_layout.addWidget(self._custom_roi_wdg)

        # info label groupbox ---------------------------------------------------
        self._info_lbl_wdg = QGroupBox()
        _info_layout = QVBoxLayout(self._info_lbl_wdg)
        _info_layout.setSpacing(5)
        _info_layout.setContentsMargins(3, 3, 3, 3)
        self.lbl_info = QLabel("....")
        _info_layout.addWidget(self.lbl_info)

        main_layout.addWidget(self._info_lbl_wdg)

        # snap and crop buttons widget -------------------------------------------
        self._bottom_wdg = QWidget()
        _bottom_layout = QHBoxLayout(self._bottom_wdg)
        _bottom_layout.setSpacing(10)
        _bottom_layout.setContentsMargins(0, 0, 0, 0)

        self.snap_checkbox = QCheckBox(text="Auto Snap")

        self.crop_btn = QPushButton("Crop")
        self.crop_btn.setIcon(QIconifyIcon("mdi:crop", color="green"))
        self.crop_btn.setIconSize(QSize(24, 24))

        self.select_roi_btn = QPushButton("Select in Live View")
        self.select_roi_btn.setIcon(
            QIconifyIcon(
                "material-symbols-light:screenshot-region-rounded",
                color="green",
            )
        )
        self.select_roi_btn.setIconSize(QSize(24, 24))
        self.select_roi_btn.setCheckable(True)
        self.select_roi_btn.setToolTip(
            "Open the live preview and select the camera ROI on the image"
        )
        self.select_roi_btn.setVisible(show_live_selection)

        _bottom_layout.addWidget(self.snap_checkbox)
        _bottom_layout.addStretch()
        _bottom_layout.addWidget(self.select_roi_btn)
        _bottom_layout.addWidget(self.crop_btn)

        main_layout.addWidget(self._bottom_wdg)

        # core connections -------------------------------------------------------
        self._mmc.events.systemConfigurationLoaded.connect(self._on_sys_cfg_loaded)
        self._mmc.events.pixelSizeChanged.connect(self._update_lbl_info)
        self._mmc.events.roiSet.connect(self._on_roi_set)
        self._mmc.events.propertyChanged.connect(self._on_property_changed)

        # widget connections -----------------------------------------------------
        self.camera_combo.currentTextChanged.connect(self._on_camera_changed)
        self.camera_roi_combo.currentTextChanged.connect(self._on_crop_roi_mode_change)
        self.center_checkbox.toggled.connect(self._on_center_checkbox)
        self.roi_width.valueChanged.connect(self._on_roi_width_height_changed)
        self.roi_height.valueChanged.connect(self._on_roi_width_height_changed)
        self.start_x.valueChanged.connect(self._on_start_spinbox_changed)
        self.start_y.valueChanged.connect(self._on_start_spinbox_changed)
        self.crop_btn.clicked.connect(self._on_crop_button_clicked)
        self.select_roi_btn.toggled.connect(self.roiSelectionRequested.emit)

        self.destroyed.connect(self._disconnect)

        # initialize the widget --------------------------------------------------
        self._on_sys_cfg_loaded()

    @property
    def camera(self) -> str:
        return str(self.camera_combo.currentText())

    def value(self) -> dict[str, CameraInfo]:
        """Return the camera information dict."""
        return self._cameras

    def roiValue(self) -> CameraRoiValue:
        """Return the currently displayed ROI as JSON-serializable primitives."""
        x, y, width, height = self._get_roi_values()
        return {
            "camera": self.camera,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }

    def fullFrameValue(self, camera: str | None = None) -> CameraRoiValue:
        """Return the full-frame ROI for ``camera`` without changing hardware."""
        camera = camera or self.camera
        if camera not in self._cameras:
            raise ValueError(f"Camera {camera!r} is not available")
        info = self._cameras[camera]
        return {
            "camera": camera,
            "x": 0,
            "y": 0,
            "width": info.pixel_width,
            "height": info.pixel_height,
        }

    def applyFullFrame(self) -> CameraRoiValue:
        """Clear the active camera ROI and return its actual full-frame geometry."""
        current = tuple(self._mmc.getROI(self.camera))
        full = self.fullFrameValue()
        expected = (full["x"], full["y"], full["width"], full["height"])
        if current == expected:
            return full
        self._clearROI()
        x, y, width, height = self._mmc.getROI(self.camera)
        return {
            "camera": self.camera,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }

    def setRoiValue(self, value: Mapping[str, object]) -> None:
        """Restore a planned ROI in the editor without applying it to hardware."""
        try:
            camera = value["camera"]
            x = value["x"]
            y = value["y"]
            width = value["width"]
            height = value["height"]
        except KeyError as e:
            raise ValueError("Invalid camera ROI value") from e
        if not isinstance(camera, str) or any(
            not isinstance(item, int) or isinstance(item, bool)
            for item in (x, y, width, height)
        ):
            raise ValueError("Invalid camera ROI value")
        assert isinstance(x, int)
        assert isinstance(y, int)
        assert isinstance(width, int)
        assert isinstance(height, int)

        if camera not in self._cameras:
            raise ValueError(f"Camera {camera!r} is not available")
        info = self._cameras[camera]
        if (
            x < 0
            or y < 0
            or width <= 0
            or height <= 0
            or x + width > info.pixel_width
            or y + height > info.pixel_height
        ):
            raise ValueError(
                f"ROI {(x, y, width, height)!r} is outside camera {camera!r}"
            )

        centered = (
            x == (info.pixel_width - width) // 2
            and y == (info.pixel_height - height) // 2
        )
        roi = ROI(x, y, width, height, centered)
        mode = self._get_updated_crop_mode(camera, x, y, width, height)
        self._cameras[camera] = info.replace(crop_mode=mode, roi=roi)

        widgets = (
            self.camera_combo,
            self.camera_roi_combo,
            self.start_x,
            self.start_y,
            self.roi_width,
            self.roi_height,
            self.center_checkbox,
        )
        with ExitStack() as stack:
            for widget in widgets:
                stack.enter_context(signals_blocked(widget))
            self.camera_combo.setCurrentText(camera)
            self._update_roi_values(roi)
            self.camera_roi_combo.setCurrentText(mode)

        self._hide_spinbox_button(mode != CUSTOM_ROI)
        self.start_x.setEnabled(mode == CUSTOM_ROI and not centered)
        self.start_y.setEnabled(mode == CUSTOM_ROI and not centered)
        self._custom_roi_wdg.setEnabled(mode == CUSTOM_ROI)
        self.crop_btn.setEnabled(mode == CUSTOM_ROI)
        self._update_lbl_info()
        self.roiChanged.emit(x, y, width, height, mode)

    def setLiveSelectionActive(self, active: bool) -> None:
        """Update the live-selection button without emitting a new request."""
        with signals_blocked(self.select_roi_btn):
            self.select_roi_btn.setChecked(active)

    def setRoiSelectionAvailable(self, available: bool) -> None:
        """Show the live-selection action when a host has connected a viewer."""
        self.select_roi_btn.setVisible(available)
        if not available:
            self.setLiveSelectionActive(False)

    def _disconnect(self) -> None:
        connections = (
            (self._mmc.events.systemConfigurationLoaded, self._on_sys_cfg_loaded),
            (self._mmc.events.pixelSizeChanged, self._update_lbl_info),
            (self._mmc.events.roiSet, self._on_roi_set),
            (self._mmc.events.propertyChanged, self._on_property_changed),
        )
        for signal, callback in connections:
            with suppress(Exception):
                signal.disconnect(callback)

    # ________________________________CORE CONNECTIONS________________________________

    @Slot()
    def _on_sys_cfg_loaded(self) -> None:
        cameras = self._get_loaded_cameras()
        self.snap_checkbox.hide()

        if not cameras:
            self._cameras.clear()
            with signals_blocked(self.camera_combo):
                self.camera_combo.clear()
            with signals_blocked(self.camera_roi_combo):
                self.camera_roi_combo.clear()
            self.lbl_info.clear()
            self.lbl_info.setStyleSheet("")
            self._enable(False)
            return

        self._enable(True)

        # store the camera info
        self._store_camera_info(cameras)

        # populate the camera combobox
        with signals_blocked(self.camera_combo):
            self.camera_combo.clear()
            self.camera_combo.addItems(cameras)

        # populate the crop mode combobox
        with signals_blocked(self.camera_roi_combo):
            self._reset_crop_mode_combo()

        # set current camera as the active camera in the combo box (if any)
        if curr_camera := self._mmc.getCameraDevice():
            with signals_blocked(self.camera_combo):
                self.camera_combo.setCurrentText(curr_camera)
                self.snap_checkbox.setVisible(self._show_auto_snap)

        # make sure the roi is set to full chip
        with signals_blocked(self.camera_roi_combo):
            self.camera_roi_combo.setCurrentText(FULL)

        # when mode is FULL, disable roi spinboxes and crop button
        self._hide_spinbox_button(True)
        self._custom_roi_wdg.setEnabled(False)
        self.crop_btn.setEnabled(False)

        # set the roi values in the spinboxes
        self._update_roi_values()

        # update the info label
        self._update_lbl_info()

    @Slot(str, str, object)
    def _on_property_changed(self, device: str, prop: str, value: str) -> None:
        """Handle the property changed event."""
        if device != "Core" or prop != "Camera":
            return
        # show auto snap checkbox only if the selected camera is the core active camera
        self.snap_checkbox.setVisible(self._show_auto_snap and value == self.camera)

    @Slot(str, int, int, int, int)
    def _on_roi_set(self, camera: str, x: int, y: int, width: int, height: int) -> None:
        """Handle the ROI set event."""
        if camera not in self._cameras:
            return
        # if the roi values are out of bounds, do not update, keep the current values
        # and show an 'out of bounds' error message
        if (x + width) > self._cameras[camera].pixel_width or (
            y + height
        ) > self._cameras[camera].pixel_height:
            self._clearROI()
            self._update_lbl_info()
            QMessageBox.critical(
                self,
                "Out of Bounds Error",
                f"'{camera}' ROI values are out of bounds.",
                QMessageBox.StandardButton.Ok,
            )
            return

        # if the camera is not the camera selected in the combo box, update the stored
        # camera info and return
        if self.camera != camera:
            self._update_unselected_camera_info(camera, x, y, width, height)
            return

        # if the roi is not centered, uncheck the center checkbox
        centered = (
            x == (self._cameras[camera].pixel_width - width) // 2
            and y == (self._cameras[camera].pixel_height - height) // 2
        )
        with signals_blocked(self.center_checkbox):
            self.center_checkbox.setChecked(centered)

        # update the roi values in the spinboxes
        self._update_roi_values(ROI(x, y, width, height, centered))

        # update the crop mode combo box text to match the set roi (this is mainly
        # needed when the roi is set from the core)
        crop_mode = self._get_updated_crop_mode(camera, *self._get_roi_values())
        with signals_blocked(self.camera_roi_combo):
            self.camera_roi_combo.setCurrentText(crop_mode)

        # update the stored camera info
        self._cameras[camera] = self._cameras[camera].replace(
            crop_mode=crop_mode, roi=ROI(x, y, width, height, centered)
        )

        self._custom_roi_wdg.setEnabled(crop_mode == CUSTOM_ROI)
        self.crop_btn.setEnabled(crop_mode == CUSTOM_ROI)

        self._update_lbl_info()

        if self.snap_checkbox.isChecked() and self.snap_checkbox.isVisible():
            self._mmc.snap()

        self.roiChanged.emit(x, y, width, height, crop_mode)

    # ________________________________WIDGET CONNECTIONS________________________________

    def _enable(self, enabled: bool) -> None:
        self._selector_wdg.setEnabled(enabled)
        self._custom_roi_wdg.setEnabled(enabled)
        self._bottom_wdg.setEnabled(enabled)
        self._hide_spinbox_button(not enabled)

    def _hide_spinbox_button(
        self, hide: bool, spinboxes: list[QSpinBox] | None = None
    ) -> None:
        spinboxes = spinboxes or [
            self.start_x,
            self.start_y,
            self.roi_width,
            self.roi_height,
        ]
        for spin in spinboxes:
            if hide:
                spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            else:
                spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)

    @Slot(str)
    def _on_camera_changed(self, camera: str) -> None:
        """Update the ROI When the camera combo box changes."""
        self._update_roi_values()
        self.camera_roi_combo.setCurrentText(self._cameras[camera].crop_mode)
        self._update_lbl_info()

        # show auto snap checkbox only if the selected camera is the core active camera
        self.snap_checkbox.setVisible(
            self._show_auto_snap and self._mmc.getCameraDevice() == camera
        )

    @Slot(str)
    def _on_crop_roi_mode_change(self, value: str) -> None:
        """Handle the crop mode change."""
        if value == CUSTOM_ROI:
            # enable all the spinboxes
            self._hide_spinbox_button(False)
            # disable start_x and start_y spinboxes if center_checkbox is checked
            self._hide_spinbox_button(
                self.center_checkbox.isChecked(), [self.start_x, self.start_y]
            )
            self._custom_roi_wdg.setEnabled(True)
            self.crop_btn.setEnabled(True)

            self.roiChanged.emit(*self._get_roi_values(), value)
            self._update_lbl_info()
            return

        restart_live = self._mmc.isSequenceRunning() and not self._mmc.mda.is_running()
        if restart_live:
            self._mmc.stopSequenceAcquisition()

        try:
            if value == FULL:
                self._clearROI()

            else:
                self._hide_spinbox_button(True)

                width = int(value.split(" x ")[0])
                height = int(value.split(" x ")[1])

                camera = self._cameras[self.camera]
                start_x = (camera.pixel_width - width) // 2
                start_y = (camera.pixel_height - height) // 2

                self._update_roi_values(ROI(start_x, start_y, width, height, True))
                self._mmc.setROI(self.camera, start_x, start_y, width, height)
        finally:
            if restart_live:
                self._live_restart_pending = True
                QTimer.singleShot(0, self._restart_live_after_roi_change)

        self._update_lbl_info()

    def _restart_live_after_roi_change(self) -> None:
        if not self._live_restart_pending:
            return
        self._live_restart_pending = False
        if (
            not self._mmc.mda.is_running()
            and not self._mmc.isSequenceRunning()
            and self._mmc.getCameraDevice()
        ):
            self._mmc.startContinuousSequenceAcquisition()

    @Slot(bool)
    def _on_center_checkbox(self, state: bool) -> None:
        """Handle the center checkbox state change."""
        self.start_x.setEnabled(not state)
        self.start_y.setEnabled(not state)
        self._hide_spinbox_button(state, [self.start_x, self.start_y])

        # if not checked, use the stored roi values
        if not state:
            start_x, start_y, width, height, _ = self._cameras[self.camera].roi
            self._cameras[self.camera] = self._cameras[self.camera].replace(
                roi=ROI(start_x, start_y, width, height, state)
            )
            return

        if self.camera_roi_combo.currentText() != CUSTOM_ROI:
            return

        _, _, wanted_width, wanted_height = self._get_roi_values()
        start_x = (self._cameras[self.camera].pixel_width - wanted_width) // 2
        start_y = (self._cameras[self.camera].pixel_height - wanted_height) // 2

        self.start_x.setMaximum(start_x)
        self.start_y.setMaximum(start_y)
        self.start_x.setValue(start_x)
        self.start_y.setValue(start_y)

        start_x, start_y, width, height = self._get_roi_values()

        # store the new roi values
        self._cameras[self.camera] = self._cameras[self.camera].replace(
            roi=ROI(start_x, start_y, width, height, state)
        )

        self.roiChanged.emit(start_x, start_y, width, height, CUSTOM_ROI)

    @Slot()
    def _on_start_spinbox_changed(self) -> None:
        """Handle the start_x and start_y spinbox value change."""
        if not self.start_x.isEnabled() and not self.start_y.isEnabled():
            return
        if self.camera_roi_combo.currentText() != CUSTOM_ROI:
            return
        self._emit_roi_changed_signal()

    @Slot()
    def _on_roi_width_height_changed(self) -> None:
        """Handle the roi width and height spinbox value change."""
        if self.camera_roi_combo.currentText() != CUSTOM_ROI:
            return
        self._update_start_max_value()
        self._emit_roi_changed_signal()

    @Slot()
    def _on_crop_button_clicked(self) -> None:
        """Handle the crop button click event."""
        start_x, start_y, width, height = self._get_roi_values()
        self._mmc.setROI(self.camera, start_x, start_y, width, height)
        self._update_lbl_info()

    # ________________________________________________________________________________

    def _get_loaded_cameras(self) -> list[str]:
        """Get the list of loaded cameras.

        It excludes the Micro-Manager Multi Camera Utilities adapter.
        """
        cameras: list[str] = []
        for cam in list(self._mmc.getLoadedDevicesOfType(DeviceType.Camera)):
            props = self._mmc.getDevicePropertyNames(cam)
            if bool([x for x in props if "Physical Camera" in x]):
                continue
            cameras.append(cam)
        return cameras

    def _store_camera_info(self, cameras: list[str]) -> None:
        """Store the camera information in the `_cameras` dict."""
        self._cameras.clear()
        for camera in cameras:
            x, y, width, height = self._mmc.getROI(camera)
            self._cameras[camera] = CameraInfo(
                pixel_width=width,
                pixel_height=height,
                crop_mode=FULL,
                roi=ROI(x, y, width, height, True),
            )

    def _reset_crop_mode_combo(self) -> None:
        """Reset the crop mode combo with the selected camera options."""
        self.camera_roi_combo.clear()
        items = self._prepare_roi_combo_items(self.camera)
        self.camera_roi_combo.addItems(items)

    def _prepare_roi_combo_items(self, camera: str) -> list[str]:
        """Prepare the ROI combo items that will be displayed in the combo box."""
        camera_roi = self._cameras[camera]
        items = [FULL, CUSTOM_ROI]
        options = [8, 6, 4, 2]
        for val in options:
            width = round(camera_roi.pixel_width / val)
            height = round(camera_roi.pixel_height / val)
            items.append(f"{width} x {height}")
        return items

    @Slot()
    def _update_lbl_info(self) -> None:
        """Update the info label with the current ROI information."""
        if not self._show_roi_info:
            return
        camera = self.camera
        if not camera or camera not in self._cameras:
            self.lbl_info.clear()
            self.lbl_info.setStyleSheet("")
            return
        try:
            hardware_roi = tuple(self._mmc.getROI(camera))
        except RuntimeError:
            # During configuration loading, the previous camera may already be
            # unloaded while its combobox/model state has not yet been refreshed.
            self.lbl_info.clear()
            self.lbl_info.setStyleSheet("")
            return

        start_x, start_y, width, height = self._get_roi_values()

        px_size = self._mmc.getPixelSizeUm() or 0

        width_um = width * px_size
        height_um = height * px_size
        text = f"Size: {width} px * {height} px [{width_um} µm * {height_um} µm]"

        self.lbl_info.setText(text)

        if hardware_roi == (start_x, start_y, width, height):
            self.lbl_info.setStyleSheet("")
        else:
            self.lbl_info.setStyleSheet("color: magenta;")

    def setRoiInfoVisible(self, visible: bool) -> None:
        """Show or hide the current-hardware ROI status row."""
        self._show_roi_info = visible
        self._info_lbl_wdg.setVisible(visible)
        if visible:
            self._update_lbl_info()

    def roiInfoVisible(self) -> bool:
        """Return whether the current-hardware ROI status row is enabled."""
        return self._show_roi_info

    def _update_roi_values(self, roi: ROI | None = None) -> None:
        """Set the ROI values for the specified camera."""
        roi = roi or self._cameras[self.camera].roi
        # reset the max values for start_x, start_y spinboxes. The max values should be
        # the pixel_width - roi_width and pixel_height - roi_height
        self.start_x.setMaximum(self._cameras[self.camera].pixel_width - roi.w)
        self.start_y.setMaximum(self._cameras[self.camera].pixel_height - roi.h)
        # reset the max values for the roi width and height spinboxes
        self.roi_width.setMaximum(self._cameras[self.camera].pixel_width)
        self.roi_height.setMaximum(self._cameras[self.camera].pixel_height)
        # set the start_x, start_y values
        self.start_x.setValue(roi.x)
        self.start_y.setValue(roi.y)
        # set the roi width, height values
        self.roi_width.setValue(roi.w)
        self.roi_height.setValue(roi.h)
        # set the center checkbox state
        self.center_checkbox.setChecked(roi.centered)

    def _get_roi_values(self) -> tuple[int, int, int, int]:
        """Get the current ROI values for the selected camera."""
        return (
            self.start_x.value(),
            self.start_y.value(),
            self.roi_width.value(),
            self.roi_height.value(),
        )

    def _clearROI(self) -> None:
        """Clear the Camera ROI and reset to full chip."""
        if self.camera == self._mmc.getCameraDevice():
            # CMMCore.clearROI() is the only reliable way to recover the sensor's
            # full dimensions when this widget was created after an earlier crop.
            # It emits no CMMCorePlus event, so update our model first and then emit
            # the same notification setROI would have produced.
            self._mmc.clearROI()
            x, y, width, height = self._mmc.getROI(self.camera)
            self._cameras[self.camera] = CameraInfo(
                pixel_width=width,
                pixel_height=height,
                crop_mode=FULL,
                roi=ROI(x, y, width, height, True),
            )
            self._hide_spinbox_button(True)
            self._mmc.events.roiSet.emit(self.camera, x, y, width, height)
            return
        max_width = self._cameras[self.camera].pixel_width
        max_height = self._cameras[self.camera].pixel_height
        self._hide_spinbox_button(True)
        self._mmc.setROI(self.camera, 0, 0, max_width, max_height)

    def _update_unselected_camera_info(
        self, camera: str, start_x: int, start_y: int, width: int, height: int
    ) -> None:
        centered = (
            start_x == (self._cameras[camera].pixel_width - width) // 2
            and start_y == (self._cameras[camera].pixel_height - height) // 2
        )
        crop_mode = self._get_updated_crop_mode(camera, start_x, start_y, width, height)

        self._cameras[camera] = self._cameras[camera].replace(
            crop_mode=crop_mode,
            roi=ROI(start_x, start_y, width, height, centered),
        )

    def _get_updated_crop_mode(
        self, camera: str, start_x: int, start_y: int, width: int, height: int
    ) -> str:
        """Get the updated crop mode based on the roi values."""
        cam = self._cameras[camera]
        # if the roi matches the full chip, set the mode to FULL
        if (
            start_x == 0
            and start_y == 0
            and width == cam.pixel_width
            and height == cam.pixel_height
        ):
            mode = FULL

        else:
            # if the roi matches any of the default roi options, set the mode to that
            # option otherwise set the mode to CUSTOM_ROI
            mode = CUSTOM_ROI
            roi_combo_items = self._prepare_roi_combo_items(camera)
            for item in roi_combo_items:
                if item in [FULL, CUSTOM_ROI]:
                    continue
                w, h = item.split(" x ")
                x = (cam.pixel_width - int(w)) // 2
                y = (cam.pixel_height - int(h)) // 2
                if (
                    start_x == x
                    and start_y == y
                    and width == int(w)
                    and height == int(h)
                ):
                    mode = item
                    break

        return mode

    def _emit_roi_changed_signal(self) -> None:
        """Update the camera info with the new ROI values."""
        start_x, start_y, width, height = self._get_roi_values()
        self.roiChanged.emit(start_x, start_y, width, height, CUSTOM_ROI)
        self._update_lbl_info()

    def _update_start_max_value(self) -> None:
        """Update the maximum value for the start_x and start_y spinboxes."""
        _, _, wanted_width, wanted_height = self._get_roi_values()
        self.start_x.setMaximum(self._cameras[self.camera].pixel_width - wanted_width)
        self.start_y.setMaximum(self._cameras[self.camera].pixel_height - wanted_height)

        if self.center_checkbox.isChecked():
            start_x = (self._cameras[self.camera].pixel_width - wanted_width) // 2
            start_y = (self._cameras[self.camera].pixel_height - wanted_height) // 2
            self.start_x.setValue(start_x)
            self.start_y.setValue(start_y)
