from __future__ import annotations

from collections.abc import Mapping
from importlib.util import find_spec
from itertools import permutations
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import useq
from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from superqt.utils import signals_blocked

import pymmcore_widgets
from pymmcore_widgets._humanize import humanize_time
from pymmcore_widgets._util import disable_wheel_scroll
from pymmcore_widgets.useq_widgets._autofocus_settings import (
    AutofocusSettingsDialog,
    _settings_button,
)
from pymmcore_widgets.useq_widgets._channels import ChannelTable
from pymmcore_widgets.useq_widgets._checkable_tabwidget_widget import CheckableTabWidget
from pymmcore_widgets.useq_widgets._grid import GridPlanWidget
from pymmcore_widgets.useq_widgets._positions import AF_PER_POS_TOOLTIP, PositionTable
from pymmcore_widgets.useq_widgets._time import TimePlanWidget
from pymmcore_widgets.useq_widgets._z import Mode, ZPlanWidget

if TYPE_CHECKING:
    from collections.abc import Sequence


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


AF_AXIS_TOOLTIP = (
    "Refocus during the acquisition, so that slow drift, a tilted sample or a\n"
    "stage that does not return exactly does not blur the later time points."
)
AF_DISABLED_TOOLTIP = (
    "Autofocus cannot be used with absolute Z positions (TOP_BOTTOM mode).\n"
    "It corrects the focus position, which an absolute Z plan would then override.\n"
    "Switch the Z plan to a relative mode (RANGE_AROUND or ABOVE_BELOW) to use it."
)
AF_ENABLE_TOOLTIP = "Run an autofocus routine during the acquisition."
# Filled in when a search range is first asked for; MMStudio's own default.
DEFAULT_AF_SEARCH_STEP_UM = 5.0
AF_ON_AXIS_TOOLTIP = (
    "When to autofocus: every time one of these axes changes.\n"
    "\n"
    "p - at every stage position (the usual choice: each position drifts its own way)\n"
    "t - at every time point\n"
    "g - at every tile of a grid"
)
AF_MODE_TOOLTIP = (
    "Which kind of autofocus to run. An acquisition uses one or the other,\nnever both."
)
AF_HARDWARE_TOOLTIP = (
    "Use the microscope's autofocus device, e.g. Nikon PFS or Zeiss Definite Focus.\n"
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
AF_EVERY_N_TOOLTIP = (
    "Autofocus only on every Nth time point.\n"
    "\n"
    "A software autofocus run costs several images each time, so on a long time\n"
    "series it is often enough to refocus occasionally. 1 means every time point."
)
AF_EVERY_N_NO_T_TOOLTIP = (
    'Only applies when autofocus runs on the t axis: check "t" above.'
)
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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # self.setMovable(True)
        self.tabChecked.connect(self._on_tab_checked)

        self.create_subwidgets()

        self.addTab(self.time_plan, "Time", checked=False)
        self.addTab(self.stage_positions, "Positions", checked=False)
        self.addTab(self.grid_plan, "Grid/Tiles", checked=False)
        self.addTab(self.z_plan, "Z Stack", checked=False)
        self.addTab(self.channels, "Channels", checked=False)
        self.setCurrentIndex(self.indexOf(self.channels))

        # we only show the DO_STACK and ACQUIRE_EVERY columns when the
        # corresponding tab is checked
        ch_table = self.channels.table()
        ch_table.hideColumn(ch_table.indexOf(self.channels.DO_STACK))
        ch_table.hideColumn(ch_table.indexOf(self.channels.ACQUIRE_EVERY))

    def create_subwidgets(self) -> None:
        """Create the Tabs of the widget."""
        self.time_plan = TimePlanWidget(1)
        self.stage_positions = PositionTable(1)
        self.grid_plan = GridPlanWidget()
        self.z_plan = ZPlanWidget()
        self.channels = ChannelTable(1)

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
        """Set widget value from a [`useq.MDASequence`][]."""
        if not isinstance(value, useq.MDASequence):  # pragma: no cover
            raise TypeError(f"Expected useq.MDASequence, got {type(value)}")

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


class AutofocusAxis(QGroupBox):
    """Autofocus settings: whether to use it, which kind, when, and how it searches.

    A checkable group box, so Qt's own semantics switch the whole thing on and off:
    `isChecked()` says whether autofocus will run, and unchecking it disables the
    controls inside. `Hardware` and `Software` are exclusive, since an acquisition
    carries a single autofocus plan; `Software` stays disabled until software
    autofocus methods are available (see `setSoftwareMethods`).

    It is drawn flat: the MDA widgets already wrap it in a card, and a group box's
    own frame renders inconsistently across platforms.
    """

    valueChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setTitle("Autofocus")
        self.setCheckable(True)
        self.setChecked(False)
        self.setFlat(True)
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
        self.use_hardware.setToolTip(AF_HARDWARE_TOOLTIP)
        self.use_software = QRadioButton("Software")
        self.use_software.setEnabled(False)
        self.use_software.setToolTip(AF_NO_SOFTWARE_TOOLTIP)
        self._kind_group = QButtonGroup(self)
        self._kind_group.setExclusive(True)
        self._kind_group.addButton(self.use_hardware)
        self._kind_group.addButton(self.use_software)

        # --- hardware only: search for a lock if autofocus fails where it starts ---
        self._search_label = QLabel("Search:")
        self._search_label.setToolTip(AF_SEARCH_TOOLTIP)
        # all zero: autofocus is only attempted where it starts, until asked for more
        self.search_below_um = self._search_spin("below ", 0.0, AF_SEARCH_BELOW_TOOLTIP)
        self.search_above_um = self._search_spin("above ", 0.0, AF_SEARCH_ABOVE_TOOLTIP)
        self.search_step_um = self._search_spin("step ", 0.0, AF_SEARCH_STEP_TOOLTIP)

        # --- software only: which routine, and its settings ---
        self._method_label = QLabel("Method:")
        self._method_label.setToolTip(AF_METHOD_TOOLTIP)
        self.method = QComboBox()
        self.method.setToolTip(AF_METHOD_TOOLTIP)
        self.settings_button = _settings_button(AF_SETTINGS_TOOLTIP)
        self.settings_button.setEnabled(False)
        # each method keeps its own settings, so switching back and forth does not
        # discard what was configured
        self._method_models: dict[str, type] = {}
        self._method_descriptions: dict[str, str] = {}
        self._method_settings: dict[str, dict[str, Any]] = {}
        # set by the core-aware widget, which knows what devices are loaded
        self._software_devices_ok = True
        self._software_unavailable_reason = ""

        # --- software only: skipping time points, since each run costs many images ---
        self._every_n_label = QLabel("Run every:")
        self._every_n_label.setToolTip(AF_EVERY_N_TOOLTIP)
        self.every_n_timepoints = QSpinBox()
        self.every_n_timepoints.setRange(1, 10000)
        self.every_n_timepoints.setValue(1)
        self.every_n_timepoints.setSuffix(" t")
        self.every_n_timepoints.setToolTip(AF_EVERY_N_TOOLTIP)
        disable_wheel_scroll(self.every_n_timepoints)

        # The last plan of each kind seen by setPlan(), used as the base that value()
        # edits.  It carries the fields this widget cannot edit (the motor offset,
        # retries, and a software plan's method and settings), so a
        # setValue()/value() round trip never drops them -- and never silently turns a
        # software autofocus plan into a hardware one.
        self._hardware_plan: useq.AxesBasedAF | None = None
        self._software_plan: useq.SoftwareAxesBasedAF | None = None

        axis_row = QHBoxLayout()
        axis_row.setSpacing(10)
        axis_row.setContentsMargins(0, 0, 0, 0)
        axis_row.addWidget(self.label)
        axis_row.addWidget(self.use_af_p)
        axis_row.addWidget(self.use_af_t)
        axis_row.addWidget(self.use_af_g)
        axis_row.addStretch()

        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.addWidget(self._mode_label)
        mode_row.addWidget(self.use_hardware)
        mode_row.addWidget(self.use_software)
        mode_row.addStretch()

        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.addWidget(self._search_label)
        search_row.addWidget(self.search_below_um)
        search_row.addWidget(self.search_above_um)
        search_row.addWidget(self.search_step_um)
        search_row.addStretch()

        software_row = QHBoxLayout()
        software_row.setSpacing(10)
        software_row.setContentsMargins(0, 0, 0, 0)
        software_row.addWidget(self._method_label)
        software_row.addWidget(self.method, 1)
        software_row.addWidget(self.settings_button)
        software_row.addWidget(self._every_n_label)
        software_row.addWidget(self.every_n_timepoints)

        layout = QVBoxLayout(self)
        layout.setSpacing(5)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.addLayout(axis_row)
        layout.addLayout(mode_row)
        layout.addLayout(search_row)
        layout.addLayout(software_row)

        self.toggled.connect(self._on_enabled_toggled)
        self.use_af_p.toggled.connect(self.valueChanged)
        self.use_af_t.toggled.connect(self._on_t_axis_toggled)
        self.use_af_g.toggled.connect(self.valueChanged)
        self.every_n_timepoints.valueChanged.connect(self.valueChanged)
        self.use_hardware.toggled.connect(self._on_kind_toggled)
        self.search_below_um.valueChanged.connect(self._on_search_range_changed)
        self.search_above_um.valueChanged.connect(self._on_search_range_changed)
        self.search_step_um.valueChanged.connect(self.valueChanged)
        self.method.currentTextChanged.connect(self._on_method_changed)
        self.settings_button.clicked.connect(self._edit_settings)

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

        self._update_kind_widgets()
        self.setSoftwareMethods(*_installed_software_methods())

    def _search_spin(self, prefix: str, default: float, tooltip: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 10000.0)
        spin.setDecimals(1)
        spin.setSingleStep(1.0)
        spin.setValue(default)
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
        self._update_settings_button()
        self._update_method_tooltip()
        self._refresh_software_enabled()

    def _update_method_tooltip(self) -> None:
        """Describe the selected routine on the closed combo, not just in the list."""
        doc = self._method_descriptions.get(self.softwareMethod(), "")
        self.method.setToolTip(doc or AF_METHOD_TOOLTIP)
        self._method_label.setToolTip(self.method.toolTip())

    def setSoftwareAvailable(self, available: bool, reason: str = "") -> None:
        """Say whether the microscope can run a software routine at all.

        It needs a camera to acquire with and a focus drive to move -- but no
        autofocus device, which is what sets it apart from the hardware kind.
        `reason` is shown as the tooltip when it cannot.
        """
        self._software_devices_ok = available
        self._software_unavailable_reason = reason
        self._refresh_software_enabled()

    def isSoftwareAvailable(self) -> bool:
        """Whether a software routine could run: one exists and the devices are there.

        This is a fact about the installation, not about the widget's current state,
        so it is safe to use when deciding what to enable -- unlike asking a child
        widget whether it is enabled, which also depends on this widget being enabled.
        """
        return self.method.count() > 0 and self._software_devices_ok

    def _refresh_software_enabled(self) -> None:
        has_methods = self.method.count() > 0
        available = self.isSoftwareAvailable()
        self.use_software.setEnabled(available)
        if available:
            tooltip = AF_SOFTWARE_TOOLTIP
        elif not has_methods:
            tooltip = AF_NO_SOFTWARE_TOOLTIP
        else:
            tooltip = self._software_unavailable_reason or AF_NO_SOFTWARE_TOOLTIP
        self.use_software.setToolTip(tooltip)

    def softwareMethod(self) -> str:
        """The selected software autofocus routine."""
        return str(self.method.currentText())

    def softwareSettings(self) -> dict[str, Any]:
        """The settings for the selected routine."""
        return dict(self._method_settings.get(self.softwareMethod(), {}))

    def _update_settings_button(self) -> None:
        """A routine with no known settings model has nothing to edit."""
        self.settings_button.setEnabled(self.softwareMethod() in self._method_models)

    def _on_method_changed(self) -> None:
        self._update_settings_button()
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
        )
        if dialog.exec():
            self._method_settings[method] = dialog.value()
            self.valueChanged.emit()

    def _on_enabled_toggled(self) -> None:
        # Qt re-enables the children, but each mode's own controls carry an enabled
        # state of their own that has to be reapplied on top of that
        self._update_kind_widgets()
        self.valueChanged.emit()

    def _on_t_axis_toggled(self) -> None:
        self._update_kind_widgets()
        self.valueChanged.emit()

    def _on_kind_toggled(self) -> None:
        # one connection is enough: the two radios are exclusive, so any change
        # toggles `use_hardware`
        self._update_kind_widgets()
        self.valueChanged.emit()

    def _update_kind_widgets(self) -> None:
        """Show only the options that apply to the selected kind.

        The Z search is a hardware-autofocus recovery; skipping time points matters
        for software autofocus, where each run costs many images.
        """
        hardware = self.use_hardware.isChecked()
        hardware_only: tuple[QWidget, ...] = (
            self._search_label,
            self.search_below_um,
            self.search_above_um,
            self.search_step_um,
        )
        software_only: tuple[QWidget, ...] = (
            self._method_label,
            self.method,
            self.settings_button,
            self._every_n_label,
            self.every_n_timepoints,
        )
        for wdg in hardware_only:
            wdg.setVisible(hardware)
        for wdg in software_only:
            wdg.setVisible(not hardware)
        # "Run every N" qualifies the time-point trigger, so it applies only when
        # autofocus is actually set to run on the t axis
        on_t = self.use_af_t.isChecked()
        for wdg in (self._every_n_label, self.every_n_timepoints):
            wdg.setEnabled(on_t)
            wdg.setToolTip(AF_EVERY_N_TOOLTIP if on_t else AF_EVERY_N_NO_T_TOOLTIP)

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
        updates: dict = {"axes": axes}
        if self.kind() == "software":
            # a software plan needs a `method`, which this widget cannot choose: it can
            # only edit one it was given.
            updates["every_n_timepoints"] = self.every_n_timepoints.value()
            if method := self.softwareMethod():
                updates["method"] = method
                updates["settings"] = self.softwareSettings()
            soft = self._software_plan or useq.SoftwareAxesBasedAF(axes=axes, method="")
            return soft.replace(**updates, **kwargs)
        updates["search_below_um"] = self.search_below_um.value()
        updates["search_above_um"] = self.search_above_um.value()
        updates["search_step_um"] = self.search_step_um.value()
        hard = self._hardware_plan or useq.AxesBasedAF(axes=axes)
        return hard.replace(**updates, **kwargs)

    def setPlan(self, plan: useq.AnyAutofocusPlan | None) -> None:
        """Restore the kind and per-kind settings from `plan`."""
        if plan is None:
            self.setKind(None)
            return
        with signals_blocked(self):
            if isinstance(plan, useq.SoftwareAxesBasedAF):
                self._software_plan = plan
                self.every_n_timepoints.setValue(plan.every_n_timepoints)
                if plan.method:
                    if self.method.findText(plan.method) < 0:
                        # a routine this installation does not have: keep it, so the
                        # sequence is not quietly changed to a different one
                        self.method.addItem(plan.method)
                    self.method.setCurrentText(plan.method)
                    self._method_settings[plan.method] = dict(plan.settings)
                    self._update_settings_button()
                # make sure the option can be chosen, so value() round-trips the plan
                # even before a method picker exists.
                self.use_software.setEnabled(True)
                self.use_software.setToolTip(AF_SOFTWARE_TOOLTIP)
                self.setKind("software")
            else:
                self._hardware_plan = plan
                self.search_below_um.setValue(plan.search_below_um)
                self.search_above_um.setValue(plan.search_above_um)
                self.search_step_um.setValue(plan.search_step_um)
                self.setKind("hardware")


class KeepShutterOpen(QWidget):
    valueChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.label = QLabel("Keep Shutter Open Across Axis:")
        self.leave_open_t = QCheckBox("t")
        self.leave_open_z = QCheckBox("z")

        layout = QHBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
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

        self._save_button = QPushButton("Save Settings")
        self._save_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._save_button.clicked.connect(self.save)
        self._load_button = QPushButton("Load Settings")
        self._load_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._load_button.clicked.connect(self.load)

        # -------------- Main Layout --------------

        # Stored on self (rather than an anonymous QLabel) so a subclass that
        # rebuilds this layout from scratch (e.g. MDAWidgetCollapsible,
        # MDAWidgetTopbar) can move -- not duplicate -- it into its own
        # layout. A layout only manages widgets added to it; one dropped by
        # clearing a layout without also being re-added elsewhere is not
        # deleted or hidden, just orphaned at its last position, silently
        # overlapping whatever is drawn there now.
        self._axis_order_label = QLabel("Axis Order:")
        top_row = QHBoxLayout()
        top_row.addWidget(self._axis_order_label)
        top_row.addWidget(self.axis_order)
        top_row.addStretch()

        self.keep_shutter_open = KeepShutterOpen()
        self.af_axis = AutofocusAxis()
        # Autofocus aligns its own rows internally (it has several), so its label is
        # left to size itself -- padding it to match the one above would push its
        # checkboxes far to the right of it.
        cbox_row = QVBoxLayout()
        cbox_row.setContentsMargins(0, 0, 0, 0)
        cbox_row.setSpacing(5)
        cbox_row.addWidget(self.keep_shutter_open)
        cbox_row.addWidget(self.af_axis)
        cbox_row.addStretch()

        bot_row = QHBoxLayout()
        bot_row.addWidget(self._time_warning)
        bot_row.addWidget(self._duration_label)
        bot_row.addWidget(self._save_button)
        bot_row.addWidget(self._load_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addWidget(self.tab_wdg, 1)
        layout.addLayout(cbox_row)
        layout.addLayout(bot_row)

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
        self.stage_positions.af_per_position.toggled.connect(self._on_af_toggled)

        with signals_blocked(self):
            self.tab_wdg.setChecked(self.channels, True)

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
        # Restoring a whole sequence touches several axis widgets, each wired to
        # `valueChanged` both directly and via `_update_available_axis_orders`
        # (itself triggered a second time by the tab-checked cascade from
        # `tab_wdg.setValue`'s `setChecked` calls) -- block the fan-out here and
        # emit once at the end so setValue() looks atomic to observers.
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
            axis_text = "".join(
                x for x in value.axis_order if x in self.tab_wdg.usedAxes()
            )
            self.axis_order.setCurrentText(axis_text)
        self.valueChanged.emit()

    def save(self, file: str | Path | None = None) -> None:
        """Save the current [`useq.MDASequence`][] to a file."""
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
        af_per_pos_tooltip = AF_PER_POS_TOOLTIP if state else AF_DISABLED_TOOLTIP
        # enable autofocus axis widget
        self.af_axis.setEnabled(state)
        self.af_axis.setToolTip(af_axis_tooltip)
        # enable autofocus per position checkbox
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
        """Return True if autofocus per position is checked and enabled."""
        return bool(
            self.stage_positions.af_per_position.isChecked()
            and self.stage_positions.af_per_position.isEnabled()
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
