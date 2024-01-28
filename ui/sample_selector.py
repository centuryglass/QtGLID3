from PyQt5.QtWidgets import QWidget, QLabel, QPushButton
import PyQt5.QtGui as QtGui
from PyQt5.QtGui import QPainter, QPen, QColor, QImage, QPixmap
from PyQt5.QtCore import Qt, QMargins, QPoint, QRect, QBuffer, QSize, QEvent
from PIL import Image
import math, gc

from ui.util.get_scaled_placement import getScaledPlacement
from ui.util.equal_margins import getEqualMargins
from ui.widget.loading_widget import LoadingWidget
from ui.image_utils import imageToQImage

class SampleSelector(QWidget):
    """Shows all inpainting samples as they load, allows the user to select one or discard all of them."""

    def __init__(self, config, editedImage, mask, sketch, closeSelector, makeSelection):
        super().__init__()

        self._config = config
        self._sketch = sketch
        self._makeSelection = makeSelection

        sourceImage = editedImage.getSelectionContent()
        if sketch.hasSketch:
            sketchImage = sketch.getImage().convert('RGBA')
            sourceImage = Image.alpha_composite(sourceImage.convert('RGBA'), sketchImage).convert('RGB')
        maskImage = mask.getImage()

        self._editedImage = editedImage
        self._sourcePixmap = QPixmap.fromImage(imageToQImage(sourceImage))
        self._maskPixmap = QPixmap.fromImage(imageToQImage(maskImage))
        self._sourceImageBounds = QRect(0, 0, 0, 0)
        self._maskImageBounds = QRect(0, 0, 0, 0)
        self._includeOriginal = config.get('addOriginalToSamples')
        self._sourceOptionBounds = None
        self._zoomImageBounds = None
        
        self._expectedCount = config.get('batchCount') * config.get('batchSize')
        self._imageSize = QSize(sourceImage.width, sourceImage.height)
        self._zoomMode = False
        self._zoomIndex = 0
        self._options = []
        for idx in range(self._expectedCount):
            self._options.append({"image": None, "pixmap": None, "bounds": None})

        self._instructions = QLabel(self, text="Click a sample to apply it to the source image, or click 'cancel' to"
                + " discard all samples.")
        self._instructions.show()
        if ((self._expectedCount) > 1) or self._includeOriginal:
            self._zoomButton = QPushButton(self)
            self._zoomButton.setText("Zoom in")
            self._zoomButton.clicked.connect(lambda: self.toggleZoom())
            self._zoomButton.show()
        else:
            self._zoomButton = None
        self._cancelButton = QPushButton(self)
        self._cancelButton.setText("Cancel")
        self._cancelButton.clicked.connect(closeSelector)
        self._cancelButton.show()

        self._isLoading = False
        self._loadingWidget = LoadingWidget()
        self._loadingWidget.setParent(self)
        self._loadingWidget.setGeometry(self.frameGeometry())
        self._loadingWidget.hide()
        self.resizeEvent(None)

        def freeMemoryAndClose():
            del self._sourcePixmap
            self._sourcePixmap = None
            del self._maskPixmap
            self._maskPixmap = None
            for option in self._options:
                if option["image"] is not None:
                    del option["image"]
                    option["image"] = None
                if option["pixmap"] is not None:
                    del option["pixmap"]
                    option["pixmap"] = None
            gc.collect()
            closeSelector()
        self._closeSelector = freeMemoryAndClose

    def toggleZoom(self, index=-1):
        if self._zoomButton is None:
            return
        if self._zoomMode:
            self._zoomButton.setText("Zoom in")
            self._zoomMode = False
        else: 
            self._zoomButton.setText("Zoom out")
            self._zoomMode = True
            if index > 0 and index < self._optionCount():
                self._zoomIndex = index
            else:
                self._zoomIndex = 0
        self.resizeEvent(None)
        self.update()

    def setIsLoading(self, isLoading, message=None):
        """Show or hide the loading indicator"""
        if isLoading:
            self._loadingWidget.show()
            if message:
                self._loadingWidget.setMessage(message)
            else:
                self._loadingWidget.setMessage("Loading images")
        else:
            self._loadingWidget.hide()
        self._isLoading = isLoading
        self.update()

    def setLoadingMessage(self, message):
        self._loadingWidget.setMessage(message)

    def loadSampleImage(self, imageSample, idx):
        """
        Loads an inpainting sample image into the appropriate SampleWidget.
        Parameters:
        -----------
        imageSample : Image
            Newly generated inpainting image sample.
        idx : int
            Index of the image sample
        """
        pixmap = QPixmap.fromImage(imageToQImage(imageSample))
        assert pixmap is not None
        if idx >= len(self._options):
            self._options.append({"image": imageSample, "pixmap": pixmap})
            self.resizeEvent(None)
        else:
            self._options[idx]["pixmap"] = pixmap
            self._options[idx]["image"] = imageSample
        self.update()

    def resizeEvent(self, event):
        statusArea = QRect(0, 0, self.width(), self.height() // 8)
        self._sourceImageBounds = getScaledPlacement(statusArea, self._imageSize, 5)
        self._sourceImageBounds.moveLeft(statusArea.x() + 10)
        self._maskImageBounds = QRect(self._sourceImageBounds.right() + 5,
                self._sourceImageBounds.y(),
                self._sourceImageBounds.width(),
                self._sourceImageBounds.height())

        loadingWidgetSize = int(statusArea.height() * 1.2)
        loadingBounds = QRect(self.width() // 2 - loadingWidgetSize // 2, 0,
                loadingWidgetSize, loadingWidgetSize)
        self._loadingWidget.setGeometry(loadingBounds)

        textArea = QRect(self._maskImageBounds.right(),
                statusArea.y(),
                int((statusArea.width() - self._maskImageBounds.right()) * 0.8),
                statusArea.height()).marginsRemoved(getEqualMargins(4))
        self._instructions.setGeometry(textArea)
        zoomArea = QRect(textArea.right(), statusArea.y(),
            (statusArea.width() - textArea.right()) // 2,
            statusArea.height()).marginsRemoved(getEqualMargins(10))
        if zoomArea.height() > zoomArea.width():
            zoomArea.setHeight(max(zoomArea.width(), statusArea.height() // 3))
            zoomArea.setY(statusArea.y() + (statusArea.height() // 4) - (zoomArea.height() // 2))
            cancelArea = QRect(zoomArea.left(),
                    statusArea.bottom() - (statusArea.height() // 4) - (zoomArea.height() // 2),
                    zoomArea.width(),
                    zoomArea.height())
            self._cancelButton.setGeometry(cancelArea)
        else:
            cancelArea = QRect(zoomArea.left() + zoomArea.width(), statusArea.y(),
                (statusArea.width() - zoomArea.right()),
                statusArea.height()).marginsRemoved(getEqualMargins(10))
            self._cancelButton.setGeometry(cancelArea)
        if self._zoomButton is not None:
            self._zoomButton.setGeometry(zoomArea)

        
        optionCount = self._optionCount()
        optionArea = QRect(0, statusArea.height(), self.width(), self.height() - statusArea.height())
        if self._zoomMode:
            # Make space on sides for arrows:
            arrowSize = max(self.width() // 70, 8)
            arrowMargin = arrowSize // 2
            optionArea.setLeft(optionArea.left() + arrowSize + (arrowMargin * 2))
            optionArea.setRight(optionArea.right() - arrowSize - (arrowMargin * 2))
            self._zoomImageBounds = getScaledPlacement(optionArea, self._imageSize, 2)

            arrowTop = optionArea.y() + (optionArea.height() // 2) - (arrowSize // 2)
            self._leftArrowBounds = QRect(optionArea.left() - (arrowSize + arrowMargin), arrowTop, arrowSize,
                    arrowSize)
            self._rightArrowBounds = QRect(optionArea.x() + optionArea.width() + arrowMargin, arrowTop, arrowSize,
                    arrowSize)
        else:
            margin = 10
            def getScaleFactorForRowCount(nRows):
                nColumns = math.ceil(optionCount / nRows)
                imgBounds = QRect(0, 0, optionArea.width() // nColumns, optionArea.height() // nRows)
                imgRect = getScaledPlacement(imgBounds, self._imageSize, margin)
                return imgRect.width() / self._imageSize.width()
            nRows = 1
            bestScale = 0
            lastScale = 0
            for i in range(1, optionCount + 1):
                scale = getScaleFactorForRowCount(i)
                lastScale = scale
                if scale > bestScale:
                    bestScale = scale
                    nRows = i
                elif scale < lastScale:
                    break
            nColumns = math.ceil(optionCount / nRows)
            rowSize = optionArea.height() // nRows
            columnSize = optionArea.width() // nColumns
            for idx in range(optionCount):
                row = idx // nColumns
                col = idx % nColumns
                x = columnSize * col
                y = optionArea.y() + rowSize * row
                containerRect = QRect(x, y, columnSize, rowSize)
                if idx >= len(self._options):
                    self._sourceOptionBounds = getScaledPlacement(containerRect, self._imageSize, 10)
                else:
                    self._options[idx]["bounds"] = getScaledPlacement(containerRect, self._imageSize, 10)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        if self._sourcePixmap is not None:
            painter.drawPixmap(self._sourceImageBounds, self._sourcePixmap)
        if self._maskPixmap is not None:
            painter.drawPixmap(self._maskImageBounds, self._maskPixmap)
        painter.setPen(QPen(Qt.black, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        def drawImage(option):
            painter.setPen(QPen(Qt.black, 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawRect(option['bounds'].marginsAdded(getEqualMargins(2)))
            if ('pixmap' in option) and (option['pixmap'] is not None):
                painter.drawPixmap(option['bounds'], option['pixmap'])
            else:
                painter.fillRect(option['bounds'], Qt.black)
                painter.setPen(QPen(Qt.white, 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                painter.drawText(option['bounds'], Qt.AlignCenter, "Waiting for image...")
        if self._zoomMode:
            pixmap = None
            if self._zoomIndex >= len(self._options):
                pixmap = self._sourcePixmap
            else:
                pixmap = self._options[self._zoomIndex]['pixmap']
            drawImage({ 'bounds': self._zoomImageBounds, 'pixmap': pixmap })

            # draw arrows:
            def drawArrow(arrowBounds, pts, text):
                painter.setPen(QPen(Qt.black, 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                textBounds = QRect(arrowBounds)
                textBounds.moveTop(textBounds.top() - textBounds.height())
                bgBounds = QRect(arrowBounds)
                bgBounds.setTop(textBounds.top())
                # Arrow background:
                painter.fillRect(bgBounds.marginsAdded(getEqualMargins(4)), Qt.white)
                # Arrow:
                painter.drawLine(pts[0], pts[1])
                painter.drawLine(pts[1], pts[2])
                painter.drawLine(pts[0], pts[2])
                # Index labels:
                painter.drawText(textBounds, Qt.AlignCenter, text)
            maxIdx = self._optionCount() - 1
            prevIdx = maxIdx if (self._zoomIndex == 0) else (self._zoomIndex - 1)
            nextIdx = 0 if (self._zoomIndex >= maxIdx) else (self._zoomIndex + 1)
            leftMid = QPoint(self._leftArrowBounds.left(), self._leftArrowBounds.top()
                    + self._leftArrowBounds.width() // 2)
            rightMid = QPoint(self._rightArrowBounds.left() + self._rightArrowBounds.width(),
                    self._rightArrowBounds.top() + self._rightArrowBounds.width() // 2)
            drawArrow(self._leftArrowBounds,
                    [leftMid, self._leftArrowBounds.topRight(), self._leftArrowBounds.bottomRight()],
                    str(prevIdx + 1))
            drawArrow(self._rightArrowBounds,
                    [rightMid, self._rightArrowBounds.topLeft(), self._rightArrowBounds.bottomLeft()],
                    str(nextIdx + 1))

            # write current index centered over the image:
            indexDim = self._rightArrowBounds.width()
            indexLeft = int(self._zoomImageBounds.x() + (self._zoomImageBounds.width() / 2) + (indexDim / 2))
            indexTop = int(self._zoomImageBounds.y() - indexDim - 8)
            indexBounds = QRect(indexLeft, indexTop, indexDim, indexDim)
            painter.fillRect(indexBounds, Qt.white)
            painter.drawText(indexBounds, Qt.AlignCenter, str(self._zoomIndex + 1))
            
        else:
            for option in self._options:
                drawImage(option)
            if self._sourceOptionBounds is not None:
                option = { 'bounds': self._sourceOptionBounds, 'pixmap': self._sourcePixmap }
                drawImage(option)

    def _optionCount(self):
        return max(len(self._options), self._expectedCount)  + (1 if self._includeOriginal else 0)

    def _zoomPrev(self):
        if self._zoomMode:
            self._zoomIndex = (self._optionCount() - 1) if self._zoomIndex <= 0 else (self._zoomIndex - 1)
            self.resizeEvent(None)
            self.update()
        else:
            self.toggleZoom(self._optionCount() - 1)

    def _zoomNext(self):
        if self._zoomMode:
            self._zoomIndex = 0 if self._zoomIndex >= (self._optionCount() - 1) else (self._zoomIndex + 1)
            self.resizeEvent(None)
            self.update()
        else:
            self.toggleZoom(0)
    
    def eventFilter(self, source, event):
        """Intercept mouse wheel events, use for scrolling in zoom mode:"""
        if event.type() == QEvent.Wheel:
            if event.angleDelta().y() > 0:
                self._zoomNext()
            elif event.angleDelta().y() < 0:
                self._zoomPrev()
            return True

        # Keyboard event handling:
        elif event.type() == QEvent.KeyPress:
            toggleZoom = False
            zoomIndex = -1
            if event.key() == Qt.Key_Escape:
                if self._zoomMode:
                    toggleZoom = True
                else:
                    self._makeSelection(None)
                    self._closeSelector()
                    return True
            elif (event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter) and self._zoomMode:
                if self._zoomIndex >= len(self._options):
                    # Original selected, just close selector
                    self._makeSelection(None)
                    self._closeSelector()
                else:
                    option = self._options[self._zoomIndex]
                    if isinstance(option['image'], Image.Image):
                        self._makeSelection(option['image'])
                        self._closeSelector()
                return True
            elif event.text().isdigit() and (int(event.text()) - 1) < self._optionCount():
                if (not self._zoomMode) or (int(event.text()) - 1) == self._zoomIndex:
                    toggleZoom = True
                zoomIndex = int(event.text()) - 1
            # Allow both gaming-style (WASD) and Vim-style (hjkl) navigation:
            elif self._zoomMode:
                if event.text().lower() == 'h' or event.text().lower() == 'a':
                    self._zoomPrev()
                    return True
                elif event.text().lower() == 'l' or event.text().lower() == 'd':
                    self._zoomNext()
                    return True
                elif event.text().lower() == 'k' or event.text().lower() == 'w':
                    toggleZoom = True
            elif event.text().lower() == 'j' or event.text().lower() == 's':
                    toggleZoom = True # Zoom in on "down"
            if toggleZoom:
                self.toggleZoom(zoomIndex)
                return True
            elif self._zoomMode and zoomIndex >= 0:
                self._zoomIndex = zoomIndex
                self.resizeEvent(None)
                self.update()
                return True
            return False
        else:
            return super().eventFilter(source, event)


    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self._zoomMode:
            maxIdx = max(len(self._options), self._expectedCount) - (0 if self._includeOriginal else 1)
            # Check for arrow clicks:
            if self._leftArrowBounds.contains(event.pos()):
                self._zoomPrev()
                return
            elif self._rightArrowBounds.contains(event.pos()):
                self._zoomNext()
                return
            if self._isLoading:
                return
            if self._zoomImageBounds.contains(event.pos()):
                if self._includeOriginal and self._zoomIndex == maxIdx:
                    # Original chosen, no need to change anything besides applying sketch:
                    self._makeSelection(None)
                    self._closeSelector()
                else:
                    option = self._options[self._zoomIndex]
                    if isinstance(option['image'], Image.Image):
                        self._makeSelection(option['image'])
                        self._closeSelector()
        else:
            if self._isLoading:
                return
            if self._includeOriginal and self._sourceOptionBounds is not None:
                if self._sourceOptionBounds.contains(event.pos()): # Original image chosen
                    self._makeSelection(None)
                    self._closeSelector()
                    return
            for option in self._options:
                if option['bounds'].contains(event.pos()) and isinstance(option['image'], Image.Image):
                    self._makeSelection(option['image'])
                    self._closeSelector()
                    return
