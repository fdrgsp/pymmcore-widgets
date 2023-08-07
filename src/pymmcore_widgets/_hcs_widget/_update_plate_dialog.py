from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import cast

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from ._well_plate_model import WellPlate

AlignCenter = Qt.AlignmentFlag.AlignCenter


class _Table(QTableWidget):
    """QTableWidget setup."""

    def __init__(self) -> None:
        super().__init__()

        self.horizontalHeader().setStretchLastSection(True)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setRowCount(1)
        self.setColumnCount(1)
        self.setHorizontalHeaderLabels(["Plate"])

        self.itemSelectionChanged.connect(self._update)

    def _update(self) -> None:
        self.cellClicked.emit(self.currentRow(), 0)


class _PlateDatabaseWidget(QDialog):
    """Widget to create or edit a well plate in the database."""

    valueChanged = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        plate_database: dict[str, WellPlate],
        plate_database_path: Path | str,
    ) -> None:
        super().__init__(parent)

        self._plate_db_path = plate_database_path
        self._plate_db = plate_database

        main_layout = QGridLayout()
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        self.plate_combo = QComboBox()

        self._id_label = QLabel()
        self._id_label.setText("plate name:")
        self._id = QLineEdit()
        main_layout.addWidget(self._id_label, 0, 0)
        main_layout.addWidget(self._id, 0, 1)

        self._rows_label = QLabel()
        self._rows_label.setText("rows:")
        self._rows = QSpinBox()
        self._rows.setAlignment(AlignCenter)
        main_layout.addWidget(self._rows_label, 1, 0)
        main_layout.addWidget(self._rows, 1, 1)

        self._cols_label = QLabel()
        self._cols_label.setText("columns:")
        self._cols = QSpinBox()
        self._cols.setAlignment(AlignCenter)
        main_layout.addWidget(self._cols_label, 2, 0)
        main_layout.addWidget(self._cols, 2, 1)

        self._well_size_x_label = QLabel()
        self._well_size_x_label.setText("well size x (mm):")
        self._well_size_x = QDoubleSpinBox()
        self._well_size_x.setMaximum(100000.0)
        self._well_size_x.setAlignment(AlignCenter)
        main_layout.addWidget(self._well_size_x_label, 3, 0)
        main_layout.addWidget(self._well_size_x, 3, 1)

        self._well_size_y_label = QLabel()
        self._well_size_y_label.setText("well size y (mm):")
        self._well_size_y = QDoubleSpinBox()
        self._well_size_y.setMaximum(100000.0)
        self._well_size_y.setAlignment(AlignCenter)
        main_layout.addWidget(self._well_size_y_label, 4, 0)
        main_layout.addWidget(self._well_size_y, 4, 1)

        self._well_spacing_x_label = QLabel()
        self._well_spacing_x_label.setText("well spacing x (mm):")
        self._well_spacing_x = QDoubleSpinBox()
        self._well_spacing_x.setMaximum(100000.0)
        self._well_spacing_x.setAlignment(AlignCenter)
        main_layout.addWidget(self._well_spacing_x_label, 5, 0)
        main_layout.addWidget(self._well_spacing_x, 5, 1)

        self._well_spacing_y_label = QLabel()
        self._well_spacing_y_label.setText("well spacing y (mm):")
        self._well_spacing_y = QDoubleSpinBox()
        self._well_spacing_y.setMaximum(100000.0)
        self._well_spacing_y.setAlignment(AlignCenter)
        main_layout.addWidget(self._well_spacing_y_label, 6, 0)
        main_layout.addWidget(self._well_spacing_y, 6, 1)

        is_circular_label = QLabel()
        is_circular_label.setText("circular:")
        self._circular_checkbox = QCheckBox()
        main_layout.addWidget(is_circular_label, 7, 0)
        main_layout.addWidget(self._circular_checkbox, 7, 1)

        btn_wdg = QWidget()
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(5, 5, 5, 0)
        btn_layout.setSpacing(5)
        self._delete_btn = QPushButton(text="Delete")
        self._delete_btn.clicked.connect(self._delete_plate)
        self._ok_btn = QPushButton(text="Add/Update")
        self._ok_btn.clicked.connect(self._update_plate_db)
        btn_layout.addWidget(self._ok_btn)
        btn_layout.addWidget(self._delete_btn)
        btn_wdg.setLayout(btn_layout)
        main_layout.addWidget(btn_wdg, 8, 0, 1, 3)

        self.plate_table = _Table()
        self.plate_table.cellClicked.connect(self._update_values)
        main_layout.addWidget(self.plate_table, 0, 2, 8, 1)

        self.setLayout(main_layout)

        self._populate_table()

        if self.plate_table.rowCount():
            self._update_values(1, 0)

    def _populate_table(self) -> None:
        """Populate the table with the well plate in the database."""
        self.plate_table.setRowCount(len(self._plate_db))
        for row, plate_name in enumerate(self._plate_db):
            item = QTableWidgetItem(plate_name)
            self.plate_table.setItem(row, 0, item)

    def _update_values(self, row: int, col: int) -> None:
        """Update the values of the well plate in the widget."""
        plate_item = self.plate_table.item(row, col)
        if not plate_item:
            return
        plate = self._plate_db[plate_item.text()]
        self.set_value(plate)

    def _update_plate_db(self) -> None:
        """Update the well plate in database and in current session."""
        if not self._id.text():
            raise ValueError("'Plate name' field cannot be empty.")

        new_plate = self.value()
        if new_plate is None:
            return

        self.add_to_database(new_plate)

        # update self._plate_db for the current session
        self._plate_db[new_plate.id] = new_plate

        self.valueChanged.emit(new_plate)
        self._populate_table()

        self.close()

    def _delete_plate(self) -> None:
        """Delete the selected well plate(s) from database and the current session."""
        selected_rows = {r.row() for r in self.plate_table.selectedIndexes()}
        if not selected_rows:
            return

        plate_names = [self.plate_table.item(r, 0).text() for r in selected_rows]
        # delete plate in database
        self.remove_from_database(plate_names)
        # update self._plate_db for the current session
        for plate_name in plate_names:
            self._plate_db.pop(plate_name, None)
            match = self.plate_table.findItems(plate_name, Qt.MatchExactly)
            self.plate_table.removeRow(match[0].row())

        self.valueChanged.emit(None)

        if self.plate_table.rowCount():
            self.plate_table.setCurrentCell(0, 0)
            self._update_values(0, 0)
        else:
            self.clear_values()

    def clear_values(self) -> None:
        """Clear the values of the well plate in the widget."""
        self._id.setText("")
        self._rows.setValue(0)
        self._cols.setValue(0)
        self._well_spacing_x.setValue(0.0)
        self._well_spacing_y.setValue(0.0)
        self._well_size_x.setValue(0.0)
        self._well_size_y.setValue(0.0)
        self._circular_checkbox.setChecked(False)

    def set_value(self, plate: WellPlate) -> None:
        """Set the values of the well plate."""
        self._id.setText(plate.id)
        self._rows.setValue(plate.rows)
        self._cols.setValue(plate.cols)
        self._well_spacing_x.setValue(plate.well_spacing_x)
        self._well_spacing_y.setValue(plate.well_spacing_y)
        self._well_size_x.setValue(plate.well_size_x)
        self._well_size_y.setValue(plate.well_size_y)
        self._circular_checkbox.setChecked(plate.circular)

    def value(self) -> WellPlate | None:
        """Return the well plate with the current values."""
        return WellPlate(
            circular=self._circular_checkbox.isChecked(),
            id=self._id.text(),
            cols=self._cols.value(),
            rows=self._rows.value(),
            well_size_x=self._well_size_x.value(),
            well_size_y=self._well_size_y.value(),
            well_spacing_x=self._well_spacing_x.value(),
            well_spacing_y=self._well_spacing_y.value(),
        )

    def add_to_database(self, well_plate: WellPlate) -> None:
        """Add a well plate to the json database."""
        import json

        with open(Path(self._plate_db_path)) as file:
            db = cast(list, json.load(file))
            db.append(asdict(well_plate))
        with open(Path(self._plate_db_path), "w") as file:
            json.dump(db, file)

    def remove_from_database(self, well_plate: WellPlate | list[WellPlate]) -> None:
        """Remove a WellPlate or a list WellPlate of from the json database."""
        import json

        if isinstance(well_plate, WellPlate):
            well_plate = [well_plate]

        with open(Path(self._plate_db_path)) as file:
            db = cast(list, json.load(file))
            db = [plate for plate in db if plate["id"] not in well_plate]
        with open(Path(self._plate_db_path), "w") as file:
            json.dump(db, file)
