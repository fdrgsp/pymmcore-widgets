"""ChannelSettingsTable stores the settings to use for each preset of a group.

Pick a group to list its presets, then set each one's exposure, light source,
intensity and camera properties (under "Advanced"). Settings are kept for every
group, so switching back restores them. The first column marks the preset active
on the microscope; click another row's ring to apply that preset and its settings.
"""

from contextlib import suppress

from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QApplication

from pymmcore_widgets import ChannelSettingsTable

with suppress(ImportError):
    from rich import print

app = QApplication([])

mmc = CMMCorePlus.instance()
mmc.loadSystemConfiguration()

wdg = ChannelSettingsTable()
wdg.valueChanged.connect(lambda: print(wdg.allChannelSettings()))
wdg.resize(1000, 250)
wdg.show()

app.exec()
