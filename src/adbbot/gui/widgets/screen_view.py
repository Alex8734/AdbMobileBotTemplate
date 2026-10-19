"""ScreenView: aktueller Frame + Overlays, Zoom (Mausrad), Einpassen, Cursor-Koordinaten, Klick = Tap
und Bereichsauswahl für die Template-Aufnahme."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QEvent, QPointF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView, QWidget

from adbbot.core.events import DetectionsUpdated
from adbbot.core.types import Frame, Image, Point, Rect, Size
from adbbot.gui.widgets.overlay_item import OverlayItem

MIN_ZOOM, MAX_ZOOM = 0.05, 16.0
MIN_SELECTION_PX = 3
SELECTION_COLOR = QColor(255, 210, 60)


def frame_to_qimage(image: Image) -> QImage:
    """BGR-Array → QImage. Kopiert, weil das Array dem Bot-Thread gehört."""
    data = np.ascontiguousarray(image)
    h, w = data.shape[:2]
    return QImage(data.data, w, h, 3 * w, QImage.Format.Format_BGR888).copy()


class ScreenView(QGraphicsView):
    cursor_moved = Signal(object)  # Point (Geräte-Pixel) | None
    tap_requested = Signal(object)  # Point (Geräte-Pixel)
    zoom_changed = Signal(float)
    region_selected = Signal(object)  # Rect (Geräte-Pixel), nur im Capture-Modus

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap = QGraphicsPixmapItem()
        self._pixmap.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self._pixmap)
        self._overlays: list[OverlayItem] = []
        self._frame_ts: float | None = None
        self._frame_size: Size | None = None
        self._detections: DetectionsUpdated | None = None
        self._fit = True
        self._click_to_tap = False
        self._hold_overlays = False
        self._frame: Frame | None = None
        self._capture_mode = False
        self._selection_start: Point | None = None
        self._selection_item: QGraphicsRectItem | None = None

        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.viewport().setMouseTracking(True)
        self.setBackgroundBrush(QColor(22, 23, 27))
        self.setMinimumSize(240, 240)

    # --- Zustand -----------------------------------------------------------------------------------

    @property
    def frame_ts(self) -> float | None:
        return self._frame_ts

    @property
    def frame_size(self) -> Size | None:
        return self._frame_size

    @property
    def overlay_items(self) -> list[OverlayItem]:
        return list(self._overlays)

    @property
    def zoom(self) -> float:
        return self.transform().m11()

    @property
    def click_to_tap(self) -> bool:
        return self._click_to_tap

    @click_to_tap.setter
    def click_to_tap(self, enabled: bool) -> None:
        self._click_to_tap = enabled
        self._apply_mouse_mode()

    @property
    def frame(self) -> Frame | None:
        """Der aktuell angezeigte Frame."""
        return self._frame

    @property
    def capture_mode(self) -> bool:
        """An: Livebild eingefroren, Linksziehen wählt einen Bereich (`region_selected`)."""
        return self._capture_mode

    @capture_mode.setter
    def capture_mode(self, enabled: bool) -> None:
        self._capture_mode = enabled
        self._selection_start = None
        if not enabled:
            self.clear_selection()
        self._apply_mouse_mode()

    def clear_selection(self) -> None:
        if self._selection_item is not None:
            self._scene.removeItem(self._selection_item)
            self._selection_item = None

    def _apply_mouse_mode(self) -> None:
        interactive = self._capture_mode or self._click_to_tap
        self.setDragMode(
            QGraphicsView.DragMode.NoDrag if interactive else QGraphicsView.DragMode.ScrollHandDrag
        )
        self.viewport().setCursor(
            Qt.CursorShape.CrossCursor if interactive else Qt.CursorShape.OpenHandCursor
        )

    @property
    def hold_overlays(self) -> bool:
        """An: Overlays bleiben sichtbar, auch wenn sie zu einem älteren Frame gehören."""
        return self._hold_overlays

    @hold_overlays.setter
    def hold_overlays(self, enabled: bool) -> None:
        self._hold_overlays = enabled
        self._refresh_overlays()

    # --- Daten -------------------------------------------------------------------------------------

    def set_frame(self, frame: Frame) -> None:
        if self._capture_mode:
            return  # eingefroren, damit die Auswahl zum sichtbaren Bild passt
        self._frame = frame
        self._pixmap.setPixmap(QPixmap.fromImage(frame_to_qimage(frame.image)))
        size = frame.size
        if size != self._frame_size:
            self._frame_size = size
            self._scene.setSceneRect(0, 0, size.w, size.h)
            if self._fit:
                self.fit_to_window()
        self._frame_ts = frame.ts
        self._refresh_overlays()

    def clear(self) -> None:
        """Kein Bild, keine Overlays (z. B. beim Wechsel auf einen Bot, der noch keinen Frame hat)."""
        self.clear_selection()
        self._pixmap.setPixmap(QPixmap())
        self._frame = None
        self._frame_ts = None
        self._frame_size = None
        self._detections = None
        self._refresh_overlays()

    def set_detections(self, event: DetectionsUpdated) -> None:
        self._detections = event
        self._refresh_overlays()

    def _refresh_overlays(self) -> None:
        for item in self._overlays:
            self._scene.removeItem(item)
        self._overlays = []
        event = self._detections
        if event is None or (event.frame_ts != self._frame_ts and not self._hold_overlays):
            return
        for detection in event.detections:
            item = OverlayItem(detection)
            self._scene.addItem(item)
            self._overlays.append(item)

    # --- Zoom --------------------------------------------------------------------------------------

    def fit_to_window(self) -> None:
        self._fit = True
        if self._frame_size is not None:
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.zoom_changed.emit(self.zoom)

    def set_zoom(self, factor: float) -> None:
        factor = min(MAX_ZOOM, max(MIN_ZOOM, factor))
        self.resetTransform()
        self.scale(factor, factor)
        self._fit = False
        self.zoom_changed.emit(self.zoom)

    def wheelEvent(self, event: QWheelEvent) -> None:
        steps = event.angleDelta().y() / 120
        if steps == 0:
            return
        target = min(MAX_ZOOM, max(MIN_ZOOM, self.zoom * 1.15**steps))
        self.scale(target / self.zoom, target / self.zoom)
        self._fit = False
        self.zoom_changed.emit(self.zoom)
        event.accept()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._fit:
            self.fit_to_window()

    # --- Maus --------------------------------------------------------------------------------------

    def device_point(self, view_pos: QPointF) -> Point | None:
        """Viewport-Position → Geräte-Pixel, `None` außerhalb des Frames."""
        if self._frame_size is None:
            return None
        scene_pos = self.mapToScene(view_pos.toPoint())
        x, y = int(scene_pos.x()), int(scene_pos.y())
        if 0 <= x < self._frame_size.w and 0 <= y < self._frame_size.h:
            return Point(x, y)
        return None

    def _clamped_point(self, view_pos: QPointF) -> Point | None:
        """Wie `device_point`, aber auf den Frame beschnitten (Auswahl darf über den Rand gezogen werden)."""
        if self._frame_size is None:
            return None
        scene_pos = self.mapToScene(view_pos.toPoint())
        x = min(max(int(scene_pos.x()), 0), self._frame_size.w)
        y = min(max(int(scene_pos.y()), 0), self._frame_size.h)
        return Point(x, y)

    def _update_selection(self, end: Point) -> Rect | None:
        start = self._selection_start
        if start is None:
            return None
        rect = Rect(min(start.x, end.x), min(start.y, end.y), abs(end.x - start.x), abs(end.y - start.y))
        if self._selection_item is None:
            pen = QPen(SELECTION_COLOR, 2, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self._selection_item = QGraphicsRectItem()
            self._selection_item.setPen(pen)
            self._selection_item.setBrush(QBrush(QColor(255, 210, 60, 40)))
            self._selection_item.setZValue(20)
            self._scene.addItem(self._selection_item)
        self._selection_item.setRect(rect.x, rect.y, rect.w, rect.h)
        return rect

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.cursor_moved.emit(self.device_point(event.position()))
        if self._capture_mode and self._selection_start is not None:
            point = self._clamped_point(event.position())
            if point is not None:
                self._update_selection(point)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._capture_mode and event.button() == Qt.MouseButton.LeftButton:
            end = self._clamped_point(event.position())
            rect = self._update_selection(end) if end is not None else None
            self._selection_start = None
            if rect is not None and rect.w >= MIN_SELECTION_PX and rect.h >= MIN_SELECTION_PX:
                self.region_selected.emit(rect)
            else:
                self.clear_selection()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._capture_mode and event.button() == Qt.MouseButton.LeftButton:
            self.clear_selection()
            self._selection_start = self._clamped_point(event.position())
            event.accept()
            return
        if self._click_to_tap and event.button() == Qt.MouseButton.LeftButton:
            point = self.device_point(event.position())
            if point is not None:
                self.tap_requested.emit(point)
            event.accept()
            return
        super().mousePressEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.cursor_moved.emit(None)
        super().leaveEvent(event)
