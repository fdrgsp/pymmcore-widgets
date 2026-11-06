"""Small building blocks shared by the points-plan widgets."""

from __future__ import annotations

from typing import TYPE_CHECKING

from qtpy.QtCore import QEvent, QObject, QPoint, Qt, Signal
from qtpy.QtWidgets import (
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QWidget,
)
from superqt.utils import signals_blocked

if TYPE_CHECKING:
    from collections.abc import Iterable

    from qtpy.QtWidgets import QLayout


def labeled_row(
    text: str, field: QWidget | QLayout, labels: list[QLabel] | None = None
) -> QHBoxLayout:
    """Return a `label | field` row; the label is appended to `labels` if given.

    Used instead of a QFormLayout so that labels of different groups (and of
    nested widgets) can share one fixed width, see `LabelAligner`.
    """
    label = QLabel(text)
    if labels is not None:
        labels.append(label)
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(5)
    row.addWidget(label, 0)
    if isinstance(field, QWidget):
        row.addWidget(field, 1)
    else:
        row.addLayout(field, 1)
    return row


class LabelAligner(QObject):
    """Keep a group of labels at the width of the widest one.

    The width is updated when a label's font or style changes. A label belongs to
    one aligner at a time: adding it to another one (e.g. a page-wide group that
    includes the labels of nested widgets) moves it there.
    """

    def __init__(self, parent: QObject | None, labels: Iterable[QLabel] = ()) -> None:
        super().__init__(parent)
        self._labels: list[QLabel] = []
        self.add(*labels)

    def add(self, *labels: QLabel) -> None:
        for label in labels:
            previous = getattr(label, "_label_aligner", None)
            if previous is self:
                continue
            if isinstance(previous, LabelAligner):
                previous._remove(label)
            label._label_aligner = self
            label.installEventFilter(self)
            self._labels.append(label)
        self.apply()

    def labels(self) -> list[QLabel]:
        return list(self._labels)

    def apply(self) -> None:
        if not self._labels:
            return
        width = max(label.sizeHint().width() for label in self._labels)
        for label in self._labels:
            if label.width() != width or label.minimumWidth() != width:
                label.setFixedWidth(width)

    def _remove(self, label: QLabel) -> None:
        label.removeEventFilter(self)
        self._labels.remove(label)
        self.apply()

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        if a1 is not None and a1.type() in (
            QEvent.Type.FontChange,
            QEvent.Type.StyleChange,
        ):
            self.apply()
        return False


class OverlapWidget(QWidget):
    """Overlap (%) between adjacent FOVs: one value, or separate x and y values.

    A single spin box sets the overlap along both axes. Right-click it (or use the
    context-menu key) to set different x and y overlaps, which shows a second spin
    box. `overlap_x` and `overlap_y` are always the effective x/y values.
    """

    valueChanged = Signal(object)  # tuple[float, float]

    def __init__(
        self, minimum: float, maximum: float, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._separate = False

        self.overlap_x = self._spin(minimum, maximum, "Overlap x (%)")
        self.overlap_y = self._spin(minimum, maximum, "Overlap y (%)")
        self.overlap_y.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.overlap_x, 1)
        layout.addWidget(self.overlap_y, 1)

        self.overlap_x.valueChanged.connect(self._on_x_changed)
        self.overlap_y.valueChanged.connect(self._on_y_changed)
        self._update_appearance()

    # _________________________PUBLIC METHODS_________________________ #

    def value(self) -> tuple[float, float]:
        """Return the (x, y) overlap in percent."""
        return self.overlap_x.value(), self.overlap_y.value()

    def setValue(self, value: tuple[float, float] | float) -> None:
        """Set the (x, y) overlap; different values show the separate y spin box."""
        x, y = value if isinstance(value, (tuple, list)) else (value, value)
        with signals_blocked(self.overlap_x), signals_blocked(self.overlap_y):
            self.overlap_x.setValue(x)
            self.overlap_y.setValue(y)
        self._set_separate(self.overlap_x.value() != self.overlap_y.value())
        self.valueChanged.emit(self.value())

    def isSeparate(self) -> bool:
        """Return True if x and y overlaps are set independently."""
        return self._separate

    def setSeparate(self, separate: bool) -> None:
        """Set whether x and y overlaps are set independently.

        Turning it off sets the y overlap to the x overlap.
        """
        if separate == self._separate:
            return
        self._set_separate(separate)
        if not separate and self.overlap_y.value() != self.overlap_x.value():
            with signals_blocked(self.overlap_y):
                self.overlap_y.setValue(self.overlap_x.value())
        self.valueChanged.emit(self.value())

    def setMinimum(self, minimum: float) -> None:
        """Set the minimum overlap (clamping, visibly, the current values)."""
        for spin in (self.overlap_x, self.overlap_y):
            spin.setMinimum(minimum)

    def contextMenu(self) -> QMenu:
        """Return the context menu offering separate x/y overlaps."""
        menu = QMenu(self)
        action = menu.addAction("Different X and Y Overlap")
        action.setCheckable(True)
        action.setChecked(self._separate)
        action.toggled.connect(self.setSeparate)
        return menu

    # _________________________PRIVATE METHODS_________________________ #

    def _spin(self, low: float, high: float, name: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spin.setRange(low, high)
        spin.setDecimals(1)
        spin.setAccessibleName(name)
        spin.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        spin.customContextMenuRequested.connect(
            lambda pos, s=spin: self._show_context_menu(s, pos)
        )
        return spin

    def _show_context_menu(self, spin: QDoubleSpinBox, pos: QPoint) -> None:
        self.contextMenu().exec(spin.mapToGlobal(pos))

    def _set_separate(self, separate: bool) -> None:
        self._separate = separate
        self.overlap_y.setVisible(separate)
        self._update_appearance()

    def _update_appearance(self) -> None:
        sep = self._separate
        self.overlap_x.setPrefix("x: " if sep else "")
        self.overlap_y.setPrefix("y: ")
        tip = "Right-click to use the same overlap along x and y."
        if not sep:
            tip = "Overlap along x and y. Right-click to set different x and y values."
        for spin in (self.overlap_x, self.overlap_y):
            spin.setToolTip(tip)

    def _on_x_changed(self, value: float) -> None:
        if not self._separate:
            with signals_blocked(self.overlap_y):
                self.overlap_y.setValue(value)
        self.valueChanged.emit(self.value())

    def _on_y_changed(self, value: float) -> None:
        # setting y directly to a different value means the overlaps are separate
        if not self._separate and value != self.overlap_x.value():
            self._set_separate(True)
        self.valueChanged.emit(self.value())
