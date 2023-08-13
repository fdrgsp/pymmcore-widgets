from __future__ import annotations

import math
import random
import string
import warnings
from typing import TYPE_CHECKING, cast

import numpy as np
from pymmcore_plus import CMMCorePlus
from PyQt6.QtWidgets import QWidget
from qtpy.QtCore import Qt
from qtpy.QtGui import QBrush
from qtpy.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QTabWidget,
    QVBoxLayout,
)
from superqt.utils import signals_blocked

from pymmcore_widgets._mda._mda_widget import MDAWidget
from pymmcore_widgets._util import (
    PLATE_FROM_CALIBRATION,
    draw_well_plate,
)

from ._calibration_widget import _PlateCalibration
from ._custom_plate_widget import _CustomPlateWidget
from ._fov_widget import _FOVSelectrorWidget
from ._graphics_items import _FOVCoordinates, _Well
from ._plate_graphics_scene import _HCSGraphicsScene
from ._well_plate_model import PLATE_DB_PATH, WellPlate, load_database

if TYPE_CHECKING:
    from pathlib import Path

    from useq import MDASequence

AlignCenter = Qt.AlignmentFlag.AlignCenter

ALPHABET = string.ascii_uppercase
CALIBRATED_PLATE: WellPlate | None = None
GRAPHICS_VIEW_WIDTH = 400
FOV_GRAPHICS_VIEW_SIZE = 200
GRAPHICS_VIEW_HEIGHT = 320


class _PlateAndFovTab(QWidget):
    def __init__(
        self, parent: QWidget | None = None, *, plate_database: dict[str, WellPlate]
    ) -> None:
        super().__init__(parent)

        self._plate_db = plate_database

        layout = QVBoxLayout()
        layout.setSpacing(20)
        layout.setContentsMargins(10, 10, 10, 10)
        self.setLayout(layout)

        self.scene = _HCSGraphicsScene(parent=self)
        self.view = QGraphicsView(self.scene, self)
        self.view.setStyleSheet("background:grey;")
        self.view.setMinimumSize(GRAPHICS_VIEW_WIDTH, GRAPHICS_VIEW_HEIGHT)

        # well plate combobox
        wp_combo_wdg = QWidget()
        wp_combo_layout = QHBoxLayout()
        wp_combo_layout.setContentsMargins(0, 0, 0, 0)
        wp_combo_layout.setSpacing(5)
        combo_label = QLabel()
        combo_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        combo_label.setText("Plate:")
        self.wp_combo = QComboBox()
        self.wp_combo.addItems(list(self._plate_db))
        wp_combo_layout.addWidget(combo_label)
        wp_combo_layout.addWidget(self.wp_combo)
        wp_combo_wdg.setLayout(wp_combo_layout)

        # clear and custom plate buttons
        btns_wdg = QWidget()
        btns_layout = QHBoxLayout()
        btns_layout.setContentsMargins(0, 0, 0, 0)
        btns_layout.setSpacing(5)
        btns_wdg.setLayout(btns_layout)
        self.custom_plate = QPushButton(text="Custom Plate")
        self.clear_button = QPushButton(text="Clear Selection")
        self.clear_button.clicked.connect(self.scene._clear_selection)
        btns_layout.addWidget(self.custom_plate)
        btns_layout.addWidget(self.clear_button)

        # well plate selector combo and clear selection QPushButton
        upper_wdg = QWidget()
        upper_wdg_layout = QHBoxLayout()
        upper_wdg_layout.setSpacing(5)
        upper_wdg_layout.setContentsMargins(0, 0, 0, 5)
        upper_wdg_layout.addWidget(wp_combo_wdg)
        upper_wdg_layout.addWidget(btns_wdg)
        upper_wdg.setLayout(upper_wdg_layout)

        # add widgets
        view_group = QGroupBox()
        view_group.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed))
        view_gp_layout = QVBoxLayout()
        view_gp_layout.setSpacing(0)
        view_gp_layout.setContentsMargins(10, 10, 10, 10)
        view_group.setLayout(view_gp_layout)
        view_gp_layout.addWidget(upper_wdg)
        view_gp_layout.addWidget(self.view)
        layout.addWidget(view_group)

        FOV_group = QGroupBox()
        FOV_group.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        FOV_gp_layout = QVBoxLayout()
        FOV_gp_layout.setSpacing(0)
        FOV_gp_layout.setContentsMargins(10, 10, 10, 10)
        FOV_group.setLayout(FOV_gp_layout)
        self.FOV_selector = _FOVSelectrorWidget(parent=self)
        FOV_gp_layout.addWidget(self.FOV_selector)
        layout.addWidget(FOV_group)

        verticalSpacer = QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding)
        layout.addItem(verticalSpacer)


class _CalibrationTab(QWidget):
    def __init__(
        self, parent: QWidget | None = None, *, plate_database: dict[str, WellPlate]
    ) -> None:
        super().__init__(parent)

        self._plate_db = plate_database

        layout = QVBoxLayout()
        layout.setSpacing(20)
        layout.setContentsMargins(10, 10, 10, 10)
        self.setLayout(layout)

        calibration_group = QGroupBox()
        calibration_group.setSizePolicy(
            QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        )
        calibration_group_layout = QVBoxLayout()
        calibration_group_layout.setSpacing(0)
        calibration_group_layout.setContentsMargins(10, 20, 10, 10)
        calibration_group.setLayout(calibration_group_layout)
        self.calibration_widget = _PlateCalibration(
            parent=self, plate_database=self._plate_db
        )
        calibration_group_layout.addWidget(self.calibration_widget)
        layout.addWidget(calibration_group)

        verticalSpacer = QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding)
        layout.addItem(verticalSpacer)


class _HCSMDA(MDAWidget):
    """Subclass of MDAWidget to modify PositionTable."""

    def __init__(
        self, parent: QWidget | None = None, *, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__(parent=parent, include_run_button=True, mmcore=mmcore)
        self._mmc = mmcore or CMMCorePlus.instance()
        # hide advanced checkbox
        self.position_widget._advanced_cbox.hide()
        # disconnect save and load buttons
        self.position_widget.save_positions_button.clicked.disconnect()
        self.position_widget.load_positions_button.clicked.disconnect()
        # disconnect add button
        self.position_widget.add_button.clicked.disconnect(
            self.position_widget._add_position
        )


class HCSWidget(QWidget):
    """HCS widget.

    Parameters
    ----------
    parent : Optional[QWidget]
        Optional parent widget, by default None
    mmcore: Optional[CMMCorePlus]
        Optional `CMMCorePlus` micromanager core.
        By default, None. If not specified, the widget will use the active
        (or create a new) `CMMCorePlus.instance()`.
    plate_database_path : Optional[Path | str]
        Optional path to a custom well plate database. The database must be a
        json file containing a list of `WellPlate` dictionaries.

    The `HCSWidget` provides a GUI to construct a `useq.MDASequence` object that
    can be used to automate the acquisition of multi-well plate or custom defined
    areas.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        mmcore: CMMCorePlus | None = None,
        plate_database_path: Path | str | None = None,
    ) -> None:
        super().__init__(parent)

        self._plate_db_path = plate_database_path or PLATE_DB_PATH
        self._plate_db = load_database(self._plate_db_path)

        self._mmc = mmcore or CMMCorePlus.instance()

        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        self.setLayout(layout)

        # scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAlignment(AlignCenter)
        layout.addWidget(scroll)

        # plate database widget
        self._plate = _CustomPlateWidget(
            parent=self,
            plate_database=self._plate_db,
            plate_database_path=self._plate_db_path,
        )
        self._plate.valueChanged.connect(self._update_well_plate_combo)

        # tabwidget
        # plate and fov selection tab
        self._plate_and_fov_tab = _PlateAndFovTab(plate_database=self._plate_db)
        self._plate_and_fov_tab.wp_combo.currentTextChanged.connect(
            self._on_well_plate_combo_changed
        )
        self._plate_and_fov_tab.custom_plate.clicked.connect(
            self._show_custom_plate_dialog
        )
        # calibration tab
        self._calibration_tab = _CalibrationTab(plate_database=self._plate_db)
        self._calibration = self._calibration_tab.calibration_widget
        self._calibration.valueChanged.connect(self._on_plate_from_calibration)
        self._calibration._test_button.clicked.connect(self._test_calibration)
        # MDA tab
        self._mda = _HCSMDA(parent=self)
        self._mda.position_widget.add_button.clicked.connect(self._generate_pos_list)
        self._mda.position_widget.load_positions_button.clicked.connect(
            self._load_positions
        )
        self._mda.position_widget.save_positions_button.clicked.connect(
            self._save_positions
        )
        # add widgets to tabwidget
        self.tabwidget = QTabWidget()
        self.tabwidget.addTab(self._plate_and_fov_tab, "  Plate and FOVs Selection  ")
        self.tabwidget.addTab(self._calibration_tab, "  Plate Calibration  ")
        self.tabwidget.addTab(self._mda, "  MDA  ")
        scroll.setWidget(self.tabwidget)

        # connect
        self._mmc.events.systemConfigurationLoaded.connect(self._on_sys_cfg_loaded)
        self._mmc.events.roiSet.connect(self._on_roi_set)

        self._on_sys_cfg_loaded()

    def _set_enabled(self, enabled: bool) -> None:
        self._plate_and_fov_tab.setEnabled(enabled)
        self._calibration_tab.setEnabled(enabled)

    def _on_sys_cfg_loaded(self) -> None:
        self._set_enabled(True)
        self._on_well_plate_combo_changed(
            self._plate_and_fov_tab.wp_combo.currentText()
        )

    def _on_well_plate_combo_changed(self, value: str) -> None:
        """Update the graphics scene when the well plate combobox is changed."""
        # clear the scene
        self._plate_and_fov_tab.scene.clear()
        # draw the well plate
        self._update_plate_graphics_scene(value)
        # update the calibration widget
        self._calibration._update_gui(value)

    def _on_roi_set(self) -> None:
        """Update the graphics scene when a camera ROI is set."""
        self._on_well_plate_combo_changed(
            self._plate_and_fov_tab.wp_combo.currentText()
        )

    def _on_plate_from_calibration(self, coords: tuple) -> None:
        """Update the graphics scene with a plate defined form calibration."""
        global CALIBRATED_PLATE

        x_list, y_list = zip(*coords)
        CALIBRATED_PLATE = WellPlate(
            circular=False,
            id=PLATE_FROM_CALIBRATION,
            cols=1,
            rows=1,
            well_size_x=(max(x_list) - min(x_list)) / 1000,
            well_size_y=(max(y_list) - min(y_list)) / 1000,
            well_spacing_x=0,
            well_spacing_y=0,
        )

        self._plate_and_fov_tab.scene.clear()
        self._update_plate_graphics_scene(CALIBRATED_PLATE)

    def _update_plate_graphics_scene(self, well_plate: str | WellPlate) -> None:
        """Update the graphics scene with the selected well plate."""
        self.wp = (
            self._plate_db[well_plate] if isinstance(well_plate, str) else well_plate
        )
        # draw the plate
        # self._plate_and_fov_tab.plate._draw_plate_wells(self.wp)
        draw_well_plate(
            self._plate_and_fov_tab.view,
            self._plate_and_fov_tab.scene,
            self.wp,
            None,
            None,
        )
        # select the plate area if is not a multi well
        items = self._plate_and_fov_tab.scene.items()
        if len(items) == 1:
            item = cast(_Well, items[0])
            item.setSelected(True)
            item.brush = QBrush(Qt.GlobalColor.magenta)
        # load plate info in the FOV selector widget
        self._plate_and_fov_tab.FOV_selector._load_plate_info(self.wp)

    def _show_custom_plate_dialog(self) -> None:
        """Show the custom plate Qdialog widget."""
        if hasattr(self, "_plate"):
            self._plate.close()
        self._plate.show()
        self._plate.plate_table.clearSelection()
        self._plate.reset_values()

    def _update_well_plate_combo(self, new_plate: WellPlate | None) -> None:
        """Update the well plate combobox with the updated plate database."""
        with signals_blocked(self._plate_and_fov_tab.wp_combo):
            self._plate_and_fov_tab.wp_combo.clear()
            self._plate_and_fov_tab.wp_combo.addItems(list(self._plate_db))

        if new_plate is not None:
            self._plate_and_fov_tab.wp_combo.setCurrentText(new_plate.id)
            self._on_well_plate_combo_changed(new_plate.id)
        else:
            self._plate_and_fov_tab.wp_combo.setCurrentIndex(0)
            self._on_well_plate_combo_changed(
                self._plate_and_fov_tab.wp_combo.itemText(0)
            )

    def _generate_pos_list(self) -> None:
        pass
        # if not self._calibration.is_calibrated:
        #     warnings.warn("Plate not calibrated! Calibrate it first.", stacklevel=2)
        #     return
        # if not self._mmc.getPixelSizeUm():
        #    warnings.warn(
        #        "Pixel Size not defined! Set pixel size first.", stacklevel=2
        #    )
        #     return

        # # get list of selected wells
        # well_list = self._plate_and_fov_tab.scene.value()
        # if not well_list:
        #     warnings.warn(
        #         "No Well selected! Select at least one well first.", stacklevel=2
        #     )
        #     return

        # self._mda.position_widget.clear()

        # wells_in_stage_coords = self._get_wells_stage_coords(well_list)
        # wells_and_fovs_in_stage_coords = self._get_fovs_stage_coords(
        #     wells_in_stage_coords
        # )

        # if wells_and_fovs_in_stage_coords is None:
        #     return

        # for well_name, stage_coord_x, stage_coord_y in wells_and_fovs_in_stage_coords:
        #     zpos = self._mmc.getPosition() if self._mmc.getFocusDevice() else None
        #     # TODO: fix autofocus
        #     self._mda.position_widget._add_table_row(
        #         well_name, stage_coord_x, stage_coord_y, zpos, None
        #     )

    def _get_wells_stage_coords(
        self, well_list: list[tuple[str, int, int]]
    ) -> list[tuple[str, float, float]] | None:
        """Get the calibrated stage coords of the selected wells."""
        if self.wp is None or self._calibration.A1_well is None:
            return None

        calculated_spacing_x = self._calibration._calculated_well_spacing_x
        calculated_spacing_y = self._calibration._calculated_well_spacing_x

        if calculated_spacing_x is None or calculated_spacing_y is None:
            return None

        # center stage coords of calibrated well a1
        a1_x = self._calibration.A1_well[1]
        a1_y = self._calibration.A1_well[2]
        center = np.array([[a1_x], [a1_y]])
        # rotation matrix from calibration
        r_matrix = self._calibration.plate_rotation_matrix

        ordered_well_list: list[tuple[str, float, float]] = []
        # original_pos_list = []
        for pos in well_list:
            well, row, col = pos
            # find center stage coords for all the selected wells
            x = a1_x if well == "A1" else a1_x + (calculated_spacing_x * col)
            y = a1_y if well == "A1" else a1_y - (calculated_spacing_y * row)
            # apply rotation matrix
            if well != "A1" and r_matrix is not None:
                coords = [[x], [y]]
                transformed = np.linalg.inv(r_matrix).dot(coords - center) + center
                x_rotated, y_rotated = transformed
                x = x_rotated[0]
                y = y_rotated[0]
            ordered_well_list.append((well, x, y))

        return ordered_well_list

    def _get_fovs_stage_coords(
        self, ordered_wells_list: list[tuple[str, float, float]] | None
    ) -> list[tuple[str, float, float]] | None:
        """Get the calibrated stage coords of each FOV of the selected wells."""
        if self.wp is None or ordered_wells_list is None:
            return None

        calculated_size_x = self._calibration._calculated_well_size_x
        calculated_size_y = self._calibration._calculated_well_size_y

        if calculated_size_x is None or calculated_size_y is None:
            return None

        fovs = [
            item.get_center_and_size()
            for item in self._plate_and_fov_tab.FOV_selector.scene.items()
            if isinstance(item, _FOVCoordinates)
        ]
        fovs.reverse()

        # center coord in px of _SelectFOV QGraphicsView
        cx = FOV_GRAPHICS_VIEW_SIZE / 2
        cy = FOV_GRAPHICS_VIEW_SIZE / 2
        # rotation matrix from calibration
        r_matrix = self._calibration.plate_rotation_matrix

        pos_list: list[tuple[str, float, float]] = []
        for pos in ordered_wells_list:
            well_name, center_stage_x, center_stage_y = pos
            for idx, fov in enumerate(fovs):
                # center fov scene x, y coord fx and fov scene width and height
                center_fov_scene_x, center_fov_scene_y, w_fov_scene, h_fov_scene = fov
                # find 1 px value in um depending on well dimension
                px_val_x = calculated_size_x / w_fov_scene  # µm
                px_val_y = calculated_size_y / h_fov_scene  # µm
                # shift point coords in scene when center is (0, 0)
                new_fx = center_fov_scene_x - cx
                new_fy = center_fov_scene_y - cy
                # find stage coords of fov point
                stage_coord_x = center_stage_x + (new_fx * px_val_x)
                stage_coord_y = center_stage_y + (new_fy * px_val_y)
                # apply rotation matrix
                if r_matrix is not None:
                    center = np.array([[center_stage_x], [center_stage_y]])
                    coords = [[stage_coord_x], [stage_coord_y]]
                    transformed = np.linalg.inv(r_matrix).dot(coords - center) + center
                    x_rotated, y_rotated = transformed
                    stage_coord_x = x_rotated[0]
                    stage_coord_y = y_rotated[0]

                pos_list.append(
                    (f"{well_name}_pos{idx:03d}", stage_coord_x, stage_coord_y)
                )

        return pos_list

    def _test_calibration(self) -> None:
        """Move to the edge of the selected well to test the calibratiion."""
        if not self._calibration.plate:
            return

        # well name, row and col
        well_letter = self._calibration._well_letter_combo.currentText()
        well_number = self._calibration._well_number_combo.currentText()
        row = self._calibration._well_letter_combo.currentIndex()
        col = self._calibration._well_number_combo.currentIndex()

        test_well_in_stage_coords = self._get_wells_stage_coords(
            [(f"{well_letter}{well_number}", row, col)]
        )
        wells_and_fovs_in_stage_coords = self._get_fovs_stage_coords(
            test_well_in_stage_coords
        )

        if wells_and_fovs_in_stage_coords is None:
            return
        _, center_x, center_y = wells_and_fovs_in_stage_coords[0]

        self._mmc.waitForDevice(self._mmc.getXYStageDevice())

        if self._calibration.plate.circular:
            self._move_to_circle_edge(
                center_x, center_y, self._calibration.plate.well_size_x / 2
            )
        else:
            self._move_to_rectangle_edge(
                center_x,
                center_y,
                self._calibration.plate.well_size_x,
                self._calibration.plate.well_size_y,
            )

    def _move_to_circle_edge(self, xc: float, yc: float, radius: float) -> None:
        """Move to the edge of a circle with center (xc, yc) and radius `radius`."""
        # random angle
        alpha = 2 * math.pi * random.random()
        move_x = radius * math.cos(alpha) + xc
        move_y = radius * math.sin(alpha) + yc
        self._mmc.setXYPosition(move_x, move_y)

    def _move_to_rectangle_edge(
        self, xc: float, yc: float, well_size_x: float, well_size_y: float
    ) -> None:
        """Move to the edge of a rectangle.

        ...with center (xc, yc) and size (well_size_x, well_size_y).
        """
        x_top_left, y_top_left = xc - (well_size_x / 2), yc + (well_size_y / 2)
        x_bottom_right, y_bottom_right = xc + (well_size_x / 2), yc - (well_size_y / 2)
        # random 4 edge points
        edge_points = [
            (x_top_left, np.random.uniform(y_top_left, y_bottom_right)),  # left
            (np.random.uniform(x_top_left, x_bottom_right), y_top_left),  # top
            (x_bottom_right, np.random.uniform(y_top_left, y_bottom_right)),  # right
            (np.random.uniform(x_top_left, x_bottom_right), y_bottom_right),  # bottom
        ]
        self._mmc.setXYPosition(*edge_points[np.random.randint(0, 4)])

    def _save_positions(self) -> None:
        if not self._mda.position_widget._table.rowCount():
            return

        (dir_file, _) = QFileDialog.getSaveFileName(
            self, "Saving directory and filename.", "", "json(*.json)"
        )
        if not dir_file:
            return

        import json

        save_file = self._mda.position_widget.value()
        center_coords = {
            "name": "A1_center_coords",
            "x": self._calibration.A1_stage_coords_center[0],
            "y": self._calibration.A1_stage_coords_center[1],
        }
        save_file.insert(0, center_coords)  # type: ignore

        with open(str(dir_file), "w") as file:
            json.dump(save_file, file)

    def _load_positions(self) -> None:
        if not self._calibration.is_calibrated:
            warnings.warn("Plate not calibrated! Calibrate it first.", stacklevel=2)
            return

        (filename, _) = QFileDialog.getOpenFileName(
            self, "Select a position list file", "", "json(*.json)"
        )
        if filename:
            import json

            with open(filename) as file:
                self._add_loaded_positions_and_translate(json.load(file))

    def _add_loaded_positions_and_translate(self, pos_list: list) -> None:
        new_xc, new_yc = self._calibration.A1_stage_coords_center

        self._mda.position_widget.clear()

        delta_x, delta_y = (0.0, 0.0)
        for pos in pos_list:
            name = pos.get("name")

            if name == "A1_center_coords":
                old_xc = pos.get("x")
                old_yc = pos.get("y")
                delta_x = old_xc - new_xc
                delta_y = old_yc - new_yc
                continue

            new_x = pos.get("x") - delta_x
            new_y = pos.get("y") - delta_y
            zpos = pos.get("z")

            # TODO: fix for autofocus
            self._mda.position_widget._add_table_row(name, new_x, new_y, zpos, None)

    def get_state(self) -> MDASequence:
        """Get current state of widget and build a useq.MDASequence.

        Returns
        -------
        useq.MDASequence
        """
        return self._mda.get_state()

    def set_state(self, state: dict | MDASequence | str | Path) -> None:
        """Set current state of MDA widget.

        Parameters
        ----------
        state : dict | MDASequence | str | Path
            MDASequence state in the form of a dict, MDASequence object, or a str or
            Path pointing to a sequence.yaml file
        """
        # TODO: block gridplan + add fovs, etc...
        return self._mda.set_state(state)
