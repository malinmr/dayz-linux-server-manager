from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap, QRadialGradient, QColor
from PySide6.QtWidgets import QLabel


class Lamp(QLabel):
    """A small round indicator light, like a physical panel lamp."""

    COLORS = {
        "off": "#5d6670",
        "green": "#2ecc71",
        "red": "#e74c3c",
        "amber": "#f39c12",
    }

    def __init__(self, diameter=20, parent=None):
        super().__init__(parent)
        self.diameter = diameter
        self.setFixedSize(diameter, diameter)
        self.set_state("off")

    def set_state(self, state):
        hex_color = self.COLORS.get(state, self.COLORS["off"])
        d = self.diameter
        pixmap = QPixmap(d, d)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        base = QColor(hex_color)
        gradient = QRadialGradient(d * 0.35, d * 0.32, d * 0.85)
        gradient.setColorAt(0.0, base.lighter(170))
        gradient.setColorAt(0.55, base)
        gradient.setColorAt(1.0, base.darker(150))
        painter.setBrush(gradient)
        painter.setPen(QColor(0, 0, 0, 140))
        painter.drawEllipse(1, 1, d - 2, d - 2)
        painter.end()
        self.setPixmap(pixmap)
