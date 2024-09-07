"""
A simple widget that just draws a border around its content.
"""
from typing import Optional

from PySide6.QtCore import QMargins
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QFrame, QWidget

from src.util.visual.contrast_color import contrast_color

DEFAULT_MARGIN = 2
DEFAULT_LINE_MARGIN = 1
DEFAULT_LINE_WIDTH = 1


class BorderedWidget(QFrame):
    """BorderedWidget draws a 1-pixel border around its content."""

    def __init__(self, parent: Optional[QWidget] = None):
        """Initialize the widget, optionally adding it to a parent."""
        super().__init__(parent)
        self._color = QColor()
        self._contents_margin = DEFAULT_MARGIN
        self._line_margin = DEFAULT_LINE_MARGIN
        self.setFrameStyle(QFrame.Shape.Panel | QFrame.Shadow.Plain)
        self.setLineWidth(DEFAULT_LINE_WIDTH)
        self.contents_margin = self._contents_margin
        self.color = contrast_color(self)
        self.setAutoFillBackground(True)

    @property
    def color(self) -> QColor:
        """Returns the drawn border color."""
        return self._color

    @color.setter
    def color(self, new_color: QColor) -> None:
        """Updates the drawn border color."""
        if new_color != self._color:
            self._color = new_color
            palette = self.palette()
            palette.setColor(QPalette.ColorRole.Mid, new_color)
            self.setPalette(palette)
            self.update()

    @property
    def contents_margin(self) -> int:
        """Returns the contents margin (equal on all sides)."""
        return self._contents_margin

    @contents_margin.setter
    def contents_margin(self, new_margin: int) -> None:
        """Updates the contents margin (equal on all sides)."""
        self._contents_margin = new_margin
        self.setContentsMargins(QMargins(new_margin, new_margin, new_margin, new_margin))
        self.update()

    @property
    def line_margin(self) -> int:
        """Returns the margin around the border (equal on all sides)."""
        return self._line_margin

    @line_margin.setter
    def line_margin(self, new_margin: int) -> None:
        """Updates the margin around the border (equal on all sides)."""
        self._line_margin = new_margin
        self.update()

    @property
    def line_width(self) -> int:
        """Returns the line width of the drawn border."""
        return self.lineWidth()

    @line_width.setter
    def line_width(self, new_width: int) -> None:
        """Updates the line width of the drawn border."""
        self.setLineWidth(new_width)
