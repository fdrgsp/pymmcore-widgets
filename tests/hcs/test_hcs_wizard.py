from __future__ import annotations

from typing import TYPE_CHECKING

import useq

from pymmcore_widgets.hcs import HCSWizard

if TYPE_CHECKING:
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
