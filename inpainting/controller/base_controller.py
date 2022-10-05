from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QObject, QThread, pyqtSignal
from PIL import Image, ImageFilter
from inpainting.data_model.config import Config
from inpainting.data_model.edited_image import EditedImage
from inpainting.data_model.mask_canvas import MaskCanvas
from inpainting.data_model.sketch_canvas import SketchCanvas
from inpainting.ui.main_window import MainWindow
from inpainting.ui.modal_utils import showErrorDialog
import os, glob, sys

class BaseInpaintController():
    """
    Shared base class for managing impainting. 

    Subclasses will need to override self._inpaint, and may also want to override self.startApp.
    """
    def __init__(self, args):
        self._app = QApplication(sys.argv)
        self._config = Config()
        self._adjustConfigDefaults()
        self._config.applyArgs(args)
        self._editedImage = EditedImage(self._config, args.init_image)

        initialSelectionSize = self._editedImage.getSelectionBounds().size()
        self._maskCanvas = MaskCanvas(self._config, initialSelectionSize)
        self._sketchCanvas = SketchCanvas(self._config, initialSelectionSize)
        # Connect mask/sketch size to image selection size:
        def resizeCanvases(selectionBounds):
            size = selectionBounds.size()
            self._maskCanvas.resize(size)
            self._sketchCanvas.resize(size)
        self._editedImage.selectionChanged.connect(resizeCanvases)

        self._thread = None

        # Set up timelapse image saving if it is configured:
        timelapsePath = self._config.get('timelapsePath')
        if timelapsePath != '':
            # Make sure path exists, find first unused image index:
            if not os.path.exists(timelapsePath):
                os.mkdir(timelapsePath)
            elif os.path.isfile(timelapsePath):
                print("timelapsePath: expected directory path, got file: " + timelapsePath)
            self._nextTimelapseFrame = 0
            for name in glob.glob(f"{timelapsePath}/*.png"):
                n=int(os.path.splitext(ntpath.basename(name))[0])
                if n > self._lastTimelapseFrame:
                    self._nextTimelapseFrame = n + 1
            def saveTimelapseImage():
                filename = os.path.join(self._timelapsePath, f"{self._nextTimelapseFrame:05}.png")
                self._editedImage.saveImage(filename)
                self._nextTimelapseFrame += 1
            editedImage.contentChanged.connect(saveTimelapseImage)

    def _adjustConfigDefaults(self):
        # no-op, override to adjust config before data initialization
        return

    def startApp(self):
        screen = self._app.primaryScreen()
        size = screen.availableGeometry()
        self._window = MainWindow(self._config, self._editedImage, self._maskCanvas, self._sketchCanvas, self)
        self._window.setGeometry(0, 0, size.width(), size.height())
        self._window.show()
        self._app.exec_()
        sys.exit()

    def _inpaint(self):
        raise Exception('BaseInpaintController should not be used directly, use a subclass.')

    def _applyStatusUpdate(self, statusDict):
        return

    def startAndManageInpainting(self):
        if not self._editedImage.hasImage():
            showErrorDialog(self._window, "Failed", "Load an image for editing before trying to start inpainting.")
            return
        if self._thread is not None:
            showErrorDialog(self._window, "Failed", "Existing inpainting operation not yet finished, wait a little longer.")
            return
        self._thread = QThread()

        upscaleMode = self._config.get('upscaleMode')
        downscaleMode = self._config.get('downscaleMode')
        def resizeImage(pilImage, width, height):
            """Resize a PIL image using the appropriate scaling mode:"""
            if width == pilImage.width and height == pilImage.height:
                return pilImage
            if width > pilImage.width or height > pilImage.height:
                return pilImage.resize((width, height), upscaleMode)
            return pilImage.resize((width, height), downscaleMode)

        selection = self._editedImage.getSelectionContent()

        # If sketch mode was used, write the sketch onto the image selection:
        inpaintImage = selection.copy()
        inpaintMask = self._maskCanvas.getImage()
        sketchImage = self._sketchCanvas.getImage()
        sketchImage = resizeImage(sketchImage, inpaintImage.width, inpaintImage.height).convert('RGBA')
        if self._sketchCanvas.hasSketch: 
            inpaintImage = inpaintImage.convert('RGBA')
            inpaintImage = Image.alpha_composite(inpaintImage, sketchImage).convert('RGB')
        keepSketch = self._sketchCanvas.hasSketch and self._config.get('saveSketchInResult')

        # If scaling is enabled, scale selection as close to 256x256 as possible while attempting to minimize
        # aspect ratio changes. Keep the unscaled version so it can be used for compositing if "keep sketch"
        # is checked.
        unscaledInpaintImage = inpaintImage

        if self._config.get('scaleSelectionBeforeInpainting'):
            maxEditSize = self._config.get('maxEditSize')
            largestDim = max(inpaintImage.width, inpaintImage.height)
            scale = maxEditSize.width() / largestDim
            width = int(inpaintImage.width * scale + 1)
            width = max(64, width - (width % 64))
            height = int(inpaintImage.height * scale + 1)
            height = max(64, height - (height % 64))
            inpaintImage = resizeImage(inpaintImage, width, height)
            inpaintMask = resizeImage(inpaintMask, width, height)
        else:
            inpaintMask = resizeImage(inpaintMask, inpaintImage.width, inpaintImage.height)


        doInpaint = lambda img, mask, save, statusSignal: self._inpaint(img, mask, save, statusSignal)
        config = self._config
        class InpaintThreadWorker(QObject):
            finished = pyqtSignal()
            imageReady = pyqtSignal(Image.Image, int, int)
            statusSignal = pyqtSignal(dict)
            errorSignal = pyqtSignal(str)

            def __init__(self):
                super().__init__()

            def run(self):
                def sendImage(img, y, x):
                    img = resizeImage(img, unscaledInpaintImage.width, unscaledInpaintImage.height)
                    self.imageReady.emit(img, y, x)
                try:
                    doInpaint(inpaintImage, inpaintMask, sendImage, self.statusSignal)
                except Exception as err:
                    self.errorSignal.emit(str(err))
                self.finished.emit()
        self._worker = InpaintThreadWorker()
        self._worker.moveToThread(self._thread)

        self._window.setSampleSelectorVisible(True)
        self._window.setIsLoading(True)

        def handleError(err):
            self._window.setIsLoading(False)
            self._window.setSampleSelectorVisible(False)
            showErrorDialog(self._window, "Inpainting failure", err)
        self._worker.errorSignal.connect(handleError)

        def updateStatus(statusDict):
            self._applyStatusUpdate(statusDict)
        self._worker.statusSignal.connect(updateStatus)


        def loadSamplePreview(img, y, x):
            if config.get('removeUnmaskedChanges'):
                #if keepSketch:
                #    img = Image.alpha_composite(img.convert('RGBA'), sketchImage)
                maskAlpha = inpaintMask.convert('L').point( lambda p: 255 if p < 1 else 0 ).filter(ImageFilter.GaussianBlur())
                img = resizeImage(img, selection.width, selection.height)
                maskAlpha = resizeImage(maskAlpha, selection.width, selection.height)
                img = Image.composite(unscaledInpaintImage if keepSketch else selection, img, maskAlpha)
            self._window.loadSamplePreview(img, y, x)
        self._worker.imageReady.connect(loadSamplePreview)

        self._worker.finished.connect(lambda: self._window.setIsLoading(False))
        self._thread.started.connect(self._worker.run)
        self._thread.finished.connect(self._thread.deleteLater)

        def clearOldThread():
            self._thread = None
        self._thread.finished.connect(clearOldThread)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.start()

    def zoomOut(self):
        try:
            image = self._editedImage.getPilImage()
            # Find next unused number in ./zoom/xxxxx.png:
            zoomCount = 0
            while os.path.exists(f"zoom/{zoomCount:05}.png"):
                zoomCount += 1
            # Save current image, update zoom count:
            image.save(f"zoom/{zoomCount:05}.png")
            zoomCount += 1

            # scale image content to 86%, paste it centered into the image:
            newSize = (int(image.width * 0.86), int(image.height * 0.86))
            scaledImage = image.resize(newSize, self._config.get('downscaleMode'))
            newImage = Image.new('RGB', (image.width, image.height), color = 'white')
            insertAt = (int(image.width * 0.08), int(image.height * 0.08))
            newImage.paste(scaledImage, insertAt)
            self._editedImage.setImage(newImage)

            # reload zoom mask:
            self._maskCanvas.setImage('zoomMask.png')
        except Exception as err:
            showErrorDialog(self._window, "Zoom failed", err)
