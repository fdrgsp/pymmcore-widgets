from __future__ import annotations

import contextlib
from typing import MutableMapping

from pymmcore_plus import CMMCorePlus
from pymmcore_plus.model import ConfigGroup, ConfigPreset
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

from ._group_preset_dialog import GroupPresetDialog


class _GroupsPresetsTable(QTableWidget):
    """Set table properties for Group and Preset TableWidget."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
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

        # widgets ---------------------------------------------------------

        # table
        self._table = _GroupsPresetsTable(self)

        # groups presets dialog
        self._groups_presets_dialog = GroupPresetDialog(self, self._mmc)
        self._groups_presets_dialog.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.Dialog
        )

        # buttons
        self.edit_btn = QPushButton(text="Edit")
        self.edit_btn.clicked.connect(self._edit_cfg)
        self.save_btn = QPushButton(text="Save")
        self.save_btn.clicked.connect(self._save_cfg)
        self.load_btn = QPushButton(text="Load")
        self.load_btn.clicked.connect(self._load_cfg)

        # Layout ----------------------------------------------------------

        # buttons
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
        self.edit_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.save_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.load_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.destroyed.connect(self._disconnect)

        # core
        self._mmc.events.systemConfigurationLoaded.connect(self._populate_table)
        self._mmc.events.configGroupDeleted.connect(self._populate_table)
        self._mmc.events.configDefined.connect(self._populate_table)

        self._populate_table()

        self.resize(self.minimumSizeHint())

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

        # resize to contents
        self._table.resizeColumnToContents(0)
        self.resize(self.sizeHint())

    def _reset_table(self) -> None:
        self._disconnect_wdgs()
        self._table.clearContents()
        self._table.setRowCount(0)

    def _disconnect_wdgs(self) -> None:
        for r in range(self._table.rowCount()):
            wdg = self._table.cellWidget(r, 1)
            if isinstance(wdg, PresetsWidget):
                with contextlib.suppress(Exception):
                    wdg._disconnect()

    def _create_group_widget(
        self, group_name: str
    ) -> PresetsWidget | PropertyWidget | None:
        """Return a widget depending on presets and device-property."""
        group = ConfigGroup.create_from_core(self._mmc, group_name)

        presets = group.presets

        if not presets:
            return None

        if len(presets) > 1:
            return PresetsWidget(group_name)

        # get preset with most settings (since could be different between presets)
        preset_key, preset_count = self._get_preset_with_most_settings(presets)
        if preset_count > 1:
            return PresetsWidget(group_name)

        settings = presets[preset_key].settings[0]
        return PropertyWidget(settings.device_name, settings.property_name)

    def _get_preset_with_most_settings(
        self, presets: MutableMapping[str, ConfigPreset]
    ) -> tuple[str, int]:
        max_settings_count = 0
        key_with_most_settings = ""
        for key, preset in presets.items():
            if len(preset.settings) > max_settings_count:
                max_settings_count = len(preset.settings)
                key_with_most_settings = key
        return key_with_most_settings, max_settings_count

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

    def _edit_cfg(self) -> None:
        if self._groups_presets_dialog.isHidden():
            self._groups_presets_dialog.show()
        else:
            self._groups_presets_dialog.raise_()
            self._groups_presets_dialog.activateWindow()

    def _disconnect(self) -> None:
        self._mmc.events.systemConfigurationLoaded.disconnect(self._populate_table)
        self._mmc.events.configGroupDeleted.disconnect(self._populate_table)
        self._mmc.events.configDefined.disconnect(self._populate_table)
