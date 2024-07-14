from __future__ import annotations

from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QAbstractScrollArea,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pymmcore_widgets._core import load_system_config
from pymmcore_widgets._presets_widget import PresetsWidget
from pymmcore_widgets._property_widget import PropertyWidget

UNNAMED_PRESET = "NewPreset"


class _GroupsPresetsTable(QTableWidget):
    """Set table properties for Group and Preset TableWidget."""

    def __init__(self) -> None:
        super().__init__()
        hdr = self.horizontalHeader()
        hdr.setStretchLastSection(True)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignHCenter)
        vh = self.verticalHeader()
        vh.setVisible(False)
        vh.setSectionResizeMode(vh.ResizeMode.Fixed)
        vh.setDefaultSectionSize(24)
        self.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setColumnCount(2)
        self.setHorizontalHeaderLabels(["Group", "Preset"])


class GroupPresetTableWidget(QGroupBox):
    """A Widget to create, edit, delete and set micromanager group presets.

    Parameters
    ----------
    parent : QWidget | None
        Optional parent widget. By default, None.
    mmcore : CMMCorePlus | None
        Optional `CMMCorePlus` micromanager core.
        By default, None. If not specified, the widget will use the active
        (or create a new) `CMMCorePlus.instance()`.
    """

    def __init__(
        self, *, parent: QWidget | None = None, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__(parent=parent)

        self._mmc = mmcore or CMMCorePlus.instance()

        # Layout ----------------------------------------------------------

        # table
        self._table = _GroupsPresetsTable()

        # buttons
        self.edit_btn = QPushButton(text="Edit")
        # self.edit_btn.clicked.connect(self._edit_cfg)
        self.save_btn = QPushButton(text="Save")
        self.save_btn.clicked.connect(self._save_cfg)
        self.load_btn = QPushButton(text="Load")
        self.load_btn.clicked.connect(self._load_cfg)
        btns_layout = QHBoxLayout()
        btns_layout.setSpacing(5)
        btns_layout.setContentsMargins(0, 0, 0, 0)
        btns_layout.addStretch()
        btns_layout.addWidget(self.edit_btn)
        btns_layout.addWidget(self.save_btn)
        btns_layout.addWidget(self.load_btn)

        # main
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(5)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.addWidget(self._table)
        main_layout.addLayout(btns_layout)

        # Connections -----------------------------------------------------

        # widget
        # self.table_wdg.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.edit_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.save_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.load_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # core
        self._mmc.events.systemConfigurationLoaded.connect(self._populate_table)
        # self._mmc.events.configGroupDeleted.connect(self._on_group_deleted)
        # self._mmc.events.configDefined.connect(self._on_new_group_preset)

        self.destroyed.connect(self._disconnect)

        self._populate_table()

    def _on_system_cfg_loaded(self) -> None:
        self._populate_table()

    def _reset_table(self) -> None:
        self._disconnect_wdgs()
        self._table.clearContents()
        self._table.setRowCount(0)

    def _disconnect_wdgs(self) -> None:
        for r in range(self._table.rowCount()):
            wdg = self._table.cellWidget(r, 1)
            if isinstance(wdg, PresetsWidget):
                wdg._disconnect()

    def _populate_table(self) -> None:
        self._reset_table()
        if groups := self._mmc.getAvailableConfigGroups():
            for row, group in enumerate(groups):
                self._table.insertRow(row)
                self._table.setItem(row, 0, QTableWidgetItem(str(group)))
                wdg = self._create_group_widget(group)
                self._table.setCellWidget(row, 1, wdg)
                if isinstance(wdg, PresetsWidget):
                    wdg = wdg._combo
                elif isinstance(wdg, PropertyWidget):
                    wdg = wdg._value_widget  # type: ignore

        # resize to contents the table
        self._table.resizeColumnsToContents()
        self.resize(self.sizeHint())

    def _get_cfg_data(self, group: str, preset: str) -> tuple[str, str, str, int]:
        # Return last device-property-value for the preset and the
        # total number of device-property-value included in the preset.
        data = list(self._mmc.getConfigData(group, preset))
        if not data:
            return "", "", "", 0
        assert len(data), "No config data"
        dev, prop, val = data[-1]
        return dev, prop, val, len(data)

    def _create_group_widget(self, group: str) -> PresetsWidget | PropertyWidget:
        """Return a widget depending on presets and device-property."""
        # get group presets
        presets = list(self._mmc.getAvailableConfigs(group))

        if not presets:
            return  # type: ignore

        # use only the first preset since device
        # and property are the same for the presets
        device, prop, _, dev_prop_val_count = self._get_cfg_data(group, presets[0])

        if len(presets) > 1 or dev_prop_val_count > 1 or dev_prop_val_count == 0:
            return PresetsWidget(group)
        else:
            return PropertyWidget(device, prop)

    def _save_cfg(self) -> None:
        (filename, _) = QFileDialog.getSaveFileName(
            self, "Save Micro-Manager Configuration."
        )
        if filename:
            self._mmc.saveSystemConfiguration(
                filename if str(filename).endswith(".cfg") else f"{filename}.cfg"
            )

    def _load_cfg(self) -> None:
        """Open file dialog to select a config file."""
        (filename, _) = QFileDialog.getOpenFileName(
            self, "Select a Micro-Manager configuration file", "", "cfg(*.cfg)"
        )
        if filename:
            load_system_config(filename, mmcore=self._mmc)

    def _disconnect(self) -> None:
        self._mmc.events.systemConfigurationLoaded.disconnect(self._populate_table)
        # self._mmc.events.configGroupDeleted.disconnect(self._on_group_deleted)
        # self._mmc.events.configDefined.disconnect(self._on_new_group_preset)
