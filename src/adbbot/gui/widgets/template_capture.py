"""Template-Capture-Dialog: ausgeschnittenen Bereich als Template (+ Metadaten) speichern.

Ablauf in der GUI: „Template aufnehmen“ → Livebild friert ein → Rechteck aufziehen → dieser Dialog →
Speichern → `ReloadTemplates`-Command. Beschleunigt die Bot-Entwicklung am meisten.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from adbbot.core.exceptions import TemplateError
from adbbot.core.types import Image, Rect, Size
from adbbot.gui.widgets.screen_view import frame_to_qimage
from adbbot.utils.geometry import clip
from adbbot.vision.template import TemplateStore

PREVIEW_MAX_PX = 280


class TemplateCaptureDialog(QDialog):
    saved = Signal(str, object)  # Template-Name, Pfad der PNG-Datei

    def __init__(
        self,
        image: Image,
        selection: Rect,
        frame_size: Size,
        store: TemplateStore,
        page_names: Sequence[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Template aufnehmen")
        self.image = image
        self.selection = selection
        self.frame_size = frame_size
        self.store = store
        self.saved_path: Path | None = None

        preview = QLabel()
        pixmap = QPixmap.fromImage(frame_to_qimage(image))
        if pixmap.width() > PREVIEW_MAX_PX or pixmap.height() > PREVIEW_MAX_PX:
            pixmap = pixmap.scaled(
                PREVIEW_MAX_PX,
                PREVIEW_MAX_PX,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        preview.setPixmap(pixmap)
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info = QLabel(f"Auswahl: x={selection.x}, y={selection.y}, {selection.w} × {selection.h} px")

        folders = {name.split("/", 1)[0] for name in store.names() if "/" in name}
        self.page_combo = QComboBox()
        self.page_combo.setEditable(True)
        self.page_combo.addItems(sorted(set(page_names) | folders))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("z. B. play_button (a–z, 0–9, _)")

        self.threshold_check = QCheckBox("Eigener Threshold")
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.5, 1.0)
        self.threshold_spin.setSingleStep(0.01)
        self.threshold_spin.setDecimals(2)
        self.threshold_spin.setValue(0.9)
        self.threshold_spin.setEnabled(False)
        self.threshold_check.toggled.connect(self.threshold_spin.setEnabled)

        self.region_check = QCheckBox("Suchregion speichern (Auswahl + Rand)")
        self.margin_spin = QSpinBox()
        self.margin_spin.setRange(0, 5000)
        self.margin_spin.setValue(40)
        self.margin_spin.setSuffix(" px")
        self.margin_spin.setEnabled(False)
        self.region_check.toggled.connect(self.margin_spin.setEnabled)

        self.variant_check = QCheckBox("Als Variante speichern (name@n.png)")
        self.overwrite_check = QCheckBox("Vorhandenes Template überschreiben")
        self.variant_check.toggled.connect(self._on_variant_toggled)
        self.overwrite_check.toggled.connect(self._on_overwrite_toggled)

        self.target_label = QLabel()
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #ff6b6b;")
        self.error_label.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.save_button = buttons.addButton("Speichern", QDialogButtonBox.ButtonRole.ApplyRole)
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save)
        buttons.rejected.connect(self.reject)

        threshold_row = QHBoxLayout()
        threshold_row.addWidget(self.threshold_check)
        threshold_row.addWidget(self.threshold_spin)
        region_row = QHBoxLayout()
        region_row.addWidget(self.region_check)
        region_row.addWidget(self.margin_spin)
        form = QFormLayout()
        form.addRow("Page", self.page_combo)
        form.addRow("Name", self.name_edit)
        form.addRow(threshold_row)
        form.addRow(region_row)
        form.addRow(self.variant_check)
        form.addRow(self.overwrite_check)

        layout = QVBoxLayout(self)
        layout.addWidget(preview)
        layout.addWidget(info)
        layout.addLayout(form)
        layout.addWidget(self.target_label)
        layout.addWidget(self.error_label)
        layout.addWidget(buttons)

        self.page_combo.currentTextChanged.connect(self._update_target)
        self.name_edit.textChanged.connect(self._update_target)
        self._update_target()

    def template_name(self) -> str:
        page = self.page_combo.currentText().strip().strip("/")
        name = self.name_edit.text().strip()
        return f"{page}/{name}" if page else name

    def region(self) -> Rect | None:
        if not self.region_check.isChecked():
            return None
        m, s = self.margin_spin.value(), self.selection
        return clip(Rect(s.x - m, s.y - m, s.w + 2 * m, s.h + 2 * m), self.frame_size)

    def _on_variant_toggled(self, checked: bool) -> None:
        if checked:
            self.overwrite_check.setChecked(False)  # Variante und Überschreiben schließen sich aus

    def _on_overwrite_toggled(self, checked: bool) -> None:
        if checked:
            self.variant_check.setChecked(False)

    def _update_target(self, *_args: object) -> None:
        name = self.template_name()
        exists = bool(self.name_edit.text().strip()) and self.store.exists(name)
        self.variant_check.setEnabled(exists)
        self.overwrite_check.setEnabled(exists)
        if not exists:
            self.variant_check.setChecked(False)
            self.overwrite_check.setChecked(False)
        self.target_label.setText(f"→ {name}.png" + ("  (existiert bereits)" if exists else ""))
        self.error_label.clear()

    def _save(self) -> None:
        reference = self.frame_size if self.frame_size != self.store.reference_resolution else None
        try:
            path = self.store.save(
                self.template_name(),
                self.image,
                threshold=self.threshold_spin.value() if self.threshold_check.isChecked() else None,
                region=self.region(),
                reference_resolution=reference,
                variant=self.variant_check.isChecked(),
                overwrite=self.overwrite_check.isChecked(),
            )
        except TemplateError as e:
            self.error_label.setText(str(e))
            return
        self.saved_path = path
        self.saved.emit(self.template_name(), path)
        self.accept()
