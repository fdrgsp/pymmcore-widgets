from __future__ import annotations

from qtpy.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ._well_detection import DetectionSettings


class WellDetectionSettingsDialog(QDialog):
    """Image-processing settings only; plate pose is calculated from detections."""

    def __init__(
        self,
        settings: DetectionSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Well Detection Settings")
        self._sigma = QDoubleSpinBox()
        self._sigma.setRange(0.1, 10)
        self._sigma.setValue(settings.edge_sigma)
        self._sigma.setSuffix(" px")
        self._threshold = QDoubleSpinBox()
        self._threshold.setRange(0.01, 1)
        self._threshold.setSingleStep(0.05)
        self._threshold.setValue(settings.hough_threshold)
        self._threshold.setToolTip("Lower values propose more candidate wells.")
        self._coverage = QDoubleSpinBox()
        self._coverage.setRange(50, 100)
        self._coverage.setValue(settings.min_coverage * 100)
        self._coverage.setSuffix(" %")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main = QVBoxLayout(self)
        rows = [
            (QLabel("Edge smoothing:", self), self._sigma),
            (QLabel("Detection threshold:", self), self._threshold),
            (QLabel("Minimum edge coverage:", self), self._coverage),
        ]
        label_width = max(label.sizeHint().width() for label, _ in rows)
        for label, spin in rows:
            label.setFixedWidth(label_width)
            label.setBuddy(spin)
            row = QHBoxLayout()
            row.addWidget(label)
            row.addWidget(spin, 1)
            main.addLayout(row)
        main.addWidget(buttons)

    def value(self) -> DetectionSettings:
        """Return the edited image-processing settings."""
        return DetectionSettings(
            self._sigma.value(), self._threshold.value(), self._coverage.value() / 100
        )
