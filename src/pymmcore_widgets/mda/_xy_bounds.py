from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import QSize, Qt
from qtpy.QtGui import QTransform
from qtpy.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
    QVBoxLayout,
    QWidget,
)
from superqt.iconify import QIconifyIcon

from pymmcore_widgets.useq_widgets._grid import _BoundsWidget

if TYPE_CHECKING:
    import useq
    from pyconify.iconify_types import Flip
    from pymmcore import CMMCore


FIXED_POLICY = (QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
CTR = Qt.AlignmentFlag.AlignCenter
RADIUS = 4
ICON_SIZE = TRANSLATE_ICON = 24
if os.name != "nt":
    TRANSLATE_ICON *= 2


# we have zero idea why this works. But we found that the classic
# translate -> rotate -> inverse-translate does not work on Windows
# We don't know why this would be OS dependent in the first place
def _rotate(deg: int, size_x: int, size_y: int) -> QTransform:
    return QTransform().translate(size_x, size_y).rotate(deg)


ICONS_GO: dict[str, str] = {
    "top": "mdi:arrow-up-thick",
    "left": "mdi:arrow-left-thick",
    "right": "mdi:arrow-right-thick",
    "bottom": "mdi:arrow-down-thick",
    "top_left": "mdi:arrow-top-left-thick",
    "top_right": "mdi:arrow-top-right-thick",
    "bottom_left": "mdi:arrow-bottom-left-thick",
    "bottom_right": "mdi:arrow-bottom-right-thick",
}
ICONS_MARK: dict[str, tuple[str, Flip | None]] = {
    "top": ("mdi:border-top-variant", None),
    "left": ("mdi:border-left-variant", None),
    "right": ("mdi:border-right-variant", None),
    "bottom": ("mdi:border-bottom-variant", None),
    "top_left": ("mdi:border-style", None),
    "top_right": ("mdi:border-style", "horizontal"),
    "bottom_left": ("mdi:border-style", "vertical"),
    "bottom_right": ("mdi:border-style", "horizontal,vertical"),
}


class XYBoundsControl(QWidget):
    """Buttons to mark and visit bounds on the XY stage."""

    def __init__(
        self, compact_layout: bool = False, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)

        self.btn_top = _MarkVisitButton("top")
        self.btn_left = _MarkVisitButton("left")
        self.btn_right = _MarkVisitButton("right")
        self.btn_bottom = _MarkVisitButton("bottom")
        self.btn_top_left = _MarkVisitButton("top_left")
        self.btn_top_right = _MarkVisitButton("top_right")
        self.btn_bottom_left = _MarkVisitButton("bottom_left")
        self.btn_bottom_right = _MarkVisitButton("bottom_right")

        if compact_layout:
            for button in (
                self.btn_top,
                self.btn_left,
                self.btn_right,
                self.btn_bottom,
                self.btn_top_left,
                self.btn_top_right,
                self.btn_bottom_left,
                self.btn_bottom_right,
            ):
                button.setIconSize(QSize(16, 16))
                button.setFixedSize(30, 26)

        self.go_middle = QCheckBox("Move")
        self.go_middle.setSizePolicy(*FIXED_POLICY)
        self.go_middle.toggled.connect(self._update_buttons_icon)

        self._bounds_wdg = _BoundsWidget()
        self._bounds_wdg.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )

        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        if compact_layout:
            self._buttons_widget = self._make_compact_widget()
            top_layout.addWidget(self._buttons_widget, 1)
        else:
            self._buttons_widget = self._make_direction_pad()
            top_layout.setSpacing(15)
            top_layout.addWidget(self._buttons_widget, 0, Qt.AlignmentFlag.AlignVCenter)
            top_layout.addWidget(self._bounds_wdg, 0, Qt.AlignmentFlag.AlignVCenter)
        top_layout.addStretch()

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addLayout(top_layout, int(compact_layout))
        if not compact_layout:
            main_layout.addStretch()
        self.setLayout(main_layout)
        self.setWindowTitle("Mark XY Boundaries")

    def _make_direction_pad(self) -> QWidget:
        buttons_widget = QWidget()
        buttons_widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        grid = QGridLayout(buttons_widget)
        grid.setSpacing(10)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(self.btn_top, 0, 2, CTR)
        grid.addWidget(self.btn_left, 2, 0, CTR)
        grid.addWidget(self.btn_right, 2, 4, CTR)
        grid.addWidget(self.btn_bottom, 4, 2, CTR)
        grid.addWidget(self.btn_top_left, 0, 0, CTR)
        grid.addWidget(self.btn_top_right, 0, 4, CTR)
        grid.addWidget(self.btn_bottom_left, 4, 0, CTR)
        grid.addWidget(self.btn_bottom_right, 4, 4, CTR)
        grid.addWidget(self.go_middle, 2, 2, CTR)
        return buttons_widget

    def _make_compact_widget(self) -> QWidget:
        compact_widget = QWidget()
        compact_widget.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding
        )
        layout = QGridLayout(compact_widget)
        # Match the horizontal inset used by the common grid form below, so
        # Mode, Top, and Left share its label-column starting edge.
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(4)

        self._edge_mode = QRadioButton("Edges")
        self._corner_mode = QRadioButton("Corners")
        mode_group = QButtonGroup(compact_widget)
        mode_group.addButton(self._edge_mode)
        mode_group.addButton(self._corner_mode)
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(10)
        mode_row.addWidget(QLabel("Mode:"))
        mode_row.addWidget(self._edge_mode)
        mode_row.addWidget(self._corner_mode)
        self._action_separator = QFrame()
        self._action_separator.setFrameShape(QFrame.Shape.VLine)
        self._action_separator.setFrameShadow(QFrame.Shadow.Sunken)
        mode_row.addWidget(self._action_separator)

        self._mark_action = QRadioButton("Mark")
        self._move_action = QRadioButton("Move")
        action_group = QButtonGroup(compact_widget)
        action_group.addButton(self._mark_action)
        action_group.addButton(self._move_action)
        mode_row.addWidget(QLabel("Action:"))
        mode_row.addWidget(self._mark_action)
        mode_row.addWidget(self._move_action)
        mode_row.addStretch()
        mode_widget = QWidget()
        mode_widget.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        mode_widget.setLayout(mode_row)
        layout.addWidget(mode_widget, 0, 0, 1, 2)

        self._compact_action_stacks: list[QStackedWidget] = []
        specs = (
            ("Top", self._bounds_wdg.top, self.btn_top, self.btn_top_left, 0, 0),
            (
                "Bottom",
                self._bounds_wdg.bottom,
                self.btn_bottom,
                self.btn_top_right,
                0,
                1,
            ),
            (
                "Left",
                self._bounds_wdg.left,
                self.btn_left,
                self.btn_bottom_left,
                1,
                0,
            ),
            (
                "Right",
                self._bounds_wdg.right,
                self.btn_right,
                self.btn_bottom_right,
                1,
                1,
            ),
        )
        for text, field, edge_button, corner_button, row, column in specs:
            control = QWidget()
            control_layout = QHBoxLayout(control)
            control_layout.setContentsMargins(0, 0, 0, 0)
            control_layout.setSpacing(2)
            label = QLabel(f"{text}:")
            label.setFixedWidth(46)
            control_layout.addWidget(label)
            control_layout.addWidget(field)

            action_stack = QStackedWidget()
            action_stack.setFixedSize(30, 26)
            action_stack.addWidget(edge_button)
            action_stack.addWidget(corner_button)
            self._compact_action_stacks.append(action_stack)
            control_layout.addWidget(action_stack)
            layout.addWidget(control, row + 1, column)
        # The bounds page is kept as tall as the other grid modes.  Share that
        # available height between its three visual rows instead of leaving an
        # unused block underneath the compact controls.
        for row in range(3):
            layout.setRowStretch(row, 1)

        self._corner_mode.toggled.connect(self._set_corner_mode)
        self._move_action.toggled.connect(self.go_middle.setChecked)
        self.go_middle.toggled.connect(self._sync_action_mode)
        self._edge_mode.setChecked(True)
        self._mark_action.setChecked(True)
        return compact_widget

    def _set_corner_mode(self, corners: bool) -> None:
        for stack in self._compact_action_stacks:
            stack.setCurrentIndex(int(corners))

    def _sync_action_mode(self, move: bool) -> None:
        (self._move_action if move else self._mark_action).setChecked(True)

    def _update_buttons_icon(self, state: bool) -> None:
        """Switch the icon of the buttons between `mark` and `visit`."""
        for btn in self.findChildren(_MarkVisitButton):
            btn.setVisit() if state else btn.setMark()


class CoreXYBoundsControl(XYBoundsControl):
    """Buttons to mark and visit bounds on the XY stage from a CMMCorePlus instance."""

    def __init__(
        self,
        core: CMMCore | None = None,
        device: str = "",
        compact_layout: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(compact_layout, parent)
        self._mmc = core or CMMCorePlus.instance()
        self._device = device
        # Raw (uncorrected) centers marked this session, keyed by "top",
        # "bottom", "left", "right" -- see _mark_axis.
        self._raw_marks: dict[str, float] = {}

        self.top = self._bounds_wdg.top
        self.left = self._bounds_wdg.left
        self.right = self._bounds_wdg.right
        self.bottom = self._bounds_wdg.bottom

        self.btn_top.clicked.connect(lambda: self._mark_or_visit(top=True))
        self.btn_left.clicked.connect(lambda: self._mark_or_visit(left=True))
        self.btn_right.clicked.connect(lambda: self._mark_or_visit(left=False))
        self.btn_bottom.clicked.connect(lambda: self._mark_or_visit(top=False))
        self.btn_top_left.clicked.connect(lambda: self._mark_or_visit(True, True))
        self.btn_top_right.clicked.connect(lambda: self._mark_or_visit(True, False))
        self.btn_bottom_left.clicked.connect(lambda: self._mark_or_visit(False, True))
        self.btn_bottom_right.clicked.connect(lambda: self._mark_or_visit(False, False))

    def value(self) -> dict[str, float]:
        """Return the current value of the grid plan widget."""
        return self._bounds_wdg.value()

    def setValue(self, plan: useq.GridFromEdges) -> None:
        """Set the value of the grid plan widget."""
        self._bounds_wdg.setValue(plan)
        # These marks belong to whatever region was previously being defined
        # interactively; a freshly loaded plan should not anchor future marks
        # to them.
        self._raw_marks.clear()

    def _mark_or_visit(self, top: bool | None = None, left: bool | None = None) -> None:
        self._visit(top, left) if self.go_middle.isChecked() else self._mark(top, left)

    def _mark(self, top: bool | None = None, left: bool | None = None) -> None:
        device = self._device or self._mmc.getXYStageDevice()
        fov_w = fov_h = 0.0
        if px := self._mmc.getPixelSizeUm():
            *_, width, height = self._mmc.getROI()
            fov_w, fov_h = width * px, height * px
        if top is not None:
            y = self._mmc.getYPosition(device)
            key, other_key = ("top", "bottom") if top else ("bottom", "top")
            wdg, other_wdg = (self.top, self.bottom) if top else (self.bottom, self.top)
            self._mark_axis(y, key, other_key, wdg, other_wdg, fov_h)
        if left is not None:
            x = self._mmc.getXPosition(device)
            key, other_key = ("left", "right") if left else ("right", "left")
            wdg, other_wdg = (
                (self.left, self.right) if left else (self.right, self.left)
            )
            self._mark_axis(x, key, other_key, wdg, other_wdg, fov_w)

    def _mark_axis(
        self,
        raw: float,
        key: str,
        other_key: str,
        wdg: QDoubleSpinBox,
        other_wdg: QDoubleSpinBox,
        extent: float,
    ) -> None:
        """Record *raw* for *key* and push both ends of this axis outward.

        useq.GridFromEdges expects each bound to be the *outer* edge of the
        image at that position (i.e. including the field of view), not the
        camera-center stage position marked here -- and it only ever reads a
        pair of bounds via ``max(top, bottom)`` / ``min(left, right)``, never
        by trusting which field is "top" and which is "bottom" (see its
        ``_offset_x``/``_offset_y``). So rather than assuming stage position
        increases toward "top" or "right" -- an axis-orientation convention
        that not every XY stage shares -- the outward direction is derived by
        comparing the two raw marked centers directly: whichever is
        numerically larger gets pushed further out that way, whichever is
        smaller gets pushed the other way. This self-corrects even if the
        first of the pair had nothing to compare against yet (see below).
        """
        self._raw_marks[key] = raw
        exact_other_raw = self._raw_marks.get(other_key)
        if exact_other_raw is None:
            # Nothing marked yet this session to compare against. Falling
            # back to the other field's *current* displayed value still
            # gives the right direction whenever it holds a real bound (e.g.
            # restored from a saved plan) -- it's off by half an extent from
            # that bound's own raw center, which only matters if the two
            # ends are implausibly close together. If it's still at its
            # untouched default, this is a best-effort guess for a single,
            # brand-new mark with no reference at all; marking the opposite
            # end afterward re-derives both from their exact raw values below.
            reference = other_wdg.value()
        else:
            reference = exact_other_raw
        outward = extent / 2 if raw >= reference else -extent / 2
        wdg.setValue(raw + outward)
        if exact_other_raw is not None:
            # Both raw centers are known exactly -- recompute the opposite
            # bound too, in case its own earlier mark had only the fallback
            # reference above to go on.
            other_wdg.setValue(exact_other_raw - outward)

    def _visit(self, top: bool | None = None, left: bool | None = None) -> None:
        device = self._device or self._mmc.getXYStageDevice()
        if top is None:
            y = self._mmc.getYPosition(device)
        else:
            y = self.top.value() if top else self.bottom.value()
        if left is None:
            x = self._mmc.getXPosition(device)
        else:
            x = self.left.value() if left else self.right.value()

        self._mmc.setXYPosition(device, x, y)
        self._mmc.waitForDevice(device)


# -------- helpers --------


class _PositionSpinBox(QDoubleSpinBox):
    def __init__(self) -> None:
        super().__init__()
        self.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.setRange(-99999999, 99999999)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSuffix(" µm")


class _MarkVisitButton(QPushButton):
    def __init__(
        self,
        name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._name = name
        self._visit_icon = QIconifyIcon(ICONS_GO[self._name])
        glyph, flip = ICONS_MARK[self._name]
        self._mark_icon = QIconifyIcon(glyph, flip=flip)

        self.setIcon(self._mark_icon)
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(f"Mark the {self._name} bound.")

    def setMark(self) -> None:
        """Set the icon to the mark icon."""
        self.setIcon(self._mark_icon)
        self.setToolTip(f"Mark the {self._name} bound.")

    def setVisit(self) -> None:
        """Set the icon to the visit icon."""
        self.setIcon(self._visit_icon)
        self.setToolTip(f"Move to the {self._name} bound.")


class _LeftAlignedPushButton(QPushButton):
    """A native push button whose icon/text group starts at the left edge."""

    def paintEvent(self, event: object) -> None:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_PushButtonBevel, option)

        contents = self.style().subElementRect(
            QStyle.SubElement.SE_PushButtonContents, option, self
        )
        icon_width = 0 if option.icon.isNull() else option.iconSize.width()
        spacing = 4 if icon_width and option.text else 0
        label_width = (
            icon_width + spacing + painter.fontMetrics().horizontalAdvance(option.text)
        )
        option.rect = contents
        option.rect.setWidth(min(contents.width(), label_width + 4))
        painter.drawControl(QStyle.ControlElement.CE_PushButtonLabel, option)


class MarkVisit(QWidget):
    def __init__(
        self,
        mark_glyph: str,
        mark_text: str = "",
        icon_size: int = ICON_SIZE,
        radius: int = RADIUS,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)

        mode = "top" if "top" in mark_text.lower() else "bottom"

        self.mark = _LeftAlignedPushButton(QIconifyIcon(mark_glyph), mark_text)
        self.mark.setIconSize(QSize(icon_size, icon_size))
        self.mark.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.visit = QPushButton(QIconifyIcon(ICONS_GO[mode]), "")
        self.visit.setIconSize(QSize(icon_size, icon_size))
        self.visit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.visit.setToolTip(f"Move to {mode}.")

        layout = QHBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.mark)
        layout.addWidget(self.visit)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    core = CMMCorePlus.instance()
    core.loadSystemConfiguration()
    wdg = CoreXYBoundsControl(core)

    wdg.show()
    sys.exit(app.exec())
