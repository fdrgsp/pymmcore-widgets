import os
import platform
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from pytestqt.qtbot import QtBot

from pymmcore_widgets import InstallWidget, _install_widget

LINUX = platform.system() == "Linux"
PY311 = sys.version_info[:2] == (3, 11)
CI = os.getenv("CI", True)


@pytest.mark.skipif(bool(LINUX or not CI), reason="enabled CI=1")
def test_install_widget_download(qtbot: QtBot, tmp_path: Path):
    wdg = InstallWidget()
    qtbot.addWidget(wdg)

    # mock the process of downloading
    with patch.object(_install_widget.QThread, "start"):
        wdg._install_dest = str(tmp_path)
        wdg._on_install_clicked()
        wdg._cmd_thread.stdout_ready.emit("emitting stdout")
        wdg._cmd_thread.process_finished.emit(0)

    qtbot.waitUntil(lambda: wdg._cmd_thread is None)
    assert "emitting stdout" in wdg.feedback_textbox.toPlainText()


@pytest.mark.skipif(bool(LINUX or not CI), reason="enabled CI=1")
def test_install_widget(qtbot: QtBot, tmp_path: Path):
    wdg = InstallWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    dest = tmp_path / "MicroManager-2.0.0-gamma"
    dest.mkdir()
    assert dest.exists()

    with patch.object(_install_widget, "find_micromanager") as mock1:
        with patch.object(_install_widget, "_reveal") as rev_mock:
            mock1.return_value = [str(dest)]
            wdg.table.refresh()

            # test reveal
            wdg.table.selectRow(0)
            qtbot.waitUntil(wdg._act_reveal.isEnabled)
            wdg.table.reveal()
            rev_mock.assert_called_once_with(str(dest))

    with patch.object(_install_widget.QMessageBox, "warning") as mock2:
        mock2.return_value = _install_widget.QMessageBox.StandardButton.Yes
        wdg.table.uninstall()

    assert not dest.exists()


def test_install_row_is_offered_wherever_mmcore_can_install(qtbot: QtBot):
    """Every platform mmcore install supports must be able to install.

    Where no full nightly build is published (Apple Silicon, Linux), it falls
    back to the mm-test-adapters bundle -- which is exactly how those users get
    a working demo configuration, so hiding the row left them with a page that
    could only uninstall.
    """
    assert _install_widget.CAN_INSTALL == (
        platform.system() in ("Darwin", "Linux", "Windows")
    )

    with patch.object(_install_widget, "_available_releases", return_value=[]):
        wdg = InstallWidget()
    qtbot.addWidget(wdg)
    wdg.show()

    assert wdg.install_row.isVisible()
    assert wdg.install_btn.isVisible()


def test_offered_releases_match_the_installer_used(qtbot: QtBot):
    """The release list has to come from the source that will resolve it."""
    nightly = {"20250101": "url", "20240202": "url"}
    adapters = {"20250303": "75.20250303", "20240404": "74.20240404"}

    with (
        patch.object(_install_widget, "available_versions", return_value=nightly),
        patch(
            "pymmcore_plus.install._available_test_adapter_releases",
            return_value=adapters,
        ),
    ):
        with patch.object(_install_widget, "FULL_RELEASES", True):
            assert _install_widget._available_releases() == list(nightly)
        with patch.object(_install_widget, "FULL_RELEASES", False):
            assert _install_widget._available_releases() == ["20250303", "20240404"]


def test_progress_bar_output_is_rendered_not_dumped():
    """`mmcore install` draws a rich progress bar; a QTextEdit is not a terminal.

    Without this the feedback box fills with screenfuls of raw escapes like
    "[38;2;153;48;86m" instead of the line the user was meant to see.
    """
    clean = _install_widget.clean_output
    bar = (
        "\x1b[2K\x1b[38;2;153;48;86m━━━\x1b[0m "
        "\x1b[35m57.4%\x1b[0m • \x1b[32m294 kB\x1b[0m"
    )
    assert clean(bar) == "━━━ 57.4% • 294 kB"
    # cursor control on its own carries nothing to show
    assert clean("\x1b[?25h") == ""
    # a bar redrawing in place: only the final state was ever visible
    assert clean("Downloading...\r 50%\r100%") == "100%"
    assert clean("Installation successful.\n") == "Installation successful."


def test_release_listing_survives_being_offline(qtbot: QtBot):
    """A dead network costs the dated releases, not the whole widget."""
    offline = OSError("no network")
    with (
        patch.object(_install_widget, "available_versions", side_effect=offline),
        patch(
            "pymmcore_plus.install._available_test_adapter_releases",
            side_effect=offline,
        ),
    ):
        assert _install_widget._available_releases() == []

        wdg = InstallWidget()
        qtbot.addWidget(wdg)
        assert [
            wdg.version_combo.itemText(i) for i in range(wdg.version_combo.count())
        ] == ["latest-compatible", "latest"]
