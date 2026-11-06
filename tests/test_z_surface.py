from __future__ import annotations

import useq

from pymmcore_widgets._z_surface import ZSurface

TILTED = ((0, 0, 0), (1000, 0, 10), (0, 1000, 0))


def test_focus_surface() -> None:
    surface = ZSurface(method="plane", points=TILTED)
    assert surface.problem() == ""
    assert surface.focus_surface() == useq.FocusSurface(method="plane", points=TILTED)
    # the same JSON as a FocusSurface's
    json = surface.model_dump_json()
    assert useq.FocusSurface.model_validate_json(json) == surface.focus_surface()
    assert ZSurface.model_validate_json(json) == surface


def test_problem() -> None:
    assert ZSurface().problem() == "At least 3 reference points are needed (0 set)."
    on_a_line = ZSurface(points=((0, 0, 0), (10, 10, 1), (20, 20, 2)))
    assert on_a_line.problem() == (
        "The reference points lie on a line: add one away from it."
    )
