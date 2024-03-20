from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, cast

from fonticon_mdi6 import MDI6
from pymmcore_plus import CMMCorePlus, Keyword
from qtpy.QtCore import QSize, Qt
from qtpy.QtWidgets import (
    QBoxLayout,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QWidget,
    QWizard,
)
from superqt.fonticon import icon
from useq import AxesBasedAF, MDASequence, Position

from pymmcore_widgets import HCSWizard
from pymmcore_widgets._util import get_next_available_path
from pymmcore_widgets.arduino._arduino_led_widget import ArduinoLedWidget
from pymmcore_widgets.hcs._main_wizard_widget import HCSData
from pymmcore_widgets.useq_widgets import MDASequenceWidget
from pymmcore_widgets.useq_widgets._mda_sequence import PYMMCW_METADATA_KEY, MDATabs
from pymmcore_widgets.useq_widgets._time import TimePlanWidget

from ._core_channels import CoreConnectedChannelTable
from ._core_grid import CoreConnectedGridPlanWidget
from ._core_positions import CoreConnectedPositionTable
from ._core_z import CoreConnectedZPlanWidget
from ._save_widget import SaveGroupBox

if TYPE_CHECKING:
    from pyfirmata2 import Arduino, Pin

HCS = "hcs"
STIMULATION = "stimulation"
CRITICAL_MSG = (
    "'Arduino LED Stimulation' is selected but an error occurred while trying "
    "to communicate with the Arduino. \nPlease, verify that the device is "
    "connected and try again."
)
POWER_EXCEEDED_MSG = (
    "The maximum power of the LED has been exceeded. \nPlease, reduce "
    "the power and try again."
)


class CoreMDATabs(MDATabs):
    def __init__(
        self, parent: QWidget | None = None, core: CMMCorePlus | None = None
    ) -> None:
        self._mmc = core or CMMCorePlus.instance()
        super().__init__(parent)

    def create_subwidgets(self) -> None:
        self.time_plan = TimePlanWidget(1)
        self.stage_positions = CoreConnectedPositionTable(1, self._mmc)
        self.z_plan = CoreConnectedZPlanWidget(self._mmc)
        self.grid_plan = CoreConnectedGridPlanWidget(self._mmc)
        self.channels = CoreConnectedChannelTable(1, self._mmc)

    def _enable_tabs(self, enable: bool) -> None:
        """Enable or disable the tab checkboxes and their contents.

        However, we can still mover through the tabs and see their contents.
        """
        # disable tab checkboxes
        for cbox in self._cboxes:
            cbox.setEnabled(enable)
        # disable tabs contents
        self.time_plan.setEnabled(enable)
        self.stage_positions.setEnabled(enable)
        self.z_plan.setEnabled(enable)
        self.grid_plan.setEnabled(enable)
        self.channels.setEnabled(enable)


class MDAWidget(MDASequenceWidget):
    """Main MDA Widget connected to a [`pymmcore_plus.CMMCorePlus`][] instance.

    It provides a GUI to construct and run a [`useq.MDASequence`][].  Unlike
    [`useq_widgets.MDASequenceWidget`][pymmcore_widgets.MDASequenceWidget], this
    widget is connected to a [`pymmcore_plus.CMMCorePlus`][] instance, enabling
    awareness and control of the current state of the microscope.

    Parameters
    ----------
    parent : QWidget | None
        Optional parent widget, by default None.
    mmcore : CMMCorePlus | None
        Optional [`CMMCorePlus`][pymmcore_plus.CMMCorePlus] micromanager core.
        By default, None. If not specified, the widget will use the active
        (or create a new)
        [`CMMCorePlus.instance`][pymmcore_plus.core._mmcore_plus.CMMCorePlus.instance].
    """

    def __init__(
        self, *, parent: QWidget | None = None, mmcore: CMMCorePlus | None = None
    ) -> None:
        # create a couple core-connected variants of the tab widgets
        self._mmc = mmcore or CMMCorePlus.instance()

        super().__init__(parent=parent, tab_widget=CoreMDATabs(None, self._mmc))

        self.save_info = SaveGroupBox(parent=self)
        self.save_info.valueChanged.connect(self.valueChanged)
        self.control_btns = _MDAControlButtons(self._mmc, self)

        # -------- HCS wizard --------
        self.hcs = HCSWizard(parent=self)
        self._hcs_value: HCSData | None = None
        # rename the finish button to "Add Positions"
        self.hcs.fov_page.setButtonText(
            QWizard.WizardButton.FinishButton, "Add Positions"
        )
        self.hcs_button = QPushButton("HCS")
        self.hcs_button.setIcon(icon(MDI6.vector_polyline))
        self.hcs_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hcs_button.setToolTip("Open the HCS wizard.")

        pos_table_layout = cast(QBoxLayout, self.stage_positions.layout().itemAt(2))
        pos_table_layout.insertWidget(2, self.hcs_button)

        # -------- initialize -----------

        self._on_sys_config_loaded()

        # ------------ layout ------------

        layout = cast("QBoxLayout", self.layout())
        layout.insertWidget(0, self.save_info)
        layout.addWidget(self.control_btns)

        # ------------ Arduino ------------
        self._arduino_led_wdg = ArduinoLedWidget(self)
        layout.insertWidget(4, self._arduino_led_wdg)
        layout.insertStretch(5, 1)

        # ------------ connect signals ------------

        self.control_btns.run_btn.clicked.connect(self.run_mda)
        self.control_btns.pause_btn.released.connect(self._mmc.mda.toggle_pause)
        self.control_btns.cancel_btn.released.connect(self._mmc.mda.cancel)
        self._mmc.mda.events.sequenceStarted.connect(self._on_mda_started)
        self._mmc.mda.events.sequenceFinished.connect(self._on_mda_finished)
        self._mmc.events.systemConfigurationLoaded.connect(self._on_sys_config_loaded)

        # HCS wizard connections
        self.stage_positions.valueChanged.connect(self._set_hcs_value)
        self.hcs_button.clicked.connect(self._show_hcs)
        # connect the HCS wizard valueChanged signal to the MDAWidget so that it can
        # populate the Positions table with the new positions from the HCS wizard
        self.hcs.valueChanged.connect(self._add_to_positios_table)

        self.destroyed.connect(self._disconnect)

    # ------------------- public Methods ----------------------

    def value(self) -> MDASequence:
        """Set the current state of the widget from a [`useq.MDASequence`][]."""
        val = super().value()
        replace: dict = {}

        # if the z plan is relative and there are stage positions but the 'include z' is
        # unchecked, use the current z stage position as the relative starting one.
        if (
            val.z_plan
            and val.z_plan.is_relative
            and (val.stage_positions and not self.stage_positions.include_z.isChecked())
        ):
            z = self._mmc.getZPosition() if self._mmc.getFocusDevice() else None
            replace["stage_positions"] = tuple(
                pos.replace(z=z) for pos in val.stage_positions
            )

        # if there is an autofocus_plan but the autofocus_motor_offset is None, set it
        # to the current value
        if (afplan := val.autofocus_plan) and afplan.autofocus_motor_offset is None:
            p2 = afplan.replace(autofocus_motor_offset=self._mmc.getAutoFocusOffset())
            replace["autofocus_plan"] = p2

        # if there are no stage positions, use the current stage position
        if not val.stage_positions:
            replace["stage_positions"] = (self._get_current_stage_position(),)
            # if "p" is not in the axis order, we need to add it or the position will
            # not be in the event
            if "p" not in val.axis_order:
                axis_order = list(val.axis_order)
                # add the "p" axis at the beginning or after the "t" as the default
                if "t" in axis_order:
                    axis_order.insert(axis_order.index("t") + 1, "p")
                else:
                    axis_order.insert(0, "p")
                replace["axis_order"] = tuple(axis_order)

        if replace:
            val = val.replace(**replace)

        meta: dict = val.metadata.setdefault(PYMMCW_METADATA_KEY, {})
        meta[STIMULATION] = self._arduino_led_wdg.value()
        meta[HCS] = self._hcs_value or {}
        if self.save_info.isChecked():
            meta.update(self.save_info.value())

        return val

    def setValue(self, value: MDASequence) -> None:
        """Get the current state of the widget as a [`useq.MDASequence`][]."""
        super().setValue(value)
        meta = value.metadata.get(PYMMCW_METADATA_KEY, {})
        # save info
        self.save_info.setValue(meta)
        # if the HCS wizard has been used, set the positions from the HCS wizard
        self._hcs_value = None
        if hcs := meta.get(HCS):
            self._hcs_value = HCSData.from_dict(hcs)
            self.hcs.setValue(self._hcs_value)
        # update arduino led widget
        if ard := meta.get(STIMULATION):
            self._arduino_led_wdg.setValue(ard)

    def get_next_available_path(self, requested_path: Path) -> Path:
        """Get the next available path.

        This method is called immediately before running an MDA to ensure that the file
        being saved does not overwrite an existing file. It is also called at the end
        of the experiment to update the save widget with the next available path.

        It may be overridden to provide custom behavior, but it should always return a
        Path object to a non-existing file or folder.

        The default behavior adds/increments a 3-digit counter at the end of the path
        (before the extension) if the path already exists.

        Parameters
        ----------
        requested_path : Path
            The path we are requesting for use.
        """
        return get_next_available_path(requested_path=requested_path)

    def run_mda(self) -> None:
        """Run the MDA sequence experiment."""
        # in case the user does not press enter after editing the save name.
        self.save_info.save_name.editingFinished.emit()

        # if autofocus has been requested, but the autofocus device is not engaged,
        # and position-specific offsets haven't been set, show a warning
        pos = self.stage_positions
        if (
            self.af_axis.value()
            and not self._mmc.isContinuousFocusLocked()
            and (not self.tab_wdg.isChecked(pos) or not pos.af_per_position.isChecked())
            and not self._confirm_af_intentions()
        ):
            return

        # Arduino checks___________________________________
        # hide the Arduino LED control widget if visible
        self._arduino_led_wdg._arduino_led_control.hide()
        if not self._arduino_led_wdg.isChecked():
            self._set_arduino_props(None, None)
        else:
            # check if power exceeded
            if self._arduino_led_wdg.is_max_power_exceeded():
                self._set_arduino_props(None, None)
                self._show_critical_led_message(POWER_EXCEEDED_MSG)
                return

            # check if the Arduino and the LED pin are available
            arduino = self._arduino_led_wdg.board()
            led = self._arduino_led_wdg.ledPin()
            if arduino is None or led is None or not self._test_arduino_connection(led):
                self._set_arduino_props(None, None)
                self._arduino_led_wdg._arduino_led_control._enable(False)
                self._show_critical_led_message(CRITICAL_MSG)
                return

            # enable the Arduino board and the LED pin in the MDA engine
            self._set_arduino_props(arduino, led)

        sequence = self.value()

        # technically, this is in the metadata as well, but isChecked is more direct
        if self.save_info.isChecked():
            save_path = self._update_save_path_from_metadata(
                sequence, update_metadata=True
            )
        else:
            save_path = None

        # run the MDA experiment asynchronously∏
        self._mmc.run_mda(sequence, output=save_path)

    # ------------------- private Methods ----------------------

    def _set_arduino_props(self, arduino: Arduino | None, led: Pin | None) -> None:
        """Enable the Arduino board and the LED pin in the MDA engine."""
        if not self._mmc.mda.engine:
            return
        self._mmc.mda.engine.setArduinoBoard(arduino)  # type: ignore
        self._mmc.mda.engine.setArduinoLedPin(led)  # type: ignore

    def _test_arduino_connection(self, led: Pin) -> bool:
        """Test the connection with the Arduino."""
        try:
            led.write(0.0)
            return True
        except Exception:
            return False

    def _show_critical_led_message(self, msg: str) -> None:
        QMessageBox.critical(self, "Arduino Error", msg, QMessageBox.StandardButton.Ok)
        return

    def _on_sys_config_loaded(self) -> None:
        # TODO: connect objective change event to update suggested step
        self.z_plan.setSuggestedStep(_guess_NA(self._mmc) or 0.5)
        self._hcs_value = None

    def _show_hcs(self) -> None:
        # if hcs is open, raise it, otherwise show it
        if self.hcs.isVisible():
            self.hcs.raise_()
        else:
            self.hcs.show()

    def _set_hcs_value(self) -> None:
        """Set the `_hcs_value` attribute.

        If the user changes the number of positions in the table after adding them with
        the HCS wizard, the `_using_hcs` attribute should be set to None.
        """
        self._hcs_value = None
        hcs_pos = self.hcs.get_positions() or []
        if self.stage_positions._table.rowCount() == len(hcs_pos):
            self._hcs_value = self.hcs.value()

    def _add_to_positios_table(self, value: HCSData) -> None:
        """Add a list of positions to the Positions table."""
        # positions = value.positions
        positions = self.hcs.get_positions()

        if not positions:
            return

        # get the current sequence from the MDAWidget
        sequence = self.value()

        # replace the stage_positions with the new positions form the HCS wizard
        sequence = sequence.replace(stage_positions=positions)

        # if autofocus is enabled and locked, add the autofocus plan
        if (
            self._mmc.getAutoFocusDevice()
            and self.af_axis.value()
            and self._mmc.isContinuousFocusLocked()
        ):
            sequence = sequence.replace(
                autofocus_plan=AxesBasedAF(
                    axes=self.af_axis.value(),
                    autofocus_motor_offset=self._mmc.getAutoFocusOffset(),
                )
            )

        # update the MDAWidget
        self.setValue(sequence)
        self._hcs_value = value

    def _get_current_stage_position(self) -> Position:
        """Return the current stage position."""
        x = self._mmc.getXPosition() if self._mmc.getXYStageDevice() else None
        y = self._mmc.getYPosition() if self._mmc.getXYStageDevice() else None
        z = self._mmc.getPosition() if self._mmc.getFocusDevice() else None
        return Position(x=x, y=y, z=z)

    def _update_save_path_from_metadata(
        self,
        sequence: MDASequence,
        update_widget: bool = True,
        update_metadata: bool = False,
    ) -> Path | None:
        """Get the next available save path from sequence metadata and update widget.

        Parameters
        ----------
        sequence : MDASequence
            The MDA sequence to get the save path from. (must be in the
            'pymmcore_widgets' key of the metadata)
        update_widget : bool, optional
            Whether to update the save widget with the new path, by default True.
        update_metadata : bool, optional
            Whether to update the Sequence metadata with the new path, by default False.
        """
        if (
            (meta := sequence.metadata.get(PYMMCW_METADATA_KEY, {}))
            and (save_dir := meta.get("save_dir"))
            and (save_name := meta.get("save_name"))
        ):
            requested = (Path(save_dir) / str(save_name)).expanduser().resolve()
            next_path = self.get_next_available_path(requested)
            if next_path != requested:
                if update_widget:
                    self.save_info.setValue(next_path)
                    if update_metadata:
                        meta.update(self.save_info.value())
            return next_path
        return None

    def _confirm_af_intentions(self) -> bool:
        msg = (
            "You've selected to use autofocus for this experiment, "
            f"but the '{self._mmc.getAutoFocusDevice()!r}' autofocus device "
            "is not currently engaged. "
            "\n\nRun anyway?"
        )

        response = QMessageBox.warning(
            self,
            "Confirm AutoFocus",
            msg,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return bool(response == QMessageBox.StandardButton.Ok)

    def _enable_widgets(self, enable: bool) -> None:
        for child in self.children():
            if isinstance(child, CoreMDATabs):
                child._enable_tabs(enable)
            elif child is not self.control_btns and hasattr(child, "setEnabled"):
                child.setEnabled(enable)

    def _on_mda_started(self) -> None:
        self._enable_widgets(False)

    def _on_mda_finished(self, sequence: MDASequence) -> None:
        self._enable_widgets(True)
        # update the save name in the gui with the next available path
        # FIXME: this is actually a bit error prone in the case of super fast
        # experiments and delayed writers that haven't yet written anything to disk
        # (e.g. the next available path might be the same as the current one)
        # however, the quick fix of using a QTimer.singleShot(0, ...) makes for
        # difficulties in testing.
        # FIXME: Also, we really don't care about the last sequence at this point
        # anyway.  We should just update the save widget with the next available path
        # based on what's currently in the save widget, since that's what really
        # matters (not whatever the last requested mda was)
        self._update_save_path_from_metadata(sequence)

    def _disconnect(self) -> None:
        with suppress(Exception):
            self._mmc.mda.events.sequenceStarted.disconnect(self._on_mda_started)
            self._mmc.mda.events.sequenceFinished.disconnect(self._on_mda_finished)


class _MDAControlButtons(QWidget):
    """Run, pause, and cancel buttons at the bottom of the MDA Widget."""

    def __init__(self, mmcore: CMMCorePlus, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._mmc = mmcore
        self._mmc.mda.events.sequencePauseToggled.connect(self._on_mda_paused)
        self._mmc.mda.events.sequenceStarted.connect(self._on_mda_started)
        self._mmc.mda.events.sequenceFinished.connect(self._on_mda_finished)

        icon_size = QSize(24, 24)
        self.run_btn = QPushButton("Run")
        self.run_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.run_btn.setIcon(icon(MDI6.play_circle_outline, color="lime"))
        self.run_btn.setIconSize(icon_size)

        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pause_btn.setIcon(icon(MDI6.pause_circle_outline, color="green"))
        self.pause_btn.setIconSize(icon_size)
        self.pause_btn.hide()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cancel_btn.setIcon(icon(MDI6.stop_circle_outline, color="#C33"))
        self.cancel_btn.setIconSize(icon_size)
        self.cancel_btn.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch()
        layout.addWidget(self.run_btn)
        layout.addWidget(self.pause_btn)
        layout.addWidget(self.cancel_btn)

        self.destroyed.connect(self._disconnect)

    def _on_mda_started(self) -> None:
        self.run_btn.hide()
        self.pause_btn.show()
        self.cancel_btn.show()

    def _on_mda_finished(self) -> None:
        self.run_btn.show()
        self.pause_btn.hide()
        self.cancel_btn.hide()
        self._on_mda_paused(False)

    def _on_mda_paused(self, paused: bool) -> None:
        if paused:
            self.pause_btn.setIcon(icon(MDI6.play_circle_outline, color="lime"))
            self.pause_btn.setText("Resume")
        else:
            self.pause_btn.setIcon(icon(MDI6.pause_circle_outline, color="green"))
            self.pause_btn.setText("Pause")

    def _disconnect(self) -> None:
        with suppress(Exception):
            self._mmc.mda.events.sequencePauseToggled.disconnect(self._on_mda_paused)
            self._mmc.mda.events.sequenceStarted.disconnect(self._on_mda_started)
            self._mmc.mda.events.sequenceFinished.disconnect(self._on_mda_finished)


def _guess_NA(core: CMMCorePlus) -> float | None:
    with suppress(RuntimeError):
        if not (pix_cfg := core.getCurrentPixelSizeConfig()):
            return None  # pragma: no cover

        data = core.getPixelSizeConfigData(pix_cfg)
        for obj in core.guessObjectiveDevices():
            key = (obj, Keyword.Label)
            if key in data:
                val = data[key]
                for word in val.split():
                    try:
                        na = float(word)
                    except ValueError:
                        continue
                    if 0.1 < na < 1.5:
                        return na
    return None
