from PyQt5.QtWidgets import QWidget
from PyQt5.QtGui import QPainter, QPen
from PyQt5.QtCore import Qt, QPoint, QRect, QSize, QMargins

"""Adds general-purpose utility functions for managing component layout"""

def getScaledPlacement(containerRect, innerSize, marginWidth=0):
    """
    Calculate the most appropriate placement of a scaled rectangle within a container, without changing aspect ratio.
    Parameters:
    -----------
    containerRect : QRect
        Bounds of the container where the scaled rectangle will be placed.        
    innerSize : QSize
        S of the rectangle to be scaled and placed within the container.
    marginWidth : int
        Distance in pixels of the area around the container edges that should remain empty.
    Returns:
    --------
    placement : QRect
        Size and position of the scaled rectangle within containerRect.
    scale : number
        Amount that the inner rectangle's width and height should be scaled.
    """
    containerSize = containerRect.size() - QSize(marginWidth * 2, marginWidth * 2)
    scale = min(containerSize.width()/innerSize.width(), containerSize.height()/innerSize.height())
    x = containerRect.x() + marginWidth
    y = containerRect.y() + marginWidth
    if (innerSize.width() * scale) < containerSize.width():
        x += (containerSize.width() - innerSize.width() * scale) / 2
    if (innerSize.height() * scale) < containerSize.height():
        y += (containerSize.height() - innerSize.height() * scale) / 2
    return QRect(int(x), int(y), int(innerSize.width() * scale), int(innerSize.height() * scale))

def QEqualMargins(size):
    """Returns a QMargins object that is equally spaced on all sides."""
    return QMargins(size, size, size, size)

# Simple widget that just draws a black border around its content
class BorderedWidget(QWidget):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QPen(Qt.black, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        widgetSize = self.size()
        painter.drawLine(QPoint(0, 0), QPoint(0, widgetSize.height()))
        painter.drawLine(QPoint(0, 0), QPoint(widgetSize.width(), 0))
        painter.drawLine(QPoint(widgetSize.width(), 0), QPoint(widgetSize.width(), widgetSize.height()))
        painter.drawLine(QPoint(0, widgetSize.height()), QPoint(widgetSize.width(), widgetSize.height()))
