"""
Runs IntraPaint with no real image editing functionality. Intended for testing only.
"""
from PIL import Image
from controller.base_controller import BaseInpaintController

class MockController(BaseInpaintController):
    """Mock controller for UI testing, performs no real inpainting"""

    def _inpaint(self, selection, mask, load_image):
        print("Mock inpainting call:")
        print(f"\tselection: {selection}")
        print(f"\tmask: {mask}")
        config_options = self._config.list()
        for option_name in config_options:
            value = self._config.get(option_name)
            print(f"\t{option_name}: {value}")
        with Image.open(open('mask.png', 'rb')).convert('RGB') as test_sample:
            for y in range(0, self._config.get('batch_count')):
                for x in range(0, self._config.get('batch_size')):
                    load_image(test_sample, y, x)
