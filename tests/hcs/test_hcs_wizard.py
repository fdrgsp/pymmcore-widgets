from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import useq

from pymmcore_widgets.hcs import HCSWizard

if TYPE_CHECKING:
    from pathlib import Path

    from pymmcore_plus import CMMCorePlus
    from pytestqt.qtbot import QtBot


def test_hcs_wizard(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    """Test the HCSWizard."""

    plan = useq.WellPlatePlan(
        plate="96-well",
        a1_center_xy=(1000, 1500),
        rotation=0.3,
        selected_wells=slice(0, 8, 2),
    )

    wdg = HCSWizard(mmcore=global_mmcore)
    wdg.setValue(plan)
    qtbot.addWidget(wdg)
    wdg.show()
    wdg.next()
    wdg.next()
    wdg.next()
    wdg.accept()

    # we haven't done anything, the plan should be the same
    assert wdg.value() == plan


def test_single_area_hides_automated(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    wizard = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wizard)
    page = wizard.calibration_page
    page.widget.setValue("96-well")
    assert page.tabs.isTabVisible(1)
    page.tabs.setCurrentIndex(1)
    coverslip = useq.WellPlate(
        rows=1, columns=1, well_size=(20, 10), well_spacing=(0, 0), circular_wells=False
    )
    page.widget.setValue(coverslip)
    assert not page.tabs.isTabVisible(1)
    assert page.tabs.currentWidget() is page.widget
    assert not page.widget._current_calibration_widget().circularWell()
    page.widget.setValue(useq.WellPlatePlan(plate=coverslip, a1_center_xy=(100, 200)))
    assert not page.tabs.isTabVisible(1)
    assert page.isComplete()
    strip = useq.WellPlate(rows=1, columns=8, well_size=5, well_spacing=9)
    page.widget.setValue(strip)
    assert page.tabs.isTabVisible(1)


def test_hcs_wizard_cover_area(
    qtbot: QtBot, global_mmcore: CMMCorePlus, tmp_path: Path
) -> None:
    """Cover Area plans round-trip through the wizard, save and load."""
    coverage = useq.GridFromShape(shape="ellipse", width=4000, height=3000, overlap=10)
    plan = useq.WellPlatePlan(
        plate="96-well",
        a1_center_xy=(1000, 1500),
        rotation=0.3,
        selected_wells=([0, 1], [0, 3]),
        well_points_plan=coverage,
    )
    wdg = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wdg)
    wdg.setValue(plan)

    pp_widget = wdg.points_plan_page.widget
    assert pp_widget._selector.cover_area_radio_btn.isChecked()
    # the loaded (smaller than the 6.4 mm well) area is shown
    assert pp_widget.cover_area_wdg.area_width.value() == 4000

    value = wdg.value()
    assert value is not None
    wpp = value.well_points_plan
    assert isinstance(wpp, useq.GridFromShape)
    # FOV filled in from the core, rotation follows the plate calibration
    px = global_mmcore.getPixelSizeUm()
    assert wpp.fov_width == global_mmcore.getImageWidth() * px
    assert wpp.fov_height == global_mmcore.getImageHeight() * px
    assert wpp.rotation == pytest.approx(0.3)
    assert (wpp.shape, wpp.width, wpp.height, wpp.overlap) == (
        coverage.shape,
        4000,
        3000,
        (10, 10),
    )
    n = wpp.num_positions()
    assert len(value) == 2 * n
    assert f"{n * 2} total (2 wells)" in pp_widget._well_view.infoText()

    # positions are the well centers plus the plan offsets
    first_well = value.selected_well_positions[0]
    first_offset = next(iter(wpp))
    first = value[0]
    assert first.x == pytest.approx((first_well.x or 0) + first_offset.x)
    assert first.y == pytest.approx((first_well.y or 0) + first_offset.y)

    # save and load
    path = tmp_path / "plan.json"
    wdg.save(str(path))
    wdg2 = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wdg2)
    wdg2.load(str(path))
    assert wdg2.value() == value


def test_hcs_wizard_cover_area_blocks_finish(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    wdg = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wdg)
    wdg.setValue(useq.WellPlatePlan(plate="96-well", a1_center_xy=(0, 0)))
    page = wdg.points_plan_page
    cov = page.widget.cover_area_wdg

    page.widget._selector.cover_area_radio_btn.setChecked(True)
    # by default, covers the full 6.4 mm circular well
    assert cov.value().width == 6400
    assert page.isComplete()

    # a rectangle as wide as the circular well does not fit inside it
    cov.shape.setCurrentText("rectangle")
    cov.area_width.setValue(6400)
    assert not cov.isValid()
    assert not page.isComplete()
    cov.area_width.setValue(1000)
    cov.area_height.setValue(1000)
    assert page.isComplete()

    # missing calibration (no FOV) blocks Finish too
    page.widget._selector.fov_w.setValue(0)
    assert not page.isComplete()
    # other modes are not affected
    page.widget._selector.single_radio_btn.setChecked(True)
    assert page.isComplete()

    # changing the selected wells keeps the user input, and updates the total
    page.widget._selector.fov_w.setValue(512)
    page.widget._selector.cover_area_radio_btn.setChecked(True)
    cov_value = cov.value()
    plate_value = wdg.plate_page.widget.value()
    wdg.plate_page.widget.setValue(plate_value.replace(selected_wells=([0, 1], [0, 0])))
    wdg._on_plate_changed(wdg.plate_page.widget.value())  # as on a user selection
    assert cov.value() == cov_value
    assert "(2 wells)" in page.widget._well_view.infoText()


def test_hcs_wizard_file_wrapper(
    qtbot: QtBot, global_mmcore: CMMCorePlus, tmp_path: Path
) -> None:
    """Saved files are a versioned envelope; bare WellPlatePlan files still load."""
    import json

    from pymmcore_widgets.hcs._hcs_wizard import WIZARD_FILE_FORMAT

    plan = useq.WellPlatePlan(
        plate="96-well",
        a1_center_xy=(0, 0),
        selected_wells=([0], [0]),
        well_points_plan=useq.GridFromShape(
            shape="ellipse", width=6400, height=6400, fov_width=500, fov_height=500
        ),
    )
    wdg = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wdg)
    wdg.setValue(plan)
    cov = wdg.points_plan_page.widget.cover_area_wdg
    cov.shape.setCurrentText("rectangle")
    cov.area_width.setValue(3000)
    cov.area_height.setValue(2000)
    cov.inside_area.setChecked(True)

    path = tmp_path / "wizard.json"
    wdg.save(str(path))
    data = json.loads(path.read_text())
    assert data["format"] == WIZARD_FILE_FORMAT
    assert data["version"] == 1
    assert data["editor"] == {}
    saved_plan = useq.WellPlatePlan.model_validate(data["well_plate_plan"])
    assert isinstance(saved_plan.well_points_plan, useq.GridFromShape)
    assert saved_plan.well_points_plan.placement == "inside"

    wdg2 = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wdg2)
    wdg2.load(str(path))
    cov2 = wdg2.points_plan_page.widget.cover_area_wdg
    assert wdg2.value() == wdg.value()
    assert cov2.inside_area.isChecked()
    assert (cov2.area_width.value(), cov2.area_height.value()) == (3000, 2000)

    # legacy: a bare WellPlatePlan file
    legacy = tmp_path / "legacy.json"
    legacy.write_text(wdg.value().model_dump_json())  # type: ignore[union-attr]
    wdg3 = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wdg3)
    wdg3.load(str(legacy))
    assert wdg3.value() == wdg.value()

    # files from a newer, unknown version are rejected clearly
    data["version"] = 99
    newer = tmp_path / "newer.json"
    newer.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="version"):
        wdg3.load(str(newer))


def test_hcs_wizard_grid_in_cover_area(
    qtbot: QtBot, global_mmcore: CMMCorePlus
) -> None:
    grid = useq.GridRowsColumns(rows=2, columns=3, fov_width=512, fov_height=512)
    plan = useq.WellPlatePlan(
        plate="96-well",
        a1_center_xy=(0, 0),
        selected_wells=([0], [0]),
        well_points_plan=grid,
    )
    wdg = HCSWizard(mmcore=global_mmcore)
    qtbot.addWidget(wdg)
    wdg.setValue(plan)
    selector = wdg.points_plan_page.widget._selector
    assert selector.cover_area_radio_btn.isChecked()
    assert selector.active_plan_type is useq.GridRowsColumns
    assert wdg.value() == plan
    # a grid doesn't need the coverage validation
    selector.fov_w.setValue(0)
    assert wdg.points_plan_page.isComplete()
