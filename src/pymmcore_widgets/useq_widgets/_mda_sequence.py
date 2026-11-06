from __future__ import annotations

from collections.abc import Mapping
from importlib.util import find_spec
from itertools import pairwise, permutations
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import useq
from qtpy.QtCore import QEvent, Qt, Signal
from qtpy.QtGui import QIcon
from qtpy.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedLayout,
    QStyle,
    QStyleOptionGroupBox,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from superqt.iconify import QIconifyIcon
from superqt.utils import signals_blocked

import pymmcore_widgets
from pymmcore_widgets._humanize import humanize_time
from pymmcore_widgets._util import QWIDGETSIZE_MAX, disable_wheel_scroll
from pymmcore_widgets.useq_widgets._autofocus_settings import (
    AutofocusSettingsDialog,
    TestRunner,
    _settings_button,
)
from pymmcore_widgets.useq_widgets._axis_tabs import (
    MDA_ICONS,
    _AxisTabBar,
    _AxisTabButton,
    _CheckableCard,
    _wrap_in_card,
)
from pymmcore_widgets.useq_widgets._channels import ChannelTable
from pymmcore_widgets.useq_widgets._checkable_tabwidget_widget import CheckableTabWidget
from pymmcore_widgets.useq_widgets._grid import GridPlanWidget
from pymmcore_widgets.useq_widgets._positions import AF_PER_POS_TOOLTIP, PositionTable
from pymmcore_widgets.useq_widgets._time import TimePlanWidget
from pymmcore_widgets.useq_widgets._z import Mode, ZPlanWidget

if TYPE_CHECKING:
    from collections.abc import Sequence

    from qtpy.QtGui import QShowEvent


def _check_order(x: str, first: str, second: str) -> bool:
    return first in x and second in x and x.index(first) > x.index(second)


PYMMCW_METADATA_KEY = "pymmcore_widgets"
NULL_SEQUENCE = useq.MDASequence()
AXES = "tpgcz"
ALLOWED_ORDERS = {"".join(p) for x in range(1, 6) for p in permutations(AXES, x)}
for x in list(ALLOWED_ORDERS):
    for first, second in (
        ("p", "g"),  # p cannot come after g
        ("p", "z"),  # p cannot come after z
        ("g", "z"),  # g cannot come after z
    ):
        if _check_order(x, first, second):
            ALLOWED_ORDERS.discard(x)


def populate_axis_order_combo(combo: QComboBox, used_axes: Sequence[str]) -> None:
    """Populate `combo` with the valid axis-order permutations of `used_axes`.

    Preserves the current selection if it's still a valid ordering, instead of
    resetting to whatever ends up first in the repopulated list.

    Disables the combo when there is at most one valid ordering (i.e. nothing
    meaningful to choose between).
    """
    current = combo.currentText()
    with signals_blocked(combo):
        combo.clear()
        for p in permutations(used_axes):
            if (strp := "".join(p)) in ALLOWED_ORDERS:
                combo.addItem(strp)
        if (idx := combo.findText(current)) >= 0:
            combo.setCurrentIndex(idx)
        combo.setEnabled(combo.count() > 1)


AF_AXIS_TOOLTIP = ""
AF_DISABLED_TOOLTIP = (
    "Autofocus cannot be used with absolute Z positions (TOP_BOTTOM mode).\n"
    "It corrects the focus position, which an absolute Z plan would then override.\n"
    "Switch the Z plan to a relative mode (RANGE_AROUND or ABOVE_BELOW) to use it."
)
AF_PER_POS_SOFTWARE_TOOLTIP = (
    "Per-position offsets are for the hardware autofocus device.\n"
    "A software routine finds focus from the images, so it has no offset to set."
)
AF_ENABLE_TOOLTIP = "Run an autofocus routine during the acquisition."
# Filled in when a search range is first asked for; MMStudio's own default.
DEFAULT_AF_SEARCH_STEP_UM = 5.0
AF_ON_AXIS_TOOLTIP = (
    "When to autofocus: every time one of these axes changes.\n"
    "\n"
    "p - at every stage position\n"
    "t - at every time point\n"
    "g - at every tile of a grid"
)
AF_MODE_TOOLTIP = (
    "Which kind of autofocus to run. An acquisition uses one or the other,\nnever both."
)
AF_HARDWARE_TOOLTIP = (
    "Use the microscope's autofocus device, e.g. Nikon PFS.\n"
    "\n"
    "It reflects light off the coverslip, so it is fast, takes no camera images and\n"
    "does not expose the sample -- but it holds a fixed distance from the coverslip\n"
    "rather than finding the sharpest image, and only locks within a limited range.\n"
    "\n"
    "Requires an autofocus device in the current configuration."
)
AF_SOFTWARE_TOOLTIP = (
    "Find the sharpest image by acquiring a short Z series and scoring each image.\n"
    "\n"
    "It focuses on the sample itself rather than on the coverslip, and needs no\n"
    "special hardware -- but every run costs several images, so it takes time and\n"
    "exposes the sample.\n"
    "\n"
    "Only one kind of autofocus runs in an acquisition."
)
AF_METHOD_TOOLTIP = (
    "Which image-based routine to run.\n"
    "\n"
    "They differ in how they search and how many images that costs; each one's\n"
    "own settings are behind the Settings button."
)
AF_SETTINGS_TOOLTIP = (
    "Edit this routine's settings: how far to search, how sharpness is measured,\n"
    "and which channel and exposure to focus with."
)
AF_NO_SOFTWARE_TOOLTIP = (
    "No software autofocus methods are available in this installation."
)
AF_INTERVAL_TOOLTIPS = {
    "p": (
        "Autofocus on every Nth position, starting with the first.\n"
        "Other enabled axes can still trigger autofocus between these positions."
    ),
    "g": (
        "Autofocus on every Nth grid tile, starting with the first.\n"
        "The tile index starts over in each position's grid.\n"
        "Other enabled axes can still trigger autofocus between these tiles."
    ),
    "t": (
        "Autofocus only on every Nth time point, starting with the first.\n"
        "Also limits position and grid triggers to these time points.\n"
        "Has no effect without a time series."
    ),
}
AF_SEARCH_TOOLTIP = (
    "What to do when the autofocus device cannot lock where it starts.\n"
    "\n"
    "Hardware autofocus only locks within a limited range of the coverslip, so a\n"
    "large move -- a new well, a tilted plate, or drift -- can leave the sample\n"
    "outside it. The focus device is then stepped down, and then up, retrying at\n"
    "each step until it locks.\n"
    "\n"
    "Set both distances to 0 to disable the search and only try the current position."
)
AF_SEARCH_BELOW_TOOLTIP = (
    "How far below the starting position to search for a lock. 0 disables it."
)
AF_SEARCH_ABOVE_TOOLTIP = (
    "How far above the starting position to search for a lock, after searching\n"
    "below. 0 disables it."
)
AF_SEARCH_STEP_TOOLTIP = (
    "Distance between attempts while searching for a lock.\n"
    "Smaller is more thorough but slower; it should be no larger than the device's\n"
    "lock range."
)


class MDATabs(CheckableTabWidget):
    """Checkable QTabWidget for editing a useq.MDASequence.

    It contains a Tab for each of the MDASequence, axis (channels, positions, etc...).
    """

    time_plan: TimePlanWidget
    stage_positions: PositionTable
    grid_plan: GridPlanWidget
    z_plan: ZPlanWidget
    channels: ChannelTable

    # (title, attribute) of every axis, in tab order
    _AXES = (
        ("Channels", "channels"),
        ("Positions", "stage_positions"),
        ("Grid/Tiles", "grid_plan"),
        ("Z Stack", "z_plan"),
        ("Time Series", "time_plan"),
    )
    # Inset every page's content so no editor sits flush against the tab's
    # edges (see `_add_tab`).
    _PAGE_MARGIN = 9

    def __init__(self, parent: QWidget | None = None) -> None:
        # Resolve editor widgets to their wrapping tab pages.
        self._page_for: dict[QWidget, QWidget] = {}
        # Preserve untinted icons for theme changes.
        self._tab_icons: dict[QWidget, QIcon] = {}
        super().__init__(parent)
        self.tabChecked.connect(self._on_tab_checked)

        self.create_subwidgets()
        self._build_tabs()

        # we only show the DO_STACK and ACQUIRE_EVERY columns when the
        # corresponding tab is checked
        ch_table = self.channels.table()
        ch_table.hideColumn(ch_table.indexOf(self.channels.DO_STACK))
        ch_table.hideColumn(ch_table.indexOf(self.channels.ACQUIRE_EVERY))

    def _build_tabs(self) -> None:
        """Add a tab for each axis editor, all unchecked, with Channels selected.

        Subclasses may override this to present the editors differently (e.g. as
        collapsible sections).
        """
        self.setTabBar(_AxisTabBar(self))
        self.setDocumentMode(True)
        for title, attr in self._AXES:
            widget = cast("QWidget", getattr(self, attr))
            card = attr in ("grid_plan", "z_plan")
            self._add_tab(widget, title, attr, checked=False, card=card)
            # Each tab fills its page, so tables do not need resize grips.
            if (grip := getattr(widget, "_resize_grip", None)) is not None:
                grip.hide()
        self.setCurrentWidget(self.channels)
        if tab_bar := self.tabBar():
            tab_bar.setExpanding(False)

    def _add_tab(
        self,
        widget: QWidget,
        title: str,
        icon_key: str,
        *,
        checked: bool,
        card: bool = False,
    ) -> int:
        """Add a tab with an icon-on-top / title-below button widget.

        Every page is a container holding ``widget`` inset by ``_PAGE_MARGIN``,
        so no editor sits flush against the tab's edges; ``card=True`` puts a
        bordered card inside a scroll area in between. ``widget`` is stored in
        ``_page_for`` either way, so all other methods address the real widget
        rather than whichever container it ended up in.
        """
        icon = QIconifyIcon(MDA_ICONS[icon_key])
        index = int(self.count())

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(
            self._PAGE_MARGIN, self._PAGE_MARGIN, self._PAGE_MARGIN, self._PAGE_MARGIN
        )
        if card:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(_wrap_in_card(widget, stretch=True))
            page_layout.addWidget(scroll)
        else:
            page_layout.addWidget(widget)
            # Keep editors with a maximum height at the top of the page.
            if widget.maximumHeight() < QWIDGETSIZE_MAX:
                page_layout.addStretch()
        self._page_for[widget] = page
        self._tab_icons[page] = icon
        # reparenting into the page can leave the editor hidden
        widget.show()

        QTabWidget.addTab(self, page, "")

        btn = _AxisTabButton(title, icon, checked=checked)
        # _AxisTabButton implements the checkbox API used by the base class.
        self._cboxes.append(cast("QCheckBox", btn))
        # A disabled page would override later enablement of its editor.
        btn.toggled.connect(lambda c, w=widget: self._on_tab_checkbox_toggled(c, w))
        if not checked:
            widget.setEnabled(False)
        if tab_bar := self.tabBar():
            tab_bar.setTabButton(index, QTabBar.ButtonPosition.LeftSide, btn)
            tab_bar.setTabToolTip(index, title)

        return index

    def indexOf(self, widget: QWidget | None) -> int:
        """Return the tab index for ``widget``, resolving through its page."""
        page = self._page_for.get(widget, widget) if widget is not None else None
        return int(QTabWidget.indexOf(self, page))

    def setCurrentWidget(self, widget: QWidget | None) -> None:
        """Select the tab for ``widget``, resolving through its page."""
        if widget is None:
            return
        QTabWidget.setCurrentWidget(self, self._page_for.get(widget, widget))

    def currentWidget(self) -> QWidget | None:
        """Return the axis/section widget, not its wrapping page container."""
        page = QTabWidget.currentWidget(self)
        for widget, p in self._page_for.items():
            if p is page:
                return widget
        return page

    def tabIcon(self, index: int) -> QIcon:
        """Return the stored icon; icons live in the button widget, not the tab."""
        return self.tab_icon(index) or QIcon()

    def tab_icon(self, index: int) -> QIcon | None:
        """Return the plain (undyed) icon a tab was created with, if any."""
        page = QTabWidget.widget(self, index)
        return self._tab_icons.get(page) if page is not None else None

    def set_tab_icon(self, index: int, icon: QIcon) -> None:
        """Replace the displayed button icon, preserving the source in `tab_icon`."""
        if tab_bar := self.tabBar():
            btn = tab_bar.tabButton(index, QTabBar.ButtonPosition.LeftSide)
            if isinstance(btn, _AxisTabButton):
                btn.set_icon(icon)

    def create_subwidgets(self) -> None:
        """Create the Tabs of the widget."""
        self.time_plan = TimePlanWidget(0)
        self.stage_positions = PositionTable(0)
        self.grid_plan = GridPlanWidget()
        self.z_plan = ZPlanWidget()
        self.channels = ChannelTable(0)

    def isAxisUsed(self, key: str | QWidget) -> bool:
        """Return True if the given axis is used in the sequence.

        Parameters
        ----------
        key : str | QWidget
            The axis to check. Can be one of "c", "t", "p", "g", "z", or the
            corresponding widget instance (e.g. self.channels, etc...)
        """
        if isinstance(key, str):
            _map: dict[str, QWidget] = {
                "c": self.channels,
                "t": self.time_plan,
                "p": self.stage_positions,
                "z": self.z_plan,
                "g": self.grid_plan,
            }
            if (lower_key := key[0].lower()) in _map:
                key = _map[lower_key]
            else:
                raise ValueError(f"Invalid key: {key!r}")  # pragma: no cover
        return bool(self.isChecked(key))

    def usedAxes(self) -> tuple[str, ...]:
        """Return all axes used by the sequence or any position sub-sequence."""
        axes = {key for key in "tpgzc" if self.isAxisUsed(key)}
        positions = list(self.stage_positions.value()) if "p" in axes else []

        # Position sub-sequences are branches of this acquisition, so their
        # axes also belong in the one, global axis order.  This is recursive to
        # support sequences created programmatically even though the position
        # editor itself prevents nesting positions in positions.
        while positions:
            position = positions.pop()
            if sequence := position.sequence:
                axes.update(sequence.used_axes)
                positions.extend(sequence.stage_positions)

        return tuple(key for key in "tpgzc" if key in axes)

    def value(self) -> useq.MDASequence:
        """Return the current sequence as a [`useq.MDASequence`][]."""
        grid_plan = self.grid_plan.value() if self.isAxisUsed("g") else None
        positions = self.stage_positions.value() if self.isAxisUsed("p") else ()

        # If a global absolute grid plan is used, x/y on positions are
        # meaningless (the grid defines them). Clear them to avoid useq
        # validation warnings.
        if grid_plan is not None and not grid_plan.is_relative and positions:
            positions = tuple(
                pos.replace(x=None, y=None)
                if pos.x is not None or pos.y is not None
                else pos
                for pos in positions
            )

        return useq.MDASequence(
            z_plan=self.z_plan.value() if self.isAxisUsed("z") else None,
            time_plan=self.time_plan.value() if self.isAxisUsed("t") else None,
            stage_positions=positions,
            channels=self.channels.value() if self.isAxisUsed("c") else (),
            grid_plan=grid_plan,
            metadata={PYMMCW_METADATA_KEY: {"version": pymmcore_widgets.__version__}},
        )

    def setValue(self, value: useq.MDASequence) -> None:
        """Set widget value from a [`useq.MDASequence`][].

        Preserve the current tab while checking the restored sequence's axes.
        """
        if not isinstance(value, useq.MDASequence):  # pragma: no cover
            raise TypeError(f"Expected useq.MDASequence, got {type(value)}")

        current = self.currentIndex()
        widget: (
            ChannelTable | TimePlanWidget | ZPlanWidget | PositionTable | GridPlanWidget
        )
        for f in ("channels", "time_plan", "z_plan", "stage_positions", "grid_plan"):
            widget = getattr(self, f)
            if field_val := getattr(value, f):
                widget.setValue(field_val)
                self.setChecked(widget, True)
            else:
                # widget.setValue(None)
                self.setChecked(widget, False)
        self.setCurrentIndex(current)

    def _on_tab_checked(self, idx: int, checked: bool) -> None:
        """Handle tabChecked signal.

        Hide columns in the channels tab accordingly.
        """
        _map = {
            self.indexOf(self.z_plan): self.channels.DO_STACK,
            self.indexOf(self.time_plan): self.channels.ACQUIRE_EVERY,
        }
        if idx in _map:
            ch_table = self.channels.table()
            ch_table.setColumnHidden(ch_table.indexOf(_map[idx]), not checked)


def _installed_software_methods() -> tuple[dict[str, type], dict[str, str]]:
    """The software autofocus routines the acquisition engine can run.

    Returns their settings models and their descriptions, taken from
    `pymmcore_plus` so the widget works out of the box; pass something else to
    `AutofocusAxis.setSoftwareMethods` to offer a different set.
    """
    try:
        from pymmcore_plus.autofocus import available_methods, get_method
    except ImportError:  # pragma: no cover  (an older pymmcore-plus)
        return {}, {}
    entries = {name: get_method(name) for name in available_methods()}
    return (
        {name: entry.settings_model for name, entry in entries.items()},
        {name: entry.description for name, entry in entries.items()},
    )


class AutofocusAxis(_CheckableCard):
    """Autofocus settings: whether to use it, which kind, when, and how it searches.

    A checkable group box, whose header switches the controls on and off:
    `isChecked()` says whether autofocus will run, and unchecking it disables the
    controls inside. `Hardware` and `Software` are exclusive, since an acquisition
    carries a single autofocus plan; `Software` stays disabled until software
    autofocus methods are available (see `setSoftwareMethods`).

    The rows keep the same compact spacing in both autofocus modes.
    """

    valueChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setTitle("Autofocus")
        self.setCheckable(True)
        self.setChecked(False)
        self.setToolTip(AF_ENABLE_TOOLTIP)

        self.label = QLabel("On Axis:")
        self.label.setToolTip(AF_ON_AXIS_TOOLTIP)
        self.use_af_p = QCheckBox("p")
        self.use_af_t = QCheckBox("t")
        self.use_af_g = QCheckBox("g")
        for _box in (self.use_af_p, self.use_af_t, self.use_af_g):
            _box.setToolTip(AF_ON_AXIS_TOOLTIP)

        # --- which kind of autofocus (exclusive) ---
        self._mode_label = QLabel("Mode:")
        self._mode_label.setToolTip(AF_MODE_TOOLTIP)
        self.use_hardware = QRadioButton("Hardware")
        self.use_hardware.setChecked(True)
        self.use_software = QRadioButton("Software")
        self._kind_group = QButtonGroup(self)
        self._kind_group.addButton(self.use_hardware)
        self._kind_group.addButton(self.use_software)

        # --- hardware only: search for a lock if autofocus fails where it starts ---
        self._search_label = QLabel("Search:")
        self._search_label.setToolTip(AF_SEARCH_TOOLTIP)
        # all zero: autofocus is only attempted where it starts, until asked for more
        self.search_below_um = self._search_spin("below ", AF_SEARCH_BELOW_TOOLTIP)
        self.search_above_um = self._search_spin("above ", AF_SEARCH_ABOVE_TOOLTIP)
        self.search_step_um = self._search_spin("step ", AF_SEARCH_STEP_TOOLTIP)

        # --- software only: which routine, and its settings ---
        self._method_label = QLabel("Method:")
        self.method = QComboBox()
        self.settings_button = _settings_button(AF_SETTINGS_TOOLTIP)
        # each method keeps its own settings, so switching back and forth does not
        # discard what was configured
        self._method_models: dict[str, type] = {}
        self._method_descriptions: dict[str, str] = {}
        self._method_settings: dict[str, dict[str, Any]] = {}
        # set by the core-aware widget, which knows which microscope to drive
        self._test_runner: TestRunner | None = None
        # set by the core-aware widget, which knows what devices are loaded
        self._software_devices_ok = True
        self._software_unavailable_reason = ""
        self._hardware_devices_ok = True
        self._hardware_unavailable_reason = ""

        self.every_n_positions = QSpinBox()
        self.every_n_grid_positions = QSpinBox()
        self.every_n_timepoints = QSpinBox()
        self._axis_intervals = (
            ("p", self.use_af_p, QLabel("every"), self.every_n_positions),
            ("g", self.use_af_g, QLabel("every"), self.every_n_grid_positions),
            ("t", self.use_af_t, QLabel("every"), self.every_n_timepoints),
        )
        for axis, _, _, spin in self._axis_intervals:
            spin.setRange(1, 10000)
            spin.setValue(1)
            spin.setSuffix(f" {axis}")
            spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            disable_wheel_scroll(spin)

        # Preserve plan fields this widget cannot edit, such as offsets and retries.
        self._hardware_plan: useq.AxesBasedAF | None = None
        self._software_plan: useq.SoftwareAxesBasedAF | None = None

        # one label column for the three rows, so their controls line up
        _labels = (
            self.label,
            self._mode_label,
            self._search_label,
            self._method_label,
        )
        label_width = max(w.sizeHint().width() for w in _labels)
        for w in _labels:
            w.setFixedWidth(label_width)

        axis_row = QHBoxLayout()
        axis_row.setSpacing(4)
        for i, (_, checkbox, label, spin) in enumerate(self._axis_intervals):
            if i:
                axis_row.addSpacing(15)
            group = QWidget()
            row = QHBoxLayout(group)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            row.addWidget(checkbox)
            row.addSpacing(5)
            row.addWidget(label)
            row.addWidget(spin)
            axis_row.addWidget(group)
        axis_row.addStretch()
        mode_row = _row(self.use_hardware, self.use_software)
        search_row = _row(
            self.search_below_um,
            self.search_above_um,
            self.search_step_um,
        )

        software_row = _row(self.method, self.settings_button)

        # Reserve the larger size of the two modes, including the hidden page.
        self._kind_stack = QStackedLayout()
        for row in (search_row, software_row):
            page = QWidget()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
            page.setLayout(row)
            self._kind_stack.addWidget(page)

        options = QWidget()
        options.setLayout(self._kind_stack)
        self._kind_labels = QStackedLayout()
        self._kind_labels.addWidget(self._search_label)
        self._kind_labels.addWidget(self._method_label)
        kind_label = QWidget()
        kind_label.setLayout(self._kind_labels)
        controls = []
        for row in (axis_row, mode_row):
            page = QWidget()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
            page.setLayout(row)
            controls.append(page)
        self._rows = (
            (self.label, controls[0]),
            (self._mode_label, controls[1]),
            (kind_label, options),
        )

        layout = QGridLayout(self)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(5)
        layout.setContentsMargins(5, 5, 5, 0)
        layout.setColumnStretch(1, 1)
        for i, row_widgets in enumerate(self._rows):
            for column, widget in enumerate(row_widgets):
                layout.addWidget(widget, 2 * i, column)

        self.toggled.connect(self._on_af_state_changed)
        for _, checkbox, _, spin in self._axis_intervals:
            checkbox.toggled.connect(self._on_af_state_changed)
            spin.valueChanged.connect(self.valueChanged)
        # one connection is enough: the two radios are exclusive, so any change
        # toggles `use_hardware`
        self.use_hardware.toggled.connect(self._on_af_state_changed)
        self.search_below_um.valueChanged.connect(self._on_search_range_changed)
        self.search_above_um.valueChanged.connect(self._on_search_range_changed)
        self.search_step_um.valueChanged.connect(self.valueChanged)
        self.method.currentTextChanged.connect(self._on_method_changed)
        self.settings_button.clicked.connect(self._edit_settings)

        self._update_kind_widgets()
        self.setSoftwareMethods(*_installed_software_methods())
        self._update_row_spacing()

    def _update_row_spacing(self) -> None:
        layout = cast("QGridLayout", self.layout())
        heights = [
            max(layout.itemAtPosition(2 * i, c).sizeHint().height() for c in (0, 1))
            for i in range(len(self._rows))
        ]
        # QRect.center() rounds odd and even heights differently. Measure the
        # actual center-to-center steps rather than averaging adjacent heights.
        centers = [(height - 1) // 2 for height in heights]
        steps = [
            height + after - before + layout.verticalSpacing()
            for height, (before, after) in zip(heights, pairwise(centers), strict=False)
        ]
        option = QStyleOptionGroupBox()
        self.initStyleOption(option)
        title = self.style().subControlRect(
            QStyle.ComplexControl.CC_GroupBox,
            option,
            QStyle.SubControl.SC_GroupBoxLabel,
            self,
        )
        title_center = title.center().y()
        pitch = max(max(steps), self.contentsRect().top() + centers[0] - title_center)
        for i, step in enumerate(steps):
            layout.setRowMinimumHeight(2 * i + 1, pitch - step)
        top = title_center + pitch - self.contentsRect().top() - centers[0]
        layout.setContentsMargins(5, top, 5, 0)
        layout.activate()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.LayoutRequest:
            self._update_row_spacing()
        return bool(super().event(event))

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._update_row_spacing()

    def _search_spin(self, prefix: str, tooltip: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 10000.0)
        spin.setDecimals(1)
        spin.setSingleStep(1.0)
        spin.setPrefix(prefix)
        spin.setSuffix(" \u00b5m")
        spin.setToolTip(tooltip)
        disable_wheel_scroll(spin)
        return spin

    def _on_search_range_changed(self) -> None:
        """Give the search a step as soon as a range asks for one.

        A range with no step is not a search anyone meant to configure, and the
        schema rejects it, so asking for one fills in a usable step rather than
        leaving the section in a state that cannot be turned into a plan.
        """
        wants_search = bool(
            self.search_below_um.value() or self.search_above_um.value()
        )
        if wants_search and not self.search_step_um.value():
            with signals_blocked(self.search_step_um):
                self.search_step_um.setValue(DEFAULT_AF_SEARCH_STEP_UM)
        self.valueChanged.emit()

    # -------------------------------- kind --------------------------------

    def kind(self) -> str | None:
        """Return `"hardware"`, `"software"`, or `None` if autofocus is off.

        A mode that is switched off because the microscope cannot run it -- no
        autofocus device, or no software routines -- yields `None`, so no plan is
        built for something that could not run.
        """
        if not self.isEnabled() or not self.isChecked():
            return None
        if self.use_software.isChecked():
            return "software" if self.use_software.isEnabled() else None
        return "hardware" if self.use_hardware.isEnabled() else None

    def setKind(self, kind: str | None) -> None:
        """Select the kind of autofocus, or switch autofocus off with `None`."""
        with signals_blocked(self), signals_blocked(self.use_hardware):
            self.setChecked(kind is not None)
            if kind == "software":
                self.use_software.setChecked(True)
            elif kind == "hardware":
                self.use_hardware.setChecked(True)
        self._update_kind_widgets()

    def setSoftwareMethods(
        self,
        methods: Sequence[str] | Mapping[str, type] | None,
        descriptions: Mapping[str, str] | None = None,
    ) -> None:
        """Offer these software autofocus routines, enabling the `Software` option.

        Pass a mapping of name to settings dataclass to get an editable settings form
        for each; a plain sequence of names offers the routines without one.
        `descriptions` says how each routine finds focus, shown when hovering it --
        which is the only way to choose between them without reading the source.
        """
        methods = methods or {}
        names = list(methods)
        self._method_models = dict(methods) if isinstance(methods, Mapping) else {}
        self._method_descriptions = dict(descriptions or {})

        with signals_blocked(self.method):
            current = self.method.currentText()
            self.method.clear()
            self.method.addItems(names)
            for i, name in enumerate(names):
                if doc := self._method_descriptions.get(name):
                    self.method.setItemData(i, doc, Qt.ItemDataRole.ToolTipRole)
            if current in names:
                self.method.setCurrentText(current)
        self._update_method_tooltip()
        self._update_kind_widgets()

    def _update_method_tooltip(self) -> None:
        """Describe the selected routine on the closed combo, not just in the list."""
        doc = self._method_descriptions.get(self.softwareMethod(), "")
        self.method.setToolTip(doc or AF_METHOD_TOOLTIP)
        self._method_label.setToolTip(self.method.toolTip())

    def setTestRunner(self, runner: TestRunner | None) -> None:
        """Supply a way to run a routine now, for the settings dialog's Test button.

        Testing a routine drives the stage and the camera, so it takes a
        core-aware widget to say *which* microscope. Without one the button is
        not offered: the settings can still be edited, just not tried out.
        """
        self._test_runner = runner

    def setHardwareAvailable(self, available: bool, reason: str = "") -> None:
        """Say whether the microscope has an autofocus device to drive.

        `reason` is shown as the tooltip when it does not.
        """
        self._hardware_devices_ok = available
        self._hardware_unavailable_reason = reason
        self._update_kind_widgets()

    def isHardwareAvailable(self) -> bool:
        """Whether a hardware autofocus device is there to drive."""
        return self._hardware_devices_ok

    def setSoftwareAvailable(self, available: bool, reason: str = "") -> None:
        """Say whether the microscope can run a software routine at all.

        It needs a camera to acquire with and a focus drive to move -- but no
        autofocus device, which is what sets it apart from the hardware kind.
        `reason` is shown as the tooltip when it cannot.
        """
        self._software_devices_ok = available
        self._software_unavailable_reason = reason
        self._update_kind_widgets()

    def isSoftwareAvailable(self) -> bool:
        """Whether a software routine could run: one exists and the devices are there.

        This is a fact about the installation, not about the widget's current state,
        so it is safe to use when deciding what to enable -- unlike asking a child
        widget whether it is enabled, which also depends on this widget being enabled.
        """
        return self.method.count() > 0 and self._software_devices_ok

    def softwareMethod(self) -> str:
        """The selected software autofocus routine."""
        return str(self.method.currentText())

    def softwareSettings(self) -> dict[str, Any]:
        """The settings for the selected routine."""
        return dict(self._method_settings.get(self.softwareMethod(), {}))

    def _on_method_changed(self) -> None:
        self._update_kind_widgets()
        self._update_method_tooltip()
        self.valueChanged.emit()

    def _edit_settings(self) -> None:
        method = self.softwareMethod()
        if (model := self._method_models.get(method)) is None:  # pragma: no cover
            return
        dialog = AutofocusSettingsDialog(
            model,
            self.softwareSettings(),
            method=method,
            parent=self,
            # so a routine built from others can be pointed at them here
            methods=self._method_models,
            descriptions=self._method_descriptions,
            test_runner=self._test_runner,
        )
        if dialog.exec():
            self._method_settings[method] = dialog.value()
            self.valueChanged.emit()

    def _on_af_state_changed(self) -> None:
        self._update_kind_widgets()
        self.valueChanged.emit()

    def _update_kind_widgets(self) -> None:
        """Apply every enabled and visible state in one place.

        A control is enabled only if the microscope can actually use it and
        autofocus is switched on.

        The Z search is a hardware-autofocus recovery; the time-point schedule
        applies to both kinds and all axis triggers.
        """
        on = self.isChecked()
        self.settings_button.setEnabled(
            on and self.softwareMethod() in self._method_models
        )

        # --- which modes the microscope can run ---
        self.use_hardware.setEnabled(on and self._hardware_devices_ok)
        self.use_hardware.setToolTip(
            AF_HARDWARE_TOOLTIP
            if self._hardware_devices_ok
            else (self._hardware_unavailable_reason or AF_HARDWARE_TOOLTIP)
        )
        software = self.isSoftwareAvailable()
        self.use_software.setEnabled(on and software)
        if software:
            tooltip = AF_SOFTWARE_TOOLTIP
        elif self.method.count() == 0:
            tooltip = AF_NO_SOFTWARE_TOOLTIP
        else:
            tooltip = self._software_unavailable_reason or AF_NO_SOFTWARE_TOOLTIP
        self.use_software.setToolTip(tooltip)

        index = 0 if self.use_hardware.isChecked() else 1
        self._kind_stack.setCurrentIndex(index)
        self._kind_labels.setCurrentIndex(index)
        for axis, checkbox, label, spin in self._axis_intervals:
            enabled = on and checkbox.isChecked() and self.kind() is not None
            tooltip = AF_INTERVAL_TOOLTIPS[axis]
            if not checkbox.isChecked():
                tooltip += f'\n\nCheck "{axis}" to edit this setting.'
            for wdg in (label, spin):
                wdg.setEnabled(enabled)
                wdg.setToolTip(tooltip)
        self._update_row_spacing()

    # ------------------------------- value --------------------------------

    def value(self) -> tuple[str, ...]:
        """Return the autofocus axes, or `()` if autofocus is off."""
        af_axis: tuple[str, ...] = ()
        if self.kind() is None:
            return af_axis
        if self.use_af_p.isChecked():
            af_axis += ("p",)
        if self.use_af_t.isChecked():
            af_axis += ("t",)
        if self.use_af_g.isChecked():
            af_axis += ("g",)
        return af_axis

    def setValue(self, value: tuple[str, ...]) -> None:
        """Set widget value from a tuple of autofocus axes."""
        self.use_af_p.setChecked("p" in value)
        self.use_af_t.setChecked("t" in value)
        self.use_af_g.setChecked("g" in value)
        self._update_kind_widgets()
        if value and not self.isChecked():
            self.setKind(self.kind() or "hardware")

    def plan(self, axes: tuple[str, ...], **kwargs: object) -> useq.AnyAutofocusPlan:
        """Build the autofocus plan of the selected kind, for `axes`.

        `kwargs` are extra fields for the plan (e.g. `autofocus_motor_offset`), and
        take precedence over the values carried by the last plan this widget was given.
        """
        updates: dict = {
            "axes": axes,
            "every_n_positions": self.every_n_positions.value(),
            "every_n_grid_positions": self.every_n_grid_positions.value(),
            "every_n_timepoints": self.every_n_timepoints.value(),
        }
        if self.kind() == "software":
            if method := self.softwareMethod():
                updates["method"] = method
                updates["settings"] = self.softwareSettings()
            soft = self._software_plan or useq.SoftwareAxesBasedAF(axes=axes, method="")
            return soft.replace(**(updates | kwargs))
        updates["search_below_um"] = self.search_below_um.value()
        updates["search_above_um"] = self.search_above_um.value()
        updates["search_step_um"] = self.search_step_um.value()
        hard = self._hardware_plan or useq.AxesBasedAF(axes=axes)
        return hard.replace(**(updates | kwargs))

    def setPlan(self, plan: useq.AnyAutofocusPlan | None) -> None:
        """Restore the kind and per-kind settings from `plan`."""
        if plan is None:
            self.setKind(None)
            return
        with signals_blocked(self):
            self.every_n_positions.setValue(plan.every_n_positions)
            self.every_n_grid_positions.setValue(plan.every_n_grid_positions)
            self.every_n_timepoints.setValue(plan.every_n_timepoints)
            if isinstance(plan, useq.SoftwareAxesBasedAF):
                self._software_plan = plan
                if plan.method:
                    if self.method.findText(plan.method) < 0:
                        # a routine this installation does not have: keep it, so the
                        # sequence is not quietly changed to a different one
                        self.method.addItem(plan.method)
                    self.method.setCurrentText(plan.method)
                    self._method_settings[plan.method] = dict(plan.settings)
                self.setKind("software")
            else:
                self._hardware_plan = plan
                self.search_below_um.setValue(plan.search_below_um)
                self.search_above_um.setValue(plan.search_above_um)
                self.search_step_um.setValue(plan.search_step_um)
                self.setKind("hardware")


def _row(*widgets: QWidget) -> QHBoxLayout:
    """Return a row of `widgets` packed to the left."""
    row = QHBoxLayout()
    row.setSpacing(10)
    for widget in widgets:
        row.addWidget(widget)
    row.addStretch()
    return row


class KeepShutterOpen(QWidget):
    valueChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        lbl = QLabel("Keep Shutter Open Across Axis:")
        self.leave_open_t = QCheckBox("t")
        self.leave_open_z = QCheckBox("z")

        layout = QHBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(lbl)
        layout.addWidget(self.leave_open_z)
        layout.addWidget(self.leave_open_t)
        layout.addStretch()

        self.leave_open_t.toggled.connect(self.valueChanged)
        self.leave_open_z.toggled.connect(self.valueChanged)

        self.setToolTip("Keep the shutter open across the selected axes.")

    def value(self) -> tuple[str, ...]:
        """Return the axes to keep the shutter open across."""
        shutters: tuple[str, ...] = ()
        if self.leave_open_z.isChecked() and self.leave_open_z.isEnabled():
            shutters += ("z",)
        if self.leave_open_t.isChecked() and self.leave_open_t.isEnabled():
            shutters += ("t",)
        return shutters

    def setValue(self, value: tuple[str, ...]) -> None:
        """Set widget value from a tuple of axes to keep the shutter open across."""
        self.leave_open_z.setChecked("z" in value)
        self.leave_open_t.setChecked("t" in value)


class MDASequenceWidget(QWidget):
    """A widget that provides a GUI to construct and edit a [`useq.MDASequence`][].

    This widget requires no connection to a microscope or core instance.  It strictly
    deals with loading and creating `useq-schema` [`useq.MDASequence`][] objects.
    """

    valueChanged = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        tab_widget: MDATabs | None = None,
    ) -> None:
        super().__init__(parent)

        # -------------- Main MDA Axis Widgets --------------

        self.tab_wdg = tab_widget or MDATabs(self)

        self.axis_order = QComboBox()
        self.axis_order.setToolTip("Slowest to fastest axis order.")
        self.axis_order.setMinimumWidth(80)
        disable_wheel_scroll(self.axis_order)

        # -------------- Other Widgets --------------

        # QLabel with standard warning icon to indicate time overflow
        style = self.style()
        warning_icon = style.standardIcon(style.StandardPixmap.SP_MessageBoxWarning)
        self._time_warning = QLabel()
        self._time_warning.setToolTip(
            "The current settings will be unable to satisfy<br>"
            "the time interval requested in the time tab."
        )
        self._time_warning.setPixmap(warning_icon.pixmap(24, 24))
        self._time_warning.hide()
        self._duration_label = QLabel()
        self._duration_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._duration_label.setWordWrap(True)

        self._save_button = QPushButton("Save MDA Settings")
        self._save_button.setIcon(QIconifyIcon("mdi:content-save-outline"))
        self._save_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._save_button.clicked.connect(self.save)
        self._load_button = QPushButton("Load MDA Settings")
        self._load_button.setIcon(QIconifyIcon("material-symbols:upload-rounded"))
        self._load_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._load_button.clicked.connect(self.load)

        # -------------- Main Layout --------------

        self.keep_shutter_open = KeepShutterOpen()
        self.af_axis = AutofocusAxis()

        # Global settings are not a tab: they have no on/off state and always
        # apply, so they sit in a card of their own between the tabs and the
        # footer -- always visible, whichever tab is showing.
        self._settings_widget = settings_widget = QWidget()
        settings_layout = QVBoxLayout(settings_widget)
        settings_layout.setContentsMargins(5, 5, 5, 5)
        settings_layout.setSpacing(5)
        axis_layout = QHBoxLayout()
        axis_layout.addWidget(QLabel("Axis Order:"))
        self.axis_order.setMaximumWidth(100)
        axis_layout.addWidget(self.axis_order)
        axis_layout.addStretch()
        settings_layout.addLayout(axis_layout)
        settings_layout.addWidget(self.keep_shutter_open)
        self._settings_box = _wrap_in_card(settings_widget)
        self._settings_row = settings_row = QVBoxLayout()
        settings_row.setSpacing(10)
        settings_row.addWidget(self._settings_box)
        settings_row.addWidget(self.af_axis)

        footer = QFrame()
        footer.setObjectName("mdaExecutionFooter")
        self._footer_layout = footer_layout = QVBoxLayout(footer)

        estimate_row = QHBoxLayout()
        estimate_row.addWidget(self._time_warning)
        estimate_row.addWidget(self._duration_label, 1)
        footer_layout.addLayout(estimate_row)

        # a subclass appends its own actions (e.g. Run) after the stretch
        self._actions_row = actions_row = QHBoxLayout()
        actions_row.addWidget(self._save_button)
        actions_row.addWidget(self._load_button)
        actions_row.addStretch()
        footer_layout.addLayout(actions_row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tab_wdg, 1)
        layout.addLayout(settings_row)
        layout.addWidget(footer)
        self._apply_layout_metrics()

        # -------------- Connections --------------

        self.channels.valueChanged.connect(self.valueChanged)
        self.time_plan.valueChanged.connect(self.valueChanged)
        self.stage_positions.valueChanged.connect(self._update_available_axis_orders)
        self.z_plan.valueChanged.connect(self._validate_af_with_z_plan)
        self.grid_plan.valueChanged.connect(self._on_grid_plan_value_changed)
        self.tab_wdg.tabChecked.connect(self._on_tab_checked)
        self.axis_order.currentTextChanged.connect(self.valueChanged)
        self.valueChanged.connect(self._update_time_estimate)

        self.keep_shutter_open.valueChanged.connect(self.valueChanged)
        self.af_axis.valueChanged.connect(self.valueChanged)
        # the radios are exclusive, so any change of kind toggles `use_hardware`
        self.af_axis.use_hardware.toggled.connect(self._on_af_kind_toggled)
        self.stage_positions.af_per_position.toggled.connect(self._on_af_toggled)

    def _apply_layout_metrics(self) -> None:
        """Set the spacing and insets of the cards and footer around the tabs."""
        margin = MDATabs._PAGE_MARGIN
        # Left/right line up with the tab pages' own inset; the gap above is
        # already there (the page's bottom margin), so only the bottom needs
        # adding, to keep the card off the footer.
        self._settings_row.setContentsMargins(margin, 0, margin, margin // 2)
        self._footer_layout.setContentsMargins(8, 4, 8, 8)
        cast("QVBoxLayout", self.layout()).setSpacing(7)

    # ----------- Aliases for tab_wdg widgets -----------

    @property
    def channels(self) -> ChannelTable:
        return self.tab_wdg.channels

    @property
    def time_plan(self) -> TimePlanWidget:
        return self.tab_wdg.time_plan

    @property
    def z_plan(self) -> ZPlanWidget:
        return self.tab_wdg.z_plan

    @property
    def stage_positions(self) -> PositionTable:
        return self.tab_wdg.stage_positions

    @property
    def grid_plan(self) -> GridPlanWidget:
        return self.tab_wdg.grid_plan

    # -------------- Public API --------------

    def value(self) -> useq.MDASequence:
        """Return the current value of the widget as a [`useq.MDASequence`][].

        Returns
        -------
        useq.MDASequence
            The current [`useq.MDASequence`][] value of the widget.
        """
        val = self.tab_wdg.value()

        # things to update
        replace: dict = {
            # update mda axis order
            "axis_order": self.axis_order.currentText(),
            # update keep_shutter_open_across
            "keep_shutter_open_across": self.keep_shutter_open.value(),
        }

        if self._use_af_per_position():
            # check if the autofocus offsets are the same for all positions
            # and simplify to a single global autofocus plan if so.
            replace.update(self._simplify_af_offsets(val))
        elif af_axes := self.af_axis.value():
            # otherwise use selected af axes as global autofocus plan
            replace["autofocus_plan"] = self.af_axis.plan(af_axes)

        if replace:
            val = val.replace(**replace)

        return val

    def setValue(self, value: useq.MDASequence) -> None:
        """Set the current value of the widget from a [`useq.MDASequence`][].

        Parameters
        ----------
        value : useq.MDASequence
            The [`useq.MDASequence`][] to set.
        """
        # Restore atomically: observers should see only the completed sequence.
        with signals_blocked(self):
            self.tab_wdg.setValue(value)

            keep_shutter_open = value.keep_shutter_open_across
            self.keep_shutter_open.setValue(keep_shutter_open)

            # update autofocus axes checkboxes
            axis: set[str] = set()
            af_plan = value.autofocus_plan
            # update from global autofocus plan
            if af_plan:
                axis.update(af_plan.axes)
            # update from autofocus plans in each position sub-sequence
            if value.stage_positions:
                for pos in value.stage_positions:
                    if pos.sequence and (pos_af := pos.sequence.autofocus_plan):
                        axis.update(pos_af.axes)
                        af_plan = af_plan or pos_af
            self.af_axis.setValue(tuple(axis))
            # restore the kind of autofocus and its per-kind settings
            self.af_axis.setPlan(af_plan)
            # setPlan switches the kind with the radios' signals blocked
            self._on_af_kind_toggled()
            axis_text = "".join(
                x for x in value.axis_order if x in self.tab_wdg.usedAxes()
            )
            self.axis_order.setCurrentText(axis_text)
        self.valueChanged.emit()

    def _commit_editors(self) -> None:
        """Take what is being typed in the focused editor, whichever one it is.

        A table's spin box only takes a typed value on Enter or when losing the
        focus, and on macOS clicking a button does not take the focus: without this,
        Run and Save would use the value from before the typing.
        """
        focused = QApplication.focusWidget()
        if focused is not None and self.isAncestorOf(focused):
            focused.clearFocus()

    def save(self, file: str | Path | None = None) -> None:
        """Save the current [`useq.MDASequence`][] to a file."""
        self._commit_editors()
        if not isinstance(file, (str, Path)):
            file, _ = QFileDialog.getSaveFileName(
                self,
                "Save MDASequence and filename.",
                "",
                self._settings_extensions(),
            )
            if not file:  # pragma: no cover
                return

        dest = Path(file)
        if not dest.suffix:
            dest = dest.with_suffix(".yaml")
        if dest.suffix in {".yaml", ".yml"}:
            yaml = self.value().yaml(exclude_unset=True, exclude_defaults=True)
            data = cast("str", yaml)
        elif dest.suffix == ".json":
            data = self.value().model_dump_json(
                exclude_unset=True, exclude_defaults=True
            )
        else:  # pragma: no cover
            raise ValueError(f"Invalid file extension: {dest.suffix!r}")

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(data)

    def load(self, file: str | Path | None = None) -> None:
        """Load a [`useq.MDASequence`][] from a file."""
        if not isinstance(file, (str, Path)):
            file, _ = QFileDialog.getOpenFileName(
                self,
                "Select an MDAsequence file.",
                "",
                self._settings_extensions(),
            )
            if not file:  # pragma: no cover
                return

        src = Path(file)
        if not src.is_file():  # pragma: no cover
            raise FileNotFoundError(f"File not found: {src}")

        try:
            mda_seq = useq.MDASequence.from_file(src)
        except Exception as e:  # pragma: no cover
            raise ValueError(f"Failed to load MDASequence file: {src}") from e

        self.setValue(mda_seq)

    # -------------- Private API --------------

    def _settings_extensions(self) -> str:
        """Returns the available extensions for MDA settings save/load."""
        if find_spec("yaml") is not None:
            # YAML available
            return "All (*.yaml *yml *.json);;YAML (*.yaml *.yml);;JSON (*.json)"
        # Only JSON
        return "All (*.json);;JSON (*.json)"

    def _enable_af(self, state: bool) -> None:
        """Enable or disable autofocus settings."""
        af_axis_tooltip = AF_AXIS_TOOLTIP if state else AF_DISABLED_TOOLTIP
        # enable autofocus axis widget
        self.af_axis.setEnabled(state)
        self.af_axis.setToolTip(af_axis_tooltip)
        self._enable_af_per_position(state)

    def _enable_af_per_position(self, state: bool) -> None:
        """Enable or disable the autofocus per position checkbox.

        Per-position offsets are hardware autofocus offsets, so a software routine
        rules them out just as an absolute Z plan does.
        """
        software = self.af_axis.use_software.isChecked()
        if not state:
            af_per_pos_tooltip = AF_DISABLED_TOOLTIP
        elif software:
            af_per_pos_tooltip = AF_PER_POS_SOFTWARE_TOOLTIP
        else:
            af_per_pos_tooltip = AF_PER_POS_TOOLTIP
        state = state and not software
        self.stage_positions.af_per_position.setEnabled(state)
        self.stage_positions.af_per_position.setToolTip(af_per_pos_tooltip)
        # hide the autofocus columns if autofocus per position is disabled
        if not state:
            # not simply calling self.stage_positions.af_per_position.setChecked(False)
            # because we want to keep the previous state of the checkbox
            self.stage_positions._on_af_per_position_toggled(False)
        # show the autofocus columns only if it was checked before
        elif self.stage_positions.af_per_position.isChecked():
            self.stage_positions._on_af_per_position_toggled(True)

    def _validate_af_with_z_plan(self) -> None:
        """Check if the autofocus plan can be used with the current Z Plan.

        If the Z Plan is set to TOP_BOTTOM, the autofocus plan cannot be used.
        """
        if self.z_plan.mode() == Mode.TOP_BOTTOM:
            # if any autofocus axis is selected, show a warning.
            if self.af_axis.value() or self._use_af_per_position():
                QMessageBox.warning(
                    self,
                    "Autofocus Plan Disabled",
                    "Autofocus cannot be used with a Z Plan with Absolute "
                    "Z Positions (TOP_BOTTOM mode). It has been disabled.\n\n"
                    "To re-enable it, select a Z Plan with Relative Positions"
                    "(RANGE_AROUND or ABOVE_BELOW modes).",
                    buttons=QMessageBox.StandardButton.Ok,
                    defaultButton=QMessageBox.StandardButton.Ok,
                )
            self._enable_af(False)
        else:
            self._enable_af(True)

        self.valueChanged.emit()

    def _use_af_per_position(self) -> bool:
        """Return True if autofocus per position is checked and enabled.

        The Positions tab must be checked too: else there are no positions, and the
        global autofocus axes are used.
        """
        return bool(
            self.stage_positions.af_per_position.isChecked()
            and self.stage_positions.af_per_position.isEnabled()
            and self.tab_wdg.isChecked(self.stage_positions)
        )

    def _on_tab_checked(self, tab_idx: int) -> None:
        """Before updating autofocus axes, check if the autofocus plan can be used."""
        if tab_idx == self.tab_wdg.indexOf(self.z_plan):
            if self.tab_wdg.isChecked(self.z_plan):
                self._validate_af_with_z_plan()
            else:
                self._enable_af(True)

        if tab_idx in (
            self.tab_wdg.indexOf(self.grid_plan),
            self.tab_wdg.indexOf(self.stage_positions),
        ):
            with signals_blocked(self):
                self._on_grid_plan_value_changed()

        self._update_available_axis_orders()

    def _on_grid_plan_value_changed(self) -> None:
        """Disable position X/Y when a global absolute grid is active."""
        gp = self.grid_plan
        has_abs_grid = self.tab_wdg.isChecked(gp) and not gp.value().is_relative
        self.stage_positions.setXYEnabled(not has_abs_grid)
        self.valueChanged.emit()

    def _on_af_toggled(self) -> None:
        # if the 'af_per_position' checkbox in the PositionTable is checked, turn
        # autofocus on and set checked also the autofocus p axis checkbox.
        if self._use_af_per_position() and self.tab_wdg.isChecked(self.stage_positions):
            self.af_axis.setChecked(True)
            self.af_axis.use_af_p.setChecked(True)

    def _uses_absolute_z(self) -> bool:
        """Return whether an absolute (top-bottom) z plan is selected."""
        return bool(
            self.tab_wdg.isChecked(self.z_plan)
            and self.z_plan.mode() == Mode.TOP_BOTTOM
        )

    def _on_af_kind_toggled(self) -> None:
        """Per-position offsets only apply to hardware autofocus."""
        self._enable_af_per_position(not self._uses_absolute_z())

    def _update_available_axis_orders(self) -> None:
        """Handle tabChecked signal.

        Hide columns in the channels tab accordingly.
        """
        populate_axis_order_combo(self.axis_order, self.tab_wdg.usedAxes())
        self.valueChanged.emit()

    def _update_time_estimate(self) -> None:
        """Update the time estimate label."""
        val = self.value()
        try:
            self._time_estimate = val.estimate_duration()
        except ValueError as e:  # pragma: no cover
            self._duration_label.setText(f"Error estimating time:\n{e}")
            return

        self._time_warning.setVisible(self._time_estimate.time_interval_exceeded)

        d = humanize_time(self._time_estimate.total_duration)
        d = f"Estimated duration: {d}" if d else ""
        self._duration_label.setText(d)

    def _simplify_af_offsets(self, seq: useq.MDASequence) -> dict:
        """If all positions have the same af offset, remove it from each position.

        Instead, add a global autofocus plan to the sequence.
        This function returns a dict of fields to update in the sequence.
        """
        if not seq.stage_positions:
            return {}

        # gather all the autofocus offsets in the subsequences
        af_offsets = {
            pos.sequence.autofocus_plan.autofocus_motor_offset
            for pos in seq.stage_positions
            if pos.sequence is not None
            # only a hardware plan has an offset to simplify
            and isinstance(pos.sequence.autofocus_plan, useq.AxesBasedAF)
        }

        # if they aren't all the same, there's nothing we can do to simplify it.
        if len(af_offsets) != 1:
            return {"stage_positions": self._update_af_axes(seq.stage_positions)}

        # otherwise, make a global AF plan and remove it from each position
        stage_positions = []
        for pos in seq.stage_positions:
            if pos.sequence and pos.sequence.autofocus_plan:
                # remove autofocus plan from the position
                pos = pos.replace(sequence=pos.sequence.replace(autofocus_plan=None))
                # after removing the autofocus plan, if the sequence is empty,
                # remove it altogether.
                if pos.sequence == NULL_SEQUENCE:
                    pos = pos.replace(sequence=None)
            stage_positions.append(pos)
        af_plan = self.af_axis.plan(
            self.af_axis.value(), autofocus_motor_offset=af_offsets.pop()
        )
        return {"autofocus_plan": af_plan, "stage_positions": stage_positions}

    def _update_af_axes(
        self, positions: Sequence[useq.Position]
    ) -> Sequence[useq.Position]:
        """Add the autofocus axes to each subsequence."""
        new_pos = []
        for pos in positions:
            if (seq := pos.sequence) and (af_plan := seq.autofocus_plan):
                af_plan = af_plan.replace(axes=self.af_axis.value())
                pos = pos.replace(sequence=seq.replace(autofocus_plan=af_plan))
            new_pos.append(pos)

        return tuple(new_pos)
