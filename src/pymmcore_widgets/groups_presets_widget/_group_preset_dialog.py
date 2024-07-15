from __future__ import annotations

from pymmcore_plus import CMMCorePlus
from pymmcore_plus.model import ConfigGroup, ConfigPreset, Setting
from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from superqt.utils import signals_blocked

from ._properties_groupboxes import (
    _CameraGroupBox,
    _LightPathGroupBox,
    _ObjectiveGroupBox,
    _OtherGroupBox,
    _PropertiesGroupBox,
    _StageGroupBox,
)


class ListWidget(QGroupBox):
    itemSelectionChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None, title: str = "") -> None:
        super().__init__(parent)
        self.setTitle(title)

        self.list = QListWidget()
        self.list.setEditTriggers(
            QListWidget.EditTrigger.DoubleClicked
            | QListWidget.EditTrigger.SelectedClicked
        )
        self.list.itemSelectionChanged.connect(self._on_selection_changed)

        # Second column: buttons ------------------------------------
        self.new = QPushButton("New...")
        self.remove = QPushButton("Remove...")
        self.duplicate = QPushButton("Duplicate...")
        self.activate = QPushButton("Set Active")

        button_column = QVBoxLayout()
        button_column.addWidget(self.new)
        button_column.addWidget(self.remove)
        button_column.addWidget(self.duplicate)
        button_column.addWidget(self.activate)
        button_column.addStretch()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)
        layout.addWidget(self.list)
        layout.addLayout(button_column)

    def _on_selection_changed(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self.itemSelectionChanged.emit(item.text())


class GroupPresetDialog(QWidget):
    def __init__(
        self, parent: QWidget | None = None, mmcore: CMMCorePlus | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Optical Configuration")

        self._mmc = mmcore or CMMCorePlus.instance()
        self._model_map: dict[str, ConfigGroup] = {}

        # Groups -----------------------------------------------------
        self._active_group_item_name = ""
        self._group_list = ListWidget(self, title="Groups")
        self._group_list.duplicate.hide()
        self._group_list.activate.hide()
        self._group_list.list.addItems(self._mmc.getAvailableConfigGroups())
        self._group_list.itemSelectionChanged.connect(self._load_group)
        self._group_list.list.currentTextChanged.connect(self._on_group_name_changed)
        self._group_list.list.itemChanged.connect(self._on_group_item_changed)
        self._group_list.new.clicked.connect(self._add_group)
        self._group_list.remove.clicked.connect(self._remove_group_dialog)

        # Presets -----------------------------------------------------
        self._active_preset_item_name = ""
        self._preset_list = ListWidget(self, title="Presets")
        self._preset_list.itemSelectionChanged.connect(self._on_selection_changed)
        self._preset_list.list.currentTextChanged.connect(self._on_preset_name_changed)
        self._preset_list.list.itemChanged.connect(self._on_preset_item_changed)
        self._preset_list.new.clicked.connect(self._add_preset)
        self._preset_list.remove.clicked.connect(self._remove_preset_dialog)
        self._preset_list.duplicate.clicked.connect(self._duplicate_preset)
        self._preset_list.activate.clicked.connect(self._activate_preset)

        # Properties -----------------------------------------------------
        self._light_group = _LightPathGroupBox(self, mmcore=self._mmc)
        self._light_group.valueChanged.connect(self._on_value_changed)
        self._camera_group = _CameraGroupBox(self, mmcore=self._mmc)
        self._camera_group.valueChanged.connect(self._on_value_changed)
        self._stage_group = _StageGroupBox(self, mmcore=self._mmc)
        self._stage_group.valueChanged.connect(self._on_value_changed)
        self._obj_group = _ObjectiveGroupBox(self, mmcore=self._mmc)
        self._obj_group.valueChanged.connect(self._on_value_changed)
        self._other_group = _OtherGroupBox(self, mmcore=self._mmc)
        self._other_group.valueChanged.connect(self._on_value_changed)

        self.PROP_GROUPS: list[_PropertiesGroupBox] = [
            self._light_group,
            self._camera_group,
            self._stage_group,
            self._obj_group,
            self._other_group,
        ]

        group_splitter = QSplitter(Qt.Orientation.Vertical, self)
        group_splitter.setContentsMargins(0, 0, 0, 0)
        group_splitter.addWidget(self._light_group)
        group_splitter.addWidget(self._camera_group)
        group_splitter.addWidget(self._stage_group)
        group_splitter.addWidget(self._obj_group)
        group_splitter.addWidget(self._other_group)
        group_splitter.setStretchFactor(0, 3)
        group_splitter.setStretchFactor(1, 3)
        group_splitter.setStretchFactor(2, 3)
        group_splitter.setStretchFactor(3, 3)
        group_splitter.setStretchFactor(4, 3)

        # Buttons -----------------------------------------------------
        self._apply_button = QPushButton("Apply Changes")
        self._apply_button.clicked.connect(self._apply)
        self._save_button = QPushButton("Save")
        self._save_button.clicked.connect(self._save_cfg)
        self._load_button = QPushButton("Load")
        self._load_button.clicked.connect(self._load_cfg)

        # Layout -----------------------------------------------------
        left_layout = QVBoxLayout()
        left_layout.addWidget(self._group_list)
        left_layout.addWidget(self._preset_list)

        top_layout = QHBoxLayout()
        top_layout.addLayout(left_layout)
        top_layout.addWidget(group_splitter, 1)

        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()
        bottom_layout.addWidget(self._save_button)
        bottom_layout.addWidget(self._load_button)
        bottom_layout.addWidget(self._apply_button)

        main_layout = QVBoxLayout(self)
        main_layout.addLayout(top_layout)
        main_layout.addLayout(bottom_layout)

        self.resize(1080, 920)

        # core connections
        self._mmc.events.systemConfigurationLoaded.connect(self._on_sys_config_loaded)

        self._on_sys_config_loaded()

    def value(self) -> dict[str, ConfigGroup]:
        """Return the current state of the widget."""
        return self._model_map

    def _on_sys_config_loaded(self) -> None:
        """Update the widget when the system configuration is loaded."""
        self._reset()
        groups = self._mmc.getAvailableConfigGroups()
        for group_name in groups:
            group = ConfigGroup.create_from_core(self._mmc, group_name)
            self._model_map[group_name] = group
            item = self._create_editable_item(group_name)
            self._group_list.list.addItem(item)
        self._group_list.list.setCurrentRow(0)

    def _reset(self) -> None:
        self._model_map.clear()

        for prop_wdg in self.PROP_GROUPS:
            prop_wdg.setValue([])
            prop_wdg.setChecked(False)

        with signals_blocked(self._group_list):
            self._group_list.list.clear()
        with signals_blocked(self._preset_list):
            self._preset_list.list.clear()

    def _load_group(self, group: str) -> None:
        with signals_blocked(self._preset_list):
            self._preset_list.list.clear()
            for n in self._model_map[group].presets:
                item = self._create_editable_item(n)
                self._preset_list.list.addItem(item)
        self._preset_list.list.setCurrentRow(0)

    def _add_group(self) -> None:
        self._group_list.list.clearSelection()

        i = 1
        new_name = "NewGroup"
        while new_name in self._model_map:
            new_name = f"NewGroup {i}"
            i += 1
        self._model_map[new_name] = ConfigGroup(name=new_name)
        item = self._create_editable_item(new_name)
        self._group_list.list.addItem(item)
        self._group_list.list.setCurrentItem(item)

        # activate the group line and make it editable by the user
        self._group_list.list.editItem(item)

        self._add_preset(edit=False)

        # clear the properties
        for prop_wdg in self.PROP_GROUPS:
            prop_wdg.setValue([])

    def _remove_group_dialog(self) -> None:
        if (current := self._group_list.list.currentItem()) is None:
            return
        if self._show_confirmation_dialog(
            self, "Remove Group", f"Are you sure you want to remove {current.text()!r}?"
        ):
            self._model_map.pop(current.text())
            with signals_blocked(self._preset_list):
                while self._preset_list.list.count():
                    self._preset_list.list.takeItem(0)
            self._group_list.list.takeItem(self._group_list.list.currentRow())

    def _load_preset(self, name: str) -> None:
        group = self._group_list.list.currentItem().text()
        settings = self._model_map[group].presets[name].settings
        for prop_wdg in self.PROP_GROUPS:
            prop_wdg.setValue(settings)

    def _add_preset(self, *, edit: bool = True) -> None:
        group = self._group_list.list.currentItem().text()
        i = 1
        new_name = "NewPreset"
        while new_name in self._model_map[group].presets:
            new_name = f"NewPreset {i}"
            i += 1
        self._model_map[group].presets[new_name] = ConfigPreset(name=new_name)
        item = self._create_editable_item(new_name)
        self._preset_list.list.addItem(item)
        self._preset_list.list.setCurrentItem(item)
        # activate the preset line and make it editable by the user
        if edit:
            self._preset_list.list.editItem(item)

    def _remove_preset_dialog(self) -> None:
        if (current := self._preset_list.list.currentItem()) is None:
            return
        if self._show_confirmation_dialog(
            self,
            "Remove Preset",
            f"Are you sure you want to remove {current.text()!r}?",
        ):
            group = self._group_list.list.currentItem().text()
            self._model_map[group].presets.pop(current.text())
            self._preset_list.list.takeItem(self._preset_list.list.currentRow())

    def _duplicate_preset(self) -> None:
        if (current := self._preset_list.list.currentItem()) is None:
            return
        selected = current.text()
        new_name = f"{selected} (Copy)"
        i = 1
        group = self._group_list.list.currentItem().text()
        while new_name in self._model_map[group].presets:
            new_name = f"{selected} (Copy {i})"
            i += 1
        settings = self._model_map[group].presets[selected].settings
        group = self._group_list.list.currentItem().text()
        self._model_map[group].presets[new_name] = ConfigPreset(
            name=new_name, settings=settings
        )
        item = self._create_editable_item(new_name)
        self._preset_list.list.addItem(item)
        self._preset_list.list.setCurrentItem(item)  # select it

    def _activate_preset(self) -> None:
        if (current := self._preset_list.list.currentItem()) is None:
            return
        group = self._group_list.list.currentItem().text()
        for dev, prop, value in self._model_map[group].presets[current.text()].settings:
            self._mmc.setProperty(dev, prop, value)

    def _create_editable_item(self, name: str) -> QListWidgetItem:
        item = QListWidgetItem(name)
        item.setFlags(
            Qt.ItemFlag.ItemIsEditable
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEnabled
        )
        return item

    def _show_confirmation_dialog(self, parent: QWidget, title: str, text: str) -> bool:
        return (  # type: ignore
            QMessageBox.question(
                parent,
                title,
                text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def _on_group_name_changed(self, text: str) -> None:
        self._active_group_item_name = text

    def _on_selection_changed(self) -> None:
        if item := self._preset_list.list.currentItem():
            self._load_preset(item.text())

    def _on_group_item_changed(self, item: QListWidgetItem) -> None:
        new_text = item.text()
        previous_text = self._active_group_item_name
        if new_text in self._model_map and new_text != previous_text:
            QMessageBox.warning(self, "Duplicate Item", f"{new_text!r} already exists.")
            item.setText(previous_text)
        else:
            # update the group name in the model. We need to update the name arg
            current = self._model_map[self._active_group_item_name]
            current.name = new_text
            self._model_map.pop(previous_text)
            self._model_map[new_text] = current
            self._active_group_item_name = new_text

    def _on_preset_name_changed(self, text: str) -> None:
        self._active_preset_item_name = text

    def _on_preset_item_changed(self, item: QListWidgetItem) -> None:
        new_text = item.text()
        previous_text = self._active_preset_item_name
        group = self._group_list.list.currentItem().text()
        if new_text in self._model_map[group].presets and new_text != previous_text:
            QMessageBox.warning(self, "Duplicate Item", f"{new_text!r} already exists.")
            item.setText(previous_text)
        else:
            current = self._model_map[group].presets[self._active_preset_item_name]
            current.name = new_text
            self._model_map[group].presets.pop(previous_text)
            self._model_map[group].presets[new_text] = current
            self._active_preset_item_name = new_text

    def _on_value_changed(self) -> None:
        if (current := self._preset_list.list.currentItem()) is None:
            return
        current_name = current.text()
        group = self._group_list.list.currentItem().text()
        self._model_map[group].presets[current_name].settings = self._current_settings()

        from rich import print

        print(self._model_map)

    def _current_settings(self) -> list[Setting]:
        tmp = {}
        for prop_wdg in self.PROP_GROUPS:
            if prop_wdg.isChecked():
                tmp.update({(dev, prop): val for dev, prop, val in prop_wdg.value()})
        return [Setting(*k, v) for k, v in tmp.items()]

    def _validate_all_presets(self) -> bool:
        """Make sure all the groups in the _model_map have presets with settings."""
        for group in self._model_map.values():
            for preset in group.presets.values():
                if not preset.settings:
                    QMessageBox.critical(
                        self,
                        "Invalid Presets",
                        f"All presets in the '{group.name}' group must have at least "
                        "one property selected.",
                        QMessageBox.StandardButton.Ok,
                    )
                    return False
        return True

    def _apply(self) -> None:
        """Apply the changes to the core."""
        if not self._validate_all_presets():
            return

        # delete all groups in the core that are not in the _model_map
        for group_name in self._mmc.getAvailableConfigGroups():
            if group_name not in self._model_map:
                self._mmc.deleteConfigGroup(group_name)

        # update the core with the new groups
        for group in self._model_map.values():
            # if the group is already in the core, update only if it is different
            if group.name in self._mmc.getAvailableConfigGroups():
                current_group = ConfigGroup.create_from_core(self._mmc, group.name)
                # if the group is the same, continue
                if current_group == group:
                    continue
                # delete the currentb group in the core
                self._mmc.deleteConfigGroup(group.name)
            # add the new group to the core
            group.apply_to_core(self._mmc)

    def _save_cfg(self) -> None:
        """Open file dialog to save the current configuration."""
        (filename, _) = QFileDialog.getSaveFileName(
            self,
            "Save Micro-Manager Configuration.",
            self._mmc.systemConfigurationFile(),
            "cfg(*.cfg)",
        )
        if filename:
            self._mmc.saveSystemConfiguration(
                filename if str(filename).endswith(".cfg") else f"{filename}.cfg"
            )

    def _load_cfg(self) -> None:
        """Open file dialog to select and load a config file."""
        (filename, _) = QFileDialog.getOpenFileName(
            self, "Select a Micro-Manager configuration file", "", "cfg(*.cfg)"
        )
        if filename:
            self._mmc.unloadAllDevices()
            self._mmc.loadSystemConfiguration(filename)
