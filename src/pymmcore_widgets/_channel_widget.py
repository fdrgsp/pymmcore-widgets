from typing import Optional, Union

from pymmcore_plus import CMMCorePlus, DeviceType
from qtpy.QtWidgets import QComboBox, QVBoxLayout, QWidget

from ._presets_widget import PresetsWidget
from ._util import ComboMessageBox


class ChannelWidget(QWidget):
    """
    A QComboBox-based widget to select which micromanager channel configuration to use.

    Parameters
    ----------
    channel_group : Optional[str]
        Name of the micromanager group defining the microscope channels. By default,
        it will be guessed using the `CMMCorePlus` [getOrGuessChannelGroup]()
        method and a choice dialog will be presented if there are multiple options.
        This method looks for a group configuration name matching the default regex
        `re.compile("(chan{1,2}(el)?|filt(er)?)s?", re.IGNORECASE)`.
        A different string/regex can be set using the `CMMCorePlus`
        [channelGroup_pattern]() setter method.
    parent : Optional[QWidget]
        Optional parent widget. By default, None.
    mmcore: Optional[CMMCorePlus]
        Optional `CMMCorePlus` micromanager core.
        By default, None. If not specified, the widget will use the active
        (or create a new) `CMMCorePlus.instance()`.
    """

    def __init__(
        self,
        channel_group: Optional[str] = None,
        parent: Optional[QWidget] = None,
        *,
        mmcore: Optional[CMMCorePlus] = None,
    ) -> None:

        super().__init__(parent)
        self._mmc = mmcore or CMMCorePlus.instance()

        self._channel_group = channel_group or self._get_channel_group()

        self.channel_wdg = self._create_channel_widget(self._channel_group)

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)
        self.layout().addWidget(self.channel_wdg)

        self._mmc.events.systemConfigurationLoaded.connect(self._on_sys_cfg_loaded)
        self._mmc.events.channelGroupChanged.connect(self._on_channel_group_changed)
        self._mmc.events.configSet.connect(self._on_channel_set)

        # presetDeleted signal is handled by the PresetsWidget
        self._mmc.events.configDefined.connect(self._on_new_group_preset)
        self._mmc.events.configGroupDeleted.connect(self._on_group_deleted)

        self.destroyed.connect(self._disconnect_from_core)
        self._on_sys_cfg_loaded()

    def _get_channel_group(self) -> Union[str, None]:
        candidates = self._mmc.getOrGuessChannelGroup()
        if len(candidates) == 1:
            return candidates[0]
        elif candidates:
            dialog = ComboMessageBox(candidates, "Select Channel Group:", self)
            if dialog.exec_() == dialog.DialogCode.Accepted:
                return str(dialog.currentText())
        return None  # pragma: no cover

    def _create_channel_widget(
        self, channel_group: Optional[str]
    ) -> Union[PresetsWidget, QComboBox]:
        if channel_group:
            channel_wdg = PresetsWidget(channel_group)
            self._mmc.setChannelGroup(channel_group)
        else:
            channel_wdg = QComboBox()
            channel_wdg.setEnabled(False)
        return channel_wdg

    def _on_sys_cfg_loaded(self) -> None:
        channel_group = self._channel_group or self._get_channel_group()
        if channel_group is not None:
            self._mmc.setChannelGroup(channel_group)
            # if the channel_group name is the same as the one in the previously
            # loaded cfg and it contains different presets, the 'channelGroupChanged'
            # signal is not emitted and we get a ValueError. So we need to call:
            self._on_channel_group_changed(channel_group)

    def _on_channel_set(self, group: str, preset: str) -> None:
        ch = self._mmc.getChannelGroup()
        if group != ch:
            return  # pragma: no cover
        for d in self._mmc.getConfigData(ch, preset):
            _dev = d[0]
            _type = self._mmc.getDeviceType(_dev)
            if _type is DeviceType.Shutter:
                self._mmc.setProperty("Core", "Shutter", _dev)
                break

    def _on_channel_group_changed(self, new_channel_group: str) -> None:
        """When Channel group is changed, recreate combo."""
        _wdg = QWidget()
        self.channel_wdg.setParent(_wdg)
        self.channel_wdg.deleteLater()
        self._update_widget(new_channel_group)

    def _on_new_group_preset(self, group: str) -> None:
        if group == self._channel_group:
            self._on_channel_group_changed(group)
        elif not self._mmc.getChannelGroup():
            if new_channel_group := self._get_channel_group():
                self._on_channel_group_changed(new_channel_group)
            else:
                self._on_channel_group_changed("")

    def _on_group_deleted(self, group: str) -> None:
        if group == self._channel_group:
            self._on_channel_group_changed("")

    def _update_widget(self, channel_group: str) -> None:
        self.channel_wdg = self._create_channel_widget(channel_group)
        self.layout().addWidget(self.channel_wdg)

    def _disconnect_from_core(self) -> None:
        self._mmc.events.systemConfigurationLoaded.disconnect(self._on_sys_cfg_loaded)
        self._mmc.events.channelGroupChanged.disconnect(self._on_channel_group_changed)
        self._mmc.events.configSet.disconnect(self._on_channel_set)
        self._mmc.events.configDefined.disconnect(self._on_new_group_preset)
        self._mmc.events.configGroupDeleted.connect(self._on_group_deleted)
