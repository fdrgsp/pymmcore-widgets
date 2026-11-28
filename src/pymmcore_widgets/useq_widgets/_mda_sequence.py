from __future__ import annotations

from importlib.util import find_spec
from itertools import permutations
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from weakref import WeakMethod

import useq
from qtpy.QtCore import QEvent, Qt, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from superqt.iconify import QIconifyIcon
from superqt.utils import signals_blocked

import pymmcore_widgets
from pymmcore_widgets._humanize import humanize_time
from pymmcore_widgets.useq_widgets._axis_tabs import (
    MDA_ICONS,
    _AxisTabBar,
    _AxisTabButton,
    _wrap_in_card,
)
from pymmcore_widgets.useq_widgets._channels import ChannelTable
from pymmcore_widgets.useq_widgets._checkable_tabwidget_widget import CheckableTabWidget
from pymmcore_widgets.useq_widgets._grid import GridPlanWidget
from pymmcore_widgets.useq_widgets._positions import AF_PER_POS_TOOLTIP, PositionTable
from pymmcore_widgets.useq_widgets._time import TimePlanWidget
from pymmcore_widgets.useq_widgets._z import Mode, ZPlanWidget

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


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
AF_AXIS_TOOLTIP = "Use Hardware Autofocus on the selected axes."
AF_DISABLED_TOOLTIP = (
    "The hardware autofocus cannot be used with absolute Z positions (TOP_BOTTOM mode)."
)
AF_ON_AXIS_TOOLTIP = (
    "When to autofocus: every time one of these axes changes.\n"
    "\n"
    "p - at every stage position\n"
    "t - at every time point\n"
    "g - at every tile of a grid"
)
TOP_BOTTOM_AF_TOOLTIP = "Cannot use while 'Autofocus' is in use."


class MDATabs(CheckableTabWidget):
    """Checkable QTabWidget for editing a useq.MDASequence.

    It contains a Tab for each of the MDASequence, axis (channels, positions, etc...),
    and one for the autofocus (see `add_autofocus`).
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
        ("Z Stack", "z_plan"),
        ("Time", "time_plan"),
        ("Grid", "grid_plan"),
    )
    # inset of every page's content from the tab's edges
    _PAGE_MARGIN = 9

    def __init__(self, parent: QWidget | None = None) -> None:
        # the page wrapping each editor (see `_add_tab`)
        self._page_for: dict[QWidget, QWidget] = {}
        # the editors not enabled by their tab's checkbox (see `_add_tab`)
        self._self_enabled: set[QWidget] = set()
        super().__init__(parent)
        self.setTabBar(_AxisTabBar(self))
        self.setDocumentMode(True)
        self.tabChecked.connect(self._on_tab_checked)

        self.create_subwidgets()

        for title, attr in self._AXES:
            card = attr in ("grid_plan", "z_plan")
            self._add_tab(getattr(self, attr), title, attr, card=card)
        self.setCurrentWidget(self.channels)

        # we only show the DO_STACK and ACQUIRE_EVERY columns when the
        # corresponding tab is checked
        ch_table = self.channels.table()
        ch_table.hideColumn(ch_table.indexOf(self.channels.DO_STACK))
        ch_table.hideColumn(ch_table.indexOf(self.channels.ACQUIRE_EVERY))

    def _add_tab(
        self,
        widget: QWidget,
        title: str,
        icon_key: str,
        *,
        checked: bool = False,
        card: bool = False,
        enables: bool = True,
    ) -> None:
        """Add a tab for `widget`, with an `_AxisTabButton` checkbox.

        The checkbox enables `widget`, unless `enables` is False. `widget` is inset
        in a page, inside a card in a scroll area with `card`: the methods taking a
        widget resolve it to its page (see `indexOf`).
        """
        page = QWidget()
        page_layout = QVBoxLayout(page)
        margin = self._PAGE_MARGIN
        page_layout.setContentsMargins(margin, margin, margin, margin)
        if card:
            # the card is as tall as its content, at the top of the page
            content = QWidget()
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(0, 0, 0, 0)
            content_layout.addWidget(_wrap_in_card(widget))
            content_layout.addStretch()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(content)
            page_layout.addWidget(scroll)
        else:
            page_layout.addWidget(widget)
        self._page_for[widget] = page

        index = int(QTabWidget.addTab(self, page, ""))
        btn = _AxisTabButton(title, QIconifyIcon(MDA_ICONS[icon_key]), checked=checked)
        # _AxisTabButton has the QCheckBox API used by CheckableTabWidget
        self._cboxes.append(cast("QCheckBox", btn))
        if enables:
            widget.setEnabled(checked)
            btn.toggled.connect(lambda c, w=widget: self._on_tab_checkbox_toggled(c, w))
        else:
            self._self_enabled.add(widget)
            btn.toggled.connect(lambda c, w=widget: self._on_tab_switched(c, w))
        if tab_bar := self.tabBar():
            tab_bar.setTabButton(index, QTabBar.ButtonPosition.LeftSide, btn)
            tab_bar.setTabToolTip(index, title)

    def add_autofocus(self, autofocus: AutofocusAxis) -> None:
        """Add the Autofocus tab after the axes, its checkbox switching `autofocus`.

        Unlike an axis tab, it does not enable its widget: `autofocus` disables its
        own controls when switched off, and its enabled state says whether it can
        be used at all (e.g. not with an absolute Z plan), which the tab follows.
        """
        self._autofocus = autofocus
        self._add_tab(
            autofocus,
            "Autofocus",
            "autofocus",
            checked=autofocus.isChecked(),
            card=True,
            enables=False,
        )
        self._autofocus_button().toggled.connect(autofocus.setChecked)
        autofocus.setSwitch(self._check_autofocus)
        autofocus.availabilityChanged.connect(self._update_autofocus_availability)
        self._update_autofocus_availability()

    def _autofocus_button(self) -> _AxisTabButton:
        index = self.indexOf(self._autofocus)
        left = QTabBar.ButtonPosition.LeftSide
        return cast("_AxisTabButton", self.tabBar().tabButton(index, left))

    def _check_autofocus(self, checked: bool) -> None:
        # switched from code (e.g. by 'Set AF Offset per Position'): stay on this tab
        jump, self.change_tab_on_check = self.change_tab_on_check, False
        try:
            self.setChecked(self._autofocus, checked)
        finally:
            self.change_tab_on_check = jump

    def _update_autofocus_availability(self) -> None:
        button = self._autofocus_button()
        button.setEnabled(self._autofocus.isEnabled())
        button.setToolTip(self._autofocus.toolTip())

    def indexOf(self, widget: QWidget | None) -> int:
        """Return the tab index for `widget`, resolving it to its page."""
        page = self._page_for.get(widget, widget) if widget is not None else None
        return int(QTabWidget.indexOf(self, page))

    def setCurrentWidget(self, widget: QWidget | None) -> None:
        """Select the tab for `widget`, resolving it to its page."""
        if widget is not None:
            QTabWidget.setCurrentWidget(self, self._page_for.get(widget, widget))

    def currentWidget(self) -> QWidget | None:
        """Return the editor of the current tab, not its page."""
        page = QTabWidget.currentWidget(self)
        return next((w for w, p in self._page_for.items() if p is page), page)

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
        """Return a tuple of the axes currently used in the sequence."""
        return tuple(k for k in ("tpgzc") if self.isAxisUsed(k))

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


class AutofocusAxis(QWidget):
    """Hardware autofocus settings: whether to use it, and on which axes.

    `isChecked()` says whether autofocus is switched on; switched off, its axes are
    disabled. The control that switches it, e.g. a tab's checkbox, belongs to its
    container, which `setSwitch` keeps in step.
    """

    valueChanged = Signal()
    toggled = Signal(bool)
    # whether it can be used, or why not (its enabled state or tooltip), changed
    availabilityChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._checked = False
        # the setter of its switch (see `setSwitch`)
        self._set_switch: WeakMethod[Callable[[bool], object]] | None = None
        # the last plan set, for the fields this widget does not edit
        self._plan: useq.AxesBasedAF | None = None

        self.use_af_p = QCheckBox("p")
        self.use_af_t = QCheckBox("t")
        self.use_af_g = QCheckBox("g")

        # everything but the switch, which its container provides
        self._body = QWidget()
        self._body.setEnabled(False)
        self._body.setToolTip(AF_ON_AXIS_TOOLTIP)
        layout = QHBoxLayout(self._body)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("On Axis:"))
        layout.addWidget(self.use_af_p)
        layout.addWidget(self.use_af_t)
        layout.addWidget(self.use_af_g)
        layout.addStretch()
        QVBoxLayout(self).addWidget(self._body)

        self.use_af_p.toggled.connect(self.valueChanged)
        self.use_af_t.toggled.connect(self.valueChanged)
        self.use_af_g.toggled.connect(self.valueChanged)
        self.toggled.connect(self.valueChanged)

        self.setToolTip(AF_AXIS_TOOLTIP)

    def isChecked(self) -> bool:
        """Whether autofocus is switched on."""
        return self._checked

    def setChecked(self, checked: bool) -> None:
        """Switch autofocus on or off, along with its switch (see `setSwitch`)."""
        if checked == self._checked:
            return
        self._checked = checked
        self._body.setEnabled(checked)
        if self._set_switch is not None and (set_switch := self._set_switch()):
            set_switch(checked)
        self.toggled.emit(checked)

    def setSwitch(self, set_checked: Callable[[bool], object]) -> None:
        """Keep the control that switches autofocus in step, with `set_checked`.

        It is a method, kept weakly so that its owner can be freed.
        """
        self._set_switch = WeakMethod(set_checked)

    def event(self, event: QEvent | None) -> bool:
        if event is not None and event.type() in (
            QEvent.Type.EnabledChange,
            QEvent.Type.ToolTipChange,
        ):
            self.availabilityChanged.emit()
        return bool(super().event(event))

    def plan(self, axes: tuple[str, ...], **kwargs: Any) -> useq.AxesBasedAF:
        """Return an autofocus plan for `axes`, with the other fields of `setPlan`'s.

        `kwargs` are further fields (e.g. `autofocus_motor_offset`).
        """
        return (self._plan or useq.AxesBasedAF(axes=axes)).replace(axes=axes, **kwargs)

    def setPlan(self, plan: useq.AxesBasedAF | None) -> None:
        """Switch autofocus on, keeping the fields of `plan`, or off with `None`.

        The axes are set with `setValue`.
        """
        self._plan = plan
        self.setChecked(plan is not None)

    def value(self) -> tuple[str, ...]:
        """Return the autofocus axes, or `()` if autofocus is off."""
        af_axis: tuple[str, ...] = ()
        if not self.isEnabled() or not self.isChecked():
            return af_axis
        if self.use_af_p.isChecked():
            af_axis += ("p",)
        if self.use_af_t.isChecked():
            af_axis += ("t",)
        if self.use_af_g.isChecked():
            af_axis += ("g",)
        return af_axis

    def setValue(self, value: tuple[str, ...]) -> None:
        """Set widget value from a tuple of autofocus axes, switching it on for any."""
        self.use_af_p.setChecked("p" in value)
        self.use_af_t.setChecked("t" in value)
        self.use_af_g.setChecked("g" in value)
        if value:
            self.setChecked(True)


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
        self._save_button.setIcon(QIconifyIcon("mdi:content-save-outline"))
        self._save_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._save_button.clicked.connect(self.save)
        self._load_button = QPushButton("Load Settings")
        self._load_button.setIcon(QIconifyIcon("mdi:folder-open-outline"))
        self._load_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._load_button.clicked.connect(self.load)

        # -------------- Main Layout --------------

        self.keep_shutter_open = KeepShutterOpen()
        self.af_axis = AutofocusAxis()
        self.tab_wdg.add_autofocus(self.af_axis)

        # settings that always apply, so they are not in a tab
        settings = QWidget()
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(5, 5, 5, 5)
        settings_layout.setSpacing(5)
        axis_row = QHBoxLayout()
        axis_row.addWidget(QLabel("Axis Order:"))
        axis_row.addWidget(self.axis_order)
        axis_row.addStretch()
        settings_layout.addLayout(axis_row)
        settings_layout.addWidget(self.keep_shutter_open)
        self._settings_box = _wrap_in_card(settings)
        # in line with the tab pages
        margin = MDATabs._PAGE_MARGIN
        settings_row = QVBoxLayout()
        settings_row.setContentsMargins(margin, 0, margin, 0)
        settings_row.addWidget(self._settings_box)

        footer = QWidget()
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(margin, 0, margin, margin)
        estimate_row = QHBoxLayout()
        estimate_row.addWidget(self._time_warning)
        estimate_row.addWidget(self._duration_label, 1)
        footer_layout.addLayout(estimate_row)
        # a subclass adds its own actions (e.g. Run) after the stretch
        self._actions_row = QHBoxLayout()
        self._actions_row.addWidget(self._save_button)
        self._actions_row.addWidget(self._load_button)
        self._actions_row.addStretch()
        footer_layout.addLayout(self._actions_row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tab_wdg, 1)
        layout.addLayout(settings_row)
        layout.addWidget(footer)

        # -------------- Connections --------------

        self.channels.valueChanged.connect(self.valueChanged)
        self.time_plan.valueChanged.connect(self.valueChanged)
        self.stage_positions.valueChanged.connect(self.valueChanged)
        self.z_plan.valueChanged.connect(self._validate_af_with_z_plan)
        self.grid_plan.valueChanged.connect(self._on_grid_plan_value_changed)
        self.tab_wdg.tabChecked.connect(self._on_tab_checked)
        self.axis_order.currentTextChanged.connect(self.valueChanged)
        self.valueChanged.connect(self._update_time_estimate)

        self.keep_shutter_open.valueChanged.connect(self.valueChanged)
        self.af_axis.valueChanged.connect(self.valueChanged)
        self.stage_positions.af_per_position.toggled.connect(self._on_af_toggled)
        self.valueChanged.connect(self._update_z_modes)

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
        # observers see only the restored sequence, and no conflicting setting is
        # resolved against a half-restored one (see `_update_z_modes`)
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
        """Enable the autofocus settings unless an absolute Z plan is used.

        TOP_BOTTOM cannot be selected while autofocus is in use (see
        `_update_z_modes`), but it can be set from code.
        """
        self._enable_af(not self._uses_absolute_z())
        self.valueChanged.emit()

    def _uses_absolute_z(self) -> bool:
        """Return whether an absolute (TOP_BOTTOM) Z plan is used."""
        return bool(
            self.tab_wdg.isChecked(self.z_plan)
            and self.z_plan.mode() == Mode.TOP_BOTTOM
        )

    def _update_z_modes(self) -> None:
        """Keep the absolute Z mode from being selected while autofocus is in use.

        Set from code, an absolute Z plan wins instead: it disables autofocus (see
        `_validate_af_with_z_plan`).
        """
        af_in_use = bool(self.af_axis.value()) or self._use_af_per_position()
        z_plan = self.z_plan
        if af_in_use and z_plan.mode() == Mode.TOP_BOTTOM:
            # not used while the Z tab is unchecked, but it would be once checked
            z_plan.setMode(Mode.RANGE_AROUND)
        reason = TOP_BOTTOM_AF_TOOLTIP if af_in_use else ""
        z_plan.setModeBlocked(Mode.TOP_BOTTOM, reason)

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
            self._validate_af_with_z_plan()

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
        # if the 'af_per_position' checkbox in the PositionTable is checked, switch
        # autofocus on, on the p axis too.
        if self._use_af_per_position() and self.tab_wdg.isChecked(self.stage_positions):
            self.af_axis.setChecked(True)
            self.af_axis.use_af_p.setChecked(True)

    def _update_available_axis_orders(self) -> None:
        """Handle tabChecked signal.

        Hide columns in the channels tab accordingly.
        """
        with signals_blocked(self.axis_order):
            self.axis_order.clear()

            # show allowed permutations of selected axes
            for p in permutations(self.tab_wdg.usedAxes()):
                if (strp := "".join(p)) in ALLOWED_ORDERS:
                    self.axis_order.addItem(strp)

            self.axis_order.setEnabled(self.axis_order.count() > 1)

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
            if pos.sequence is not None and pos.sequence.autofocus_plan
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
