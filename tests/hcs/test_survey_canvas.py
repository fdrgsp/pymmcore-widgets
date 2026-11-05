from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from qtpy.QtWidgets import QWidget
from vispy import scene

from pymmcore_widgets.hcs._plate_survey import SurveyGeometry, SurveyTile
from pymmcore_widgets.hcs._survey_canvas import SurveyCanvas
from pymmcore_widgets.hcs._well_detection import DetectedWell


def test_image_transform_and_independent_overlay(qtbot, monkeypatch):
    from pymmcore_widgets.hcs._survey_canvas import StageViewer

    # Real VisPy scene/visuals; omit creation of a native GL surface for this test.
    def init(self, parent=None):
        QWidget.__init__(self, parent)
        self.view = SimpleNamespace(scene=scene.Node())
        self.canvas = Mock()

    monkeypatch.setattr(StageViewer, "__init__", init)
    monkeypatch.setattr(StageViewer, "add_image", Mock())
    monkeypatch.setattr(StageViewer, "zoom_to_fit", Mock())
    monkeypatch.setattr(StageViewer, "clear", Mock())
    viewer = SurveyCanvas()
    qtbot.addWidget(viewer)
    geometry = SurveyGeometry((0, 0, 400, 300), (10, 20), (2, 0, 0, 2))
    tile = SurveyTile(np.ones((10, 20)), (100, 200))
    viewer.add_tile(tile, geometry)
    image, transform = viewer.add_image.call_args.args
    assert image is tile.image
    # The frame center maps to the settled stage XY; both opposite corners agree.
    np.testing.assert_allclose(transform @ [10, 5, 0, 1], [100, 200, 0, 1])
    np.testing.assert_allclose(transform @ [0, 0, 0, 1], [80, 190, 0, 1])
    np.testing.assert_allclose(transform @ [20, 10, 0, 1], [120, 210, 0, 1])
    square = DetectedWell((300, 200), 10, 1, 1, (10, 10), 0, rectangular=True)
    wells = (DetectedWell((100, 200), 10, 1, 1), square)
    viewer.set_detections(wells, ["A1", "A2"])
    np.testing.assert_allclose(viewer._centers._data["a_position"][0][:2], (100, 200))
    assert viewer._labels.text == ["A1", "A2"]
    np.testing.assert_allclose(square.outline()[[0, 2]], [(290, 190), (310, 210)])
    viewer.set_detection_visible(False)
    assert not viewer._detections.visible
    assert viewer.add_image.call_count == 1
    viewer.set_detection_visible(True)
    assert viewer._detections.visible
    viewer.set_detections((), [])
    assert viewer._labels.text == ""
    assert viewer.add_image.call_count == 1
