from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pymmcore_plus import CMMCorePlus
from qtpy import QtWidgets as QtW
from qtpy.QtCore import QSize, Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from useq import MDASequence, NoGrid  # type: ignore

from .._util import _select_output_unit, guess_channel_group
from ._channel_table_widget import ChannelTable
from ._general_mda_widgets import _MDAControlButtons, _MDATimeLabel
from ._grid_widget import GridWidget
from ._positions_table_widget import PositionTable
from ._time_plan_widget import TimePlanWidget
from ._zstack_widget import ZStackWidget

if TYPE_CHECKING:
    from typing_extensions import TypedDict

    class PositionDict(TypedDict, total=False):
        """Position dictionary."""

        x: float | None
        y: float | None
        z: float | None
        name: str | None
        sequence: MDASequence | None


LBL_SIZEPOLICY = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

GROUP_STYLE = (
    "QGroupBox::indicator {border: 0px; width: 0px; height: 0px; border-radius: 0px;}"
)


class TabBar(QTabBar):
    """A TabBar subclass that allows to control the minimum width of each tab."""

    def __init__(self, parent: QWidget | None = None, *, checkbox_width: int = 0):
        super().__init__(parent)

        self._checkbox_width = checkbox_width

    def tabSizeHint(self, index: int) -> QSize:
        size = QTabBar.tabSizeHint(self, index)
        w = int(size.width() + self._checkbox_width)
        return QSize(w, size.height())


class Grid(GridWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent=parent)
        self._grid = NoGrid()

    def _update_info(self) -> None:
        super()._update_info()
        self.valueChanged.emit(self.value())
        print(self.value())


class MDAWidget(QWidget):
    """A Multi-dimensional acquisition Widget.

    The `MDAWidget` provides a GUI to construct a
    [`useq.MDASequence`](https://github.com/tlambert03/useq-schema) object.
    If the `include_run_button` parameter is set to `True`, a "run" button is added
    to the GUI and, when clicked, the generated
    [`useq.MDASequence`](https://github.com/tlambert03/useq-schema)
    is passed to the
    [`CMMCorePlus.instance`][pymmcore_plus.core._mmcore_plus.CMMCorePlus.run_mda]
    method and the acquisition
    is executed.

    Parameters
    ----------
    parent : QWidget | None
        Optional parent widget, by default None.
    include_run_button: bool
        By default, `False`. If `True`, a "run" button is added to the widget.
        The acquisition defined by the
        [`useq.MDASequence`](https://github.com/tlambert03/useq-schema)
        built through the widget is executed when clicked.
    mmcore : CMMCorePlus | None
        Optional [`pymmcore_plus.CMMCorePlus`][] micromanager core.
        By default, None. If not specified, the widget will use the active
        (or create a new)
        [`CMMCorePlus.instance`][pymmcore_plus.core._mmcore_plus.CMMCorePlus.instance].
    """

    def __init__(
        self,
        *,
        parent: QtW.QWidget | None = None,
        include_run_button: bool = False,
        mmcore: CMMCorePlus | None = None,
    ) -> None:
        super().__init__(parent=parent)

        self._mmc = mmcore or CMMCorePlus.instance()
        self._include_run_button = include_run_button

        # Widgets for Channels, Time, ZStack, and Positions in the Scroll Area
        self.channel_groupbox = ChannelTable()
        self.channel_groupbox.setTitle("")
        self.channel_groupbox.setEnabled(False)
        self.channel_groupbox.valueChanged.connect(self._enable_run_btn)
        self.channel_groupbox._advanced_cbox.toggled.connect(self._update_total_time)

        self.time_groupbox = TimePlanWidget()
        self.time_groupbox.setTitle("")
        self.time_groupbox.setChecked(False)
        self.time_groupbox.setStyleSheet(GROUP_STYLE)
        self.time_groupbox.toggled.connect(self._update_total_time)
        self.time_groupbox.toggled.connect(self._on_time_toggled)

        self.stack_groupbox = ZStackWidget()
        self.stack_groupbox.setTitle("")
        self.stack_groupbox.setChecked(False)
        self.stack_groupbox.setStyleSheet(GROUP_STYLE)
        self.stack_groupbox.toggled.connect(self._update_total_time)

        self.position_groupbox = PositionTable()
        self.position_groupbox.setTitle("")
        self.position_groupbox.setChecked(False)
        self.position_groupbox.setStyleSheet(GROUP_STYLE)
        self.position_groupbox.toggled.connect(self._update_total_time)

        self.grid_groupbox = QGroupBox()
        self.grid_groupbox.setLayout(QVBoxLayout())
        self.grid_groupbox.layout().setContentsMargins(0, 0, 0, 0)
        self.grid_groupbox.layout().setSpacing(0)
        self.grid_groupbox.setTitle("")
        self.grid_groupbox.setEnabled(False)
        self.grid_groupbox.setStyleSheet(GROUP_STYLE)
        self._mda_grid_wdg = Grid()
        self._mda_grid_wdg.valueChanged.connect(self._update_total_time)
        self.grid_groupbox.layout().addWidget(self._mda_grid_wdg)
        spacer = QSpacerItem(
            0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        self.grid_groupbox.layout().addSpacerItem(spacer)
        # hide grid button
        self._mda_grid_wdg.layout().itemAt(
            self._mda_grid_wdg.layout().count() - 1
        ).widget().hide()

        # below the scroll area, some feedback widgets and buttons
        self.time_lbl = _MDATimeLabel()

        self.buttons_wdg = _MDAControlButtons()
        self.buttons_wdg.pause_button.hide()
        self.buttons_wdg.cancel_button.hide()
        self.buttons_wdg.run_button.hide()

        # LAYOUT

        central_layout = QVBoxLayout()
        central_layout.setSpacing(20)
        central_layout.setContentsMargins(10, 10, 10, 10)

        self._tab = QTabWidget()

        self._checkbox_channel = QCheckBox("")
        self._checkbox_channel.setObjectName("Channels")
        self._checkbox_channel.toggled.connect(self._on_toggled)
        self._checkbox_z = QCheckBox("")
        self._checkbox_z.setObjectName("ZStack")
        self._checkbox_z.toggled.connect(self._on_toggled)
        self._checkbox_time = QCheckBox("")
        self._checkbox_time.setObjectName("Time")
        self._checkbox_time.toggled.connect(self._on_toggled)
        self._checkbox_position = QCheckBox("")
        self._checkbox_position.setObjectName("Positions")
        self._checkbox_position.toggled.connect(self._on_toggled)
        self._checkbox_grid = QCheckBox("")
        self._checkbox_grid.setObjectName("Grid")
        self._checkbox_grid.toggled.connect(self._on_toggled)

        self._tabbar = TabBar(checkbox_width=self._checkbox_channel.sizeHint().width())

        self._tab.addTab(self.channel_groupbox, "")

        zwdg = QWidget()
        zwdg.setLayout(QVBoxLayout())
        zwdg.layout().setContentsMargins(0, 0, 0, 0)
        zwdg.layout().setSpacing(0)
        zwdg.layout().addWidget(self.stack_groupbox)
        spacer = QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding)
        zwdg.layout().addSpacerItem(spacer)
        self._tab.addTab(zwdg, "")

        self._tab.addTab(self.position_groupbox, "")

        twdg = QWidget()
        twdg.setLayout(QVBoxLayout())
        twdg.layout().setContentsMargins(0, 0, 0, 0)
        twdg.layout().setSpacing(0)
        twdg.layout().addWidget(self.time_groupbox)
        spacer = QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding)
        twdg.layout().addSpacerItem(spacer)
        self._tab.addTab(twdg, "")

        self._tab.addTab(self.grid_groupbox, "")

        self._tabbar.addTab("Channels")
        self._tabbar.setTabButton(
            0, QTabBar.ButtonPosition.LeftSide, self._checkbox_channel
        )
        self._tabbar.addTab("Z Stack")
        self._tabbar.setTabButton(1, QTabBar.ButtonPosition.LeftSide, self._checkbox_z)
        self._tabbar.addTab("Positions")
        self._tabbar.setTabButton(
            2, QTabBar.ButtonPosition.LeftSide, self._checkbox_position
        )
        self._tabbar.addTab("Time")
        self._tabbar.setTabButton(
            3, QTabBar.ButtonPosition.LeftSide, self._checkbox_time
        )

        self._tabbar.addTab("Grid")
        self._tabbar.setTabButton(
            4, QTabBar.ButtonPosition.LeftSide, self._checkbox_grid
        )

        self._tab.setTabBar(self._tabbar)

        central_layout.addWidget(self._tab)
        self._central_widget = QWidget()
        self._central_widget.setLayout(central_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setWidget(self._central_widget)

        self.setLayout(QVBoxLayout())
        self.layout().setSpacing(10)
        self.layout().setContentsMargins(10, 10, 10, 10)
        self.layout().addWidget(scroll)
        self.layout().addWidget(self.time_lbl)
        self.layout().addWidget(self.buttons_wdg)

        # CONNECTIONS

        self.buttons_wdg.pause_button.released.connect(self._mmc.mda.toggle_pause)
        self.buttons_wdg.cancel_button.released.connect(self._mmc.mda.cancel)
        # connect valueUpdated signal
        self.channel_groupbox.valueChanged.connect(self._update_total_time)
        self.stack_groupbox.valueChanged.connect(self._update_total_time)
        self.time_groupbox.valueChanged.connect(self._update_total_time)
        self.time_groupbox.toggled.connect(self._update_total_time)
        self.position_groupbox.valueChanged.connect(self._update_total_time)
        # connect mmcore signals
        self._mmc.mda.events.sequenceStarted.connect(self._on_mda_started)
        self._mmc.mda.events.sequenceFinished.connect(self._on_mda_finished)
        self._mmc.events.systemConfigurationLoaded.connect(self._on_sys_cfg_loaded)

        # connect run button
        if self._include_run_button:
            self.buttons_wdg.run_button.clicked.connect(self._on_run_clicked)
            self.buttons_wdg.run_button.show()

        self._on_sys_cfg_loaded()

    def _on_toggled(self, checked: bool) -> None:
        _sender = self.sender().objectName()
        if _sender == "Channels":
            self._tab.setCurrentIndex(0)
            self.channel_groupbox.setEnabled(checked)
            self._enable_run_btn()
            self._update_total_time()
        elif _sender == "ZStack":
            self._tab.setCurrentIndex(1)
            self.stack_groupbox.setChecked(checked)
        elif _sender == "Positions":
            self._tab.setCurrentIndex(2)
            self.position_groupbox.setChecked(checked)
        elif _sender == "Time":
            self._tab.setCurrentIndex(3)
            self.time_groupbox.setChecked(checked)
        elif _sender == "Grid":
            self._tab.setCurrentIndex(4)
            self.grid_groupbox.setEnabled(checked)

    def _on_sys_cfg_loaded(self) -> None:
        if channel_group := self._mmc.getChannelGroup() or guess_channel_group():
            self._mmc.setChannelGroup(channel_group)
        self.channel_groupbox.clear()

    def _set_enabled(self, enabled: bool) -> None:
        self.time_groupbox.setEnabled(enabled)
        self.buttons_wdg.acquisition_order_comboBox.setEnabled(enabled)
        self.channel_groupbox.setEnabled(enabled)

        if not self._mmc.getXYStageDevice():
            self.position_groupbox.setChecked(False)
            self.position_groupbox.setEnabled(False)
        else:
            self.position_groupbox.setEnabled(enabled)

        if not self._mmc.getFocusDevice():
            self.stack_groupbox.setChecked(False)
            self.stack_groupbox.setEnabled(False)
        else:
            self.stack_groupbox.setEnabled(enabled)

    def _on_mda_started(self) -> None:
        self._set_enabled(False)
        if self._include_run_button:
            self.buttons_wdg.pause_button.show()
            self.buttons_wdg.cancel_button.show()
        self.buttons_wdg.run_button.hide()

    def _on_mda_finished(self) -> None:
        self._set_enabled(True)
        self.buttons_wdg.pause_button.hide()
        self.buttons_wdg.cancel_button.hide()
        if self._include_run_button:
            self.buttons_wdg.run_button.show()

    def _on_mda_paused(self, paused: bool) -> None:
        self.buttons_wdg.pause_button.setText("Resume" if paused else "Pause")

    def set_state(self, state: dict | MDASequence | str | Path) -> None:
        """Set current state of MDA widget.

        Parameters
        ----------
        state : dict | MDASequence | str | Path
            MDASequence state in the form of a dict, MDASequence object, or a str or
            Path pointing to a sequence.yaml file
        """
        # sourcery skip: low-code-quality
        if isinstance(state, (str, Path)):
            state = MDASequence.parse_file(state)
        elif isinstance(state, dict):
            state = MDASequence(**state)
        if not isinstance(state, MDASequence):
            raise TypeError("state must be an MDASequence, dict, or yaml file")

        self.buttons_wdg.acquisition_order_comboBox.setCurrentText(state.axis_order)

        # set channel table
        if state.channels:
            self.channel_groupbox.set_state([c.dict() for c in state.channels])

        # set Z
        if state.z_plan:
            self.stack_groupbox.setChecked(True)
            self.stack_groupbox.set_state(state.z_plan.dict())
        else:
            self.stack_groupbox.setChecked(False)

        # set time
        if state.time_plan:
            self.time_groupbox.setChecked(True)
            self.time_groupbox.set_state(state.time_plan.dict())
        else:
            self.time_groupbox.setChecked(False)

        # set stage positions
        if state.stage_positions:
            self.position_groupbox.setChecked(True)
            self.position_groupbox.set_state(list(state.stage_positions))
        else:
            self.position_groupbox.setChecked(False)

    def get_state(self) -> MDASequence:
        """Get current state of widget and build a useq.MDASequence.

        Returns
        -------
        useq.MDASequence
        """
        channels = self.channel_groupbox.value()

        z_plan = (
            self.stack_groupbox.value() if self.stack_groupbox.isChecked() else None
        )
        time_plan = (
            self.time_groupbox.value() if self.time_groupbox.isChecked() else None
        )

        stage_positions: list[PositionDict] = []
        _, _, width, height = self._mmc.getROI(self._mmc.getCameraDevice())
        width = int(width * self._mmc.getPixelSizeUm())
        height = int(height * self._mmc.getPixelSizeUm())
        if self.position_groupbox.isChecked():
            for p in self.position_groupbox.value():
                if p.get("sequence"):
                    p_sequence = MDASequence(**p.get("sequence"))  # type: ignore
                    p_sequence = p_sequence.replace(
                        axis_order=self.buttons_wdg.acquisition_order_comboBox.currentText()
                    )
                    p_sequence.set_fov_size((width, height))  # type: ignore
                    p["sequence"] = p_sequence

                stage_positions.append(p)

        if not stage_positions:
            stage_positions = self._get_current_position()

        grid_plan = (
            self._mda_grid_wdg.value() if self.grid_groupbox.isChecked() else NoGrid()
        )

        sequence = MDASequence(
            axis_order=self.buttons_wdg.acquisition_order_comboBox.currentText(),
            channels=channels,
            stage_positions=stage_positions,
            z_plan=z_plan,
            time_plan=time_plan,
            grid_plan=grid_plan,
        )
        sequence.set_fov_size((width, height))  # type: ignore

        return sequence

    def _get_current_position(self) -> list[PositionDict]:
        return [
            {
                "name": "Pos000",
                "x": (
                    self._mmc.getXPosition() if self._mmc.getXYStageDevice() else None
                ),
                "y": (
                    self._mmc.getYPosition() if self._mmc.getXYStageDevice() else None
                ),
                "z": (self._mmc.getZPosition() if self._mmc.getFocusDevice() else None),
            }
        ]

    def _on_run_clicked(self) -> None:
        """Run the MDA sequence experiment."""
        # construct a `useq.MDASequence` object from the values inserted in the widget
        experiment = self.get_state()
        # run the MDA experiment asynchronously
        self._mmc.run_mda(experiment)
        return

    def _enable_run_btn(self) -> None:
        self.buttons_wdg.run_button.setEnabled(
            self.channel_groupbox._table.rowCount() > 0
            and self._checkbox_channel.isChecked()
        )

    def _on_time_toggled(self, checked: bool) -> None:
        """Hide the warning if the time groupbox is unchecked."""
        if not checked and self.time_groupbox._warning_widget.isVisible():
            self.time_groupbox.setWarningVisible(False)
        else:
            self._update_total_time()

    def _update_total_time(self, *, tiles: int = 1) -> None:
        """Update the minimum total acquisition time info."""
        print("___________")

        if not self.channel_groupbox.value() or not self._checkbox_channel.isChecked():
            self.time_lbl._total_time_lbl.setText(
                "Minimum total acquisition time: 0 sec."
            )
            return

        total_time: float = 0.0
        _per_timepoints: dict[int, float] = {}
        t_per_tp_msg = ""

        for e in self.get_state():
            if e.exposure is None:
                continue

            total_time = total_time + (e.exposure / 1000)
            if self.time_groupbox.isChecked():
                _t = e.index["t"]
                _exp = e.exposure / 1000
                _per_timepoints[_t] = _per_timepoints.get(_t, 0) + _exp

        if _per_timepoints:
            time_value = self.time_groupbox.value()
            timepoints = time_value["loops"]
            interval = time_value["interval"].total_seconds()
            total_time = total_time + (timepoints - 1) * interval

            # check if the interval is smaller than the sum of the exposure times
            sum_ch_exp = sum(
                (c["exposure"] / 1000)
                for c in self.channel_groupbox.value()
                if c["exposure"] is not None
            )
            self.time_groupbox.setWarningVisible(0 < interval < sum_ch_exp)

            # group by time
            _group_by_time: dict[float, list[int]] = {
                n: [k for k in _per_timepoints if _per_timepoints[k] == n]
                for n in set(_per_timepoints.values())
            }

            t_per_tp_msg = "\nMinimum acquisition time(s) per timepoint: "
            if len(_group_by_time) == 1:
                min_aq_tp, _tp_unit = _select_output_unit(float(_per_timepoints[0]))
                t_per_tp_msg = f"{t_per_tp_msg}{min_aq_tp:.4f} {_tp_unit}."
            else:
                # print longest timepoint first and other in brackets
                _tp = []
                for idx, i in enumerate(sorted(_per_timepoints.values(), reverse=True)):
                    aq, u = _select_output_unit(float(i))
                    if idx == 0:
                        t_per_tp_msg = f"{t_per_tp_msg}{aq:.4f} {u} ("
                    elif (aq, u) in _tp:
                        continue
                    else:
                        t_per_tp_msg = f"{t_per_tp_msg}{aq:.4f} {u},  "
                    _tp.append((aq, u))
                t_per_tp_msg = f"{t_per_tp_msg[:-3]})."

        _min_tot_time, _unit = _select_output_unit(total_time)
        tot_acq_msg = f"Minimum total acquisition time: {_min_tot_time:.4f} {_unit}."
        self.time_lbl._total_time_lbl.setText(f"{tot_acq_msg}{t_per_tp_msg}")
