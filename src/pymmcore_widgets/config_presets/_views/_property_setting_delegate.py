from __future__ import annotations

from contextlib import suppress
from enum import Enum, auto
from typing import TYPE_CHECKING

from pymmcore_plus import PropertyType
from qtpy.QtCore import (
    QAbstractItemModel,
    QEvent,
    QModelIndex,
    QRect,
    QSignalBlocker,
    QSize,
    Qt,
    QTimer,
)
from qtpy.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QStyleOptionComboBox,
    QStyleOptionSlider,
    QStyleOptionSpinBox,
    QStyleOptionViewItem,
    QWidget,
)

from pymmcore_widgets._models import DevicePropertySetting
from pymmcore_widgets.device_properties._property_widget import create_property_editor

if TYPE_CHECKING:
    from qtpy.QtGui import QPainter


class ControlType(Enum):
    READ_ONLY = auto()
    CHECKBOX = auto()
    COMBOBOX = auto()
    SLIDER = auto()
    SPINBOX = auto()
    LINE_EDIT = auto()


def _control_type(setting: DevicePropertySetting) -> ControlType:
    """Classify a setting into the control type it should be painted as.

    Must mirror `create_property_editor` so the painted (idle) cell matches
    the widget that appears once editing starts.
    """
    if setting.is_read_only:
        return ControlType.READ_ONLY
    ptype = setting.property_type
    allowed = setting.allowed_values
    if ptype is PropertyType.Integer and set(allowed) == {"0", "1"}:
        return ControlType.CHECKBOX
    if allowed:
        return ControlType.COMBOBOX
    if ptype in (PropertyType.Integer, PropertyType.Float):
        return ControlType.SLIDER if setting.limits else ControlType.SPINBOX
    return ControlType.LINE_EDIT


class PropertySettingDelegate(QStyledItemDelegate):
    """Item delegate that paints native controls and creates real editors on demand."""

    # -- painting ------------------------------------------------------------

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        setting = index.data(Qt.ItemDataRole.UserRole)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        if not style or not isinstance(setting, DevicePropertySetting):
            super().paint(painter, option, index)
            return

        # Draw selection / hover background
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, opt.widget
        )

        value = str(setting.value)
        ctrl = _control_type(setting)
        if ctrl is ControlType.CHECKBOX:
            self._paint_checkbox(painter, opt, value, style)
        elif ctrl is ControlType.COMBOBOX:
            self._paint_combobox(painter, opt, value, style)
        elif ctrl is ControlType.SLIDER:
            self._paint_slider(painter, opt, setting, style)
        elif ctrl is ControlType.SPINBOX:
            self._paint_spinbox(painter, opt, value, style)
        else:
            self._draw_text(painter, opt.rect.adjusted(4, 0, -4, 0), value, opt)

    def _paint_checkbox(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        value: str,
        style: QStyle,
    ) -> None:
        cb = QStyleOptionButton()
        cb.state = option.state | QStyle.StateFlag.State_Enabled
        try:
            checked = bool(int(value))
        except (ValueError, TypeError):
            checked = False
        cb.state |= QStyle.StateFlag.State_On if checked else QStyle.StateFlag.State_Off

        # Centre the indicator inside the cell
        iw = style.pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth, cb, option.widget)
        ih = style.pixelMetric(QStyle.PixelMetric.PM_IndicatorHeight, cb, option.widget)
        x = option.rect.x() + (option.rect.width() - iw) // 2
        y = option.rect.y() + (option.rect.height() - ih) // 2
        cb.rect = QRect(x, y, iw, ih)

        style.drawControl(QStyle.ControlElement.CE_CheckBox, cb, painter, option.widget)

    def _paint_combobox(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        value: str,
        style: QStyle,
    ) -> None:
        cb = QStyleOptionComboBox()
        cb.rect = option.rect
        cb.state = option.state | QStyle.StateFlag.State_Enabled
        cb.currentText = value
        cb.editable = False
        cb.frame = True

        style.drawComplexControl(
            QStyle.ComplexControl.CC_ComboBox, cb, painter, option.widget
        )
        style.drawControl(
            QStyle.ControlElement.CE_ComboBoxLabel, cb, painter, option.widget
        )

    def _paint_spinbox(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        value: str,
        style: QStyle,
    ) -> None:
        sb = QStyleOptionSpinBox()
        sb.rect = option.rect
        sb.state = option.state | QStyle.StateFlag.State_Enabled
        sb.frame = True
        sb.buttonSymbols = QAbstractSpinBox.ButtonSymbols.UpDownArrows
        sb.stepEnabled = (
            QAbstractSpinBox.StepEnabledFlag.StepUpEnabled
            | QAbstractSpinBox.StepEnabledFlag.StepDownEnabled
        )
        style.drawComplexControl(
            QStyle.ComplexControl.CC_SpinBox, sb, painter, option.widget
        )
        text_rect = style.subControlRect(
            QStyle.ComplexControl.CC_SpinBox,
            sb,
            QStyle.SubControl.SC_SpinBoxEditField,
            option.widget,
        )
        self._draw_text(painter, text_rect, value, option)

    def _paint_slider(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        setting: DevicePropertySetting,
        style: QStyle,
    ) -> None:
        try:
            val = float(setting.value)
        except (ValueError, TypeError):
            val = 0.0
        lower, upper = setting.limits or (0.0, 1.0)

        fm = option.widget.fontMetrics() if option.widget else painter.fontMetrics()
        text_width = fm.horizontalAdvance("888888.888")
        spacing = 6
        margin = 4
        text_rect = QRect(
            option.rect.right() - text_width - margin,
            option.rect.y(),
            text_width,
            option.rect.height(),
        )
        slider_rect = QRect(
            option.rect.x() + margin,
            option.rect.y(),
            option.rect.width() - text_width - spacing - 2 * margin,
            option.rect.height(),
        )

        so = QStyleOptionSlider()
        so.rect = slider_rect
        so.state = option.state | QStyle.StateFlag.State_Enabled
        so.orientation = Qt.Orientation.Horizontal
        so.minimum = 0
        so.maximum = 1000
        rng = upper - lower
        frac = (val - lower) / rng if rng else 0.0
        pos = int(max(0.0, min(1.0, frac)) * so.maximum)
        so.sliderPosition = pos
        so.sliderValue = pos
        so.subControls = (
            QStyle.SubControl.SC_SliderGroove | QStyle.SubControl.SC_SliderHandle
        )
        style.drawComplexControl(
            QStyle.ComplexControl.CC_Slider, so, painter, option.widget
        )

        if setting.property_type is PropertyType.Float:
            disp = f"{val:.4f}".rstrip("0").rstrip(".")
        else:
            disp = str(int(val))
        self._draw_text(painter, text_rect, disp, option)

    @staticmethod
    def _draw_text(
        painter: QPainter,
        rect: QRect,
        text: str,
        option: QStyleOptionViewItem,
    ) -> None:
        painter.save()
        painter.setPen(option.palette.text().color())
        painter.drawText(
            rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text
        )
        painter.restore()

    # -- sizing --------------------------------------------------------------

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        hint = super().sizeHint(option, index)
        if isinstance(index.data(Qt.ItemDataRole.UserRole), DevicePropertySetting):
            hint = hint.expandedTo(QSize(hint.width(), 26))
        return hint

    # -- editing -------------------------------------------------------------

    def createEditor(
        self, parent: QWidget | None, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QWidget | None:
        setting = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(setting, DevicePropertySetting):
            return super().createEditor(parent, option, index)  # pragma: no cover

        if _control_type(setting) in (ControlType.READ_ONLY, ControlType.CHECKBOX):
            return None

        widget = create_property_editor(setting)
        widget.setParent(parent)
        widget.setValue(setting.value)
        widget.valueChanged.connect(self._commit_editor)
        widget.setAutoFillBackground(True)
        # Let right-clicks pass through to the table view's contextMenuEvent
        no_ctx = Qt.ContextMenuPolicy.NoContextMenu
        widget.setContextMenuPolicy(no_ctx)
        for child in widget.findChildren(QWidget):
            child.setContextMenuPolicy(no_ctx)

        # Auto-show combo box dropdown so one click opens it
        if isinstance(widget, QComboBox):

            def _show_popup(w: QComboBox = widget) -> None:
                with suppress(Exception):
                    w.showPopup()

            QTimer.singleShot(0, _show_popup)

        return widget

    def _commit_editor(self) -> None:
        """Commit the editor that sent the signal."""
        if (editor := self.sender()) is not None:
            self.commitData.emit(editor)

    def setEditorData(self, editor: QWidget | None, index: QModelIndex) -> None:
        setting = index.data(Qt.ItemDataRole.UserRole)
        if (
            isinstance(setting, DevicePropertySetting)
            and editor
            and hasattr(editor, "setValue")
        ):
            # Block signals so valueChanged -> commitData doesn't fire before
            # the view registers the editor in its internal hash.
            with QSignalBlocker(editor):
                editor.setValue(setting.value)
        else:  # pragma: no cover
            super().setEditorData(editor, index)

    def setModelData(
        self,
        editor: QWidget | None,
        model: QAbstractItemModel | None,
        index: QModelIndex,
    ) -> None:
        if model and editor and hasattr(editor, "value"):
            model.setData(index, editor.value(), Qt.ItemDataRole.EditRole)
        else:  # pragma: no cover
            super().setModelData(editor, model, index)

    def editorEvent(
        self,
        event: QEvent | None,
        model: QAbstractItemModel | None,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> bool:
        """Toggle checkboxes on single click."""
        setting = index.data(Qt.ItemDataRole.UserRole)
        if (
            isinstance(setting, DevicePropertySetting)
            and _control_type(setting) is ControlType.CHECKBOX
            and event
            and event.type() == QEvent.Type.MouseButtonRelease
            and model
        ):
            new_val = "0" if setting.value == "1" else "1"
            model.setData(index, new_val, Qt.ItemDataRole.EditRole)
            return True
        return super().editorEvent(event, model, option, index)  # type: ignore [no-any-return]
