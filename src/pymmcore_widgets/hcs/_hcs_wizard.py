from __future__ import annotations

import json
import warnings
from contextlib import suppress
from itertools import groupby
from pathlib import Path
from typing import TYPE_CHECKING, Any

import useq
from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import QEvent, QObject, Slot
from qtpy.QtWidgets import (
    QFileDialog,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)
from superqt.iconify import QIconifyIcon
from useq import WellPlatePlan

from pymmcore_widgets.useq_widgets import PointsPlanWidget, WellPlateWidget

from ._automated_plate_calibration_widget import AutomatedPlateCalibrationWidget
from ._plate_calibration import supports_automated_calibration
from ._plate_calibration_widget import PlateCalibrationWidget

if TYPE_CHECKING:
    from qtpy.QtGui import QShowEvent

# Wizard save files are a versioned envelope around the WellPlatePlan, so that
# wizard-only state can be added later. Bare WellPlatePlan files (written by older
# versions, or by other tools) still load.
WIZARD_FILE_FORMAT = "pymmcore-widgets.hcs-wizard"
WIZARD_FILE_VERSION = 1


def write_wizard_file(path: str | Path, plan: useq.WellPlatePlan) -> None:
    """Write a versioned HCS wizard file."""
    data = {
        "format": WIZARD_FILE_FORMAT,
        "version": WIZARD_FILE_VERSION,
        "well_plate_plan": json.loads(plan.model_dump_json(exclude_unset=True)),
    }
    Path(path).write_text(json.dumps(data, indent=2))


def read_wizard_file(path: str | Path) -> useq.WellPlatePlan:
    """Read an HCS wizard file, or a bare WellPlatePlan file."""
    path = Path(path)
    data: Any = None
    if path.suffix == ".json":
        data = json.loads(path.read_text())
    if not (isinstance(data, dict) and data.get("format") == WIZARD_FILE_FORMAT):
        return WellPlatePlan.from_file(path)
    version = data.get("version")
    if not isinstance(version, int) or version > WIZARD_FILE_VERSION:
        raise ValueError(
            f"Unsupported HCS wizard file version {version!r} (this version of "
            f"pymmcore-widgets reads up to version {WIZARD_FILE_VERSION})."
        )
    return WellPlatePlan.model_validate(data["well_plate_plan"])


class HCSWizard(QWizard):
    """A wizard to setup an High Content Screening (HCS) experiment.

    This widget can be used to select a plate, calibrate it, and then select the number
    of images (and their arrangement) to acquire per well.  The output is a
    [useq.WellPlatePlan][] object, which can be retrieved with the `value()` method.

    Parameters
    ----------
    parent : QWidget | None
        The parent widget. By default, None.
    mmcore : CMMCorePlus | None
        The CMMCorePlus instance. By default, None.
    """

    def __init__(
        self, parent: QWidget | None = None, *, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__(parent)
        self._mmc = mmcore or CMMCorePlus.instance()
        self._calibrated: bool = False
        self._pending_done: int | None = None

        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setWindowTitle("HCS Wizard")

        # WIZARD PAGES ----------------------

        self.plate_page = _PlatePage(self)
        self.calibration_page = _PlateCalibrationPage(self._mmc, self)
        self.points_plan_page = _PointsPlanPage(self._mmc, self)

        self.addPage(self.plate_page)
        self.addPage(self.calibration_page)
        self.addPage(self.points_plan_page)

        # SAVE/LOAD BUTTONS ----------------------

        # add custom button to save
        self.setOption(QWizard.WizardOption.HaveCustomButton1, True)
        if save_btn := self.button(QWizard.WizardButton.CustomButton1):
            save_btn.setText("Save")
            save_btn.setIcon(QIconifyIcon("mdi:content-save-outline"))
            save_btn.clicked.connect(self.save)
            save_btn.setEnabled(False)
        # add custom button to load
        self.setOption(QWizard.WizardOption.HaveCustomButton2, True)
        if load_btn := self.button(QWizard.WizardButton.CustomButton2):
            load_btn.setText("Load")
            load_btn.setIcon(QIconifyIcon("material-symbols:upload-rounded"))
            load_btn.clicked.connect(self.load)
        if cancel_btn := self.button(QWizard.WizardButton.CancelButton):
            cancel_btn.setIcon(QIconifyIcon("material-symbols:close-rounded"))
        self._initial_size_set = False

        # CONNECTIONS ---------------------------

        self.plate_page.widget.valueChanged.connect(self._on_plate_changed)
        self._on_plate_changed(self.plate_page.widget.value())
        self.calibration_page.widget.calibrationChanged.connect(
            self._on_calibration_changed
        )
        self.calibration_page.automated.busyChanged.connect(self._on_survey_busy)

    def showEvent(self, event: QShowEvent | None) -> None:
        # Size the wizard once, before the window first appears, to the preferred
        # size of the Field of View Selection panel (shown without scrolling),
        # grown to fit every page. The wizard's chrome (title, buttons) is only
        # measurable once it is laid out, i.e. now.
        super().showEvent(event)
        if self._initial_size_set or (page := self.currentPage()) is None:
            return
        self._initial_size_set = True
        pages = (self.plate_page, self.calibration_page, self.points_plan_page)
        size = self.points_plan_page.sizeHint()
        for p in pages:
            size = size.expandedTo(p.minimumSizeHint())
        center = self.geometry().center()
        self.resize(size + self.size() - page.size())
        geo = self.geometry()
        geo.moveCenter(center)
        self.move(geo.topLeft())

    def done(self, result: int) -> None:
        """Wait for a cancelled survey to release hardware before closing."""
        if self.calibration_page.automated.isBusy():
            self._pending_done = result
            self.calibration_page.automated.cancel()
            return
        super().done(result)

    def _on_survey_busy(self, busy: bool) -> None:
        self.plate_page.setEnabled(not busy)
        self.calibration_page.widget.setEnabled(not busy)
        for button in (
            QWizard.WizardButton.BackButton,
            QWizard.WizardButton.CustomButton1,
            QWizard.WizardButton.CustomButton2,
        ):
            self.button(button).setEnabled(not busy)
        if not busy:
            self._on_calibration_changed(self._calibrated)
            if self._pending_done is not None:
                result, self._pending_done = self._pending_done, None
                self.done(result)

    def value(self) -> useq.WellPlatePlan | None:
        """Return the current well plate plan, or None if the plan is uncalibrated."""
        calib_plan = self.calibration_page.widget.value()
        if not self._calibrated or not calib_plan:  # pragma: no cover
            return None

        plate_plan = self.plate_page.widget.value()
        if plate_plan.plate != calib_plan.plate:  # pragma: no cover
            warnings.warn("Plate Plan and Calibration Plan do not match.", stacklevel=2)
            return None

        selected_rows, selected_columns = plate_plan.selected_wells or ((), ())
        rows = groupby(
            sorted(zip(selected_rows, selected_columns, strict=True)),
            key=lambda well: well[0],
        )
        wells: list[tuple[int, int]] = []
        for index, (_, row) in enumerate(rows):
            row_wells = list(row)
            wells.extend(reversed(row_wells) if index % 2 else row_wells)
        return useq.WellPlatePlan(
            plate=plate_plan.plate,
            selected_wells=tuple(zip(*wells, strict=True)),
            rotation=calib_plan.rotation,
            a1_center_xy=calib_plan.a1_center_xy,
            well_points_plan=self.points_plan_page.widget.value(),
        )

    def setValue(self, value: useq.WellPlatePlan) -> None:
        """Set the state of the wizard to a WellPlatePlan."""
        self.plate_page.widget.setValue(value)
        self.calibration_page.widget.setValue(value)
        # WellPlateWidget.setValue doesn't emit, so synchronize the well geometry
        # (after the calibration, so that it isn't reset as a plate change)
        self._on_plate_changed(self.plate_page.widget.value())
        # update the points plan fov size if it's not set
        point_plan = value.well_points_plan
        if point_plan.fov_width is None or point_plan.fov_height is None:
            point_plan.fov_width, point_plan.fov_height = (
                self.points_plan_page._get_fov_size()
            )
        self.points_plan_page.widget.setValue(point_plan)

    @Slot()
    def save(self, path: str | None = None) -> None:
        """Save the current well plate plan to disk."""
        if not isinstance(path, str):
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Well Plate Plan", "", "JSON (*.json)"
            )
        elif not path.endswith(".json"):  # pragma: no cover
            raise ValueError("Path must end with '.json'")
        if path and (value := self.value()):
            write_wizard_file(path, value)

    @Slot()
    def load(self, path: str | None = None) -> None:
        """Load a well plate plan from disk."""
        if not isinstance(path, str):
            path, _ = QFileDialog.getOpenFileName(
                self, "Load Well Plate Plan", "", "JSON (*.json)"
            )
        if path:
            self.setValue(read_wizard_file(path))

    @Slot(object)
    def _on_plate_changed(self, plate_plan: useq.WellPlatePlan) -> None:
        """Synchronize the points plan with the well size/shape."""
        # update the calibration widget with the new plate if it's different
        current_calib_plan = self.calibration_page.widget.value()
        plate_changed = (
            current_calib_plan is None or current_calib_plan.plate != plate_plan.plate
        )
        if plate_changed:
            self.calibration_page.widget.setValue(plate_plan.plate)

        pp_widget = self.points_plan_page.widget

        # set the well size on the points plan widget to the current plate well size
        well_width, well_height = plate_plan.plate.well_size
        pp_widget.setWellSize(well_width, well_height)

        # set the well shape (circular/rectangular) to match the plate's wells
        shape = (
            useq.Shape.ELLIPSE
            if plate_plan.plate.circular_wells
            else useq.Shape.RECTANGLE
        )
        pp_widget.setWellShape(shape)
        # the well view shows the total number of FOVs across the selected wells
        selected = plate_plan.selected_wells
        pp_widget.setSelectedWellCount(len(selected[0]) if selected else 0)

        # additionally, restrict the max width and height of the random points widget
        # to the plate size minus the fov size.
        fovw = pp_widget._selector.fov_w.value()
        fovh = pp_widget._selector.fov_h.value()

        # when the plate itself changes, reset the random points bounding shape to
        # match the new well shape (only on an actual plate change, so we don't
        # clobber a shape the user picked manually while just selecting wells)
        random_wdg = pp_widget.random_points_wdg
        if plate_changed:
            random_wdg.shape.setCurrentText(shape.value)

        # if the random points shape is a rectangle, but the wells are circular,
        # reduce the max width and height by 1.4 to keep the points inside the wells
        if random_wdg.shape.currentText() == useq.Shape.RECTANGLE.value:
            if plate_plan.plate.circular_wells:
                well_width /= 1.4
                well_height /= 1.4

        random_wdg.max_width.setMaximum(well_width * 1000)
        random_wdg.max_width.setValue(well_width * 1000 - fovw / 1.4)
        random_wdg.max_height.setMaximum(well_height * 1000)
        random_wdg.max_height.setValue(well_height * 1000 - fovh / 1.4)

    @Slot(bool)
    def _on_calibration_changed(self, calibrated: bool) -> None:
        self._calibrated = calibrated
        # Cover Area plans rotate the covered area with the plate
        calib_plan = self.calibration_page.widget.value()
        rotation = calib_plan.rotation if calibrated and calib_plan else None
        self.points_plan_page.widget.setPlateRotation(rotation)
        self.button(QWizard.WizardButton.CustomButton1).setEnabled(
            calibrated and not self.calibration_page.automated.isBusy()
        )


# ---------------------------------- PAGES ---------------------------------------


class _PlatePage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setTitle("Plate and Well Selection")

        self.widget = WellPlateWidget()
        self.widget.setShowRotation(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.widget)


class _SameWidth(QObject):
    """Keep widgets at the widest one's preferred width, as their content changes.

    Used so the Manual and Automated side panels (and their point tables) do not
    change size when switching tabs.
    """

    def __init__(self, *widgets: QWidget) -> None:
        super().__init__(widgets[0])
        self._widgets = widgets
        for widget in widgets:
            widget.installEventFilter(self)
        self.apply()

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        if a1 is not None and a1.type() in (
            QEvent.Type.LayoutRequest,
            QEvent.Type.FontChange,
            QEvent.Type.StyleChange,
        ):
            self.apply()
        return False

    def apply(self) -> None:
        width = max(w.sizeHint().width() for w in self._widgets)
        for widget in self._widgets:
            if (widget.minimumWidth(), widget.maximumWidth()) != (width, width):
                widget.setFixedWidth(width)


class _PlateCalibrationPage(QWizardPage):
    def __init__(self, mmcore: CMMCorePlus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Plate Calibration")

        self._is_complete = False
        self.widget = PlateCalibrationWidget(mmcore=mmcore)
        self.automated = AutomatedPlateCalibrationWidget(mmcore, self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.widget, "Manual")
        self.tabs.addTab(self.automated, "Automated")
        self.automated.testPositionRequested.connect(self.widget._move_to_xy_position)
        self.automated.centersAccepted.connect(self._apply_automated_centers)
        self.automated.busyChanged.connect(self._on_busy_changed)
        self.widget.calibrationChanged.connect(self._on_calibration_changed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)
        _SameWidth(self.widget._panel, self.automated._panel)

    def isComplete(self) -> bool:
        return self._is_complete and not self.automated.isBusy()

    def _apply_automated_centers(
        self, centers: dict[tuple[int, int], tuple[float, float]], tolerance: float
    ) -> None:
        self.widget.setCalibratedWellCenters(centers, max_error_um=tolerance)

    def _on_busy_changed(self, busy: bool) -> None:
        self.completeChanged.emit()

    @Slot(bool)
    def _on_calibration_changed(self, calibrated: bool) -> None:
        self._is_complete = calibrated
        if plan := self.widget.value():
            show_automated = supports_automated_calibration(plan.plate)
            if not show_automated:
                self.tabs.setCurrentIndex(0)
            self.tabs.setTabVisible(1, show_automated)
            self.automated.setPlate(plan.plate)
            self.automated.setCalibration(plan if calibrated else None)
        self.completeChanged.emit()


class _PointsPlanPage(QWizardPage):
    def __init__(self, mmcore: CMMCorePlus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mmc = mmcore
        self.setTitle("Field of View Selection")

        self.widget = PointsPlanWidget()
        self.widget._selector.fov_widgets.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.widget)

        # Cover Area cannot produce a plan without a valid FOV (or with an invalid
        # area), so the Finish button follows its validity
        self.widget.valueChanged.connect(lambda *_: self.completeChanged.emit())

        self._mmc.events.pixelSizeChanged.connect(self._on_px_size_changed)
        self._mmc.events.systemConfigurationLoaded.connect(self._on_px_size_changed)
        # the image size (and thus the FOV size) also changes with ROI and binning
        self._mmc.events.roiSet.connect(self._on_px_size_changed)
        self._mmc.events.propertyChanged.connect(self._on_property_changed)
        self._on_px_size_changed()

    def isComplete(self) -> bool:
        # (the Cover Area widget is always valid on its Grid tab)
        cover_area = self.widget.cover_area_wdg
        if self.widget._selector._active_plan_widget is cover_area:
            if not cover_area.isValid():
                return False
        return bool(super().isComplete())

    def _on_property_changed(self, device: str, prop: str, _value: object) -> None:
        with suppress(RuntimeError):
            if device == self._mmc.getCameraDevice() and prop == "Binning":
                self._on_px_size_changed()

    @Slot()
    def _on_px_size_changed(self) -> None:
        val = self.widget.value()
        val.fov_width, val.fov_height = self._get_fov_size()
        self.widget.setValue(val)

    def _get_fov_size(self) -> tuple[float, float] | tuple[None, None]:
        with suppress(RuntimeError):
            if self._mmc.getCameraDevice() and (px := self._mmc.getPixelSizeUm()):
                return self._mmc.getImageWidth() * px, self._mmc.getImageHeight() * px
        return (None, None)
