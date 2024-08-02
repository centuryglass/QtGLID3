"""Fill areas within an image."""
from typing import Optional

from PyQt6.QtCore import Qt, QPoint, QRect
from PyQt6.QtGui import QIcon, QCursor, QMouseEvent, QKeySequence, QColor, QPainter, QTransform
from PyQt6.QtWidgets import QWidget, QFormLayout, QApplication

from src.config.application_config import AppConfig
from src.config.cache import Cache
from src.config.key_config import KeyConfig
from src.image.layers.image_stack import ImageStack
from src.image.layers.transform_layer import TransformLayer
from src.tools.base_tool import BaseTool
from src.ui.input_fields.check_box import CheckBox
from src.util.image_utils import flood_fill
from src.util.shared_constants import PROJECT_DIR


# The `QCoreApplication.translate` context for strings in this file
TR_ID = 'tools.selection_fill_tool'


def _tr(*args):
    """Helper to make `QCoreApplication.translate` more concise."""
    return QApplication.translate(TR_ID, *args)


RESOURCES_FILL_ICON = f'{PROJECT_DIR}/resources/icons/selection_fill_icon.svg'
RESOURCES_FILL_CURSOR = f'{PROJECT_DIR}/resources/cursors/selection_fill_cursor.svg'
CURSOR_SIZE = 25

SELECTION_FILL_LABEL = _tr('Selection fill')
SELECTION_FILL_TOOLTIP = _tr('Select areas with solid colors')
SELECTION_FILL_CONTROL_HINT = _tr('LMB:select - RMB:deselect - ')
FILL_BY_SELECTION = _tr('Fill selection holes')
FILL_BY_SELECTION_TOOLTIP = _tr('Fill based on selection shape only.')


class SelectionFillTool(BaseTool):
    """Lets the user select image areas with solid colors."""

    def __init__(self, image_stack: ImageStack) -> None:
        super().__init__()
        cache = Cache()
        self._image_stack = image_stack
        self._control_panel: Optional[QWidget] = None
        self._icon = QIcon(RESOURCES_FILL_ICON)
        self._color = QColor()

        def _update_color(color_str: str) -> None:
            if color_str == self._color.name():
                return
            self._color = QColor(color_str)
            self._color.setAlphaF(1.0)
        _update_color(AppConfig().get(AppConfig.SELECTION_COLOR))
        AppConfig().connect(self, AppConfig.SELECTION_COLOR, _update_color)

        self._threshold = cache.get(Cache.FILL_THRESHOLD)
        self._sample_merged = cache.get(Cache.SAMPLE_MERGED)
        self._fill_by_selection_checkbox = CheckBox()
        self._fill_by_selection_checkbox.setText(FILL_BY_SELECTION)
        self._fill_by_selection_checkbox.setToolTip(FILL_BY_SELECTION_TOOLTIP)

        cursor_icon = QIcon(RESOURCES_FILL_CURSOR)
        self.cursor = QCursor(cursor_icon.pixmap(CURSOR_SIZE, CURSOR_SIZE))
        cache.connect(self, Cache.FILL_THRESHOLD, self._update_threshold)
        cache.connect(self, Cache.SAMPLE_MERGED, self._update_sample_merged)

    def get_hotkey(self) -> QKeySequence:
        """Returns the hotkey(s) that should activate this tool."""
        return KeyConfig().get_keycodes(KeyConfig.SELECTION_FILL_TOOL_KEY)

    def get_icon(self) -> QIcon:
        """Returns an icon used to represent this tool."""
        return self._icon

    def get_label_text(self) -> str:
        """Returns label text used to represent this tool."""
        return SELECTION_FILL_LABEL

    def get_tooltip_text(self) -> str:
        """Returns tooltip text used to describe this tool."""
        return SELECTION_FILL_TOOLTIP

    def get_input_hint(self) -> str:
        """Return text describing different input functionality."""
        return f'{SELECTION_FILL_CONTROL_HINT}{super().get_input_hint()}'

    def get_control_panel(self) -> Optional[QWidget]:
        """Returns a panel providing controls for customizing tool behavior, or None if no such panel is needed."""
        if self._control_panel is not None:
            return self._control_panel
        cache = Cache()
        self._control_panel = QWidget()
        layout = QFormLayout(self._control_panel)
        threshold_slider = cache.get_control_widget(Cache.FILL_THRESHOLD)
        layout.addRow(cache.get_label(Cache.FILL_THRESHOLD), threshold_slider)
        sample_merged_checkbox = cache.get_control_widget(Cache.SAMPLE_MERGED)
        layout.addRow(sample_merged_checkbox)
        self._fill_by_selection_checkbox.valueChanged.connect(lambda checked: sample_merged_checkbox.setEnabled(
            not checked))
        layout.addRow(self._fill_by_selection_checkbox)
        return self._control_panel

    def mouse_click(self, event: Optional[QMouseEvent], image_coordinates: QPoint) -> bool:
        """Fill the region under the mouse on left-click, clear on right-click."""
        assert event is not None
        if QApplication.keyboardModifiers() != Qt.KeyboardModifier.NoModifier:
            return True
        if event.buttons() == Qt.MouseButton.LeftButton or event.buttons() == Qt.MouseButton.RightButton:
            clear_mode = event.buttons() == Qt.MouseButton.RightButton
            layer = self._image_stack.active_layer
            mask_pos = self._image_stack.selection_layer.position
            merged_pos = self._image_stack.merged_layer_bounds.topLeft()
            if isinstance(layer, TransformLayer):
                layer_pos = layer.map_to_image(QPoint())
            else:
                layer_pos = QPoint()
            if self._fill_by_selection_checkbox.isChecked():
                image = self._image_stack.selection_layer.image
                paint_transform = QTransform()
                sample_point = image_coordinates + mask_pos
            elif self._sample_merged:
                image = self._image_stack.qimage(crop_to_image=False)
                sample_point = image_coordinates - merged_pos
                offset = -mask_pos + merged_pos
                paint_transform = QTransform.fromTranslate(offset.x(), offset.y())
            else:
                image = layer.image
                if isinstance(layer, TransformLayer):
                    sample_point = layer.map_from_image(image_coordinates)
                    paint_transform = layer.transform
                else:
                    sample_point = image_coordinates
                    paint_transform = QTransform()
                layer_pos_in_mask = layer_pos - mask_pos
                transformed_origin = paint_transform.map(QPoint(0, 0))
                img_offset = layer_pos_in_mask - transformed_origin
                paint_transform *= QTransform.fromTranslate(img_offset.x(), img_offset.y())
            if not QRect(QPoint(), image.size()).contains(sample_point):
                return True
            mask = flood_fill(image, sample_point, self._color, self._threshold, False)
            selection_image = self._image_stack.selection_layer.image
            assert mask is not None
            painter = QPainter(selection_image)
            if clear_mode:
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
            painter.setTransform(paint_transform)
            painter.drawImage(QRect(QPoint(), mask.size()), mask)
            painter.end()
            self._image_stack.selection_layer.image = selection_image
            return True
        return False

    def _update_threshold(self, threshold: float) -> None:
        self._threshold = threshold

    def _update_sample_merged(self, sample_merged: bool) -> None:
        self._sample_merged = sample_merged
