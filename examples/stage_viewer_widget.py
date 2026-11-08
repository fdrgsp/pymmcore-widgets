# NOTE: run in ipython with `%run examples/stage_viewer_widget.py`

import numpy as np
from qtpy.QtGui import QMouseEvent

from pymmcore_widgets.control._stage_explorer._stage_viewer import StageViewer

img = np.random.randint(0, 255, (256, 256), dtype=np.uint8)

stage_viewer = StageViewer()
stage_viewer.show()
stage_viewer.add_image(img)
T = np.eye(4)
T[3, :3] = 400, 250, 0  # x, y, z shift
stage_viewer.add_image(img, T.T)
stage_viewer.zoom_to_fit()


@stage_viewer.canvas.events.mouse_press.connect
def _on_mouse_press(event: QMouseEvent) -> None:
    """Print the world position of the click."""
    print(stage_viewer.canvas_to_world(event.pos))
