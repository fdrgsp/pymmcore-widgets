import useq
from pymmcore_plus import CMMCorePlus
from qtpy.QtWidgets import QApplication, QHBoxLayout, QSplitter, QVBoxLayout, QWidget

from pymmcore_widgets import (
    CameraRoiWidget,
    GroupPresetTableWidget,
    MDAWidget,
    StageExplorer,
    StageWidget,
)

app = QApplication([])

mmc = CMMCorePlus.instance()
mmc.loadSystemConfiguration()
mmc.setProperty("Camera", "BitDepth", "12")

# set camera roi (rectangular helps confirm orientation)
mmc.setROI(0, 0, 600, 400)

xy = mmc.getXYStageDevice()
if mmc.hasProperty(xy, "Velocity"):
    mmc.setProperty(xy, "Velocity", 2)

explorer = StageExplorer()
stage_ctrl = StageWidget(mmc.getXYStageDevice())
stage_ctrl.setStep(512)
stage_ctrl.snap_checkbox.setChecked(True)

z_ctrl = StageWidget(mmc.getFocusDevice())
z_ctrl.snap_checkbox.setChecked(True)

mda_widget = MDAWidget()
group_wdg = GroupPresetTableWidget()
cam_roi = CameraRoiWidget()


# Toolbar "Send to MDA": put the explorer's ROIs in the MDA position table.
def _on_send_to_mda(positions: list[useq.Position], replace: bool) -> None:
    table = mda_widget.stage_positions
    if not replace:
        positions = [*table.value(exclude_unchecked=False), *positions]
    table.setValue(positions)
    mda_widget.tab_wdg.setChecked(table, True)
    mda_widget.tab_wdg.setCurrentWidget(table)


explorer.sendToMDARequested.connect(_on_send_to_mda)

# Mark the MDA positions on the map (toolbar: "Show MDA Positions").
mda_widget.stage_positions.valueChanged.connect(
    lambda: explorer.set_mda_positions(mda_widget.stage_positions.value())
)


# layout

splitter = QSplitter()
left = QWidget()
llayout = QVBoxLayout(left)
llayout.addWidget(group_wdg)
llayout.addWidget(cam_roi)
splitter.addWidget(left)
splitter.addWidget(explorer)
right = QWidget()
rlayout = QVBoxLayout(right)
rtop = QHBoxLayout()
rtop.addWidget(stage_ctrl)
rtop.addWidget(z_ctrl)
rlayout.addLayout(rtop)
rlayout.addWidget(mda_widget)
splitter.addWidget(right)
splitter.show()

app.exec()
