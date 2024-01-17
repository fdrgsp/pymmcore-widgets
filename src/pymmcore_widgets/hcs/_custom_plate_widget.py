from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import cast

from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QBrush, QPen
from qtpy.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QGraphicsScene,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ._util import ResizingGraphicsView, draw_well_plate
from ._well_plate_model import Plate, load_database

AlignCenter = Qt.AlignmentFlag.AlignCenter
StyleSheet = "background:grey; border: 0px; border-radius: 5px;"
BRUSH = QBrush(Qt.GlobalColor.green)
PEN = QPen(Qt.GlobalColor.black)
PEN.setWidth(1)
OPACITY = 0.7


def _make_widget_with_label(label: QLabel, widget: QWidget) -> QWidget:
    label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    wdg = QWidget()
    wdg.setLayout(QHBoxLayout())
    wdg.layout().setContentsMargins(0, 0, 0, 0)
    wdg.layout().setSpacing(5)
    wdg.layout().addWidget(label)
    wdg.layout().addWidget(widget)
    return wdg


class _Table(QTableWidget):
    """QTableWidget setup."""

    def __init__(self) -> None:
        super().__init__()

        self.horizontalHeader().setStretchLastSection(True)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setRowCount(1)
        self.setColumnCount(1)
        self.setHorizontalHeaderLabels(["Plate Database"])

        self.itemSelectionChanged.connect(self._update)

    def _update(self) -> None:
        self.cellClicked.emit(self.currentRow(), 0)


class _CustomPlateWidget(QDialog):
    """Widget to create or edit a well plate in the database."""

    valueChanged = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        plate_database_path: Path | str,
        plate_database: dict[str, Plate] | None = None,
    ) -> None:
        super().__init__(parent)

        self._plate_db_path = plate_database_path
        self._plate_db = plate_database or load_database(self._plate_db_path)

        # plate name
        id_label = QLabel()
        id_label.setText("Plate Name:")
        self._id = QLineEdit()
        plate_name = _make_widget_with_label(id_label, self._id)
        # circulat well
        is_circular_label = QLabel()
        is_circular_label.setText("Circular Well:")
        self._circular_checkbox = QCheckBox()
        circular = _make_widget_with_label(is_circular_label, self._circular_checkbox)
        spacer = QSpacerItem(
            0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        circular.layout().addItem(spacer)

        # columns
        cols_label = QLabel(text="Number of Columns:")
        self._cols = QSpinBox()
        self._cols.setMaximum(26)  # 26 letters in the alphabet
        self._cols.setAlignment(AlignCenter)
        cols = _make_widget_with_label(cols_label, self._cols)
        # rows
        rows_label = QLabel(text="Number of Rows:")
        self._rows = QSpinBox()
        self._rows.setAlignment(AlignCenter)
        rows = _make_widget_with_label(rows_label, self._rows)

        # well size x
        well_size_x_label = QLabel()
        well_size_x_label.setText("Well Size x (mm):")
        self._well_size_x = QDoubleSpinBox()
        self._well_size_x.setMaximum(100000.0)
        self._well_size_x.setAlignment(AlignCenter)
        well_size_x = _make_widget_with_label(well_size_x_label, self._well_size_x)
        # well size y
        well_size_y_label = QLabel()
        well_size_y_label.setText("Well Size y (mm):")
        self._well_size_y = QDoubleSpinBox()
        self._well_size_y.setMaximum(100000.0)
        self._well_size_y.setAlignment(AlignCenter)
        well_size_y = _make_widget_with_label(well_size_y_label, self._well_size_y)

        # well spacing x
        well_spacing_x_label = QLabel()
        well_spacing_x_label.setText("Well Spacing x (mm):")
        well_spacing_x_label.setToolTip(
            "Distance between the center of two wells along the horizontal axes."
        )
        self._well_spacing_x = QDoubleSpinBox()
        self._well_spacing_x.setMaximum(100000.0)
        self._well_spacing_x.setAlignment(AlignCenter)
        well_spacing_x = _make_widget_with_label(
            well_spacing_x_label, self._well_spacing_x
        )

        well_spacing_y_label = QLabel()
        well_spacing_y_label.setText("Well Spacing y (mm):")
        well_spacing_y_label.setToolTip(
            "Distance between the center of two wells along the vertical axes."
        )
        self._well_spacing_y = QDoubleSpinBox()
        self._well_spacing_y.setMaximum(100000.0)
        self._well_spacing_y.setAlignment(AlignCenter)
        well_spacing_y = _make_widget_with_label(
            well_spacing_y_label, self._well_spacing_y
        )

        # set size
        for lbl in [
            id_label,
            is_circular_label,
            cols_label,
            rows_label,
            well_size_x_label,
            well_size_y_label,
            well_spacing_x_label,
            well_spacing_y_label,
        ]:
            lbl.setMinimumWidth(well_spacing_x_label.sizeHint().width())

        # top_groupbox
        top_groupbox = QGroupBox()
        top_groupbox.setLayout(QGridLayout())
        top_groupbox.layout().setContentsMargins(10, 10, 10, 10)
        top_groupbox.layout().setVerticalSpacing(10)
        top_groupbox.layout().setHorizontalSpacing(20)

        top_groupbox.layout().addWidget(plate_name, 0, 0)
        top_groupbox.layout().addWidget(circular, 0, 1)
        top_groupbox.layout().addWidget(cols, 1, 0)
        top_groupbox.layout().addWidget(rows, 1, 1)
        top_groupbox.layout().addWidget(well_size_x, 2, 0)
        top_groupbox.layout().addWidget(well_size_y, 2, 1)
        top_groupbox.layout().addWidget(well_spacing_x, 3, 0)
        top_groupbox.layout().addWidget(well_spacing_y, 3, 1)

        # table
        table_groupbox = QGroupBox()
        table_groupbox.setLayout(QVBoxLayout())
        table_groupbox.layout().setContentsMargins(10, 10, 10, 10)
        self.plate_table = _Table()
        table_groupbox.layout().addWidget(self.plate_table)
        self.plate_table.cellClicked.connect(self._update_values)

        # plate preview
        self.scene = QGraphicsScene()
        self.view = ResizingGraphicsView(self.scene)
        self.view.setStyleSheet("background:grey; border-radius: 5px;")
        self.view.setMinimumWidth(self.plate_table.sizeHint().width())
        preview_wdg = QWidget()
        preview_wdg.setLayout(QVBoxLayout())
        preview_wdg.layout().setContentsMargins(0, 0, 0, 0)
        preview_wdg.layout().addWidget(self.view)
        bottom_groupbox = QGroupBox()
        bottom_groupbox.setLayout(QHBoxLayout())
        bottom_groupbox.layout().setContentsMargins(10, 10, 10, 10)
        bottom_groupbox.layout().setSpacing(10)
        bottom_groupbox.layout().addWidget(table_groupbox)
        bottom_groupbox.layout().addWidget(preview_wdg)

        # buttons
        btn_wdg = QGroupBox()
        btn_wdg.setLayout(QHBoxLayout())
        btn_wdg.layout().setContentsMargins(5, 5, 5, 5)
        btn_wdg.layout().setSpacing(5)
        self._delete_btn = QPushButton(text="Delete")
        self._delete_btn.clicked.connect(self._delete_plate)
        self._ok_btn = QPushButton(text="Add/Update")
        self._ok_btn.clicked.connect(self._update_plate_db)
        btn_wdg.layout().addWidget(self._ok_btn)
        btn_wdg.layout().addWidget(self._delete_btn)

        # main
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(10, 10, 10, 10)
        self.layout().setSpacing(10)
        self.layout().addWidget(top_groupbox)
        self.layout().addWidget(bottom_groupbox)
        self.layout().addWidget(btn_wdg)

        self.setMinimumHeight(self.sizeHint().height())

        # connect all widgets to their valueChanged signal
        for wdg in (
            self._rows,
            self._cols,
            self._well_size_x,
            self._well_size_y,
            self._well_spacing_x,
            self._well_spacing_y,
        ):
            wdg.valueChanged.connect(self._draw_well_plate)
        self._circular_checkbox.toggled.connect(self._draw_well_plate)

        self._populate_table()

    def _populate_table(self) -> None:
        """Populate the table with the well plate in the database."""
        self.plate_table.setRowCount(len(self._plate_db))
        for row, plate_name in enumerate(self._plate_db):
            item = QTableWidgetItem(plate_name)
            self.plate_table.setItem(row, 0, item)
        self._update_values(row=0)
        draw_well_plate(
            self.view,
            self.scene,
            self._plate_db[self.plate_table.item(0, 0).text()],
            brush=BRUSH,
            pen=PEN,
            opacity=OPACITY,
            text=False,
        )
        self._id.adjustSize()

    def _update_values(self, row: int) -> None:
        """Update the values of the well plate in the widget."""
        plate_item = self.plate_table.item(row, 0)
        if not plate_item:
            return
        plate = self._plate_db[plate_item.text()]
        self.setValue(plate)

    def _draw_well_plate(self) -> None:
        """Draw the well plate."""
        plate = self.value()
        if plate is None:
            return
        draw_well_plate(
            self.view,
            self.scene,
            plate,
            brush=BRUSH,
            pen=PEN,
            opacity=OPACITY,
            text=False,
        )

    def _update_plate_db(self) -> None:
        """Update the well plate in database and in current session."""
        if not self._id.text():
            raise ValueError("'Plate name' field cannot be empty!")

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
            self._update_values(row=0)
        else:
            self.reset_values()

    def reset_values(self) -> None:
        """Reset the values of the well plate in the widget."""
        self._id.setText("")
        self._rows.setValue(0)
        self._cols.setValue(0)
        self._well_spacing_x.setValue(0.0)
        self._well_spacing_y.setValue(0.0)
        self._well_size_x.setValue(0.0)
        self._well_size_y.setValue(0.0)
        self._circular_checkbox.setChecked(False)

    def setValue(self, plate: Plate) -> None:
        """Set the values of the well plate."""
        self._id.setText(plate.id)
        self._rows.setValue(plate.rows)
        self._cols.setValue(plate.columns)
        self._well_spacing_x.setValue(plate.well_spacing_x)
        self._well_spacing_y.setValue(plate.well_spacing_y)
        self._well_size_x.setValue(plate.well_size_x)
        self._well_size_y.setValue(plate.well_size_y)
        self._circular_checkbox.setChecked(plate.circular)

    def value(self) -> Plate | None:
        """Return the well plate with the current values."""
        return Plate(
            circular=self._circular_checkbox.isChecked(),
            id=self._id.text(),
            columns=self._cols.value(),
            rows=self._rows.value(),
            well_size_x=self._well_size_x.value(),
            well_size_y=self._well_size_y.value(),
            well_spacing_x=self._well_spacing_x.value(),
            well_spacing_y=self._well_spacing_y.value(),
        )

    def add_to_database(self, well_plate: Plate) -> None:
        """Add a well plate to the json database."""
        import json

        with open(Path(self._plate_db_path)) as file:
            db = cast(list, json.load(file))
            db.append(asdict(well_plate))
        with open(Path(self._plate_db_path), "w") as file:
            json.dump(db, file)

    def remove_from_database(self, well_plate: Plate | list[Plate]) -> None:
        """Remove a Plate or a list Plate of from the json database."""
        import json

        if isinstance(well_plate, Plate):
            well_plate = [well_plate]

        with open(Path(self._plate_db_path)) as file:
            db = cast(list, json.load(file))
            db = [plate for plate in db if plate["id"] not in well_plate]
        with open(Path(self._plate_db_path), "w") as file:
            json.dump(db, file)
