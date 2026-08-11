"""MDAWidgetCollapsible is a drop-in MDAWidget with a different presentation.

It behaves exactly like MDAWidget (same value/setValue, run button, and core
awareness) but lays out the acquisition axes as a scrollable stack of
collapsible sections instead of a checkable tab widget.
"""

from contextlib import suppress

import useq
from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QApplication

from pymmcore_widgets import MDAWidgetCollapsible

with suppress(ImportError):
    from rich import print

app = QApplication([])

mmc = CMMCorePlus.instance()
mmc.loadSystemConfiguration()

wdg = MDAWidgetCollapsible()
wdg.channels.setChannelGroups({"Channel": ["DAPI", "FITC"]})
wdg.time_plan.setValue(useq.TIntervalLoops(interval=0.5, loops=11))
wdg.valueChanged.connect(lambda: print(wdg.value()))
wdg.show()
app.exec()
