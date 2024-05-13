"""
Provides image editing functionality through the A1111/stable-diffusion-webui REST API.
"""
import sys
import threading
import re
import os
import requests
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QInputDialog
from PIL import Image

from ui.window.stable_diffusion_main_window import StableDiffusionMainWindow
from ui.modal.modal_utils import show_error_dialog
from ui.util.screen_size import screen_size
from controller.base_controller import BaseInpaintController
from api.a1111_webservice import A1111Webservice


class StableDiffusionController(BaseInpaintController):
    """StableDiffusionController using the A1111/stable-diffusion-webui REST API to handle image operations. """


    def __init__(self, args):
        """Starts the application and creates the main window on init.

        Parameters
        ----------
        args : Namespace
            Command-line arguments, as generated by the argparse module
        """
        self._server_url = args.server_url
        super().__init__(args)
        self._webservice = A1111Webservice(args.server_url)
        self._session = self._webservice._session
        self._window = None

        # Login automatically if username/password are defined as env variables.
        # Obviously this isn't terribly secure, but A1111 auth security is already pretty minimal and I'm just using
        # this for testing.
        if 'SD_UNAME' in os.environ and 'SD_PASS' in os.environ:
            self._webservice._login(os.environ['SD_UNAME'], os.environ['SD_PASS'])
            self._webservice.set_auth((os.environ['SD_UNAME'], os.environ['SD_PASS']))

        # Since stable-diffusion supports alternate generation modes, configure sketch/mask to only be available
        # when using appropriate modes:
        def update_mask_state(edit_mode):
            self._mask_canvas.set_enabled(edit_mode == 'Inpaint')
        self._config.connect(self._mask_canvas, 'edit_mode', update_mask_state)
        def update_sketch_state(edit_mode):
            self._sketch_canvas.set_enabled(edit_mode != 'Text to Image')
        self._config.connect(self._sketch_canvas, 'edit_mode', update_sketch_state)
        edit_mode = self._config.get('edit_mode')
        update_mask_state(edit_mode)
        update_sketch_state(edit_mode)


    def init_settings(self, settings_modal):
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
                int(settings['sd_checkpoints_keep_in_cpu']),
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
        vae_options.insert(0, "Automatic")
        vae_options.insert(0, "None")
        settings_modal.add_combobox_setting('sd_vae',
                'Models',
                settings['sd_vae'],
                vae_options,
                'Stable-Diffusion VAE:')
        settings_modal.set_tooltip('sd_vae',
                "Automatic: use VAE with same name as model\nNone: use embedded VAE\n" \
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
                8, 9999, "ESRGAN tile size")
        settings_modal.add_spinbox_setting('ESRGAN_tile_overlap',
                'Upscalers',
                int(settings['ESRGAN_tile_overlap']),
                8,
                9999,
                "ESRGAN tile overlap")
        return True


    def refresh_settings(self, settings_modal):
        """Loads current settings from the webui and applies them to the ui.modal SettingsModal."""
        settings = self._webservice.get_config()
        settings_modal.update_settings(settings)


    def update_settings(self, changed_settings):
        """Applies changed settings from a ui.modal SettingsModal to the stable-diffusion-webui."""
        for key in changed_settings:
            print(f"Setting {key} to {changed_settings[key]}")
        self._webservice.set_config(changed_settings)


    @staticmethod
    def health_check(url=None, webservice=None):
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
            res = None
            if webservice is None:
                res = requests.get(url, timeout=20)
            else:
                res = webservice.login_check()
            if res.status_code == 200 or (res.status_code == 401 and res.json()['detail'] == 'Not authenticated'):
                return True
            raise RuntimeError(f"{res.status_code} : {res.text}")
        except RuntimeError as err:
            print(f"error checking login: {err}")
            return False


    def interrogate(self):
        """ Calls the "interrogate" endpoint to automatically generate image prompts.

        Sends the edited image selection content to the stable-diffusion-webui API, where an image captioning model
        automatically generates an appropriate prompt. Once returned, that prompt is copied to the appropriate field
        in the UI. Displays an error dialog instead if no image is loaded or another API operation is in-progress.
        """
        if not self._edited_image.has_image():
            show_error_dialog(self._window, "Interrogate failed", "Create or load an image first.")
            return
        if self._thread is not None:
            show_error_dialog(self._window, "Interrogate failed", "Existing operation currently in progress")
            return

        class InterrogateWorker(QObject):
            """Manage interrogate requests in a child thread."""
            finished = pyqtSignal()
            prompt_ready = pyqtSignal(str)
            error_signal = pyqtSignal(Exception)

            def __init__(self, config, edited_image, webservice):
                super().__init__()
                self._config = config
                self._edited_image = edited_image
                self._webservice = webservice

            def run(self):
                """Run interrogation in the child thread, emit a signal and exit when finished."""
                try:
                    image = self._edited_image.get_selection_content()
                    config = self._config
                    self.prompt_ready.emit(self._webservice.interrogate(config, image))
                except RuntimeError as err:
                    print (f"err:{err}")
                    self.error_signal.emit(err)
                self.finished.emit()

        worker = InterrogateWorker(self._config, self._edited_image, self._webservice)
        def set_prompt(prompt_text):
            print(f'Set prompt to {prompt_text}')
            self._config.set('prompt', prompt_text)
        worker.prompt_ready.connect(set_prompt)
        def handle_error(err):
            self._window.set_is_loading(False)
            show_error_dialog(self._window, "Interrogate failure", err)
        worker.error_signal.connect(handle_error)
        self._start_thread(worker, loading_text="Running CLIP interrogate")


    def window_init(self):
        """Creates and shows the main editor window."""
        # Make sure a valid connection exists:
        def prompt_for_url(prompt_text):
            new_url, url_entered = QInputDialog.getText(self._window, 'Inpainting UI', prompt_text)
            if not url_entered: # User clicked 'Cancel'
                sys.exit()
            if new_url != '':
                self._server_url=new_url

        # Get URL if one was not provided on the command line:
        while self._server_url == '':
            print('requesting url:')
            prompt_for_url('Enter server URL:')

        # Check connection:
        while not StableDiffusionController.health_check(webservice=self._webservice):
            prompt_for_url('Server connection failed, enter a new URL or click "OK" to retry')

        try:
            self._config.set('controlnet_version', float(self._webservice.get_controlnet_version()))
        except RuntimeError:
            # The webui fork at lllyasviel/stable-diffusion-webui-forge is mostly compatible with the A1111 API, but
            # it doesn't have the ControlNet version endpoint. Berore assuming ControlNet isn't installed, check if
            # the ControlNet model list endpoint returns anything:
            try:
                model_list = self._webservice.get_controlnet_models()
                if model_list is not None and 'model_list' in model_list and len(model_list['model_list']) > 0:
                    self._config.set('controlnet_version', 1.0)
                else:
                    self._config.set('controlnet_version', -1.0)
            except RuntimeError as err:
                print(f"Loading controlnet config failed: {err}")
                self._config.set('controlnet_version', -1.0)

        option_loading_params = [
            ['styles', self._webservice.get_styles],
            ['sampling_method', self._webservice.get_samplers],
            ['upscale_method', self._webservice.get_upscalers]
        ]

        # load various option lists:
        for config_key, loading_fn in option_loading_params:
            try:
                self._config.update_options(config_key, loading_fn())
            except (KeyError, RuntimeError) as err:
                print(f"error loading {config_key} from {self._server_url}: {err}")

        # initialize remote options modal:
        # Handle final window init now that data is loaded from the API:
        self._window = StableDiffusionMainWindow(self._config, self._edited_image, self._mask_canvas,
                self._sketch_canvas, self)
        size = screen_size(self._window)
        self._window.setGeometry(0, 0, size.width(), size.height())
        self.fix_styles()
        self._window.show()


    def _scale(self, size):
        """Provide extra upscaling modes using stable-diffusion-webui."""
        width = self._edited_image.width()
        height = self._edited_image.height()
        # If downscaling, use base implementation:
        if (size.width() <= width and size.height() <= height):
            super()._scale(size)
            return
        # If upscaling, use stable-diffusion-webui upscale api:
        class UpscaleWorker(QObject):
            """Manage interrogate requests in a child thread."""
            finished = pyqtSignal()
            image_ready = pyqtSignal(Image.Image)
            status_signal = pyqtSignal(dict)
            error_signal = pyqtSignal(Exception)

            def __init__(self, config, edited_image, webservice):
                super().__init__()
                self._config = config
                self._edited_image = edited_image
                self._webservice = webservice

            def run(self):
                """Handle the upscaling request, then emit a signal and exit when finished."""
                try:
                    images, info = self._webservice.upscale(self._edited_image.get_pil_image(),
                            size.width(),
                            size.height(),
                            self._config)
                    if info is not None:
                        print(f"Upscaling result info: {info}")
                    self.image_ready.emit(images[-1])
                except Exception as err:
                    self.error_signal.emit(err)
                self.finished.emit()
        worker = UpscaleWorker(self._config, self._edited_image, self._webservice)
        def handle_error(err):
            show_error_dialog(self._window, "Upscale failure", err)
        worker.error_signal.connect(handle_error)
        def apply_upscaled(img):
            self._edited_image.set_image(img)
        worker.image_ready.connect(apply_upscaled)
        self._start_thread(worker)


    def _inpaint(self, selection, mask, save_image, status_signal):
        """Handle image editing operations using stable-diffusion-webui."""
        edit_mode = self._config.get('edit_mode')
        if edit_mode != 'Inpaint':
            mask = None

        def generate_images():
            if edit_mode == 'Text to Image':
                return self._webservice.txt2img(self._config, selection.width, selection.height, image=selection)
            return self._webservice.img2img(selection, self._config, mask=mask)


        # POST to server_url, check response
        # If invalid or error response, throw Exception
        error_count = 0
        max_errors = 10
        # refresh times in microseconds:
        min_refresh = 300000
        max_refresh = 60000000
        images = []
        errors = []

        # Check progress before starting:
        init_data = self._webservice.progress_check()
        if init_data['current_image'] is not None:
            raise RuntimeError('Image generation in progress, try again later.')

        def async_request():
            try:
                image_data, info = generate_images()
                if info is not None:
                    print(f"Image generation result info: {info}")
                for image in image_data:
                    images.append(image)
            except RuntimeError as err:
                print(f"request failed: {err}")
                errors.append(err)
        thread = threading.Thread(target=async_request)
        thread.start()

        while thread.is_alive():
            sleep_time = min(min_refresh * pow(2, error_count), max_refresh)
            thread.join(timeout=sleep_time / 1000000)
            if not thread.is_alive() or len(errors) > 0:
                break
            try:
                status = self._webservice.progress_check()
                status_text = f"{int(status['progress'] * 100)}%"
                if 'eta_relative' in status and status['eta_relative'] != 0:
                    # TODO: eta_relative is not a ms value, perhaps use it with timestamps to estimate actual ETA?
                    eta_sec = int(status['eta_relative'] / 1000)
                    minutes = eta_sec // 60
                    seconds = eta_sec % 60
                    if minutes > 0:
                        status_text = f"{status_text} ETA: {minutes}:{seconds}"
                    else:
                        status_text = f"{status_text} ETA: {seconds}s"
                status_signal.emit({'progress': status_text})
            except RuntimeError as err:
                error_count += 1
                print(f'Error {error_count}: {err}')
                if error_count > max_errors:
                    print('Inpainting failed, reached max retries.')
                    break
                continue
            error_count = 0 # Reset error count on success.
        if len(errors) > 0:
            print('Inpainting failed with error, raising...')
            raise errors[0]
        idx = 0
        for image in images:
            save_image(image, idx)
            idx += 1

    def _apply_status_update(self, status_dict):
        if 'seed' in status_dict:
            self._config.set('last_seed', str(status_dict['seed']))
        if 'progress' in status_dict:
            self._window.set_loading_message(status_dict['progress'])
