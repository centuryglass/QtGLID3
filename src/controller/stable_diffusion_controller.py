"""
Provides image editing functionality through the A1111/stable-diffusion-webui REST API.
"""
import sys
import threading
import re
import os
import datetime
from argparse import Namespace
from typing import Optional, Callable, Any

import requests
from PIL import Image
from PyQt5.QtCore import QObject, pyqtSignal, QSize
from PyQt5.QtWidgets import QInputDialog

from src.image.layer_stack import LayerStack
from src.config.application_config import AppConfig
from src.ui.modal.settings_modal import SettingsModal
from src.ui.window.stable_diffusion_main_window import StableDiffusionMainWindow
from src.ui.modal.modal_utils import show_error_dialog
from src.ui.util.screen_size import screen_size
from src.controller.base_controller import BaseInpaintController
from src.api.a1111_webservice import A1111Webservice

AUTH_ERROR_DETAIL_KEY = 'detail'
AUTH_ERROR_MESSAGE = 'Not authenticated'
INTERROGATE_ERROR_MESSAGE_NO_IMAGE = 'Open or create an image first.'
INTERROGATE_ERROR_MESSAGE_EXISTING_OPERATION = 'Existing operation currently in progress'
INTERROGATE_ERROR_TITLE = 'Interrogate failure'
INTERROGATE_LOADING_TEXT = 'Running CLIP interrogate'
URL_REQUEST_TITLE = 'Inpainting UI'
URL_REQUEST_MESSAGE = 'Enter server URL:'
URL_REQUEST_RETRY_MESSAGE = 'Server connection failed, enter a new URL or click "OK" to retry'
CONTROLNET_MODEL_LIST_KEY = 'model_list'
UPSCALE_ERROR_TITLE = 'Upscale failure'
PROGRESS_KEY_CURRENT_IMAGE = 'current_image'
PROGRESS_KEY_FRACTION = "progress"
PROGRESS_KEY_ETA_RELATIVE = 'eta_relative'

MODE_INPAINT = 'Inpaint'
MODE_TXT2IMG = 'Text to Image'

MAX_ERROR_COUNT = 10
MIN_RETRY_US = 300000
MAX_RETRY_US = 60000000


class StableDiffusionController(BaseInpaintController):
    """StableDiffusionController using the A1111/stable-diffusion-webui REST API to handle image operations. """

    def __init__(self, args: Namespace) -> None:
        """Starts the application and creates the main window on init.

        Parameters
        ----------
        args : Namespace
            Command-line arguments, as generated by the argparse module
        """
        self._server_url = args.server_url
        super().__init__(args)
        self._webservice = A1111Webservice(args.server_url)
        self._window = None

        # Login automatically if username/password are defined as env variables.
        # Obviously this isn't terribly secure, but A1111 auth security is already pretty minimal, and I'm just using
        # this for testing.
        if 'SD_UNAME' in os.environ and 'SD_PASS' in os.environ:
            self._webservice.login(os.environ['SD_UNAME'], os.environ['SD_PASS'])
            self._webservice.set_auth((os.environ['SD_UNAME'], os.environ['SD_PASS']))

        def update_mask_state(new_edit_mode) -> None:
            """Configure mask to only be available when inpainting appropriate modes."""
            self._mask_canvas.enabled = new_edit_mode == 'Inpaint'

        self._config.connect(self._mask_canvas, AppConfig.EDIT_MODE, update_mask_state)

        def update_sketch_state(new_edit_mode) -> None:
            """Disable sketch canvas while in text to image mode."""
            self._sketch_canvas.enabled = new_edit_mode != 'Text to Image'

        self._config.connect(self._sketch_canvas, AppConfig.EDIT_MODE, update_sketch_state)
        edit_mode = self._config.get(AppConfig.EDIT_MODE)
        update_mask_state(edit_mode)
        update_sketch_state(edit_mode)

    def init_settings(self, settings_modal: SettingsModal) -> bool:
        """Adds relevant stable-diffusion-webui settings to a ui.modal SettingsModal.  """
        if not isinstance(self._webservice, A1111Webservice):
            print('Disabling remote settings: only supported with the A1111 API')
            return False
        settings = self._webservice.get_config()

        # Model settings:
        models = list(map(lambda m: m['title'], self._webservice.get_models()))
        settings_modal.add_combobox_setting('sd_model_checkpoint',
                                            'Models',
                                            settings['sd_model_checkpoint'],
                                            models,
                                            'Stable-Diffusion Model:')
        settings_modal.add_checkbox_setting('sd_checkpoints_keep_in_cpu',
                                            'Models',
                                            settings['sd_checkpoints_keep_in_cpu'],
                                            'Only keep one model on GPU/TPU')
        settings_modal.set_tooltip('sd_checkpoints_keep_in_cpu',
                                   'If selected, checkpoints after the first are cached in RAM instead.')
        settings_modal.add_spinbox_setting('sd_checkpoints_limit',
                                           'Models',
                                           int(settings['sd_checkpoints_limit']),
                                           1,
                                           10,
                                           'Max checkpoints loaded:')
        vae_options = list(map(lambda v: v['model_name'], self._webservice.get_vae()))
        vae_options.insert(0, 'Automatic')
        vae_options.insert(0, 'None')
        settings_modal.add_combobox_setting('sd_vae',
                                            'Models',
                                            settings['sd_vae'],
                                            vae_options,
                                            'Stable-Diffusion VAE:')
        settings_modal.set_tooltip('sd_vae',
                                   'Automatic: use VAE with same name as model\nNone: use embedded VAE\n'
                                   + re.sub(r'<.*?>', '', settings['sd_vae_explanation']))
        settings_modal.add_spinbox_setting('sd_vae_checkpoint_cache',
                                           'Models',
                                           int(settings['sd_vae_checkpoint_cache']),
                                           1,
                                           10,
                                           'VAE models cached:')
        settings_modal.add_spinbox_setting('CLIP_stop_at_last_layers',
                                           'Models',
                                           int(settings['CLIP_stop_at_last_layers']),
                                           1,
                                           50,
                                           'CLIP skip:')

        # Upscaling:
        settings_modal.add_spinbox_setting('ESRGAN_tile',
                                           'Upscalers',
                                           int(settings['ESRGAN_tile']),
                                           8, 9999, 'ESRGAN tile size')
        settings_modal.add_spinbox_setting('ESRGAN_tile_overlap',
                                           'Upscalers',
                                           int(settings['ESRGAN_tile_overlap']),
                                           8,
                                           9999,
                                           'ESRGAN tile overlap')
        return True

    def refresh_settings(self, settings_modal: SettingsModal) -> None:
        """Loads current settings from the webui and applies them to the SettingsModal."""
        settings = self._webservice.get_config()
        settings_modal.update_settings(settings)

    def update_settings(self, changed_settings: dict[str, Any]) -> None:
        """Applies changed settings from a SettingsModal to the stable-diffusion-webui."""
        for key in changed_settings:
            print(f'Setting {key} to {changed_settings[key]}')
        self._webservice.set_config(changed_settings)

    @staticmethod
    def health_check(url: Optional[str] = None, webservice: Optional[A1111Webservice] = None) -> bool:
        """Static method to check if the stable-diffusion-webui API is available.

        Parameters
        ----------
        url : str
            URL to check for the stable-diffusion-webui API.
        webservice : A1111Webservice, optional
            If provided, the url param will be ignored and this object will be used to check the connection.
        Returns
        -------
        bool
            Whether the API is available through the provided URL or webservice object.
        """
        try:
            if webservice is None:
                res = requests.get(url, timeout=20)
            else:
                res = webservice.login_check()
            if res.status_code == 200 or (res.status_code == 401
                                          and res.json()[AUTH_ERROR_DETAIL_KEY] == AUTH_ERROR_MESSAGE):
                return True
            raise RuntimeError(f'{res.status_code} : {res.text}')
        except RuntimeError as err:
            print(f'error checking login: {err}')
            return False

    def interrogate(self) -> None:
        """ Calls the "interrogate" endpoint to automatically generate image prompts.

        Sends the edited image selection content to the stable-diffusion-webui API, where an image captioning model
        automatically generates an appropriate prompt. Once returned, that prompt is copied to the appropriate field
        in the UI. Displays an error dialog instead if no image is loaded or another API operation is in-progress.
        """
        if not self._layer_stack.has_image:
            show_error_dialog(self._window, INTERROGATE_ERROR_TITLE, INTERROGATE_ERROR_MESSAGE_NO_IMAGE)
            return
        if self._thread is not None:
            show_error_dialog(self._window, INTERROGATE_ERROR_TITLE, INTERROGATE_ERROR_MESSAGE_EXISTING_OPERATION)
            return

        class InterrogateWorker(QObject):
            """Manage interrogate requests in a child thread."""
            finished = pyqtSignal()
            prompt_ready = pyqtSignal(str)
            error_signal = pyqtSignal(Exception)

            def __init__(self, config, layer_stack, webservice):
                super().__init__()
                self._config = config
                self._layer_stack = layer_stack
                self._webservice = webservice

            def run(self):
                """Run interrogation in the child thread, emit a signal and exit when finished."""
                try:
                    image = self._layer_stack.pil_image_selection_content()
                    self.prompt_ready.emit(self._webservice.interrogate(self._config, image))
                except RuntimeError as err:
                    print(f'err:{err}')
                    self.error_signal.emit(err)
                self.finished.emit()

        worker = InterrogateWorker(self._config, self._layer_stack, self._webservice)

        def set_prompt(prompt_text: str) -> None:
            """Update the image prompt in config with the interrogate results."""
            print(f'Set prompt to {prompt_text}')
            self._config.set(AppConfig.PROMPT, prompt_text)

        worker.prompt_ready.connect(set_prompt)

        def handle_error(err: BaseException) -> None:
            """Show an error popup if interrogate fails."""
            self._window.set_is_loading(False)
            show_error_dialog(self._window, INTERROGATE_ERROR_TITLE, err)

        worker.error_signal.connect(handle_error)
        self._start_thread(worker, loading_text=INTERROGATE_LOADING_TEXT)

    def window_init(self) -> None:
        """Creates and shows the main editor window."""

        # Make sure a valid connection exists:
        def prompt_for_url(prompt_text: str) -> None:
            """Open a dialog box to get the server URL from the user."""
            new_url, url_entered = QInputDialog.getText(self._window, URL_REQUEST_TITLE, prompt_text)
            if not url_entered:  # User clicked 'Cancel'
                sys.exit()
            if new_url != '':
                self._server_url = new_url

        # Get URL if one was not provided on the command line:
        while self._server_url == '':
            prompt_for_url(URL_REQUEST_MESSAGE)

        # Check connection:
        while not StableDiffusionController.health_check(webservice=self._webservice):
            prompt_for_url(URL_REQUEST_RETRY_MESSAGE)

        try:
            self._config.set(AppConfig.CONTROLNET_VERSION, float(self._webservice.get_controlnet_version()))
        except RuntimeError:
            # The webui fork at lllyasviel/stable-diffusion-webui-forge is mostly compatible with the A1111 API, but
            # it doesn't have the ControlNet version endpoint. Before assuming ControlNet isn't installed, check if
            # the ControlNet model list endpoint returns anything:
            try:
                model_list = self._webservice.get_controlnet_models()
                if model_list is not None and CONTROLNET_MODEL_LIST_KEY in model_list and len(
                        model_list[CONTROLNET_MODEL_LIST_KEY]) > 0:
                    self._config.set(AppConfig.CONTROLNET_VERSION, 1.0)
                else:
                    self._config.set(AppConfig.CONTROLNET_VERSION, -1.0)
            except RuntimeError as err:
                print(f'Loading controlnet config failed: {err}')
                self._config.set(AppConfig.CONTROLNET_VERSION, -1.0)

        option_loading_params = [
            [AppConfig.STYLES, self._webservice.get_styles],
            [AppConfig.SAMPLING_METHOD, self._webservice.get_samplers],
            [AppConfig.UPSCALE_METHOD, self._webservice.get_upscalers]
        ]

        # load various option lists:
        for config_key, loading_fn in option_loading_params:
            try:
                options = loading_fn()
                if options is not None and len(options) > 0:
                    self._config.update_options(config_key, options)
            except (KeyError, RuntimeError) as err:
                print(f'error loading {config_key} from {self._server_url}: {err}')

        data_params = [
            [AppConfig.CONTROLNET_CONTROL_TYPES, self._webservice.get_controlnet_control_types],
            [AppConfig.CONTROLNET_MODULES, self._webservice.get_controlnet_modules],
            [AppConfig.CONTROLNET_MODELS, self._webservice.get_controlnet_models],
            [AppConfig.LORA_MODELS, self._webservice.get_loras]
        ]
        for config_key, loading_fn in data_params:
            try:
                value = loading_fn()
                if value is not None and len(value) > 0:
                    self._config.set(config_key, value)
            except (KeyError, RuntimeError) as err:
                print(f'error loading {config_key} from {self._server_url}: {err}')

        # initialize remote options modal:
        # Handle final window init now that data is loaded from the API:
        self._window = StableDiffusionMainWindow(self._config, self._layer_stack, self._mask_canvas,
                                                 self._sketch_canvas, self)
        size = screen_size(self._window)
        self._window.setGeometry(0, 0, size.width(), size.height())
        self.fix_styles()
        if self._init_image is not None:
            print('loading init image:')
            self.load_image(self._init_image)
        self._window.show()

    def _scale(self, new_size: QSize) -> None:
        """Provide extra upscaling modes using stable-diffusion-webui."""
        width = self._layer_stack.width
        height = self._layer_stack.height
        # If downscaling, use base implementation:
        if new_size.width() <= width and new_size.height() <= height:
            super()._scale(new_size)
            return

        # If upscaling, use stable-diffusion-webui upscale api:
        class UpscaleWorker(QObject):
            """Manage interrogate requests in a child thread."""
            finished = pyqtSignal()
            image_ready = pyqtSignal(Image.Image)
            status_signal = pyqtSignal(dict)
            error_signal = pyqtSignal(Exception)

            def __init__(self, config: AppConfig, layer_stack: LayerStack, webservice: A1111Webservice) -> None:
                super().__init__()
                self._config = config
                self._layer_stack = layer_stack
                self._webservice = webservice

            def run(self):
                """Handle the upscaling request, then emit a signal and exit when finished."""
                try:
                    images, info = self._webservice.upscale(self._layer_stack.pil_image(),
                                                            new_size.width(),
                                                            new_size.height(),
                                                            self._config)
                    if info is not None:
                        print(f'Upscaling result info: {info}')
                    self.image_ready.emit(images[-1])
                except IOError as err:
                    self.error_signal.emit(err)
                self.finished.emit()

        worker = UpscaleWorker(self._config, self._layer_stack, self._webservice)

        def handle_error(err: IOError) -> None:
            """Show an error dialog if upscaling fails."""
            show_error_dialog(self._window, UPSCALE_ERROR_TITLE, err)

        worker.error_signal.connect(handle_error)

        def apply_upscaled(img: Image.Image) -> None:
            """Copy the upscaled image into the layer stack."""
            self._layer_stack.set_image(img)

        worker.image_ready.connect(apply_upscaled)
        self._start_thread(worker)

    def _inpaint(self,
                 selection: Image.Image,
                 mask: Image.Image,
                 save_image: Callable[[Image.Image, int], None],
                 status_signal: pyqtSignal) -> None:
        """Handle image editing operations using stable-diffusion-webui.

        Parameters
        ----------
        selection : PIL Image, optional
            Image selection to edit
        mask : PIL Image, optional
            Mask marking edited image region.
        save_image : function (PIL Image, int)
            Function used to return each image response and its index.
        status_signal : pyqtSignal
            Signal to emit when status updates are available.
        """
        edit_mode = self._config.get(AppConfig.EDIT_MODE)
        if edit_mode != MODE_INPAINT:
            mask = None

        def generate_images() -> tuple[list[Image], dict | None]:
            """Call the appropriate image generation endpoint and return generated images."""
            if edit_mode == MODE_TXT2IMG:
                return self._webservice.txt2img(self._config, selection.width, selection.height, image=selection)
            return self._webservice.img2img(selection, self._config, mask=mask)

        # POST to server_url, check response
        # If invalid or error response, throw Exception
        error_count = 0
        max_errors = MAX_ERROR_COUNT
        # refresh times in microseconds:
        min_refresh = MIN_RETRY_US
        max_refresh = MAX_RETRY_US
        images = []
        errors = []

        # Check progress before starting:
        init_data = self._webservice.progress_check()
        if init_data[PROGRESS_KEY_CURRENT_IMAGE] is not None:
            raise RuntimeError('Image generation in progress, try again later.')

        def async_request():
            """Request image generation, wait for and pass on response data."""
            try:
                image_data, info = generate_images()
                if info is not None:
                    print(f'Image generation result info: {info}')
                for response_image in image_data:
                    images.append(response_image)
            except RuntimeError as image_gen_error:
                print(f'request failed: {image_gen_error}')
                errors.append(image_gen_error)

        thread = threading.Thread(target=async_request)
        thread.start()

        init_timestamp = None
        while thread.is_alive():
            sleep_time = min(min_refresh * pow(2, error_count), max_refresh)
            thread.join(timeout=sleep_time / 1000000)
            if not thread.is_alive() or len(errors) > 0:
                break
            try:
                status = self._webservice.progress_check()
                status_text = f'{int(status[PROGRESS_KEY_FRACTION] * 100)}%'
                if PROGRESS_KEY_ETA_RELATIVE in status and status[PROGRESS_KEY_ETA_RELATIVE] != 0:
                    timestamp = datetime.datetime.now().timestamp()
                    if init_timestamp is None:
                        init_timestamp = timestamp
                    else:
                        seconds_passed = timestamp - init_timestamp
                        fraction_complete = status[PROGRESS_KEY_FRACTION]
                        eta_sec = int(seconds_passed / fraction_complete)
                        minutes = eta_sec // 60
                        seconds = eta_sec % 60
                        if minutes > 0:
                            status_text = f'{status_text} ETA: {minutes}:{seconds}'
                        else:
                            status_text = f'{status_text} ETA: {seconds}s'
                status_signal.emit({'progress': status_text})
            except RuntimeError as err:
                error_count += 1
                print(f'Error {error_count}: {err}')
                if error_count > max_errors:
                    print('Inpainting failed, reached max retries.')
                    break
                continue
            error_count = 0  # Reset error count on success.
        if len(errors) > 0:
            print('Inpainting failed with error, raising...')
            raise errors[0]
        idx = 0
        for image in images:
            save_image(image, idx)
            idx += 1

    def _apply_status_update(self, status_dict: dict[str: str]) -> None:
        """Show status updates in the UI."""
        if 'seed' in status_dict:
            self._config.set(AppConfig.LAST_SEED, str(status_dict['seed']))
        if 'progress' in status_dict:
            self._window.set_loading_message(status_dict['progress'])
