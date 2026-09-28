from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import useq
from platformdirs import user_data_dir
from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from superqt.utils import signals_blocked

# useq has no public API to unregister a plate; we reach into the (private)
# registry directly so deleting a custom plate here also removes it from useq.
from useq._plate_registry import _PLATE_REGISTRY

if TYPE_CHECKING:
    from collections.abc import Mapping


# a JSON file (on disk, outside of the repo) mapping plate-name -> WellPlate kwargs
DEFAULT_CUSTOM_PLATE_DB_PATH = (
    Path(user_data_dir(appname="pymmcore-widgets")) / "custom_well_plates.json"
)


def load_custom_plate_database(
    path: Path | str = DEFAULT_CUSTOM_PLATE_DB_PATH,
) -> dict[str, dict]:
    """Load the custom well-plate database from disk.

    Returns an empty dict if the file doesn't exist or can't be parsed.
    """
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        db = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return db if isinstance(db, dict) else {}


def save_custom_plate_database(
    db: Mapping[str, dict], path: Path | str = DEFAULT_CUSTOM_PLATE_DB_PATH
) -> None:
    """Save the custom well-plate database to disk."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(db), indent=2))


def register_custom_plates(path: Path | str = DEFAULT_CUSTOM_PLATE_DB_PATH) -> None:
    """Load any custom plates persisted on disk and register them with useq."""
    if db := load_custom_plate_database(path):
        useq.register_well_plates(db)  # type: ignore[arg-type]


class CustomPlateWidget(QDialog):
    """Dialog to create, edit, and delete custom well plate definitions.

    Custom plates are persisted to disk (as JSON) and registered with
    [useq.register_well_plates][], making them available (by name) anywhere a
    plate can be looked up by string, such as in
    [WellPlateWidget][pymmcore_widgets.useq_widgets.WellPlateWidget].

    Parameters
    ----------
    parent : QWidget | None
        The parent widget. By default, None.
    plate_db_path : Path | str
        The path to the JSON file used to persist custom plate definitions.
    """

    plateSaved = Signal(str)
    plateDeleted = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        plate_db_path: Path | str = DEFAULT_CUSTOM_PLATE_DB_PATH,
    ) -> None:
        # imported lazily to avoid a circular import with `_well_plate_widget`
        from ._well_plate_widget import WellPlateView

        super().__init__(parent)
        self.setWindowTitle("Custom Well Plates")

        self._db_path = plate_db_path
        # make sure anything already on disk is usable immediately
        register_custom_plates(self._db_path)
        self._db = load_custom_plate_database(self._db_path)

        # WIDGETS ------------------------------------------------------------

        self._plate_list = QListWidget()
        self._plate_list.setToolTip("Custom plates saved on this computer.")

        self._name = QLineEdit()
        self._circular = QCheckBox("Circular Wells")
        self._circular.setChecked(True)

        self._rows = QSpinBox()
        self._rows.setRange(1, 1000)
        self._columns = QSpinBox()
        self._columns.setRange(1, 1000)

        self._well_size_x = QDoubleSpinBox()
        self._well_size_y = QDoubleSpinBox()
        self._spacing_x = QDoubleSpinBox()
        self._spacing_y = QDoubleSpinBox()
        for spin in (
            self._well_size_x,
            self._well_size_y,
            self._spacing_x,
            self._spacing_y,
        ):
            spin.setRange(0.0, 1_000_000.0)
            spin.setDecimals(3)
            spin.setValue(1.0)
        self._well_size_x.setToolTip("Well width in mm (diameter, if circular).")
        self._well_size_y.setToolTip("Well height in mm (diameter, if circular).")
        self._spacing_x.setToolTip(
            "Center-to-center distance in mm between wells, horizontally."
        )
        self._spacing_y.setToolTip(
            "Center-to-center distance in mm between wells, vertically."
        )

        self._new_btn = QPushButton("New")
        self._new_btn.setAutoDefault(False)
        self._save_btn = QPushButton("Save")
        self._save_btn.setAutoDefault(False)
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setAutoDefault(False)
        self._delete_btn.setEnabled(False)

        self._preview = WellPlateView()
        self._preview.setSelectionMode(WellPlateView.SelectionMode.NoSelection)
        self._preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # LAYOUT ---------------------------------------------------------------

        form_grid = QFormLayout()
        form_grid.setSpacing(8)
        form_grid.addRow("Name:", self._name)
        form_grid.addRow("", self._circular)
        form_grid.addRow("Rows:", self._rows)
        form_grid.addRow("Columns:", self._columns)
        form_grid.addRow("Well Size x (mm):", self._well_size_x)
        form_grid.addRow("Well Size y (mm):", self._well_size_y)
        form_grid.addRow("Well Spacing x (mm):", self._spacing_x)
        form_grid.addRow("Well Spacing y (mm):", self._spacing_y)

        form_box = QGroupBox("Plate Definition")
        form_layout = QVBoxLayout(form_box)
        form_layout.addLayout(form_grid)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._new_btn)
        btn_row.addWidget(self._save_btn)
        btn_row.addWidget(self._delete_btn)
        btn_row.addStretch()
        form_layout.addLayout(btn_row)

        list_box = QGroupBox("Saved Plates")
        list_layout = QVBoxLayout(list_box)
        list_layout.addWidget(self._plate_list)

        preview_box = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.addWidget(self._preview)

        # the list + form make up a narrow, fixed-width left column; the
        # preview gets all remaining space so the plate is always drawn large.
        left = QWidget()
        left.setMaximumWidth(300)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(list_box, 1)
        left_layout.addWidget(form_box, 0)

        top = QHBoxLayout()
        top.addWidget(left, 0)
        top.addWidget(preview_box, 1)

        main_layout = QVBoxLayout(self)
        main_layout.addLayout(top)

        self.resize(900, 520)
        self.setMinimumSize(760, 420)

        # CONNECTIONS -----------------------------------------------------------

        self._new_btn.clicked.connect(self._on_new_clicked)
        self._save_btn.clicked.connect(self._on_save_clicked)
        self._delete_btn.clicked.connect(self._on_delete_clicked)
        self._plate_list.currentTextChanged.connect(self._on_selection_changed)
        for wdg in (
            self._rows,
            self._columns,
            self._well_size_x,
            self._well_size_y,
            self._spacing_x,
            self._spacing_y,
        ):
            wdg.valueChanged.connect(self._update_preview)
        self._circular.toggled.connect(self._update_preview)

        self._refresh_list()
        self._update_preview()

    # _________________________PRIVATE METHODS________________________ #

    def _build_plate(self) -> useq.WellPlate:
        return useq.WellPlate(
            name=self._name.text().strip(),
            rows=self._rows.value(),
            columns=self._columns.value(),
            well_spacing=(self._spacing_x.value(), self._spacing_y.value()),
            well_size=(self._well_size_x.value(), self._well_size_y.value()),
            circular_wells=self._circular.isChecked(),
        )

    def _set_form(self, plate: useq.WellPlate) -> None:
        with signals_blocked(self._name):
            self._name.setText(plate.name)
        self._rows.setValue(plate.rows)
        self._columns.setValue(plate.columns)
        self._well_size_x.setValue(plate.well_size[0])
        self._well_size_y.setValue(plate.well_size[1])
        self._spacing_x.setValue(plate.well_spacing[0])
        self._spacing_y.setValue(plate.well_spacing[1])
        self._circular.setChecked(plate.circular_wells)
        self._update_preview()

    def _clear_form(self) -> None:
        with signals_blocked(self._plate_list):
            self._plate_list.clearSelection()
        self._delete_btn.setEnabled(False)
        self._set_form(
            useq.WellPlate(
                rows=1, columns=1, well_spacing=(1.0, 1.0), well_size=(1.0, 1.0)
            )
        )
        self._name.setFocus()

    def _refresh_list(self, select: str | None = None) -> None:
        with signals_blocked(self._plate_list):
            self._plate_list.clear()
            self._plate_list.addItems(sorted(self._db))
        if select and select in self._db:
            items = self._plate_list.findItems(select, Qt.MatchFlag.MatchExactly)
            if items:
                self._plate_list.setCurrentItem(items[0])
                return
        self._delete_btn.setEnabled(bool(self._plate_list.currentItem()))

    def _update_preview(self) -> None:
        try:
            plate = self._build_plate()
        except ValueError:
            self._preview.clear()
            return
        self._preview.drawPlate(plate)

    def _on_selection_changed(self, key: str) -> None:
        self._delete_btn.setEnabled(bool(key))
        if not key or key not in self._db:
            return
        plate = useq.WellPlate.model_validate({**self._db[key], "name": key})
        self._set_form(plate)

    def _on_new_clicked(self) -> None:
        self._clear_form()

    def _on_save_clicked(self) -> None:
        plate = self._build_plate()
        if not plate.name:
            QMessageBox.warning(self, "Missing Name", "Please enter a plate name.")
            return
        is_builtin = plate.name in useq.registered_well_plate_keys()
        if plate.name not in self._db and is_builtin:
            QMessageBox.warning(
                self,
                "Name Already in Use",
                f"{plate.name!r} is already the name of a built-in plate.\n"
                "Please choose a different name.",
            )
            return

        self._db[plate.name] = plate.model_dump()
        save_custom_plate_database(self._db, self._db_path)
        useq.register_well_plates({plate.name: plate})

        self._refresh_list(select=plate.name)
        self.plateSaved.emit(plate.name)

    def _on_delete_clicked(self) -> None:
        item = self._plate_list.currentItem()
        if item is None:
            return
        key = item.text()
        if (
            QMessageBox.question(
                self,
                "Delete Plate",
                f"Delete the custom plate {key!r}? This cannot be undone.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        self._db.pop(key, None)
        save_custom_plate_database(self._db, self._db_path)
        _PLATE_REGISTRY.pop(key, None)

        self._refresh_list()
        self._clear_form()
        self.plateDeleted.emit(key)
