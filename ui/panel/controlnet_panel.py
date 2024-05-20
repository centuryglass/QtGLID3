"""
Panel providing controls for the stable-diffusion ControlNet extension. Only supported by stable_diffusion_controller.
"""
from typing import Optional
from PyQt5.QtWidgets import QVBoxLayout, QHBoxLayout, QCheckBox, QPushButton, QLineEdit, QComboBox, QSizePolicy
from ui.widget.collapsible_box import CollapsibleBox
from ui.widget.label_wrapper import LabelWrapper
from ui.widget.param_slider import ParamSlider
from ui.config_control_setup import connected_checkbox
from data_model.config import Config
from util.validation import assert_type

class ControlnetPanel(CollapsibleBox):
    """ControlnetPanel provides controls for the stable-diffusion ControlNet extension."""


    REUSE_IMAGE_VALUE='SELECTION' #value to signal that the control image is from selection, not a file

    # Default parameters used in ControlNet requests for all modules/models
    DEFAULT_PARAMS=['module', 'model', 'low_vram', 'pixel_perfect', 'image', 'weight', 'guidance_start',
                            'guidance_end']

    def __init__(self,
            config: Config,
            control_types: Optional[dict],
            module_detail: dict,
            model_list: dict,
            title: str = 'ControlNet',
            config_key: str = Config.CONTROLNET_ARGS_0):
        """Initializes the panel based on data from the stable-diffusion-webui.

        Parameters
        ----------
        config : data_model.config.Config
            Shared application configuration object.
        control_types : dict or None
            API data defining available control types. If none, only the module and model dropdowns are used.
        module_detail : dict
            API data defining available ControlNet modules.
        model_list : dict
            API data defining available ControlNet models.
        title : str, default = "ControlNet"
            Title to display at the top of the panel.
        config_key : str, default = Config.CONTROLNET_ARGS_0
            Config key where ControlNet selection will be saved.
        """
        super().__init__(title=title, scrolling=False, start_closed=len(config.get(config_key)) == 0)
        if isinstance(control_types, dict) and len(control_types) == 0:
            control_types = None
        assert_type(model_list, dict)
        if 'model_list' not in model_list:
            raise KeyError(f'Controlnet model list had unexpected structure: {model_list}')

        initial_control_state = config.get(config_key)
        self._config = config
        self._saved_state = initial_control_state

        # Build layout:
        layout = QVBoxLayout()
        self.set_content_layout(layout)

        # Basic checkboxes:
        checkbox_row = QHBoxLayout()
        layout.addLayout(checkbox_row)
        enabled_checkbox = QCheckBox()
        enabled_checkbox.setText('Enable ControlNet')
        checkbox_row.addWidget(enabled_checkbox)

        vram_checkbox = connected_checkbox(self, config, config_key, text='Low VRAM', inner_key='low_vram')
        checkbox_row.addWidget(vram_checkbox)

        px_perfect_checkbox = connected_checkbox(self, config, config_key, text='Pixel Perfect',
                inner_key='pixel_perfect')
        checkbox_row.addWidget(px_perfect_checkbox)


        # Control image row:
        use_selection = bool('image' in initial_control_state
                and initial_control_state['image'] == ControlnetPanel.REUSE_IMAGE_VALUE)
        image_row = QHBoxLayout()
        layout.addLayout(image_row)

        load_image_button = QPushButton()
        load_image_button.setText('Set Control Image')
        load_image_button.setEnabled(not use_selection)
        image_row.addWidget(load_image_button, stretch=10)

        image_path_edit = QLineEdit('' if use_selection  or 'image' not in initial_control_state \
                else initial_control_state['image'])
        image_path_edit.setEnabled(not use_selection)
        image_row.addWidget(image_path_edit, stretch=80)

        reuse_image_checkbox = QCheckBox()
        reuse_image_checkbox.setText('Selection as Control')
        image_row.addWidget(reuse_image_checkbox, stretch=10)
        reuse_image_checkbox.setChecked(use_selection)
        def reuse_image_update(checked: bool):
            value = ControlnetPanel.REUSE_IMAGE_VALUE if checked else image_path_edit.text()
            load_image_button.setEnabled(not checked)
            image_path_edit.setEnabled(not checked)
            if checked:
                image_path_edit.setText('')
            config.set(config_key, value, inner_key='image')
        reuse_image_checkbox.stateChanged.connect(reuse_image_update)

        def image_path_update(text: str):
            if reuse_image_checkbox.checked():
                return
            config.set(config_key, text, inner_key='image')
        image_path_edit.textChanged.connect(image_path_update)

        # Mode-selection row:
        selection_row = QHBoxLayout()
        layout.addLayout(selection_row)
        control_type_combobox = None
        if control_types is not None:
            control_type_combobox = QComboBox(self)
            for control in control_types:
                control_type_combobox.addItem(control)
            control_type_combobox.setCurrentIndex(control_type_combobox.findText('All'))
            selection_row.addWidget(LabelWrapper(control_type_combobox, 'Control Type'))

        module_combobox = QComboBox(self)
        selection_row.addWidget(LabelWrapper(module_combobox, 'Control Module'))

        model_combobox = QComboBox(self)
        selection_row.addWidget(LabelWrapper(model_combobox, 'Control Model'))


        # Dynamic options section:
        options_combobox = CollapsibleBox('Options', start_closed=True)
        options_combobox.set_expanded_size_policy(QSizePolicy.Maximum)
        options_layout = QVBoxLayout()
        options_combobox.set_content_layout(options_layout)
        layout.addWidget(options_combobox)

        #on model change, update config:
        def handle_model_change():
            config.set(config_key, model_combobox.currentText(), inner_key='model')
        model_combobox.currentIndexChanged.connect(handle_model_change)

        def handle_module_change(selection: str):
            details = {}
            if 'module_detail' in module_detail:
                if selection not in module_detail['module_detail']:
                    for option in module_detail['module_list']:
                        if selection.startswith(option):
                            selection = option
                            break
                if selection not in module_detail['module_detail']:
                    print(f'Warning: invalid selection {selection} not found')
                    return
                details = module_detail['module_detail'][selection]
            config.set(config_key, selection, inner_key='module')
            while options_layout.count() > 0:
                row = options_layout.itemAt(0)
                while row.layout().count() > 0:
                    item = row.layout().itemAt(0)
                    row.layout().removeItem(item)
                    if item.widget():
                        widget = item.widget()
                        config.disconnect(widget, config_key)
                        if hasattr(widget, 'disconnect_config'):
                            widget.disconnect_config()
                        else:
                            config.disconnect(widget, config_key)
                        widget.deleteLater()
                options_layout.removeItem(row)
                row.layout().deleteLater()
            current_keys = list(config.get(config_key).keys())
            for param in current_keys:
                if param not in ControlnetPanel.DEFAULT_PARAMS:
                    config.set(config_key, None, inner_key=param)
            if selection != 'none':
                sliders = [
                    {
                        'display': 'Control Weight',
                        'name': 'weight',
                        'value': 1.0,
                        'min': 0.0,
                        'max': 2.0,
                        'step': 0.1
                    },
                    {
                        'display': 'Starting Control Step',
                        'name': 'guidance_start',
                        'value': 0.0,
                        'min': 0.0,
                        'max': 1.0,
                        'step': 0.1
                    },
                    {
                        'display': 'Ending Control Step',
                        'name': 'guidance_end',
                        'value': 1.0,
                        'min': 0.0,
                        'max': 1.0,
                        'step': 0.1
                    },
                ]
                if 'sliders' in details:
                    for slider_params in details['sliders']:
                        if slider_params is None:
                            continue
                        sliders.append(slider_params)
                slider_row = QHBoxLayout()
                for slider_params in sliders:
                    if slider_params is None:
                        continue
                    key = slider_params['name']
                    title = slider_params['display'] if 'display' in slider_params else key
                    value = slider_params['value']
                    min_val = slider_params['min']
                    max_val = slider_params['max']
                    if key == title:
                        if 'Resolution' in key:
                            key = 'processor_res'
                        elif 'threshold_a' not in config.get(config_key):
                            key = 'threshold_a'
                        elif 'threshold_b' not in config.get(config_key):
                            key = 'threshold_b'
                    step = 1 if 'step' not in slider_params else slider_params['step']
                    float_mode = any(x != int(x) for x in [value, min_val, max_val, step])
                    if float_mode:
                        value = float(value)
                        min_val = float(min_val)
                        max_val = float(max_val)
                        step = float(step)
                    else:
                        value = int(value)
                        min_val = int(min_val)
                        max_val = int(max_val)
                        step = int(step)
                    config.set(config_key, value, inner_key=key)
                    slider = ParamSlider(self, title, config, config_key, min_val, max_val, step,
                            inner_key=key)
                    if slider_row.count() > 1:
                        options_layout.addLayout(slider_row)
                        slider_row = QHBoxLayout()
                    slider_row.addWidget(slider)
                if slider_row.count() > 0:
                    options_layout.addLayout(slider_row)
            if options_layout.count() > 0:
                options_combobox.setEnabled(True)
            else:
                options_combobox.set_expanded(False)
                options_combobox.setEnabled(False)
        def module_change_handler():
            handle_module_change(module_combobox.currentText())
        module_combobox.currentIndexChanged.connect(module_change_handler)

        # Setup control types, update other boxes on change:
        def load_control_type(typename: str):
            model_combobox.currentIndexChanged.disconnect(handle_model_change)
            while model_combobox.count() > 0:
                model_combobox.removeItem(0)
            default_model = 'none'
            if control_types is not None:
                for model in control_types[typename]['model_list']:
                    model_combobox.addItem(model)
                default_model = control_types[typename]['default_model']
            else:
                for model in model_list['model_list']:
                    model_combobox.addItem(model)
            model_combobox.currentIndexChanged.connect(handle_model_change)
            if default_model != 'none':
                model_combobox.setCurrentIndex(model_combobox.findText(default_model))
            else:
                model_combobox.setCurrentIndex(0)

            module_combobox.currentIndexChanged.disconnect(module_change_handler)
            default_module = 'none'
            while module_combobox.count() > 0:
                module_combobox.removeItem(0)
            if control_types is not None:
                for module in control_types[typename]['module_list']:
                    module_combobox.addItem(module)
                default_module = control_types[typename]['default_option']
            else:
                for module in module_detail['module_list']:
                    module_combobox.addItem(module)
            module_combobox.currentIndexChanged.connect(module_change_handler)
            if default_module != 'none':
                module_combobox.setCurrentIndex(module_combobox.findText(default_module))
            else:
                module_combobox.setCurrentIndex(0)
        load_control_type('All')
        if control_type_combobox is not None:
            control_type_combobox.currentIndexChanged.connect(
                    lambda: load_control_type(control_type_combobox.currentText()))

        # Restore previous state on start:
        if 'module' in initial_control_state:
            module = module_combobox.findText(initial_control_state['module'])
            if module is not None:
                module_combobox.setCurrentIndex(module)
        if 'model' in initial_control_state:
            model = model_combobox.findText(initial_control_state['model'])
            if model is not None:
                model_combobox.setCurrentIndex(model)

        # Setup "Enabled" control:
        def set_enabled(checked: bool):
            if enabled_checkbox.isChecked() != checked:
                enabled_checkbox.setChecked(checked)
            for widget in [control_type_combobox, module_combobox, model_combobox]:
                if widget is not None:
                    widget.setEnabled(checked)
            options_combobox.setEnabled(checked and options_layout.count() > 0)
            if checked:
                config.set(config_key, self._saved_state)
            else:
                self._saved_state = config.get(config_key)
                config.set(config_key, {})
        set_enabled('model' in initial_control_state)
        enabled_checkbox.stateChanged.connect(set_enabled)
        self.show_button_bar(True)
