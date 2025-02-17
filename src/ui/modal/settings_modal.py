"""
Popup modal providing a dynamic settings interface, to be populated by the controller. Currently only used with
stable_diffusion_controller.
"""
import logging
from typing import Any, Optional

from PySide6.QtCore import Signal, QSize, Qt
from PySide6.QtGui import QIcon, QFont
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QWidget, QTabWidget, QFormLayout, \
    QScrollArea, QApplication, QLabel, QPlainTextEdit

from src.config.config import Config
from src.ui.input_fields.big_int_spinbox import BigIntSpinbox
from src.ui.input_fields.check_box import CheckBox
from src.ui.input_fields.combo_box import ComboBox
from src.ui.input_fields.dual_toggle import DualToggle
from src.ui.input_fields.line_edit import LineEdit
from src.ui.input_fields.plain_text_edit import PlainTextEdit
from src.ui.input_fields.size_field import SizeField
from src.ui.input_fields.slider_spinbox import IntSliderSpinbox
from src.ui.layout.bordered_widget import BorderedWidget
from src.util.layout import clear_layout
from src.util.parameter import DynamicFieldWidget
from src.util.shared_constants import APP_ICON_PATH

# The `QCoreApplication.translate` context for strings in this file
TR_ID = 'ui.modal.settings_modal'


def _tr(*args):
    """Helper to make `QCoreApplication.translate` more concise."""
    return QApplication.translate(TR_ID, *args)


WINDOW_TITLE = _tr('Settings')
CONFIG_PATH_LABEL = _tr('Settings file locations:')
CONFIG_PATH_TOOLTIP = _tr('IntraPaint saves its settings to these files. This section is only for reference, their'
                          ' locations cannot be changed here.')
CANCEL_BUTTON_LABEL = _tr('Cancel')
SAVE_BUTTON_LABEL = _tr('Save')
FILE_CATEGORY = QApplication.translate('config.application_config', 'Files')

logger = logging.getLogger(__name__)


class SettingsModal(QDialog):
    """Manage remote settings."""

    changes_saved = Signal(dict)

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowIcon(QIcon(APP_ICON_PATH))
        self.setWindowTitle(WINDOW_TITLE)

        self._tabs: dict[str, QWidget] = {}
        self._tab_layouts: dict[str, QFormLayout] = {}
        self._inputs: dict[str, DynamicFieldWidget] = {}
        self._changes: dict[str, Any] = {}

        self._config_paths: set[str] = set()
        self._config_path_widget = QPlainTextEdit(parent=self)
        self._config_path_widget.setReadOnly(True)
        self._config_path_widget.setHidden(True)
        self._config_path_widget.setToolTip(CONFIG_PATH_TOOLTIP)

        layout = QVBoxLayout()
        self.setLayout(layout)

        self._tab_widget = QTabWidget(self)
        self._tab_widget.setDocumentMode(True)
        layout.addWidget(self._tab_widget, stretch=20)

        bottom_panel = BorderedWidget(self)
        bottom_panel_layout = QHBoxLayout(bottom_panel)

        save_button = QPushButton()
        save_button.setText(SAVE_BUTTON_LABEL)

        def on_save():
            """Apply changes and close when the save button is clicked."""
            self.changes_saved.emit(self._changes)
            self.hide()

        save_button.clicked.connect(on_save)
        bottom_panel_layout.addWidget(save_button, stretch=1)

        cancel_button = QPushButton()
        cancel_button.setText(CANCEL_BUTTON_LABEL)
        cancel_button.clicked.connect(self.hide)
        bottom_panel_layout.addWidget(cancel_button, stretch=1)

        layout.addWidget(bottom_panel, stretch=1)

    def load_from_config(self, config: Config, categories: Optional[list[str]] = None) -> None:
        """Load settings from a Config object, or from a subset of Config object categories."""
        if categories is None:
            categories = config.get_categories()
        for category in categories:
            subcategories = config.get_subcategories(category)
            all_keys = config.get_category_keys(category)
            subcategory_key_map = {'': all_keys}
            for subcategory in subcategories:
                subcategory_key_map[subcategory] = []
                for key in config.get_category_keys(category, subcategory):
                    subcategory_key_map[''].remove(key)
                    subcategory_key_map[subcategory].append(key)
            for subcategory in ['', *subcategories]:
                key_set = subcategory_key_map[subcategory]
                if len(key_set) == 0:
                    continue
                self._add_subcategory_header(category, subcategory if subcategory != '' else category)
                for key in key_set:
                    if key in self._inputs:
                        continue
                    label = config.get_label(key)
                    try:
                        control_widget = config.get_control_widget(key, False)
                    except RuntimeError:
                        continue
                    assert hasattr(control_widget, 'valueChanged')
                    if isinstance(control_widget, CheckBox):
                        control_widget.setText('')  # External labels look better than checkbox labels in this layout.

                    def _add_change(new_value: Any, name=key):
                        self._add_change(name, new_value)

                    control_widget.valueChanged.connect(_add_change)
                    self._add_setting(key, category, control_widget, label)
        config_path = config.json_path
        if config_path is not None:
            if config_path not in self._config_paths:
                self._config_paths.add(config_path)
            if FILE_CATEGORY not in self._tab_layouts:
                self._add_tab_if_missing(FILE_CATEGORY)
            if self._config_path_widget.isHidden():
                file_tab_layout = self._tab_layouts[FILE_CATEGORY]
                self._add_subcategory_header(FILE_CATEGORY, CONFIG_PATH_LABEL)
                file_tab_layout.addRow(self._config_path_widget)
                self._config_path_widget.setHidden(False)
            self._config_path_widget.setPlainText('\n'.join(list(self._config_paths)))

    def add_custom_control(self, control_widget: DynamicFieldWidget, config: Config, key: str) -> None:
        """Adds a non-standard control to the grid."""
        assert hasattr(control_widget, 'valueChanged')
        category = config.get_category(key)
        if key in self._inputs:
            self.remove_entry(config, key, category)
        self._add_tab_if_missing(category)
        control_widget.setValue(config.get(key))
        control_widget.setToolTip(config.get_tooltip(key))

        def _add_change(new_value: Any, name=key):
            self._add_change(name, new_value)
        control_widget.valueChanged.connect(_add_change)
        self._add_setting(key, category, control_widget, config.get_label(key))

    def remove_entry(self, config: Config, key: str, category: Optional[str] = None) -> None:
        """Remove a single item from the modal"""
        if category is None:
            category = config.get_category(key)
        if category not in self._tabs:
            return
        input_widget: Optional[DynamicFieldWidget] = None
        if key in self._inputs:
            input_widget = self._inputs[key]
            del self._inputs[key]
        if key in self._changes:
            del self._changes[key]
        if input_widget is not None:
            layout = self._tab_layouts[category]
            layout.takeRow(input_widget)
            input_widget.deleteLater()

    def remove_category(self, config: Config, category: str) -> None:
        """Remove a category from the modal"""
        if category not in self._tabs:
            return
        for key in config.get_category_keys(category):
            self.remove_entry(config, key, category)
        assert category in self._tab_layouts
        layout = self._tab_layouts[category]
        del self._tab_layouts[category]
        clear_layout(layout)
        layout.deleteLater()
        tab = self._tabs[category]
        index = self._tab_widget.indexOf(tab)
        self._tab_widget.removeTab(index)
        del self._tabs[category]
        tab.deleteLater()

    def show_modal(self):
        """Shows the settings modal."""
        self.exec()

    def set_tooltip(self, setting_name: str, tooltip: str):
        """Sets tooltip text for a setting.

        Parameters
        ----------
        setting_name : str
            Name of the setting being explained.
        tooltip : str
            Text explaining the updated setting, to be shown when the mouse hovers over the setting's control widget.
        """
        if setting_name not in self._inputs:
            raise ValueError(f'{setting_name} not defined')
        self._inputs[setting_name].setToolTip(tooltip)

    def update_settings(self, settings: dict):
        """Sets all setting control widgets to match current settings values.

        Parameters
        ----------
        settings : dict
            Current state of all settings.
        """
        for key in settings:
            if key not in self._inputs:
                continue
            widget = self._inputs[key]
            assert hasattr(widget, 'setValue')
            new_value = settings[key]
            if isinstance(widget, SizeField):
                assert isinstance(new_value, QSize)
                widget.setValue(new_value)
            else:
                assert not isinstance(new_value, QSize)
                try:
                    if not isinstance(widget, (LineEdit, PlainTextEdit, DualToggle, ComboBox, BigIntSpinbox,
                                               IntSliderSpinbox, CheckBox)):
                        widget.setValue(new_value)  # Custom widget used, let it figure out typing on its own
                    elif isinstance(widget, (LineEdit, PlainTextEdit, DualToggle, ComboBox)):
                        widget.setValue(str(new_value))
                    else:
                        if new_value is None:
                            new_value = 0
                        assert isinstance(new_value, (int, float, bool, str)), (f'Key "{key}": unexpected type '
                                                                                f'{type(new_value)}')
                        if isinstance(widget, (BigIntSpinbox, IntSliderSpinbox)):
                            widget.setValue(int(new_value))
                        elif isinstance(widget, CheckBox):
                            widget.setValue(bool(new_value))
                        else:
                            widget.setValue(float(new_value))
                except ValueError as err:
                    logger.error(f'Failed to initialize "{key}"="{new_value}: {err}')
            if key in self._changes:
                del self._changes[key]

    def _add_change(self, setting: str, new_value: Any):
        self._changes[setting] = new_value

    def _add_tab_if_missing(self, tab_name: str):
        if tab_name not in self._tabs:
            tab_body = QWidget(self)
            tab_layout = QFormLayout(tab_body)
            tab_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            tab = QScrollArea(self)
            tab.setWidgetResizable(True)
            tab.setWidget(tab_body)
            self._tabs[tab_name] = tab
            self._tab_layouts[tab_name] = tab_layout
            self._tab_widget.addTab(tab, tab_name)

    def _add_subcategory_header(self, category: str, subcategory: str) -> None:
        self._add_tab_if_missing(category)
        label = QLabel(subcategory)
        label_font = QFont()
        label_font.setBold(True)
        if label_font.pixelSize() > 0:
            label_font.setPixelSize(label_font.pixelSize() + 2)
        label_font.setUnderline(True)
        label.setFont(label_font)
        label.setContentsMargins(2, 10, 2, 2)
        self._tab_layouts[category].addRow(label)

    def _add_setting(self,
                     setting_name: str,
                     panel_name: str,
                     widget: DynamicFieldWidget,
                     label_text: str):
        self._add_tab_if_missing(panel_name)
        self._tab_layouts[panel_name].addRow(label_text, widget)
        self._inputs[setting_name] = widget
