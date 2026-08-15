from __future__ import annotations

import itertools
import warnings
from collections import Counter
from contextlib import suppress
from copy import deepcopy
from typing import TYPE_CHECKING, Any, cast

from pymmcore_plus import CMMCorePlus
from pymmcore_plus.model import PixelSizeGroup, PixelSizePreset, Setting
from qtpy.QtCore import (
    QAbstractItemModel,
    QAbstractTableModel,
    QModelIndex,
    QSize,
    Qt,
    Signal,
    Slot,
)
from qtpy.QtWidgets import (
    QAbstractSpinBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QSplitter,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from superqt import QIconifyIcon
from superqt.utils import signals_blocked

from pymmcore_widgets._icons import StandardIcon
from pymmcore_widgets._models import Device, DevicePropertySetting, get_loaded_devices
from pymmcore_widgets.useq_widgets import DataTable, DataTableWidget
from pymmcore_widgets.useq_widgets._column_info import FloatColumn, TextColumn

from ._views._device_property_selector import DevicePropertySelector
from ._views._property_setting_delegate import PropertySettingDelegate

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from PyQt6.QtGui import QAction
else:
    from qtpy.QtGui import QAction

FIXED = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
PX = "px"
ID = "id"
PX_SIZE = "pixel_size"
PROP = "properties"
NEW = "New"
DEV_PROP_ROLE = QTableWidgetItem.ItemType.UserType + 1
DEFAULT_AFFINE = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)


class PixelConfigurationWidget(QWidget):
    """A Widget to define the pixel size configurations.

    Each pixel size configuration can be linked to any device and property. However,
    it's important to note that all pixel size configurations must include the same
    devices and properties. The only variation allowed between different configurations
    is in the values of the device properties.

    The layout mirrors
    [`ConfigGroupsEditor`][pymmcore_widgets.ConfigGroupsEditor]: the table on the
    left lists the resolutionIDs, its toolbar has an *Edit Properties* button that
    opens the same device/property picker used there, and the table on the right
    edits the values those properties take for the selected resolutionID.

    Parameters
    ----------
    parent : QWidget | None
        Optional parent widget, by default None
    mmcore : CMMCorePlus | None
        Optional [`pymmcore_plus.CMMCorePlus`][] micromanager core.
        By default, None. If not specified, the widget will use the active
        (or create a new)
        [`CMMCorePlus.instance`][pymmcore_plus.core._mmcore_plus.CMMCorePlus.instance].
    """

    cleanChanged = Signal(bool)
    """Emitted with the new clean state whenever it changes.

    Mirrors `QUndoStack.cleanChanged`, which
    [`ConfigGroupsEditor`][pymmcore_widgets.ConfigGroupsEditor] uses for the
    same purpose, so both editors expose one dirty-tracking interface.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        mmcore: CMMCorePlus | None = None,
    ):
        super().__init__(parent)

        self.setWindowTitle("Pixel Configuration Widget")

        self._mmc = mmcore or CMMCorePlus.instance()

        self._resID_map: dict[int, PixelSizePreset] = {}
        # Device models feeding the "Edit Properties" dialog, plus a flat lookup
        # of their properties. Both are refreshed whenever a system configuration
        # is loaded, and carry the metadata (type, limits, allowed values) that
        # the value table needs to build the right editor for each property.
        self._loaded_devices: tuple[Device, ...] = ()
        self._prop_meta: dict[tuple[str, str], DevicePropertySetting] = {}
        # Baseline the current state is compared against to decide dirtiness.
        # There is no QUndoStack here (unlike ConfigGroupsEditor), so this
        # snapshot plays that role -- and because it's a value comparison
        # rather than a one-way "edited" flag, reverting an edit by hand
        # correctly returns the widget to clean.
        self._clean_state: list[PixelSizePreset] = []
        self._was_clean = True

        # pixel and affine tables widget
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(5)
        self._px_table = _PixelTable()
        affine_lbl = QLabel("Affine Transformation:")
        self._affine_table = AffineTable()
        left_layout.addWidget(self._px_table, 1)
        left_layout.addWidget(affine_lbl, 0)
        left_layout.addWidget(self._affine_table, 0)

        # property values of the selected resolutionID
        self._value_table = _PropertyValueTable(self)

        splitter = QSplitter()
        splitter.setContentsMargins(0, 0, 0, 0)
        # avoid splitter hiding completely widgets
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left)
        splitter.addWidget(self._value_table)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        # buttons
        self._apply_btn = apply_btn = QPushButton("Apply and Close")
        apply_btn.setSizePolicy(FIXED)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setSizePolicy(FIXED)
        # Same names/placement as ConfigGroupsEditor's indicator, so an
        # embedding application can treat the two editors identically.
        self._dirty_icon = QIconifyIcon("mdi:alert-circle-outline", color="orange")
        self._clean_icon = QIconifyIcon("mdi:check-circle-outline", color="green")
        self._status_icon = QLabel(self)
        self._status_label = QLabel(self)
        btns_layout = QHBoxLayout()
        btns_layout.setContentsMargins(0, 0, 0, 0)
        btns_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        )
        btns_layout.addWidget(self._status_icon)
        btns_layout.addWidget(self._status_label)
        btns_layout.addWidget(cancel_btn)
        btns_layout.addWidget(apply_btn)

        # main layout
        main_layout = QVBoxLayout(self)
        main_layout.addWidget(splitter)
        main_layout.addLayout(btns_layout)

        # connect signals
        self._mmc.events.systemConfigurationLoaded.connect(self._on_sys_config_loaded)
        self._px_table._table.itemChanged.connect(self._on_resolutionID_name_changed)
        self._px_table.valueChanged.connect(self._on_px_table_value_changed)
        self._px_table._table.itemSelectionChanged.connect(
            self._on_px_table_selection_changed
        )
        self._px_table.table().model().rowsInserted.connect(self._on_rows_inserted)
        self._value_table.act_edit_props.triggered.connect(self._edit_properties)
        self._value_table.act_remove_props.triggered.connect(self._remove_properties)
        self._value_table.valueEdited.connect(self._on_property_value_edited)
        self._affine_table.valueChanged.connect(self._on_affine_value_changed)
        apply_btn.clicked.connect(self._on_apply)
        cancel_btn.clicked.connect(self.close)

        # Re-evaluate dirtiness after every edit path. Connected last so each
        # runs after the slot above it has updated `_resID_map` (Qt invokes
        # direct connections in connection order). Selecting a different row
        # also re-emits some of these, but that doesn't change `value()`, so
        # comparing against the baseline correctly leaves the state clean.
        self._px_table._table.itemChanged.connect(self._update_clean_state)
        self._px_table.valueChanged.connect(self._update_clean_state)
        self._affine_table.valueChanged.connect(self._update_clean_state)

        self.destroyed.connect(self._disconnect)

        self._on_sys_config_loaded()

    # -------------- Public API --------------

    def value(self) -> list[PixelSizePreset]:
        """Return the current state of the widget describing the pixel configurations.

        Returns
        -------
        list[PixelSizePreset][pymmcore_plus.model.PixelSizePreset]
            A list of pixel configurations data.

        Example:
        -------
            output = [
                PixelSizePreset(
                    name='Res10x',
                    settings=[Setting('Objective', 'Label', 'Nikon 10X S Fluor')],
                    pixel_size_um=1.0
                ),
                ...
            ]
        """
        return list(self._resID_map.values())

    def setValue(self, value: list[PixelSizePreset]) -> None:
        """Set the state of the widget describing the pixel configurations.

        Parameters
        ----------
        value : list[PixelSizePreset][pymmcore_plus.model.PixelSizePreset]
            The list of pixel configurations data to set.

        Example:
        -------
            input = [
                PixelSizePreset(
                    name='Res10x',
                    settings=[Setting('Objective', 'Label', 'Nikon 10X S Fluor')],
                    pixel_size_um=1.0
                ),
                ...
            ]
        """
        self._px_table._remove_all()
        self._resID_map.clear()

        if not value:
            self._clear_property_view()
            self.setClean()
            return

        for row, rec in enumerate(value):
            self._resID_map[row] = value[row]
            self._px_table._add_row()
            data = {
                self._px_table.ID.key: rec.name,
                self._px_table.VALUE.key: rec.pixel_size_um,
            }
            self._px_table.table().setRowData(row, data)
            self._connect_px_spinbox(row)

        self._px_table._table.selectRow(0)
        self.setClean()

    def isClean(self) -> bool:
        """Return True if the widget has no unapplied changes."""
        return self.value() == self._clean_state

    def setClean(self) -> None:
        """Mark the current state as the clean (saved) baseline."""
        # deep copy: `_resID_map` holds the same mutable PixelSizePreset
        # objects that later edits mutate in place, so a shallow copy would
        # track those edits and the widget could never look dirty.
        self._clean_state = deepcopy(self.value())
        self._update_clean_state()

    # -------------- Private API --------------

    @Slot()
    def _update_clean_state(self, *_: Any) -> None:
        """Refresh the status indicator and emit `cleanChanged` on a flip."""
        clean = self.isClean()
        if clean:
            self._status_icon.setPixmap(self._clean_icon.pixmap(16, 16))
            self._status_label.setText("No changes")
        else:
            self._status_icon.setPixmap(self._dirty_icon.pixmap(16, 16))
            self._status_label.setText("Unsaved changes")
        self._apply_btn.setEnabled(not clean)
        if clean != self._was_clean:
            self._was_clean = clean
            self.cleanChanged.emit(clean)

    @Slot()
    def _on_sys_config_loaded(self) -> None:
        self._px_table._remove_all()
        self._resID_map.clear()

        # (re)collect device/property metadata for the picker and the value table
        self._loaded_devices = tuple(get_loaded_devices(self._mmc))
        self._prop_meta = {
            prop.key(): prop for dev in self._loaded_devices for prop in dev.properties
        }

        px_groups = PixelSizeGroup.create_from_core(self._mmc)
        if not px_groups.presets:
            self._clear_property_view()
            self.setClean()
            return

        for row, px_preset in enumerate(px_groups.presets.values()):
            self._resID_map[row] = px_preset
            data = {
                self._px_table.ID.key: px_preset.name,
                self._px_table.VALUE.key: px_preset.pixel_size_um,
            }
            self._px_table._add_row()
            self._px_table.table().setRowData(row, data)
            self._connect_px_spinbox(row)

        # select first row of px_table corresponding to the first resolutionID
        self._px_table._table.selectRow(0)

        # freshly loaded from the core: this is the new clean baseline
        self.setClean()

    def _connect_px_spinbox(self, row: int) -> None:
        """Connect the pixel-size spinbox of `row` to `_on_px_value_changed`."""
        wdg = cast("QDoubleSpinBox", self._px_table._table.cellWidget(row, 1))
        wdg.valueChanged.connect(self._on_px_value_changed)

    def _selected_row(self) -> int | None:
        """Return the row of the selected resolutionID, or None if not exactly one.

        Also returns None while a removal is in flight and `_resID_map` has not
        caught up with the table yet.
        """
        items = self._px_table._table.selectedItems()
        if len(items) != 1:
            return None
        row = int(items[0].row())
        return row if row in self._resID_map else None

    # -------------- property selection / editing --------------

    def _clear_property_view(self) -> None:
        """Empty and disable the right-hand property value table."""
        self._value_table.setSettings([])
        self._value_table.view.setEnabled(False)
        self._value_table.act_edit_props.setEnabled(False)

    def _settings_for_row(self, row: int) -> list[DevicePropertySetting]:
        """Return the settings of resolutionID `row` enriched with property metadata.

        The returned objects are copies, so the value table can edit them freely
        without touching either `_resID_map` or the cached device models.
        """
        out: list[DevicePropertySetting] = []
        for setting in self._resID_map[row].settings:
            key = (setting.device_name, setting.property_name)
            if (meta := self._prop_meta.get(key)) is None:
                # property is not on any loaded device (e.g. set programmatically
                # with setValue): fall back to a plain, editable string property
                meta = DevicePropertySetting(
                    device=Device(label=setting.device_name),
                    property_name=setting.property_name,
                )
            out.append(meta.model_copy(update={"value": str(setting.property_value)}))
        return out

    def _refresh_property_view(self) -> None:
        """Show the properties of the currently selected resolutionID."""
        if (row := self._selected_row()) is None:
            self._value_table.setSettings([])
            self._value_table.view.setEnabled(False)
            # "Edit Properties" acts on every resolutionID, so it stays usable
            # as long as there is one, even with nothing selected
            self._value_table.act_edit_props.setEnabled(bool(self._resID_map))
            return

        self._value_table.setSettings(self._settings_for_row(row))
        self._value_table.view.setEnabled(True)
        self._value_table.act_edit_props.setEnabled(True)

    @Slot()
    def _on_property_value_edited(self) -> None:
        """Write the edited values back to the selected resolutionID."""
        if (row := self._selected_row()) is None:
            return  # pragma: no cover
        self._resID_map[row].settings = [
            Setting(s.device_label, s.property_name, s.value)
            for s in self._value_table.settings()
        ]
        self._update_clean_state()

    @Slot()
    def _edit_properties(self) -> None:
        """Pick which device properties define *all* the pixel configurations.

        Unlike ConfigGroupsEditor -- where each preset may have its own set of
        properties -- every pixel size configuration must describe the same
        (device, property) pairs, so the selection is always applied to all
        resolutionIDs. Only the values may differ, and those are edited in the
        table on the right.
        """
        if not self._resID_map:
            return

        dialog = QDialog(
            self,
            Qt.WindowType.Sheet
            | Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.FramelessWindowHint,
        )
        dialog.setWindowTitle("Edit Properties")
        dialog.setModal(True)

        selector = DevicePropertySelector(dialog)
        selector.setAvailableDevices(self._loaded_devices)

        # pre-check whatever the configurations already use (all resolutionIDs
        # share the same properties, so the selected row is representative)
        row = self._selected_row()
        current = self._settings_for_row(row if row is not None else 0)
        # ...and make sure the device types in use are not filtered out
        types = selector._dev_type_btns.checkedDeviceTypes()
        types.update(s.device.type for s in current)
        selector._dev_type_btns.setCheckedDeviceTypes(types)
        selector.setCheckedProperties(current)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            dialog,
        )
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)

        lay = QVBoxLayout(dialog)
        lay.addWidget(selector)
        lay.addWidget(btns)
        dialog.resize(int(self.width() * 0.8), int(self.height() * 0.8))

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._set_properties(selector.checkedProperties())

    @Slot()
    def _remove_properties(self) -> None:
        """Drop the properties selected in the value table from all resolutionIDs."""
        if not (to_drop := set(self._value_table.selectedKeys())):
            return
        keep = [s for s in self._value_table.settings() if s.key() not in to_drop]
        self._set_properties(keep)

    def _set_properties(self, props: Iterable[DevicePropertySetting]) -> None:
        """Give every resolutionID exactly the (device, property) pairs in `props`.

        Values already stored for a pair are preserved; newly added pairs start
        from the value currently reported by the core.
        """
        keys = sorted({p.key() for p in props})
        for preset in self._resID_map.values():
            known = {
                (s.device_name, s.property_name): s.property_value
                for s in preset.settings
            }
            preset.settings = [
                Setting(
                    dev,
                    prop,
                    known[(dev, prop)]
                    if (dev, prop) in known
                    else self._core_value(dev, prop),
                )
                for dev, prop in keys
            ]
        self._refresh_property_view()
        self._update_clean_state()

    def _core_value(self, device: str, prop: str) -> str:
        """Current value of `device`-`prop` in the core, or a sensible default."""
        with suppress(Exception):
            return str(self._mmc.getProperty(device, prop))
        if (meta := self._prop_meta.get((device, prop))) is not None:
            return meta.default_value  # pragma: no cover
        return ""  # pragma: no cover

    # -------------- px table --------------

    @Slot()
    def _on_px_table_selection_changed(self) -> None:
        """Update the value table when the selection in the px table changes."""
        self._refresh_property_view()
        if (row := self._selected_row()) is None:
            return
        with signals_blocked(self._affine_table):
            self._affine_table.setValue(self._resID_map[row].affine)

    @Slot(QTableWidgetItem)
    def _on_resolutionID_name_changed(self, item: QTableWidgetItem) -> None:
        """Update the resolutionID name in the configuration map."""
        res_ID_row, res_ID_name = item.row(), item.text()

        # get the old res_ID_name
        old_res_ID_name = self._resID_map[res_ID_row].name

        # if the name is the same as the current one, return
        if res_ID_name == old_res_ID_name:
            return

        # if the name already exists, raise a warning and return
        if res_ID_name in self._value_to_dict(self.value()):
            warnings.warn(f"ResolutionID '{res_ID_name}' already exists.", stacklevel=2)
            self._px_table.table().item(res_ID_row, 0).setText(old_res_ID_name)
            return

        self._resID_map[res_ID_row].name = res_ID_name

    def _value_to_dict(
        self, value: list[PixelSizePreset]
    ) -> dict[str, PixelSizePreset]:
        """list[PixelSizePreset] to dict[PixelSizePreset.name: PixelSizePreset]."""
        return {rec.name: rec for rec in value}

    @Slot()
    def _on_px_value_changed(self) -> None:
        """Update the pixel size value in the configuration map."""
        spin = cast("QDoubleSpinBox", self.sender())
        table = cast("DataTable", self.sender().parent().parent())
        row = table.indexAt(spin.pos()).row()
        self._resID_map[row].pixel_size_um = spin.value()
        self._update_affine_transformations(spin.value())
        # these spin boxes are connected per row as rows are created, so they
        # aren't covered by the shared signal hookups in __init__
        self._update_clean_state()

    def _update_affine_transformations(self, px_value: float) -> None:
        """Update the affine transformations."""
        self._affine_table.setValue([px_value, 0.0, 0.0, 0.0, px_value, 0.0])
        affine = self._affine_table.value()
        if (row := self._selected_row()) is None:
            return
        self._resID_map[row].affine = affine

    @Slot()
    def _on_affine_value_changed(self) -> None:
        """Update the affine transformations in the configuration map."""
        if (row := self._selected_row()) is None:
            return
        self._resID_map[row].affine = self._affine_table.value()

    @Slot()
    def _on_px_table_value_changed(self) -> None:
        """Update the data of the pixel table when the value changes."""
        # if the table is empty clear the configuration map and the value table
        if not self._px_table.value():
            self._resID_map.clear()
            self._clear_property_view()
            self._affine_table.setValue(DEFAULT_AFFINE)
            return

        # if an item is deleted, remove it from the configuration map
        if len(self._px_table.value()) != len(self._resID_map):
            # get the resolutionIDs in the pixel table
            res_IDs = [rec[ID] for rec in self._px_table.value()]
            # get the resolutionIDs to delete
            to_delete: list[int] = [
                row
                for row in self._resID_map
                if self._resID_map[row].name not in res_IDs
            ]
            # delete the resolutionIDs from the configuration map
            for row in to_delete:
                del self._resID_map[row]

            # renumber the keys in the configuration map
            self._resID_map = {
                new_key: self._resID_map[old_key]
                for new_key, old_key in enumerate(self._resID_map)
            }

            # rows shifted, so what the value table shows may now belong to
            # a different resolutionID
            self._refresh_property_view()

    @Slot(QModelIndex, int, int)
    def _on_rows_inserted(self, parent: Any, start: int, end: int) -> None:
        """Set the data of a newly inserted resolutionID in the _px_table."""
        # "end" is the last row inserted.
        # if "self._resID_map[end]" exists, it means it is a row added by
        # "_on_sys_config_loaded" so we don't need to set the data and we return.
        if self._resID_map.get(end):
            return

        # Otherwise it is a new row added by clicking on the "add" button. All
        # resolutionIDs must describe the same properties, so the new one starts
        # from a copy of the first one's settings (values included).
        props = list(self._resID_map[0].settings) if self._resID_map else []
        self._resID_map[end] = PixelSizePreset(NEW, props)

        self._connect_px_spinbox(end)

        # select the added row
        self._px_table._table.selectRow(end)
        self._update_clean_state()

    # -------------- apply --------------

    @Slot()
    def _on_apply(self) -> None:
        """Update the current pixel size configurations."""
        # check if there are errors in the pixel configurations
        if self._check_for_errors():
            return

        # delete all the pixel size configurations
        for resolutionID in self._mmc.getAvailablePixelSizeConfigs():
            self._mmc.deletePixelSizeConfig(resolutionID)

        # create the new pixel size configurations
        px_groups = PixelSizeGroup(presets=self._value_to_dict(self.value()))
        px_groups.apply_to_core(self._mmc)
        # what's in the widget is now what's in the core
        self.setClean()
        self.close()

    def _check_for_errors(self) -> bool:
        """Check for errors in the pixel configurations."""
        resolutionIDs = [rec[ID] for rec in self._px_table.table().iterRecords()]

        # check that all the resolutionIDs have a valid name
        for resolutionID in resolutionIDs:
            if not resolutionID:
                return self._show_error_message("All resolutionIDs must have a name.")

        # check if there are duplicated resolutionIDs
        if [item for item, count in Counter(resolutionIDs).items() if count > 1]:
            return self._show_error_message(
                "There are duplicated resolutionIDs: "
                f"{list({x for x in resolutionIDs if resolutionIDs.count(x) > 1})}"
            )

        # check that each resolutionID have at least one property
        if not all(self._resID_map[row].settings for row in range(len(resolutionIDs))):
            return self._show_error_message(
                "Each resolutionID must have at least one property."
            )

        return False

    def _show_error_message(self, msg: str) -> bool:
        """Show an error message."""
        response = QMessageBox.critical(
            self, "Configuration Error", msg, QMessageBox.StandardButton.Close
        )
        return bool(response == QMessageBox.StandardButton.Close)

    def _disconnect(self) -> None:
        self._mmc.events.systemConfigurationLoaded.disconnect(
            self._on_sys_config_loaded
        )


class _PixelTable(DataTableWidget):
    """A table to add and display the pixel size configurations."""

    ID = TextColumn(key=ID, header="ResolutionID", default=NEW, is_row_selector=False)
    VALUE = FloatColumn(
        key=PX, header="pixel value [µm]", default=0, is_row_selector=False, decimals=3
    )

    def __init__(self, rows: int = 0, parent: QWidget | None = None):
        super().__init__(rows, parent)

        self._toolbar.removeAction(self.act_check_all)
        self._toolbar.removeAction(self.act_check_none)
        self._toolbar.actions()[2].setVisible(False)  # separator

        # the table already grows with the layout here, so the drag-to-resize
        # grip would only add a stray handle above the affine table
        self._resize_grip.hide()

        h_header = cast("QHeaderView", self._table.horizontalHeader())
        h_header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        h_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)


class AffineTable(QTableWidget):
    """A table to display the affine transformations matrix."""

    valueChanged = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(20)
        self.verticalHeader().setVisible(False)

        self.setColumnCount(3)
        self.setRowCount(3)

        # add a spinbox in each cell of the table
        self._add_table_spinboxes()
        self.setValue(DEFAULT_AFFINE)

    def sizeHint(self) -> Any:
        sz = self.minimumSizeHint()
        rc = self.rowCount()
        sz.setHeight(self.rowHeight(0) * rc + (rc - 1))
        return sz

    def _add_table_spinboxes(self) -> None:
        """Add a spinbox in each cell of the table."""
        for row, col in itertools.product(range(3), range(3)):
            spin = QDoubleSpinBox()
            spin.setRange(-100000, 100000)
            spin.setDecimals(3)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            self.setCellWidget(row, col, spin)
            # disable the spinboxes in the last row
            if row == 2:
                spin.setReadOnly(True)
                spin.setEnabled(False)
                # set the value of the last row to 1.0
                if col == 2:
                    spin.setValue(1.0)
            # connect the valueChanged signal of the spinboxes to global valueChanged
            else:
                spin.valueChanged.connect(self.valueChanged)

    def value(self) -> tuple[float, float, float, float, float, float]:
        """Return the current widget value describing the affine transformation."""
        value: list[float] = []
        for row, col in itertools.product(range(2), range(3)):
            spin = cast("QDoubleSpinBox", self.cellWidget(row, col))
            value.append(spin.value())
        return tuple(value)  # type: ignore

    def setValue(self, value: Sequence[float]) -> None:
        """Set the current widget value describing the affine transformation."""
        if len(value) != 6:
            raise ValueError("The affine transformation must have 6 values.")

        for row, col in itertools.product(range(2), range(3)):
            spin = cast("QDoubleSpinBox", self.cellWidget(row, col))
            spin.setValue(value[row * 3 + col])


class _PropertyValueModel(QAbstractTableModel):
    """Table model over the settings of a single pixel size configuration.

    One row per (device, property), a read-only name column and an editable
    value column. The value column exposes each `DevicePropertySetting` under
    `Qt.ItemDataRole.UserRole`, which is what `PropertySettingDelegate` needs to
    paint and edit it with the right native control.
    """

    HEADERS = ("Property", "Value")

    valueEdited = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings: list[DevicePropertySetting] = []

    # -- data access ---------------------------------------------------------

    def settings(self) -> list[DevicePropertySetting]:
        """Return the settings currently displayed (with any edited values)."""
        return list(self._settings)

    def settingAt(self, row: int) -> DevicePropertySetting:
        """Return the setting displayed on `row`."""
        return self._settings[row]

    def setSettings(self, settings: Iterable[DevicePropertySetting]) -> None:
        """Replace the displayed settings."""
        self.beginResetModel()
        self._settings = list(settings)
        self.endResetModel()

    # -- QAbstractTableModel -------------------------------------------------

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._settings)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self.HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return self.HEADERS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():  # pragma: no cover
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 1 and not self._settings[index.row()].is_read_only:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():  # pragma: no cover
            return None
        setting = self._settings[index.row()]
        if index.column() == 0:
            if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
                return setting.display_name()
            if role == Qt.ItemDataRole.DecorationRole:
                if (key := setting.iconify_key) is not None:
                    return key.icon()
            return None
        # value column: UserRole is what PropertySettingDelegate paints from
        if role == Qt.ItemDataRole.UserRole:
            return setting
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return setting.value
        return None

    def setData(
        self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        if not index.isValid() or index.column() != 1:  # pragma: no cover
            return False
        if role != Qt.ItemDataRole.EditRole:  # pragma: no cover
            return False
        setting = self._settings[index.row()]
        if (new := str(value)) == setting.value:
            return False
        setting.value = new
        self.dataChanged.emit(index, index)
        self.valueEdited.emit()
        return True


class _PropertyValueTableView(QTableView):
    """View of the property values of the selected pixel size configuration."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setItemDelegateForColumn(1, PropertySettingDelegate(self))
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        if vh := self.verticalHeader():
            vh.setVisible(False)

    def setModel(self, model: QAbstractItemModel | None) -> None:
        """Set the model and size the columns to it.

        Resize modes only stick once the header has sections, i.e. after the
        model is in place.
        """
        super().setModel(model)
        if model is not None and (hh := self.horizontalHeader()):
            hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    def mousePressEvent(self, event: Any) -> None:
        """Single left-click on a value opens its editor (spreadsheet-like).

        Matches the behavior of ConfigGroupsEditor's presets table.
        """
        super().mousePressEvent(event)
        mods = event.modifiers()
        extend = Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier
        if event.button() == Qt.MouseButton.LeftButton and not (mods & extend):
            idx = self.indexAt(event.pos())
            if (
                idx.isValid()
                and idx.column() == 1
                and self.state() != QTableView.State.EditingState
            ):
                self.edit(idx)


class _PropertyValueTable(QWidget):
    """Toolbar + table for the properties of the selected pixel size configuration.

    This is where the whole property side of a configuration is managed:
    `act_edit_props` opens the picker that chooses *which* properties the
    configurations are made of, `act_remove_props` drops the selected ones, and
    the table itself edits the value each of them takes.
    """

    valueEdited = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._model = _PropertyValueModel(self)
        self._model.valueEdited.connect(self.valueEdited)

        self.view = _PropertyValueTableView(self)
        self.view.setModel(self._model)

        # Same action (and icon) as ConfigGroupsEditor's "Edit Properties".
        self.act_edit_props: QAction = QAction(
            StandardIcon.PROPERTY_ADD.icon(), "Edit Properties", self
        )
        self.act_edit_props.setToolTip(
            "Select the device properties that define the pixel configurations"
        )
        self.act_edit_props.setEnabled(False)

        self.act_remove_props: QAction = QAction(
            StandardIcon.DELETE.icon(), "Remove Properties", self
        )
        self.act_remove_props.setToolTip(
            "Remove the selected properties from every resolutionID"
        )
        self.act_remove_props.setEnabled(False)

        self._toolbar = QToolBar(self)
        self._toolbar.setFloatable(False)
        # match the icon size of the resolutionID table's toolbar, so the two
        # panels line up
        self._toolbar.setIconSize(QSize(22, 22))
        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._toolbar.addWidget(spacer)
        self._toolbar.addAction(self.act_edit_props)
        self._toolbar.addAction(self.act_remove_props)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        layout.addWidget(self._toolbar)
        layout.addWidget(self.view)

        # `setModel` above created the selection model
        if sm := self.view.selectionModel():
            sm.selectionChanged.connect(self._update_remove_action)
        self._model.modelReset.connect(self._update_remove_action)

    def settings(self) -> list[DevicePropertySetting]:
        """Return the settings currently displayed (with any edited values)."""
        return self._model.settings()

    def setSettings(self, settings: Iterable[DevicePropertySetting]) -> None:
        """Replace the displayed settings."""
        self._model.setSettings(settings)

    def selectedKeys(self) -> list[tuple[str, str]]:
        """Return the (device, property) pairs of the selected rows."""
        if (sm := self.view.selectionModel()) is None:  # pragma: no cover
            return []
        return [self._model.settingAt(idx.row()).key() for idx in sm.selectedRows()]

    @Slot()
    def _update_remove_action(self) -> None:
        self.act_remove_props.setEnabled(bool(self.selectedKeys()))
