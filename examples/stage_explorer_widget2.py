from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QListView,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from pymmcore_widgets import (
    CameraRoiWidget,
    GroupPresetTableWidget,
    MDAWidget,
    StageWidget,
)
from pymmcore_widgets.control._stage_explorer._stage_explorer import StageExplorer

app = QApplication([])

mmc = CMMCorePlus.instance()

# mmc.loadSystemConfiguration(r"D:\Christina\MyChristina.cfg")
# mmc.setConfig("Channel", "BF")
# mmc.setExposure(10)

mmc.loadSystemConfiguration()
mmc.setROI(0, 0, 600, 400)
xy = mmc.getXYStageDevice()
if mmc.hasProperty(xy, "Velocity"):
    mmc.setProperty(xy, "Velocity", 5)

explorer = StageExplorer()

stage_ctrl = StageWidget(mmc.getXYStageDevice())
stage_ctrl.setStep(512)
stage_ctrl.snap_checkbox.setChecked(True)

z_ctrl = StageWidget(mmc.getFocusDevice())
z_ctrl.snap_checkbox.setChecked(True)

mda_widget = MDAWidget()

group_wdg = GroupPresetTableWidget()

rtop = QHBoxLayout()
rtop.addWidget(stage_ctrl)
rtop.addWidget(z_ctrl)

right = QWidget()
rlayout = QVBoxLayout()
rlayout.addLayout(rtop)
rlayout.addWidget(mda_widget)


view = QListView()
view.setModel(explorer.roi_manager.roi_model)
view.setSelectionModel(explorer.roi_manager.selection_model)

cam_roi = CameraRoiWidget()
left = QWidget()
llayout = QVBoxLayout(left)
llayout.addWidget(view)
llayout.addWidget(cam_roi)

splitter = QSplitter()
splitter.addWidget(left)
# splitter.addWidget(group_wdg)
splitter.addWidget(explorer)
# splitter.addWidget(right)
splitter.show()

app.exec()
