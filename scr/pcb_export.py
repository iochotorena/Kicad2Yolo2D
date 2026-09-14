from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRectF, QSize
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QDoubleSpinBox,
    QVBoxLayout,
)

from .pcb_viewer_model import BoardModel

EXPORT_PRESETS = {
    "Custom": None,
    "1024 × auto": {"width_px": 1024},
    "2048 × auto": {"width_px": 2048},
    "4096 × auto": {"width_px": 4096},
    "10 px/mm": {"px_per_mm": 10.0},
    "20 px/mm": {"px_per_mm": 20.0},
    "50 px/mm": {"px_per_mm": 50.0},
    "YOLO Dataset": {"width_px": 2048, "layers": {"Edge.Cuts", "Copper", "Pads", "Vias"}},
}


@dataclass
class RasterExportOptions:
    width_px: int
    height_px: int
    include_tokens: set[str]


@dataclass
class VectorExportOptions:
    include_tokens: set[str]


class RasterExportDialog(QDialog):
    def __init__(self, model: BoardModel, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export raster image")
        self.model = model
        self._build_ui()
        self._apply_preset("Custom")
        self._update_size_preview()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.preset_combo = QComboBox(self)
        self.preset_combo.addItems(EXPORT_PRESETS.keys())
        self.preset_combo.currentTextChanged.connect(self._apply_preset)
        form.addRow("Preset", self.preset_combo)

        self.width_spin = QSpinBox(self)
        self.width_spin.setRange(1, 20000)
        self.width_spin.setValue(2048)
        self.width_spin.valueChanged.connect(self._update_size_preview)
        form.addRow("Width px", self.width_spin)

        self.height_spin = QSpinBox(self)
        self.height_spin.setRange(0, 20000)
        self.height_spin.setValue(0)
        self.height_spin.valueChanged.connect(self._update_size_preview)
        form.addRow("Height px (0=auto)", self.height_spin)

        self.ppmm_spin = QDoubleSpinBox(self)
        self.ppmm_spin.setRange(0.0, 500.0)
        self.ppmm_spin.setDecimals(2)
        self.ppmm_spin.setValue(0.0)
        self.ppmm_spin.valueChanged.connect(self._update_size_preview)
        form.addRow("Pixels per mm (0=off)", self.ppmm_spin)

        self.keep_ratio = QCheckBox(self)
        self.keep_ratio.setChecked(True)
        self.keep_ratio.toggled.connect(self._update_size_preview)
        form.addRow("Keep aspect ratio", self.keep_ratio)

        self.size_preview = QLabel(self)
        form.addRow("Resolved size", self.size_preview)
        layout.addLayout(form)

        layers_box = QGroupBox("Layers", self)
        layers_layout = QGridLayout(layers_box)
        self.layer_checks: dict[str, QCheckBox] = {}
        for index, token in enumerate(("Edge.Cuts", "Copper", "Silkscreen", "Pads", "Vias", "Courtyard", "Bounding Boxes", "References")):
            checkbox = QCheckBox(token, self)
            checkbox.setChecked(token in {"Edge.Cuts", "Copper", "Silkscreen", "Pads", "Vias"})
            self.layer_checks[token] = checkbox
            layers_layout.addWidget(checkbox, index // 2, index % 2)
        layout.addWidget(layers_box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply_preset(self, preset_name: str) -> None:
        config = EXPORT_PRESETS.get(preset_name)
        if not config:
            return
        if "width_px" in config:
            self.width_spin.setValue(config["width_px"])
            self.height_spin.setValue(0)
            self.ppmm_spin.setValue(0.0)
        if "px_per_mm" in config:
            self.ppmm_spin.setValue(config["px_per_mm"])
        layers = config.get("layers")
        if layers:
            for token, checkbox in self.layer_checks.items():
                checkbox.setChecked(token in layers)
        self._update_size_preview()

    def _resolved_size(self) -> tuple[int, int]:
        dims = self.model.dimensions
        if self.ppmm_spin.value() > 0:
            width = max(1, round(dims.width * self.ppmm_spin.value()))
            height = max(1, round(dims.height * self.ppmm_spin.value()))
            return width, height
        width = self.width_spin.value()
        height = self.height_spin.value()
        if self.keep_ratio.isChecked() or height <= 0:
            height = max(1, round(width * dims.height / dims.width))
        return width, max(1, height)

    def _update_size_preview(self) -> None:
        width, height = self._resolved_size()
        self.size_preview.setText(f"{width} × {height} px")

    def options(self) -> RasterExportOptions:
        width, height = self._resolved_size()
        include_tokens = {token for token, checkbox in self.layer_checks.items() if checkbox.isChecked()}
        return RasterExportOptions(width_px=width, height_px=height, include_tokens=include_tokens)


class VectorExportDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export vector image")
        layout = QVBoxLayout(self)
        box = QGroupBox("Layers", self)
        grid = QGridLayout(box)
        self.layer_checks: dict[str, QCheckBox] = {}
        for index, token in enumerate(("Edge.Cuts", "Copper", "Silkscreen", "Pads", "Vias", "Courtyard", "Bounding Boxes", "References")):
            checkbox = QCheckBox(token, self)
            checkbox.setChecked(token in {"Edge.Cuts", "Copper", "Silkscreen", "Pads", "Vias"})
            self.layer_checks[token] = checkbox
            grid.addWidget(checkbox, index // 2, index % 2)
        layout.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self) -> VectorExportOptions:
        include_tokens = {token for token, checkbox in self.layer_checks.items() if checkbox.isChecked()}
        return VectorExportOptions(include_tokens=include_tokens)


def export_scene_to_raster(scene, source_rect, destination: str | Path, width_px: int, height_px: int, image_format: str) -> Path:
    path = Path(destination)
    image = QImage(QSize(width_px, height_px), QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    scene.render(painter, QRectF(0, 0, width_px, height_px), source_rect)
    painter.end()
    if not image.save(str(path), image_format.upper()):
        raise ValueError(f"No se pudo guardar la imagen raster en {path}")
    return path


def export_scene_to_svg(scene, source_rect, destination: str | Path, model: BoardModel) -> Path:
    path = Path(destination)
    generator = QSvgGenerator()
    generator.setFileName(str(path))
    generator.setViewBox(source_rect)
    canvas_size = QSize(max(1, round(model.dimensions.width * 20)), max(1, round(model.dimensions.height * 20)))
    generator.setSize(canvas_size)
    generator.setTitle(path.name)
    generator.setDescription(f"PCB export for {model.pcb_path.name}")
    painter = QPainter(generator)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    scene.render(painter, QRectF(0, 0, canvas_size.width(), canvas_size.height()), source_rect)
    painter.end()
    return path
