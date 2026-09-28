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
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from superqt.iconify import QIconifyIcon
from superqt.utils import signals_blocked

# useq has no public API to unregister a plate; we reach into the (private)
# registry directly so deleting a custom plate here also removes it from useq.
from useq._plate_registry import _PLATE_REGISTRY

from pymmcore_widgets._icons import StandardIcon
from pymmcore_widgets._util import GREEN, RED

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


def _labeled_row(label_text: str, widget: QWidget) -> tuple[QLabel, QWidget]:
    """Return a `(label, row)` pair; `row` is `label` + `widget` side by side."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    label = QLabel(label_text)
    layout.addWidget(label)
    layout.addWidget(widget, 1)
    return label, row


class CustomPlateWidget(QDialog):
    """Dialog to create, edit, and delete well plate definitions.

    Lists every plate useq knows about - both built-ins (e.g. "96-well") and
    any custom ones saved through this dialog - and lets any of them be
    edited, overwritten, or deleted; nothing is treated as special-cased or
    read-only. Edits are persisted to disk (as JSON) and registered with
    [useq.register_well_plates][], making them available (by name) anywhere a
    plate can be looked up by string, such as in
    [WellPlateWidget][pymmcore_widgets.useq_widgets.WellPlateWidget]. Deleting
    a plate that was never edited here (a plain built-in) only removes it for
    the current process; it ships with useq again on the next run.

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
        # the plate currently loaded into the form (selected in the list, or
        # just saved) - lets Save tell "editing this same plate" apart from
        # "this name happens to collide with a different saved plate"
        self._editing_key: str | None = None

        # WIDGETS ------------------------------------------------------------

        self._plate_list = QListWidget()
        self._plate_list.setToolTip(
            "All available well plates. Select one to edit or delete it,\n"
            "or click New to create one."
        )
        # without this the list collapses to a couple of rows next to the taller
        # form, and newly saved plates end up scrolled out of sight
        self._plate_list.setMinimumHeight(140)

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

        self._new_btn = QPushButton(QIconifyIcon("mdi:plus-thick", color=GREEN), "New")
        self._new_btn.setAutoDefault(False)
        self._save_btn = QPushButton(QIconifyIcon("mdi:content-save-outline"), "Save")
        self._save_btn.setAutoDefault(False)
        self._delete_btn = QPushButton(StandardIcon.DELETE.icon(RED), "Delete")
        self._delete_btn.setAutoDefault(False)
        self._delete_btn.setEnabled(False)

        self._preview = WellPlateView()
        self._preview.setSelectionMode(WellPlateView.SelectionMode.NoSelection)
        self._preview.setDragMode(WellPlateView.DragMode.NoDrag)
        self._preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # LAYOUT ---------------------------------------------------------------

        # unlike the label + field rows, this one has no label to line up
        # with, so it spans the panel's full width (flush with where the
        # labels start), giving the buttons more room instead of being
        # confined to (and clipped by) the narrower field column
        btn_row = QWidget()
        btn_row_layout = QHBoxLayout(btn_row)
        btn_row_layout.setContentsMargins(0, 0, 0, 0)
        for btn in (self._new_btn, self._save_btn, self._delete_btn):
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn_row_layout.addWidget(btn, 1)

        rows = [
            _labeled_row("Name:", self._name),
            _labeled_row("", self._circular),
            _labeled_row("Rows:", self._rows),
            _labeled_row("Columns:", self._columns),
            _labeled_row("Well Size x (mm):", self._well_size_x),
            _labeled_row("Well Size y (mm):", self._well_size_y),
            _labeled_row("Well Spacing x (mm):", self._spacing_x),
            _labeled_row("Well Spacing y (mm):", self._spacing_y),
        ]
        # every label gets the same fixed width, so all the fields start at
        # the same x position, lined up like a real form
        label_width = max(label.sizeHint().width() for label, _ in rows)
        for label, _ in rows:
            label.setFixedWidth(label_width)

        form_grid = QVBoxLayout()
        form_grid.setSpacing(8)
        for _, row in rows:
            form_grid.addWidget(row)
        form_grid.addWidget(btn_row)

        form_box = QGroupBox("Plate Definition")
        form_layout = QVBoxLayout(form_box)
        form_layout.addLayout(form_grid)

        list_box = QGroupBox("Available Plates")
        list_layout = QVBoxLayout(list_box)
        list_layout.addWidget(self._plate_list)

        preview_box = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.addWidget(self._preview)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(list_box, 1)
        left_layout.addWidget(form_box, 0)

        top = QHBoxLayout()
        top.addWidget(left, 0)
        top.addWidget(preview_box, 1)

        main_layout = QVBoxLayout(self)
        main_layout.addLayout(top)

        self.setMinimumSize(760, 580)

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
            # clearSelection() alone only removes the highlight; it leaves the
            # list's "current" item in place, so re-clicking that same item
            # afterwards wouldn't re-fire currentTextChanged. setCurrentRow(-1)
            # actually clears it, so any later click is seen as a real change.
            self._plate_list.setCurrentRow(-1)
        self._editing_key = None
        self._delete_btn.setEnabled(False)
        self._set_form(
            useq.WellPlate(
                rows=1, columns=1, well_spacing=(1.0, 1.0), well_size=(1.0, 1.0)
            )
        )
        self._name.setFocus()

    def _refresh_list(self, select: str | None = None) -> None:
        # imported lazily to avoid a circular import with `_well_plate_widget`
        from ._well_plate_widget import _sort_plate

        # plates saved through this dialog on top (most recently saved first),
        # everything else useq knows about (built-ins, or plates registered
        # elsewhere) follows - all of them are equally editable/deletable here.
        customs = list(reversed(self._db))
        others = sorted(
            (k for k in useq.registered_well_plate_keys() if k not in self._db),
            key=_sort_plate,
        )

        with signals_blocked(self._plate_list):
            self._plate_list.clear()
            self._plate_list.addItems((*customs, *others))
        # the rows were repopulated with signals blocked, so ask the view for a
        # relayout/repaint explicitly rather than relying on it having noticed
        self._plate_list.viewport().update()
        if select:
            items = self._plate_list.findItems(select, Qt.MatchFlag.MatchExactly)
            if items:
                self._plate_list.setCurrentItem(items[0])
                # the list is short and sorted, so a newly saved plate can land
                # below the fold; make sure it's actually in view
                self._plate_list.scrollToItem(items[0])
                # give the list keyboard focus so the new selection renders with
                # the "active" highlight color; otherwise (e.g. on macOS) an
                # unfocused selection can render as a pale highlight with white
                # text, making the newly-saved item look like it isn't there
                self._plate_list.setFocus()
                return
        self._delete_btn.setEnabled(self._plate_list.currentItem() is not None)

    def _update_preview(self) -> None:
        try:
            plate = self._build_plate()
        except ValueError:
            self._preview.clear()
            return
        self._preview.drawPlate(plate)

    def _on_selection_changed(self, key: str) -> None:
        self._delete_btn.setEnabled(bool(key))
        self._editing_key = key or None
        if not key:
            return
        # works for built-ins too, since they're registered with useq as well
        self._set_form(useq.WellPlate.from_str(key))

    def _on_new_clicked(self) -> None:
        self._clear_form()

    def _on_save_clicked(self) -> None:
        plate = self._build_plate()
        if not plate.name:
            QMessageBox.warning(self, "Missing Name", "Please enter a plate name.")
            return
        # if the name collides with a *different* plate than the one currently
        # loaded (built-in or custom), confirm before overwriting it. Saving
        # over the plate that's already loaded/selected (an in-place edit)
        # needs no confirmation.
        if (
            plate.name in useq.registered_well_plate_keys()
            and plate.name != self._editing_key
        ):
            if (
                QMessageBox.question(
                    self,
                    "Overwrite Plate",
                    f"A plate named {plate.name!r} already exists.\n"
                    "Do you want to overwrite it?",
                )
                != QMessageBox.StandardButton.Yes
            ):
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
                f"Delete the plate {key!r}? This cannot be undone.",
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
