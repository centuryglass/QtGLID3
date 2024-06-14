"""
Provides image editing functionality through the A1111/stable-diffusion-webui REST API.
"""
import sys
import threading
import os
import datetime
from argparse import Namespace
from typing import Optional, Callable, Any, Dict, List
import logging

import requests
from PIL import Image
from PyQt5.QtCore import QObject, pyqtSignal, QSize
from PyQt5.QtWidgets import QInputDialog

from src.config.a1111_config import A1111Config
from src.image.layer_stack import LayerStack
from src.config.application_config import AppConfig
from src.ui.modal.settings_modal import SettingsModal
from src.ui.window.stable_diffusion_main_window import StableDiffusionMainWindow
from src.ui.modal.modal_utils import show_error_dialog
from src.util.screen_size import get_screen_size
from src.controller.base_controller import BaseInpaintController, MENU_TOOLS
from src.api.a1111_webservice import A1111Webservice
from src.util.menu_action import menu_action

STABLE_DIFFUSION_CONFIG_CATEGORY = 'Stable-Diffusion'

logger = logging.getLogger(__name__)

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

GENERATE_ERROR_TITLE = "Image generation failed"
GENERATE_ERROR_MESSAGE_EMPTY_MASK = ("Selection mask was empty. Either use the mask tool to mark part of the image"
                                     " generation area for inpainting, or switch to another image generation mode.")

MODE_INPAINT = 'Inpaint'
MODE_TXT2IMG = 'Text to Image'

MAX_ERROR_COUNT = 10
MIN_RETRY_US = 300000
MAX_RETRY_US = 60000000

LCM_SAMPLER = 'LCM'
LCM_LORA_1_5 = 'lcm-lora-sdv1-5'
LCM_LORA_XL = 'lcm-lora-sdxl'


def _check_lcm_mode_available(controller: 'StableDiffusionController') -> bool:
    if LCM_SAMPLER not in AppConfig.instance().get_options(AppConfig.SAMPLING_METHOD):
        return False
    loras = [lora['name'] for lora in AppConfig.instance().get(AppConfig.LORA_MODELS)]
    return LCM_LORA_1_5 in loras or LCM_LORA_XL in loras


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

    def get_config_categories(self) -> List[str]:
        """Return the list of AppConfig categories this controller supports."""
        categories = super().get_config_categories()
        categories.append(STABLE_DIFFUSION_CONFIG_CATEGORY)
        return categories

    def init_settings(self, settings_modal: SettingsModal) -> bool:
        """Adds relevant stable-diffusion-webui settings to a ui.modal SettingsModal.  """
        if not isinstance(self._webservice, A1111Webservice):
            print('Disabling remote settings: only supported with the A1111 API')
            return False
        super().init_settings(settings_modal)
        web_config = A1111Config.instance()
        web_config.load_all(self._webservice)
        settings_modal.load_from_config(web_config)

    def refresh_settings(self, settings_modal: SettingsModal) -> None:
        """Loads current settings from the webui and applies them to the SettingsModal."""
        super().refresh_settings(settings_modal)
        settings = self._webservice.get_config()
        settings_modal.update_settings(settings)

    def update_settings(self, changed_settings: dict[str, Any]) -> None:
        """Applies changed settings from a SettingsModal to the stable-diffusion-webui."""
        super().update_settings(changed_settings)
        web_config = A1111Config.instance()
        web_categories = web_config.get_categories()
        web_keys = [key for cat in web_categories for key in web_config.get_category_keys(cat)]
        web_changes = {}
        for key in changed_settings:
            if key in web_keys:
                web_changes[key] = changed_settings[key]
        if len(web_changes) > 0:
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
        except RuntimeError as status_err:
            logger.error(f'Login check returned failure response: {status_err}')
            return False
        except requests.exceptions.RequestException as req_err:
            logger.error(f'Login check connection failed: {req_err}')
            return False

    def interrogate(self) -> None:
        """ Calls the "interrogate" endpoint to automatically generate image prompts.

        Sends the image generation area content to the stable-diffusion-webui API, where an image captioning model
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

            def __init__(self, layer_stack, webservice):
                super().__init__()
                self._layer_stack = layer_stack
                self._webservice = webservice

            def run(self):
                """Run interrogation in the child thread, emit a signal and exit when finished."""
                try:
                    image = self._layer_stack.pil_image_generation_area_content()
                    self.prompt_ready.emit(self._webservice.interrogate(image))
                except RuntimeError as err:
                    logger.error(f'err:{err}')
                    self.error_signal.emit(err)
                self.finished.emit()

        worker = InterrogateWorker(self._layer_stack, self._webservice)

        def set_prompt(prompt_text: str) -> None:
            """Update the image prompt in config with the interrogate results."""
            AppConfig.instance().set(AppConfig.PROMPT, prompt_text)

        worker.prompt_ready.connect(set_prompt)

        def handle_error(err: BaseException) -> None:
            """Show an error popup if interrogate fails."""
            assert self._window is not None
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
        config = AppConfig.instance()
        try:
            config.set(AppConfig.CONTROLNET_VERSION, float(self._webservice.get_controlnet_version()))
        except RuntimeError:
            # The webui fork at lllyasviel/stable-diffusion-webui-forge is mostly compatible with the A1111 API, but
            # it doesn't have the ControlNet version endpoint. Before assuming ControlNet isn't installed, check if
            # the ControlNet model list endpoint returns anything:
            try:
                model_list = self._webservice.get_controlnet_models()
                if model_list is not None and CONTROLNET_MODEL_LIST_KEY in model_list and len(
                        model_list[CONTROLNET_MODEL_LIST_KEY]) > 0:
                    config.set(AppConfig.CONTROLNET_VERSION, 1.0)
                else:
                    config.set(AppConfig.CONTROLNET_VERSION, -1.0)
            except RuntimeError as err:
                logger.error(f'Loading controlnet config failed: {err}')
                config.set(AppConfig.CONTROLNET_VERSION, -1.0)

        option_loading_params = (
            (AppConfig.STYLES, self._webservice.get_styles),
            (AppConfig.SAMPLING_METHOD, self._webservice.get_samplers),
            (AppConfig.UPSCALE_METHOD, self._webservice.get_upscalers)
        )

        # load various option lists:
        for config_key, option_loading_fn in option_loading_params:
            try:
                options = option_loading_fn()
                if options is not None and len(options) > 0:
                    config.update_options(config_key, options)
            except (KeyError, RuntimeError) as err:
                logger.error(f'error loading {config_key} from {self._server_url}: {err}')

        data_params = (
            (AppConfig.CONTROLNET_CONTROL_TYPES, self._webservice.get_controlnet_control_types),
            (AppConfig.CONTROLNET_MODULES, self._webservice.get_controlnet_modules),
            (AppConfig.CONTROLNET_MODELS, self._webservice.get_controlnet_models),
            (AppConfig.LORA_MODELS, self._webservice.get_loras)
        )
        for config_key, data_loading_fn in data_params:
            try:
                value = data_loading_fn()
                if value is not None and len(value) > 0:
                    config.set(config_key, value)
            except (KeyError, RuntimeError) as err:
                logger.error(f'error loading {config_key} from {self._server_url}: {err}')

        # initialize remote options modal:
        # Handle final window init now that data is loaded from the API:
        self._window = StableDiffusionMainWindow(self._layer_stack, self)
        if self._fixed_window_size is not None:
            size = self._fixed_window_size
            self._window.setGeometry(0, 0, size.width(), size.height())
            self._window.setMaximumSize(self._fixed_window_size)
            self._window.setMinimumSize(self._fixed_window_size)
        else:
            size = get_screen_size(self._window)
            self._window.setGeometry(0, 0, size.width(), size.height())
            self._window.setMaximumSize(size)
        self.fix_styles()
        if self._init_image is not None:
            logger.info('loading init image:')
            self.load_image(file_path=self._init_image)
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

            def __init__(self, layer_stack: LayerStack, webservice: A1111Webservice) -> None:
                super().__init__()
                self._layer_stack = layer_stack
                self._webservice = webservice

            def run(self):
                """Handle the upscaling request, then emit a signal and exit when finished."""
                try:
                    images, info = self._webservice.upscale(self._layer_stack.pil_image(), new_size.width(),
                                                            new_size.height())
                    if info is not None:
                        logger.debug(f'Upscaling result info: {info}')
                    self.image_ready.emit(images[-1])
                except IOError as err:
                    self.error_signal.emit(err)
                self.finished.emit()

        worker = UpscaleWorker(self._layer_stack, self._webservice)

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
                 source_image_section: Image.Image,
                 mask: Image.Image,
                 save_image: Callable[[Image.Image, int], None],
                 status_signal: pyqtSignal) -> None:
        """Handle image editing operations using stable-diffusion-webui.

        Parameters
        ----------
        source_image_section : PIL Image, optional
            Image selection to edit
        mask : PIL Image, optional
            Mask marking edited image region.
        save_image : function (PIL Image, int)
            Function used to return each image response and its index.
        status_signal : pyqtSignal
            Signal to emit when status updates are available.
        """
        edit_mode = AppConfig.instance().get(AppConfig.EDIT_MODE)
        if edit_mode != MODE_INPAINT:
            mask = None
        elif self._layer_stack.selection_layer.generation_area_is_empty():
            raise RuntimeError(GENERATE_ERROR_MESSAGE_EMPTY_MASK)

        def generate_images() -> tuple[list[Image], dict | None]:
            """Call the appropriate image generation endpoint and return generated images."""
            if edit_mode == MODE_TXT2IMG:
                return self._webservice.txt2img(source_image_section.width, source_image_section.height, image=source_image_section)
            return self._webservice.img2img(source_image_section, mask=mask)

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
                    logger.debug(f'Image generation result info: {info}')
                for response_image in image_data:
                    images.append(response_image)
            except RuntimeError as image_gen_error:
                logger.error(f'request failed: {image_gen_error}')
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
                    logger.error('Inpainting failed, reached max retries.')
                    break
                continue
            error_count = 0  # Reset error count on success.
        if len(errors) > 0:
            logger.error('Inpainting failed with error, raising...')
            raise errors[0]
        idx = 0
        for image in images:
            save_image(image, idx)
            idx += 1

    def _apply_status_update(self, status_dict: Dict[str, str]) -> None:
        """Show status updates in the UI."""
        assert self._window is not None
        if 'seed' in status_dict:
            AppConfig.instance().set(AppConfig.LAST_SEED, str(status_dict['seed']))
        if 'progress' in status_dict:
            self._window.set_loading_message(status_dict['progress'])

    @menu_action(MENU_TOOLS, 'lcm_mode_shortcut', 99, False, _check_lcm_mode_available)
    def set_lcm_mode(self) -> None:
        """Apply all settings required for using an LCM LoRA module."""
        config = AppConfig.instance()
        loras = [lora['name'] for lora in config.get(AppConfig.LORA_MODELS)]
        if LCM_LORA_1_5 in loras:
            lora_name = LCM_LORA_1_5
        else:
            lora_name = LCM_LORA_XL
        lora_key = f'<lora:{lora_name}:1>'
        prompt = config.get(AppConfig.PROMPT)
        if lora_key not in prompt:
            config.set(AppConfig.PROMPT, f'{prompt} {lora_key}')
        config.set(AppConfig.GUIDANCE_SCALE, 1.5)
        config.set(AppConfig.SAMPLING_STEPS, 8)
        config.set(AppConfig.SAMPLING_METHOD, 'LCM')
        config.set(AppConfig.SEED, -1)
        if config.get(AppConfig.BATCH_SIZE) < 5:
            config.set(AppConfig.BATCH_SIZE, 5)
        image_size = self._layer_stack.size
        if image_size.width() < 1200 and image_size.height() < 1200:
            config.set(AppConfig.EDIT_SIZE, image_size)
        else:
            size = QSize(min(image_size.width(), 1024), min(image_size.height(), 1024))
            config.set(AppConfig.EDIT_SIZE, size)
