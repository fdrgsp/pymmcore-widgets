from __future__ import annotations

from itertools import product
from typing import TYPE_CHECKING, ContextManager, Sequence

import useq
from pymmcore_plus import CMMCorePlus
from pymmcore_plus.core.events import CMMCoreSignaler, PCoreSignaler
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from superqt.utils import signals_blocked

from ._hcs_widget._graphics_items import _Well

if TYPE_CHECKING:
    from qtpy.QtGui import QResizeEvent

    from ._hcs_widget._well_plate_model import WellPlate

PLATE_FROM_CALIBRATION = "custom_from_calibration"
PLATE_GRAPHICS_VIEW_HEIGHT = 320
PLATE_SCENE_SIZE = PLATE_GRAPHICS_VIEW_HEIGHT - 10


class ComboMessageBox(QDialog):
    """Dialog that presents a combo box of `items`."""

    def __init__(
        self,
        items: Sequence[str] = (),
        text: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._combo = QComboBox()
        self._combo.addItems(items)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)

        self.setLayout(QVBoxLayout())
        if text:
            self.layout().addWidget(QLabel(text))
        self.layout().addWidget(self._combo)
        self.layout().addWidget(btn_box)

    def currentText(self) -> str:
        """Returns the current QComboBox text."""
        return self._combo.currentText()  # type: ignore [no-any-return]


def guess_channel_group(
    mmcore: CMMCorePlus | None = None, parent: QWidget | None = None
) -> str | None:
    """Try to update the list of channel group choices.

    1. get a list of potential channel groups from pymmcore
    2. if there is only one, use it, if there are > 1, show a dialog box
    """
    mmcore = mmcore or CMMCorePlus.instance()
    candidates = mmcore.getOrGuessChannelGroup()
    if len(candidates) == 1:
        return candidates[0]
    elif candidates:
        dialog = ComboMessageBox(candidates, "Select Channel Group:", parent=parent)
        if dialog.exec_() == dialog.DialogCode.Accepted:
            return dialog.currentText()
    return None


def guess_objective_or_prompt(
    mmcore: CMMCorePlus | None = None, parent: QWidget | None = None
) -> str | None:
    """Try to update the list of objective choices.

    1. get a list of potential objective devices from pymmcore
    2. if there is only one, use it, if there are >1, show a dialog box
    """
    mmcore = mmcore or CMMCorePlus.instance()
    candidates = mmcore.guessObjectiveDevices()
    if len(candidates) == 1:
        return candidates[0]
    elif candidates:
        dialog = ComboMessageBox(candidates, "Select Objective Device:", parent=parent)
        if dialog.exec_() == dialog.DialogCode.Accepted:
            return dialog.currentText()
    return None


def block_core(mmcore_events: CMMCoreSignaler | PCoreSignaler) -> ContextManager:
    """Block core signals."""
    if isinstance(mmcore_events, CMMCoreSignaler):
        return mmcore_events.blocked()  # type: ignore
    elif isinstance(mmcore_events, PCoreSignaler):
        return signals_blocked(mmcore_events)  # type: ignore


def cast_grid_plan(grid: dict | useq.AnyGridPlan) -> useq.AnyGridPlan | None:
    """Get the grid type from the grid_plan."""
    if not grid:
        return None
    if isinstance(grid, dict):
        return useq.MDASequence(grid_plan=grid).grid_plan
    return grid


def fov_kwargs(core: CMMCorePlus) -> dict:
    """Return image width and height in micron to be used for the grid plan."""
    if px := core.getPixelSizeUm():
        *_, width, height = core.getROI()
        return {"fov_width": (width * px) or None, "fov_height": (height * px) or None}
    return {}


class ResizingGraphicsView(QGraphicsView):
    """A QGraphicsView that resizes the scene to fit the view."""

    def __init__(self, scene: QGraphicsScene, parent: QWidget | None = None) -> None:
        super().__init__(scene, parent)

    def resizeEvent(self, event: QResizeEvent) -> None:
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


def draw_well_plate(
    view: QGraphicsView, scene: QGraphicsScene, plate: WellPlate, text: bool = True
) -> None:
    """Draw all wells of the plate."""
    scene.clear()

    if not plate.well_size_x or not plate.well_size_y:
        return

    if plate.well_size_x == plate.well_size_y:
        width = height = PLATE_SCENE_SIZE
    elif plate.well_size_x > plate.well_size_y:
        width = PLATE_SCENE_SIZE
        # keep the ratio between well_size_x and well_size_y
        height = int(PLATE_SCENE_SIZE * plate.well_size_y / plate.well_size_x)
    else:
        # keep the ratio between well_size_x and well_size_y
        width = int(PLATE_SCENE_SIZE * plate.well_size_x / plate.well_size_y)
        height = PLATE_SCENE_SIZE

    # calculate the spacing between wells
    dx = plate.well_spacing_x - plate.well_size_x if plate.well_spacing_x else 0
    dy = plate.well_spacing_y - plate.well_size_y if plate.well_spacing_y else 0

    # convert the spacing between wells in pixels
    dx_px = dx * width / plate.well_size_x if plate.well_spacing_x else 0
    dy_px = dy * height / plate.well_size_y if plate.well_spacing_y else 0

    # the text size is the height of the well divided by 3
    text_size = height / 3 if text else None

    # draw the wells and place them in their correct row/column position
    for row, col in product(range(plate.rows), range(plate.cols)):
        _x = (width * col) + (dx_px * col)
        _y = (height * row) + (dy_px * row)
        scene.addItem(_Well(_x, _y, width, height, row, col, text_size, plate.circular))

    # set the scene size
    plate_width = (width * plate.cols) + (dx_px * (plate.cols - 1))
    plate_height = (height * plate.rows) + (dy_px * (plate.rows - 1))
    scene.setSceneRect(0, 0, plate_width, plate_height)

    # fit scene in view
    view.fitInView(view.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
