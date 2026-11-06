from __future__ import annotations

from typing import TYPE_CHECKING

import useq
from qtpy.QtCore import QEvent, QObject, Qt, Signal
from qtpy.QtWidgets import (
    QButtonGroup,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from superqt.utils import signals_blocked

from ._common import LabelAligner
from ._cover_area_widget import CoverAreaWidget
from ._random_points_widget import RandomPointWidget

if TYPE_CHECKING:
    from typing import TypeAlias

    # excluding useq.GridWidthHeight even though it's also a relative multi point plan
    RelativePointPlan: TypeAlias = (
        useq.GridRowsColumns
        | useq.GridFromShape
        | useq.RandomPoints
        | useq.RelativePosition
    )


class RelativePositionWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Single FOV")
        title.setStyleSheet("font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

    def value(self) -> useq.RelativePosition:
        return useq.RelativePosition()

    def setValue(self, plan: useq.RelativePosition) -> None:
        pass


if TYPE_CHECKING:
    PlanWidget: TypeAlias = RelativePositionWidget | RandomPointWidget | CoverAreaWidget


class _FovWidget(QDoubleSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSpecialValueText("--")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setRange(0, 100000)
        self.setValue(200)
        self.setSingleStep(10)


class RelativePointPlanSelector(QWidget):
    """Widget to select a relative multi-position point plan.

    See also: [PointsPlanWidget][pymmcore_widgets.useq_widgets.PointsPlanWidget]
    which combines this widget with a graphical representation of the points.

    In useq, a RelativeMultiPointPlan is one of:
    - useq.RelativePosition
    - useq.RandomPoints
    - useq.GridFromShape  ("Cover Area" card, "Coverage" tab)
    - useq.GridRowsColumns  ("Cover Area" card, "Grid" tab)
    - useq.GridWidthHeight  # not included in this widget
    """

    valueChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent=parent)

        # WIDGET ----------------------

        # plan widgets
        self.single_pos_wdg = RelativePositionWidget()
        self.random_points_wdg = RandomPointWidget()
        self.cover_area_wdg = CoverAreaWidget()
        # the rows/columns grid is the "Grid" tab of the Cover Area card
        self.grid_wdg = self.cover_area_wdg.grid_wdg

        # this gets changed when the radio buttons are toggled
        self._active_plan_widget: PlanWidget = self.single_pos_wdg

        # radio buttons  selection

        self.single_radio_btn = QRadioButton()
        self.single_radio_btn.setChecked(True)
        self.random_radio_btn = QRadioButton()
        self.cover_area_radio_btn = QRadioButton()
        # backwards compatible name: grids are part of the Cover Area card
        self.grid_radio_btn = self.cover_area_radio_btn
        for btn, name in (
            (self.single_radio_btn, "Single FOV"),
            (self.random_radio_btn, "Random Points"),
            (self.cover_area_radio_btn, "Cover Area"),
        ):
            btn.setAccessibleName(name)

        self._mode_btn_group = QButtonGroup()
        self._mode_btn_group.addButton(self.single_radio_btn)
        self._mode_btn_group.addButton(self.random_radio_btn)
        self._mode_btn_group.addButton(self.cover_area_radio_btn)

        self._btn2wdg: dict[QRadioButton, PlanWidget] = {
            self.single_radio_btn: self.single_pos_wdg,
            self.random_radio_btn: self.random_points_wdg,
            self.cover_area_radio_btn: self.cover_area_wdg,
        }

        self.fov_w = _FovWidget()
        self.fov_h = _FovWidget()

        # CONNECTIONS ----------------------

        self._mode_btn_group.buttonToggled.connect(self._on_radiobutton_toggled)
        self.random_points_wdg.valueChanged.connect(self._on_value_changed)
        self.cover_area_wdg.valueChanged.connect(self._on_value_changed)
        self.fov_h.valueChanged.connect(self._on_fov_changed)
        self.fov_w.valueChanged.connect(self._on_fov_changed)
        self._sync_fov_size()

        # LAYOUT ----------------------

        # the plan cards go in a scroll area, so that all of them (and the FOV
        # widgets below) stay reachable at small window sizes
        cards = QWidget()
        cards_layout = QVBoxLayout(cards)
        cards_layout.setSpacing(6)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        for btn, wdg in self._btn2wdg.items():
            wdg.setEnabled(btn.isChecked())
            grpbx = QGroupBox()
            # make a click on the groupbox act as a click on the button
            grpbx.mousePressEvent = lambda _, b=btn: b.setChecked(True)
            grpbx_layout = QVBoxLayout(grpbx)
            grpbx_layout.setContentsMargins(4, 4, 4, 4)
            grpbx_layout.addWidget(wdg)

            layout = QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(5)
            layout.addWidget(btn, 0)
            layout.addWidget(grpbx, 1)
            cards_layout.addLayout(layout, 1)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidget(cards)
        # only scroll vertically: never squeeze the cards below their minimum width
        # (updated whenever the cards' layout changes, e.g. label widths)
        self._cards = cards
        cards.installEventFilter(self)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 0, 10, 0)
        main_layout.addWidget(self._scroll, 1)

        # FOV widgets go at the bottom, and are combined into a single widget
        # for ease of showing/hiding the whole thing at once
        self.fov_widgets = QWidget()
        fov_layout = QHBoxLayout(self.fov_widgets)
        fov_layout.setContentsMargins(0, 0, 0, 0)
        fov_layout.setSpacing(2)
        fov_layout.addSpacing(24)
        fov_label = QLabel("FOV (w, h; µm):")
        fov_layout.addWidget(fov_label)
        fov_layout.addWidget(self.fov_w)
        fov_layout.addWidget(self.fov_h)
        main_layout.addWidget(self.fov_widgets, 0)

        # all the row labels on the page share the width of the longest one
        self._label_aligner = LabelAligner(
            self,
            [
                *self.random_points_wdg.labels(),
                *self.cover_area_wdg.labels(),
                fov_label,
            ],
        )
        self._update_scroll_width()

    # _________________________PUBLIC METHODS_________________________ #

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        if a0 is self._cards and a1 is not None:
            if a1.type() in (QEvent.Type.LayoutRequest, QEvent.Type.Show):
                self._update_scroll_width()
        return False

    @property
    def active_plan_type(self) -> type[RelativePointPlan]:
        if self._active_plan_widget is self.cover_area_wdg:
            return self.cover_area_wdg.planType()
        if self._active_plan_widget is self.random_points_wdg:
            return useq.RandomPoints
        return useq.RelativePosition

    def value(self) -> useq.RelativeMultiPointPlan:
        # the fov_w/h values are global to all plans
        return self._active_plan_widget.value().model_copy(
            update={
                "fov_width": self.fov_w.value() or None,
                "fov_height": self.fov_h.value() or None,
            }
        )

    def setValue(self, plan: useq.RelativeMultiPointPlan) -> None:
        """Set the value of the widget.

        Parameters
        ----------
        plan : useq.RelativePosition | useq.RandomPoints | useq.GridFromShape |
               useq.GridRowsColumns
            The point plan to set.
        """
        if plan == self.value():
            return

        with signals_blocked(self):
            # set the shared FOV first, so the plan widgets validate with it
            with signals_blocked(self.fov_w), signals_blocked(self.fov_h):
                self.fov_h.setValue(plan.fov_height or 0)
                self.fov_w.setValue(plan.fov_width or 0)
            self._sync_fov_size()
            if isinstance(plan, useq.RandomPoints):
                self.random_points_wdg.setValue(plan)
                self.random_radio_btn.setChecked(True)
            elif isinstance(plan, (useq.GridFromShape, useq.GridRowsColumns)):
                self.cover_area_wdg.setValue(plan)
                self.cover_area_radio_btn.setChecked(True)
            elif isinstance(plan, useq.RelativePosition):
                self.single_pos_wdg.setValue(plan)
                self.single_radio_btn.setChecked(True)
            else:  # pragma: no cover
                raise ValueError(f"Invalid plan type: {type(plan)}")
            self._sync_fov_size()
        self._on_value_changed()

    # _________________________PRIVATE METHODS_________________________ #

    def _on_radiobutton_toggled(self, btn: QRadioButton, checked: bool) -> None:
        wdg = self._btn2wdg[btn]
        wdg.setEnabled(checked)
        if checked:
            self._active_plan_widget = wdg
            self._scroll.ensureWidgetVisible(wdg)
            self._on_value_changed()

    def _update_scroll_width(self) -> None:
        width = (
            self._cards.minimumSizeHint().width()
            + self._scroll.verticalScrollBar().sizeHint().width()
        )
        if self._scroll.minimumWidth() != width:
            self._scroll.setMinimumWidth(width)

    def _sync_fov_size(self) -> None:
        """Share the FOV size with the Cover Area widget (it needs it to validate)."""
        self.cover_area_wdg.fov_size = (
            self.fov_w.value() or None,
            self.fov_h.value() or None,
        )

    def _on_fov_changed(self) -> None:
        self._sync_fov_size()
        self._on_value_changed()

    def _on_value_changed(self) -> None:
        self.valueChanged.emit(self.value())
