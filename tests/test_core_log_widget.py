from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

import pytest
from qtpy.QtWidgets import QApplication

from pymmcore_widgets import CoreLogWidget

if TYPE_CHECKING:
    from pathlib import Path

    from pymmcore_plus import CMMCorePlus
    from pytestqt.qtbot import QtBot


@pytest.fixture(autouse=True)
def private_log_file(global_mmcore: CMMCorePlus, tmp_path: Path) -> None:
    """Log to a file only this test uses.

    The default log file is shared by every pytest-xdist worker, and any of them
    can rotate it while a test is watching it.
    """
    log_file = tmp_path / "core.log"
    log_file.write_text("[IFO,App] Log start\n")
    global_mmcore.setPrimaryLogFile(str(log_file))


def test_core_log_widget_init(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    """Asserts that the CoreLogWidget initializes with the entire log to this point."""
    wdg = CoreLogWidget()
    qtbot.addWidget(wdg)

    # Assert log path is in the widget LineEdit
    log_path = global_mmcore.getPrimaryLogFile()
    assert log_path == wdg._log_path.text()

    wdg.clear()
    with qtbot.waitSignal(global_mmcore.events.systemConfigurationLoaded):
        global_mmcore.loadSystemConfiguration()

    def _check_log() -> None:
        if "Finished initializing" not in wdg._log_view.toPlainText():
            raise AssertionError("CoreLogWidget did not finish initializing.")

    qtbot.waitUntil(_check_log, timeout=1000)


def test_core_log_widget_update(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    wdg = CoreLogWidget()
    qtbot.addWidget(wdg)
    wdg._log_view.clear()

    # Write directly to the log file rather than using logMessage(), which goes
    # through C++ std::ofstream buffering that may not flush to disk in time
    # under heavy parallel I/O.
    new_message = "Test message"
    log_path = global_mmcore.getPrimaryLogFile()
    with open(log_path, "a") as f:
        f.write(f"[IFO,App] {new_message}\n")
        f.flush()
        os.fsync(f.fileno())

    def wait_for_update() -> None:
        QApplication.processEvents()
        wdg._reader._read_new()
        QApplication.processEvents()
        if f"[IFO,App] {new_message}" not in wdg._log_view.toPlainText():
            raise AssertionError("New message not found in CoreLogWidget.")

    qtbot.waitUntil(wait_for_update)


@pytest.mark.skipif(sys.platform == "win32", reason="Cannot rename an open file")
def test_core_log_widget_follows_rotation(
    qtbot: QtBot, global_mmcore: CMMCorePlus, tmp_path: Path
) -> None:
    log_file = tmp_path / "rotating.log"
    log_file.write_text("old line\n" * 10)
    wdg = CoreLogWidget(path=str(log_file))
    qtbot.addWidget(wdg)

    def wait_for_line(line: str) -> None:
        def _check() -> None:
            assert line in wdg._log_view.toPlainText()

        qtbot.waitUntil(_check)

    # the last lines of the old file are still read after it is renamed...
    with open(log_file, "a") as f:
        f.write("last old line\n")
    os.replace(log_file, f"{log_file}.1")
    wait_for_line("last old line")
    # ...while the new file doesn't exist yet, long enough for the watcher to drop it
    qtbot.wait(300)

    # a new file larger than the old one, so its size alone doesn't reveal the rotation
    log_file.write_text("filler\n" * 100 + "first new line\n")
    wait_for_line("first new line")
    assert str(log_file) in wdg._reader._watcher.files()

    with open(log_file, "a") as f:
        f.write("second new line\n")
    wait_for_line("second new line")


def test_core_log_widget_follows_truncation(
    qtbot: QtBot, global_mmcore: CMMCorePlus, tmp_path: Path
) -> None:
    log_file = tmp_path / "truncated.log"
    log_file.write_text("old line\n" * 10)
    wdg = CoreLogWidget(path=str(log_file))
    qtbot.addWidget(wdg)

    log_file.write_text("short\n")

    def _check() -> None:
        assert "short" in wdg._log_view.toPlainText()

    qtbot.waitUntil(_check)


def test_core_log_widget_clear(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    wdg = CoreLogWidget()
    qtbot.addWidget(wdg)

    assert wdg._log_view.toPlainText() != ""
    wdg._clear_btn.click()
    assert wdg._log_view.toPlainText() == ""


def test_core_log_widget_debug(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    wdg = CoreLogWidget()
    qtbot.addWidget(wdg)

    # The default state should be whatever the core is currently set to
    is_debug_enabled = wdg._debug_box.isChecked()
    assert global_mmcore.debugLogEnabled() == is_debug_enabled
    # and it should toggle as the button is clicked
    wdg._debug_box.toggle()
    assert global_mmcore.debugLogEnabled() != is_debug_enabled
    wdg._debug_box.toggle()
    assert global_mmcore.debugLogEnabled() == is_debug_enabled


def test_core_log_widget_autoscroll(qtbot: QtBot, global_mmcore: CMMCorePlus) -> None:
    wdg = CoreLogWidget()
    qtbot.addWidget(wdg)
    # Note that we must show the widget for the scrollbar maximum to be computed
    wdg.show()
    sb = wdg._log_view.verticalScrollBar()
    assert sb is not None

    # Stop the log file reader so only explicit _append_line calls affect
    # the scrollbar. Otherwise the reader's poll timer can add extra lines
    # during processEvents, causing the scrollbar maximum to jump
    # unpredictably (especially under parallel test execution).
    wdg._reader._stop()

    def add_new_line() -> None:
        wdg._append_line("Test message")
        QApplication.processEvents()

    # Make sure we have a scrollbar with nonzero size to test with
    # But we don't want it full yet
    wdg._log_view.clear()
    while sb.maximum() == 0:
        add_new_line()

    # Assert that adding a new line does not scroll if not at the bottom
    sb.setValue(sb.minimum())
    add_new_line()
    qtbot.waitUntil(lambda: sb.value() == sb.minimum())

    # Assert that adding a new line does scroll if at the bottom
    old_max = sb.maximum()
    sb.setValue(old_max)
    add_new_line()
    qtbot.waitUntil(lambda: sb.maximum() > old_max)
    assert sb.value() == sb.maximum()
