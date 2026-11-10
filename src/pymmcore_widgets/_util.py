from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

import psutil
from psygnal import SignalGroup, SignalInstance
from pymmcore_plus import CMMCorePlus
from qtpy.QtCore import QEvent, QMarginsF, QObject, Qt, QTimer
from qtpy.QtGui import (
    QContextMenuEvent,
    QCursor,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
)
from qtpy.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGraphicsScene,
    QGraphicsView,
    QHeaderView,
    QLabel,
    QMenu,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from superqt.utils import signals_blocked

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from contextlib import AbstractContextManager
    from typing import Any


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
        if dialog.exec() == dialog.DialogCode.Accepted:
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
        if dialog.exec() == dialog.DialogCode.Accepted:
            return dialog.currentText()
    return None


def block_core(obj: Any) -> AbstractContextManager:
    """Block core signals."""
    if isinstance(obj, QObject):
        return signals_blocked(obj)  # type: ignore [no-any-return]
    if isinstance(obj, (SignalInstance, SignalGroup)):
        return obj.blocked()
    raise TypeError(f"Cannot block signals for {obj}")


def fov_kwargs(core: CMMCorePlus) -> dict:
    """Return image width and height in micron to be used for the grid plan."""
    if px := core.getPixelSizeUm():
        *_, width, height = core.getROI()
        return {"fov_width": (width * px) or None, "fov_height": (height * px) or None}
    return {}


# examples:
# "name_001" -> ("name", "001")
# "name" -> ("name", "")
# "name_001_002" -> ("name_001", "002")
# "name_02" -> ('name_02', None)
NUM_SPLIT = re.compile(r"(.*?)(?:_(\d{3,}))?$")


def get_next_available_path(requested_path: Path | str, min_digits: int = 3) -> Path:
    """Get the next available paths (filepath or folderpath if extension = "").

    This method adds a counter of min_digits to the filename or foldername to ensure
    that the path is unique.

    Parameters
    ----------
    requested_path : Path | str
        A path to a file or folder that may or may not exist.
    min_digits : int, optional
        The min_digits number of digits to be used for the counter. By default, 3.
    """
    if isinstance(requested_path, str):  # pragma: no cover
        requested_path = Path(requested_path)

    directory = requested_path.parent
    extension = requested_path.suffix
    # ome files like .ome.tiff or .ome.zarr are special,treated as a single extension
    if (stem := requested_path.stem).endswith(".ome"):
        extension = ".ome" + extension
        stem = stem[:-4]

    bare_path = directory / stem
    # remove any existing counter from the stem to get the base name
    requested_num = None
    if match := NUM_SPLIT.match(stem):
        base_stem, num = match.groups()
        if num:
            requested_num = int(num)
            stem = base_stem

    # look for existing files in the folder that share the same base stem.
    # Multi-file formats (e.g. multi-position OME-TIFF) collapse the requested
    # "<stem><extension>" file into a bare "<stem>" directory holding one file
    # per position, so also match directories that dropped the extension.
    current_max = 0
    exists = requested_path.exists() or (bool(extension) and bare_path.is_dir())
    for existing in directory.glob("*"):
        name = existing.name
        if not extension:
            base = name
        elif name.endswith(extension):
            base = name[: -len(extension)]
        elif existing.is_dir():
            base = name
        else:
            continue
        # if the base name ends with a number, increase the current_max
        if (
            (match := NUM_SPLIT.match(base))
            and match.group(1) == stem
            and (num := match.group(2))
        ):
            current_max = max(int(num), current_max)
            # if it has more digits than expected, update the ndigits
            if len(num) > min_digits:
                min_digits = len(num)

    # if the path does not exist and there are no existing files,
    # return the requested path
    if not exists and current_max == 0:
        return requested_path

    current_max += 1
    # if the requested path has a counter that is greater than any other files, use it
    if requested_num is not None:
        current_max = max(requested_num, current_max)
    return directory / f"{stem}_{current_max:0{min_digits}d}{extension}"


class RightClickMenu(QObject):
    """Open `menu` on a right-click on any of `widgets`, even if they are disabled.

    Qt does not deliver the context menu event to a disabled widget, but its event
    filters do still see the mouse press. QAbstractSpinBox also has its own context
    menu handler, which this takes the place of.

    The menu opens once Qt is done with the click, which goes on to send a context
    menu event after the press: were the menu already open, enabling the widget
    from it would have that event open the menu again as soon as it closes.

    Parameters
    ----------
    menu : QMenu
        The menu to open, which becomes the parent of this object.
    widgets : Iterable[QWidget]
        The widgets to open it from.
    available : Callable[[], bool]
        Whether to open it at all, asked on each click.
    """

    def __init__(
        self, menu: QMenu, widgets: Iterable[QWidget], available: Callable[[], bool]
    ) -> None:
        super().__init__(menu)
        self._menu = menu
        self._widgets = tuple(widgets)
        self._available = available
        # a child timer, rather than QTimer.singleShot, so that a pending opening
        # dies with this object
        self._open_timer = QTimer(self)
        self._open_timer.setSingleShot(True)
        self._open_timer.setInterval(0)
        self._open_timer.timeout.connect(self._open)
        for widget in self._widgets:
            widget.installEventFilter(self)

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        if a0 not in self._widgets:
            return False
        right_press_on_disabled = (
            isinstance(a1, QMouseEvent)
            and a1.type() == QEvent.Type.MouseButtonPress
            and a1.button() == Qt.MouseButton.RightButton
            and not cast("QWidget", a0).isEnabled()
        )
        if not (right_press_on_disabled or isinstance(a1, QContextMenuEvent)):
            return False
        if self._available():
            self._open_timer.start()
        return True

    def _open(self) -> None:
        self._menu.exec(QCursor.pos())


class _WheelBlocker(QObject):
    """Event filter that swallows mouse-wheel events."""

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        return a1 is not None and a1.type() == QEvent.Type.Wheel


def disable_wheel_scroll(widget: QWidget) -> None:
    """Prevent `widget`'s value from being changed by mouse-wheel scrolling.

    A combo/spin box sitting inside a scrollable or collapsible form can have its
    value changed by accident when the user scrolls the page with the mouse wheel
    instead of the intended target. This installs an event filter that ignores
    wheel events on `widget` unconditionally (regardless of focus).
    """
    widget._wheel_blocker = blocker = _WheelBlocker(widget)
    widget.installEventFilter(blocker)


def combo_box_height(widget: QWidget) -> int:
    """Return the height a combo box wants with `widget`'s font and style."""
    combo = QComboBox()
    combo.setStyle(widget.style())
    combo.setFont(widget.font())
    return int(combo.sizeHint().height())


class _ComboRowHeight(QObject):
    """Event filter keeping a table's rows as tall as a combo box."""

    def __init__(self, table: QTableView) -> None:
        super().__init__(table)
        self._table = table
        self.apply()

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        if a1 is not None and a1.type() in (
            QEvent.Type.FontChange,
            QEvent.Type.StyleChange,
        ):
            self.apply()
        return False

    def apply(self) -> None:
        table = self._table
        height = combo_box_height(table) + int(table.showGrid())
        v_header = cast("QHeaderView", table.verticalHeader())
        v_header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        v_header.setDefaultSectionSize(height)
        # the default is only for new rows
        if (model := table.model()) is not None:
            for row in range(model.rowCount()):
                table.setRowHeight(row, height)


def use_combo_row_height(table: QTableView) -> None:
    """Keep the rows of `table` as tall as a combo box, at every font and zoom.

    Unlike fitting rows to their contents, this costs nothing as rows are added,
    and rows look the same across tables, whatever they hold.
    """
    table._combo_row_height = row_height = _ComboRowHeight(table)
    table.installEventFilter(row_height)


def disable_wheel_scroll_recursive(widget: QWidget) -> None:
    """Apply [`disable_wheel_scroll`][] to `widget` and all its descendants.

    Targets `QComboBox` and `QAbstractSpinBox` instances (and subclasses, e.g.
    `QSpinBox`, `QDoubleSpinBox`, `superqt.QEnumComboBox`).
    """
    targets: list[QWidget] = []
    if isinstance(widget, (QComboBox, QAbstractSpinBox)):
        targets.append(widget)
    targets.extend(widget.findChildren(QComboBox))
    targets.extend(widget.findChildren(QAbstractSpinBox))
    for target in targets:
        disable_wheel_scroll(target)


# Qt's default (i.e. unset) maximum widget width/height. A C++ macro: PyQt exposes
# it as QtWidgets.QWIDGETSIZE_MAX, but PySide does not
QWIDGETSIZE_MAX = (1 << 24) - 1

# Default image-memory budget: leave room for the acquisition store and the
# rest of the application.
MEMORY_DEFAULT_FRACTION = 0.2
# Minimum default budget; an explicit user choice may be smaller.
MEMORY_DEFAULT_FLOOR_GB = 0.5


def memory_limit_defaults() -> tuple[float, float, float]:
    """Return (min, max, default) image-memory limits in GB, bounded by RAM.

    Shared by the Stage Explorer map and the automated plate-calibration scan.
    """
    vm = psutil.virtual_memory()
    total_gb = round(vm.total / 1024**3, 1)
    available_gb = vm.available / 1024**3
    default_gb = min(
        max(MEMORY_DEFAULT_FLOOR_GB, round(available_gb * MEMORY_DEFAULT_FRACTION, 1)),
        total_gb,
    )
    return (0.1, total_gb, default_gb)


# matches pymmcore-gui's status_green/status_red/status_yellow accents
GREEN = "#4caf50"
RED = "#ef5350"
YELLOW = "#ffff00"
ORANGE = "#ff9800"


class SeparatorWidget(QWidget):
    """A 1 px gray line, horizontal by default."""

    def __init__(
        self,
        parent: QWidget | None = None,
        orientation: Qt.Orientation = Qt.Orientation.Horizontal,
    ) -> None:
        super().__init__(parent)
        self._orientation = orientation
        if orientation == Qt.Orientation.Horizontal:
            self.setFixedHeight(1)
        else:
            self.setFixedWidth(1)

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        painter = QPainter(self)
        painter.setPen(QPen(Qt.GlobalColor.gray, 1, Qt.PenStyle.SolidLine))
        rect = self.rect()
        end = (
            rect.topRight()
            if self._orientation == Qt.Orientation.Horizontal
            else rect.bottomLeft()
        )
        painter.drawLine(rect.topLeft(), end)


class ResizingGraphicsView(QGraphicsView):
    """A QGraphicsView that resizes the scene to fit the view."""

    def __init__(self, scene: QGraphicsScene, parent: QWidget | None = None) -> None:
        super().__init__(scene, parent)
        self.padding = 0.05  # fraction of the bounding rect

    def resizeEvent(self, event: QResizeEvent | None) -> None:
        if not (scene := self.scene()):
            return
        rect = scene.itemsBoundingRect()
        xmargin = rect.width() * self.padding
        ymargin = rect.height() * self.padding
        margins = QMarginsF(xmargin, ymargin, xmargin, ymargin)
        self.fitInView(rect.marginsAdded(margins), Qt.AspectRatioMode.KeepAspectRatio)
        super().resizeEvent(event)


def own_vispy_init_context(canvas: Any) -> None:
    """Make the GL context and surface vispy's Qt backend creates die with its widget.

    vispy leaves that context current on the main thread, using it to render a canvas
    that was never shown, and only the Python canvas owns it. If the garbage collector
    happens to free the canvas on a worker thread, Qt clears the worker's current
    context rather than the main thread's, which is left pointing to a deleted context:
    the next surface destroyed there segfaults. As children of the widget, they are
    deleted with it, on the main thread, and a late collection only frees their
    wrappers.
    """
    native = canvas.native
    native._secondary_context.setParent(native)
    native._surface.setParent(native)


def load_system_config(config: str = "", mmcore: CMMCorePlus | None = None) -> None:
    """Internal convenience for `loadSystemConfiguration(config)`.

    This also unloads all devices first and resets the STATE.
    If config is `None` or empty string, will load the MMConfig_demo.
    Note that it should also always be fine for the end-user to use something like
    `CMMCorePlus.instance().loadSystemConfiguration(...)` (instead of this function)
    and we need to handle that as well.  So this function shouldn't get too complex.
    """
    mmc = mmcore or CMMCorePlus.instance()
    mmc.unloadAllDevices()
    mmc.loadSystemConfiguration(config or "MMConfig_demo.cfg")
