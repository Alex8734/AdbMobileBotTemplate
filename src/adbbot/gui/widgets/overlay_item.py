"""Overlay im ScreenView: Rechteck (Stil nach `Detection.kind`) + Beschriftung in fester Bildschirmgröße."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem, QStyleOptionGraphicsItem, QWidget

from adbbot.core.types import Detection
from adbbot.vision.overlay import KIND_COLORS

KIND_QCOLORS: dict[str, QColor] = {kind: QColor(r, g, b) for kind, (b, g, r) in KIND_COLORS.items()}


def caption(detection: Detection) -> str:
    text = detection.text if detection.kind == "text" and detection.text is not None else detection.label
    if detection.kind == "missing":
        text += " ✗"
    if detection.confidence is not None:
        text += f" {detection.confidence:.2f}"
    return text


class LabelItem(QGraphicsItem):
    """Beschriftung, die beim Zoomen gleich groß bleibt (ignoriert Transformationen)."""

    PADDING = 3.0

    def __init__(self, text: str, color: QColor, parent: QGraphicsItem | None = None) -> None:
        super().__init__(parent)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.text = text
        self._color = color
        self._font = QFont()
        self._font.setPointSizeF(8.5)
        metrics = QFontMetricsF(self._font)
        height = metrics.height() + 2 * self.PADDING
        self._rect = QRectF(0, -height, metrics.horizontalAdvance(text) + 2 * self.PADDING, height)

    def boundingRect(self) -> QRectF:
        return self._rect

    def paint(
        self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None
    ) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawRect(self._rect)
        painter.setPen(QColor(0, 0, 0))
        painter.setFont(self._font)
        painter.drawText(self._rect, Qt.AlignmentFlag.AlignCenter, self.text)


class OverlayItem(QGraphicsRectItem):
    def __init__(self, detection: Detection) -> None:
        r = detection.rect
        super().__init__(r.x, r.y, r.w, r.h)
        self.detection = detection
        color = KIND_QCOLORS[detection.kind]
        pen = QPen(color, 2)
        pen.setCosmetic(True)  # Linienbreite unabhängig vom Zoom
        if detection.kind == "missing":
            pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setZValue(10)
        self.label = LabelItem(caption(detection), color, self)
        self.label.setPos(r.x, r.y)
        self.setToolTip(f"{caption(detection)}\n{r.x}, {r.y}, {r.w} × {r.h}")
