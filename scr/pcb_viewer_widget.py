from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .pcb_viewer_model import BoardModel, BBoxData, FootprintData, GraphicPrimitive, PRIMARY_LAYER_ORDER, Rect

LAYER_COLORS = {
    "Edge.Cuts": QColor("#111111"),
    "F.Cu": QColor("#d97706"),
    "B.Cu": QColor("#0369a1"),
    "F.SilkS": QColor("#1d4ed8"),
    "B.SilkS": QColor("#7c3aed"),
    "F.Mask": QColor("#16a34a"),
    "B.Mask": QColor("#15803d"),
    "F.CrtYd": QColor("#6b7280"),
    "B.CrtYd": QColor("#4b5563"),
    "Pads": QColor("#be123c"),
    "Vias": QColor("#374151"),
    "Tracks": QColor("#92400e"),
    "Bounding Boxes": QColor("#047857"),
    "Component Centers": QColor("#db2777"),
    "References": QColor("#111827"),
    "Axes": QColor("#9ca3af"),
    "Dimensions": QColor("#4338ca"),
}

EXPORT_TOKEN_MAP = {
    "Edge.Cuts": {"Edge.Cuts"},
    "Copper": {"F.Cu", "B.Cu", "Tracks"},
    "Silkscreen": {"F.SilkS", "B.SilkS"},
    "Pads": {"Pads", "F.Mask", "B.Mask"},
    "Vias": {"Vias"},
    "Courtyard": {"F.CrtYd", "B.CrtYd"},
    "Bounding Boxes": {"Bounding Boxes", "Component Centers"},
    "References": {"References"},
}


@dataclass
class LayerWidgets:
    checkbox: QCheckBox
    slider: QSlider


@dataclass
class ItemRecord:
    item: QGraphicsItem
    layer_name: str
    side: str = "Both"
    reference: str | None = None
    bbox_source: str | None = None
    role: str = "generic"


class PCBGraphicsView(QGraphicsView):
    def __init__(self, scene: QGraphicsScene, parent: QWidget | None = None) -> None:
        super().__init__(scene, parent)
        self.setRenderHints(self.renderHints() | QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._last_pan_point: QPoint | None = None

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.MiddleButton:
            self._last_pan_point = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._last_pan_point is not None:
            delta = event.pos() - self._last_pan_point
            self._last_pan_point = event.pos()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.MiddleButton:
            self._last_pan_point = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def zoom_to_fit_rect(self, rect: QRectF) -> None:
        if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            return
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)


class PCBViewerWidget(QWidget):
    footprintSelected = Signal(str)
    visibleReferencesChanged = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model: BoardModel | None = None
        self.scene = QGraphicsScene(self)
        self.view = PCBGraphicsView(self.scene, self)
        self.layer_widgets: dict[str, LayerWidgets] = {}
        self.layer_items: dict[str, list[ItemRecord]] = {}
        self.selectable_items: dict[str, QGraphicsRectItem] = {}
        self.highlight_items: dict[str, QGraphicsRectItem] = {}
        self.warning_items: dict[str, list[QGraphicsItem]] = {}
        self.reference_items: dict[str, QGraphicsSimpleTextItem] = {}
        self.footprints_by_reference: dict[str, FootprintData] = {}
        self.visible_references: set[str] = set()
        self._suspend_selection_signal = False
        self._export_layers_override: set[str] | None = None
        self._build_ui()
        self.scene.selectionChanged.connect(self._scene_selection_changed)

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        layers_panel = QWidget(self)
        layers_layout = QVBoxLayout(layers_panel)
        preset_row = QHBoxLayout()
        top_button = QPushButton("Top")
        bottom_button = QPushButton("Bottom")
        all_button = QPushButton("All")
        top_button.clicked.connect(lambda: self.apply_side_preset("Top"))
        bottom_button.clicked.connect(lambda: self.apply_side_preset("Bottom"))
        all_button.clicked.connect(lambda: self.apply_side_preset("All"))
        preset_row.addWidget(top_button)
        preset_row.addWidget(bottom_button)
        preset_row.addWidget(all_button)
        layers_layout.addLayout(preset_row)

        self.layers_container = QWidget(self)
        self.layers_form = QFormLayout(self.layers_container)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.layers_container)
        layers_layout.addWidget(QLabel("Capas"))
        layers_layout.addWidget(scroll)
        splitter.addWidget(layers_panel)

        center_panel = QWidget(self)
        center_layout = QVBoxLayout(center_panel)
        toolbar = QHBoxLayout()
        zoom_fit_button = QPushButton("Ajustar a vista")
        reset_button = QPushButton("Restablecer vista")
        center_button = QPushButton("Centrar selección")
        zoom_fit_button.clicked.connect(self.zoom_to_fit)
        reset_button.clicked.connect(self.reset_view)
        center_button.clicked.connect(self.center_on_selection)
        toolbar.addWidget(zoom_fit_button)
        toolbar.addWidget(reset_button)
        toolbar.addWidget(center_button)
        toolbar.addStretch(1)
        center_layout.addLayout(toolbar)

        toggles = QHBoxLayout()
        self.show_bboxes_cb = QCheckBox("Mostrar bounding boxes")
        self.show_bboxes_cb.setChecked(True)
        self.show_centers_cb = QCheckBox("Mostrar centros")
        self.show_refs_cb = QCheckBox("Mostrar referencias")
        self.show_refs_cb.setChecked(True)
        self.show_courtyards_cb = QCheckBox("Mostrar courtyards")
        self.show_courtyards_cb.setChecked(True)
        self.show_pads_cb = QCheckBox("Mostrar pads")
        self.show_pads_cb.setChecked(True)
        self.validate_mode_cb = QCheckBox("Validar bounding boxes")
        self.show_axes_cb = QCheckBox("Mostrar origen / ejes")
        self.show_dimensions_cb = QCheckBox("Mostrar dimensiones")
        for checkbox in (
            self.show_bboxes_cb,
            self.show_centers_cb,
            self.show_refs_cb,
            self.show_courtyards_cb,
            self.show_pads_cb,
            self.validate_mode_cb,
            self.show_axes_cb,
            self.show_dimensions_cb,
        ):
            checkbox.toggled.connect(self._refresh_visibility)
            toggles.addWidget(checkbox)
        toggles.addStretch(1)
        center_layout.addLayout(toggles)
        center_layout.addWidget(self.view)
        splitter.addWidget(center_panel)

        right_panel = QWidget(self)
        right_layout = QVBoxLayout(right_panel)

        filters_box = QGroupBox("Filtros", self)
        filters_layout = QFormLayout(filters_box)
        self.reference_filter = QLineEdit()
        self.value_filter = QLineEdit()
        self.side_filter = QComboBox()
        self.side_filter.addItems(["All", "Top", "Bottom"])
        self.bbox_source_filter = QComboBox()
        self.bbox_source_filter.addItems(["All", "courtyard", "pads", "missing"])
        self.bbox_ok_filter = QComboBox()
        self.bbox_ok_filter.addItems(["All", "OK", "Issues"])
        for widget in (
            self.reference_filter,
            self.value_filter,
            self.side_filter,
            self.bbox_source_filter,
            self.bbox_ok_filter,
        ):
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._refresh_visibility)
            else:
                widget.currentTextChanged.connect(self._refresh_visibility)
        filters_layout.addRow("Referencia", self.reference_filter)
        filters_layout.addRow("Valor", self.value_filter)
        filters_layout.addRow("Cara", self.side_filter)
        filters_layout.addRow("bbox_source", self.bbox_source_filter)
        filters_layout.addRow("bbox_ok", self.bbox_ok_filter)
        quick_row1 = QHBoxLayout()
        only_courtyard = QPushButton("Solo courtyard")
        only_repaired = QPushButton("Solo reparados")
        only_missing = QPushButton("Solo sin bbox")
        only_courtyard.clicked.connect(lambda: self._set_quick_bbox_filter("courtyard"))
        only_repaired.clicked.connect(lambda: self._set_quick_bbox_filter("pads"))
        only_missing.clicked.connect(lambda: self._set_quick_bbox_filter("missing"))
        quick_row1.addWidget(only_courtyard)
        quick_row1.addWidget(only_repaired)
        quick_row1.addWidget(only_missing)
        filters_layout.addRow("", self._wrap_layout(quick_row1))
        quick_row2 = QHBoxLayout()
        filter_top = QPushButton("Top")
        filter_bottom = QPushButton("Bottom")
        filter_all = QPushButton("All")
        filter_top.clicked.connect(lambda: self.side_filter.setCurrentText("Top"))
        filter_bottom.clicked.connect(lambda: self.side_filter.setCurrentText("Bottom"))
        filter_all.clicked.connect(lambda: self.side_filter.setCurrentText("All"))
        quick_row2.addWidget(filter_top)
        quick_row2.addWidget(filter_bottom)
        quick_row2.addWidget(filter_all)
        filters_layout.addRow("", self._wrap_layout(quick_row2))
        right_layout.addWidget(filters_box)

        self.inspector = QListWidget(self)
        self.inspector.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        inspector_box = QGroupBox("Inspector", self)
        inspector_layout = QVBoxLayout(inspector_box)
        inspector_layout.addWidget(self.inspector)
        right_layout.addWidget(inspector_box)

        self.warning_list = QListWidget(self)
        self.warning_list.itemClicked.connect(self._warning_clicked)
        warnings_box = QGroupBox("Avisos", self)
        warnings_layout = QVBoxLayout(warnings_box)
        warnings_layout.addWidget(self.warning_list)
        right_layout.addWidget(warnings_box)
        splitter.addWidget(right_panel)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

    @staticmethod
    def _wrap_layout(layout) -> QWidget:
        widget = QWidget()
        widget.setLayout(layout)
        return widget

    def load_model(self, model: BoardModel) -> None:
        self.model = model
        self.scene.clear()
        self.layer_items.clear()
        self.selectable_items.clear()
        self.highlight_items.clear()
        self.warning_items.clear()
        self.reference_items.clear()
        self.footprints_by_reference = {
            footprint.reference: footprint for footprint in model.footprints if footprint.reference
        }
        self._rebuild_layer_controls(model.layer_names)
        self._build_scene(model)
        self._populate_warnings()
        self._populate_inspector(None)
        self._refresh_visibility()
        self.zoom_to_fit()

    def _rebuild_layer_controls(self, layer_names: list[str]) -> None:
        while self.layers_form.rowCount():
            self.layers_form.removeRow(0)
        self.layer_widgets.clear()
        ordered_layers = list(dict.fromkeys(PRIMARY_LAYER_ORDER + layer_names))
        for layer_name in ordered_layers:
            checkbox = QCheckBox(layer_name)
            checkbox.setChecked(layer_name in PRIMARY_LAYER_ORDER)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(10, 100)
            slider.setValue(100 if layer_name == "Edge.Cuts" else 85)
            checkbox.toggled.connect(self._refresh_visibility)
            slider.valueChanged.connect(self._refresh_visibility)
            row = QHBoxLayout()
            row.addWidget(checkbox)
            row.addWidget(slider)
            self.layers_form.addRow(self._wrap_layout(row))
            self.layer_widgets[layer_name] = LayerWidgets(checkbox, slider)

    def _build_scene(self, model: BoardModel) -> None:
        dims = model.dimensions
        margin_x = max(dims.width * 0.05, 5.0)
        margin_y = max(dims.height * 0.05, 5.0)
        self.scene.setSceneRect(
            QRectF(
                dims.min_x - margin_x,
                dims.min_y - margin_y,
                dims.width + margin_x * 2,
                dims.height + margin_y * 2,
            )
        )
        for layer_name, primitives in model.board_layers.items():
            for primitive in primitives:
                self._add_primitive_item(primitive, layer_name)
        for track in model.tracks:
            self._add_primitive_item(track, "Tracks")
        for via in model.vias:
            self._add_pad_item(via, "Vias", None, via.side)
        self._add_axes_and_dimensions(model)
        for footprint in model.footprints:
            self._add_footprint_items(footprint)

    def _add_axes_and_dimensions(self, model: BoardModel) -> None:
        dims = model.dimensions
        axis_pen = QPen(LAYER_COLORS["Axes"])
        axis_pen.setWidthF(0)
        x_axis = self.scene.addLine(dims.min_x, 0, dims.max_x, 0, axis_pen)
        y_axis = self.scene.addLine(0, dims.min_y, 0, dims.max_y, axis_pen)
        self._register_item(x_axis, "Axes", role="axes")
        self._register_item(y_axis, "Axes", role="axes")

        dim_pen = QPen(LAYER_COLORS["Dimensions"])
        dim_pen.setStyle(Qt.PenStyle.DashLine)
        dim_pen.setWidthF(0)
        top_line = self.scene.addLine(dims.min_x, dims.min_y - 2, dims.max_x, dims.min_y - 2, dim_pen)
        left_line = self.scene.addLine(dims.min_x - 2, dims.min_y, dims.min_x - 2, dims.max_y, dim_pen)
        self._register_item(top_line, "Dimensions", role="dimensions")
        self._register_item(left_line, "Dimensions", role="dimensions")

        font = QFont()
        font.setPointSize(9)
        width_text = QGraphicsSimpleTextItem(f"W {dims.width:.3f} mm")
        width_text.setFont(font)
        width_text.setPos(dims.center_x, dims.min_y - 4)
        width_text.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.scene.addItem(width_text)
        self._register_item(width_text, "Dimensions", role="dimensions")

        height_text = QGraphicsSimpleTextItem(f"H {dims.height:.3f} mm")
        height_text.setFont(font)
        height_text.setPos(dims.min_x - 6, dims.center_y)
        height_text.setRotation(-90)
        height_text.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.scene.addItem(height_text)
        self._register_item(height_text, "Dimensions", role="dimensions")

    def _add_footprint_items(self, footprint: FootprintData) -> None:
        for primitive in footprint.copper:
            self._add_primitive_item(primitive, primitive.layer, footprint.reference, footprint.side)
        for primitive in footprint.silkscreen:
            self._add_primitive_item(primitive, primitive.layer, footprint.reference, footprint.side)
        for primitive in footprint.courtyard:
            self._add_primitive_item(primitive, primitive.layer, footprint.reference, footprint.side, role="courtyard")
        for primitive in footprint.mask:
            self._add_primitive_item(primitive, primitive.layer, footprint.reference, footprint.side)
        for primitive in footprint.other_layers:
            self._add_primitive_item(primitive, primitive.layer, footprint.reference, footprint.side)
        for pad in footprint.pads:
            self._add_pad_item(pad, "Pads", footprint.reference, footprint.side)
        self._add_bbox_item(footprint)
        self._add_center_item(footprint)
        self._add_reference_item(footprint)

    def _primitive_pen(self, layer_name: str, width: float | None = None) -> QPen:
        pen = QPen(LAYER_COLORS.get(layer_name, QColor("#6b7280")))
        pen.setCosmetic(True)
        pen.setWidthF(1.0 if width is None else max(width, 0.05))
        return pen

    def _add_primitive_item(
        self,
        primitive: GraphicPrimitive,
        layer_name: str,
        reference: str | None = None,
        side: str = "Both",
        role: str = "primitive",
    ) -> None:
        item: QGraphicsItem | None = None
        pen = self._primitive_pen(layer_name, primitive.width)
        if primitive.kind == "line" and len(primitive.points) >= 2:
            start, end = primitive.points[:2]
            item = QGraphicsLineItem(start[0], start[1], end[0], end[1])
            item.setPen(pen)
        elif primitive.kind == "circle" and len(primitive.points) >= 2:
            center, edge = primitive.points[:2]
            radius = math.dist(center, edge)
            item = QGraphicsEllipseItem(center[0] - radius, center[1] - radius, radius * 2, radius * 2)
            item.setPen(pen)
        elif primitive.kind == "arc" and len(primitive.points) >= 3:
            path = self._arc_path(primitive.points[0], primitive.points[1], primitive.points[2])
            arc_item = QGraphicsPathItem(path)
            arc_item.setPen(pen)
            item = arc_item
        elif primitive.kind == "poly" and primitive.points:
            polygon = QPolygonF([QPointF(point[0], point[1]) for point in primitive.points])
            poly_item = QGraphicsPolygonItem(polygon)
            poly_item.setPen(pen)
            poly_item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            item = poly_item
        if item is None:
            return
        self.scene.addItem(item)
        self._register_item(item, layer_name, side=side, reference=reference, role=role)

    @staticmethod
    def _arc_center(start: tuple[float, float], mid: tuple[float, float], end: tuple[float, float]) -> tuple[float, float] | None:
        x1, y1 = start
        x2, y2 = mid
        x3, y3 = end
        determinant = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
        if abs(determinant) < 1e-12:
            return None
        ux = ((x1 * x1 + y1 * y1) * (y2 - y3) + (x2 * x2 + y2 * y2) * (y3 - y1) + (x3 * x3 + y3 * y3) * (y1 - y2)) / determinant
        uy = ((x1 * x1 + y1 * y1) * (x3 - x2) + (x2 * x2 + y2 * y2) * (x1 - x3) + (x3 * x3 + y3 * y3) * (x2 - x1)) / determinant
        return ux, uy

    @staticmethod
    def _normalize_angle(angle: float) -> float:
        return angle % (2 * math.pi)

    @classmethod
    def _is_between_ccw(cls, start: float, end: float, angle: float) -> bool:
        start = cls._normalize_angle(start)
        end = cls._normalize_angle(end)
        angle = cls._normalize_angle(angle)
        if start <= end:
            return start <= angle <= end
        return angle >= start or angle <= end

    @classmethod
    def _arc_path(cls, start: tuple[float, float], mid: tuple[float, float], end: tuple[float, float]) -> QPainterPath:
        path = QPainterPath()
        path.moveTo(start[0], start[1])
        center = cls._arc_center(start, mid, end)
        if center is None:
            path.lineTo(end[0], end[1])
            return path
        radius = math.dist(center, start)
        start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
        mid_angle = math.atan2(mid[1] - center[1], mid[0] - center[0])
        end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
        ccw = cls._is_between_ccw(start_angle, end_angle, mid_angle)
        if ccw:
            span = (end_angle - start_angle) % (2 * math.pi)
        else:
            span = -((start_angle - end_angle) % (2 * math.pi))
        segments = max(12, int(abs(span) / (math.pi / 24)))
        for step in range(1, segments + 1):
            angle = start_angle + span * (step / segments)
            path.lineTo(center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle))
        return path

    def _add_pad_item(self, pad, layer_name: str, reference: str | None, side: str) -> None:
        rect = pad.board_rect
        pen = self._primitive_pen(layer_name, 0.1)
        brush_color = QColor(LAYER_COLORS.get(layer_name, QColor("#be123c")))
        brush_color.setAlpha(50 if layer_name == "Pads" else 90)
        if pad.shape in {"circle", "oval"}:
            item = QGraphicsEllipseItem(rect.min_x, rect.min_y, rect.width, rect.height)
        else:
            item = QGraphicsRectItem(rect.min_x, rect.min_y, rect.width, rect.height)
        item.setPen(pen)
        item.setBrush(brush_color)
        self.scene.addItem(item)
        self._register_item(item, layer_name, side=side, reference=reference, role="pad")

    def _add_bbox_item(self, footprint: FootprintData) -> None:
        bbox = footprint.bbox
        if bbox is None:
            size = 1.2
            triangle = QPolygonF(
                [
                    QPointF(footprint.position[0], footprint.position[1] - size),
                    QPointF(footprint.position[0] - size, footprint.position[1] + size),
                    QPointF(footprint.position[0] + size, footprint.position[1] + size),
                ]
            )
            triangle_item = QGraphicsPolygonItem(triangle)
            triangle_item.setPen(QPen(QColor("#dc2626"), 0))
            triangle_item.setBrush(QColor("#fca5a5"))
            self.scene.addItem(triangle_item)
            self.warning_items.setdefault(footprint.reference or footprint.name, []).append(triangle_item)
            self._register_item(triangle_item, "Bounding Boxes", side=footprint.side, reference=footprint.reference, role="warning")
            select_rect = QGraphicsRectItem(footprint.position[0] - 1.5, footprint.position[1] - 1.5, 3.0, 3.0)
        else:
            pen = QPen(QColor("#0f766e" if bbox.source == "courtyard" else "#b45309"), 0)
            pen.setStyle(Qt.PenStyle.SolidLine if bbox.source == "courtyard" else Qt.PenStyle.DashLine)
            bbox_item = QGraphicsRectItem(bbox.rect.min_x, bbox.rect.min_y, bbox.rect.width, bbox.rect.height)
            bbox_item.setPen(pen)
            self.scene.addItem(bbox_item)
            self._register_item(bbox_item, "Bounding Boxes", side=footprint.side, reference=footprint.reference, bbox_source=bbox.source, role="bbox")
            highlight = QGraphicsRectItem(bbox.rect.min_x, bbox.rect.min_y, bbox.rect.width, bbox.rect.height)
            highlight_pen = QPen(QColor("#2563eb"), 0)
            highlight_pen.setWidthF(0.25)
            highlight.setPen(highlight_pen)
            highlight.setBrush(Qt.BrushStyle.NoBrush)
            highlight.setVisible(False)
            self.scene.addItem(highlight)
            if footprint.reference:
                self.highlight_items[footprint.reference] = highlight
            self._register_item(highlight, "Bounding Boxes", side=footprint.side, reference=footprint.reference, role="highlight")
            if bbox.original_rect is not None:
                original = bbox.original_rect
                original_item = QGraphicsRectItem(original.min_x, original.min_y, original.width, original.height)
                original_pen = QPen(QColor("#f59e0b"), 0)
                original_pen.setStyle(Qt.PenStyle.DotLine)
                original_item.setPen(original_pen)
                original_item.setVisible(False)
                self.scene.addItem(original_item)
                self._register_item(original_item, "Bounding Boxes", side=footprint.side, reference=footprint.reference, role="pads-original-bbox")
            select_rect = QGraphicsRectItem(bbox.rect.min_x, bbox.rect.min_y, bbox.rect.width, bbox.rect.height)

        no_pen = QPen()
        no_pen.setStyle(Qt.PenStyle.NoPen)
        select_rect.setPen(no_pen)
        select_rect.setBrush(QColor(0, 0, 0, 0))
        select_rect.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        select_rect.setToolTip(self._tooltip_for_footprint(footprint))
        self.scene.addItem(select_rect)
        if footprint.reference:
            self.selectable_items[footprint.reference] = select_rect
        self._register_item(select_rect, "Bounding Boxes", side=footprint.side, reference=footprint.reference, bbox_source=footprint.bbox.source if footprint.bbox else None, role="selector")

    def _add_center_item(self, footprint: FootprintData) -> None:
        x, y = footprint.position
        pen = QPen(LAYER_COLORS["Component Centers"])
        pen.setWidthF(0)
        item1 = self.scene.addLine(x - 0.5, y, x + 0.5, y, pen)
        item2 = self.scene.addLine(x, y - 0.5, x, y + 0.5, pen)
        self._register_item(item1, "Component Centers", side=footprint.side, reference=footprint.reference, role="center")
        self._register_item(item2, "Component Centers", side=footprint.side, reference=footprint.reference, role="center")

    def _add_reference_item(self, footprint: FootprintData) -> None:
        label = footprint.reference or footprint.name
        text_item = QGraphicsSimpleTextItem(label)
        text_item.setBrush(LAYER_COLORS["References"])
        font = QFont()
        font.setPointSize(9)
        text_item.setFont(font)
        target_x = footprint.bbox.center_x if footprint.bbox else footprint.position[0]
        target_y = footprint.bbox.center_y if footprint.bbox else footprint.position[1]
        text_item.setPos(target_x, target_y)
        text_item.setToolTip(self._tooltip_for_footprint(footprint))
        text_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.scene.addItem(text_item)
        if footprint.reference:
            self.reference_items[footprint.reference] = text_item
        self._register_item(text_item, "References", side=footprint.side, reference=footprint.reference, role="reference")

    def _register_item(
        self,
        item: QGraphicsItem,
        layer_name: str,
        side: str = "Both",
        reference: str | None = None,
        bbox_source: str | None = None,
        role: str = "generic",
    ) -> None:
        self.layer_items.setdefault(layer_name, []).append(
            ItemRecord(item=item, layer_name=layer_name, side=side, reference=reference, bbox_source=bbox_source, role=role)
        )

    def _tooltip_for_footprint(self, footprint: FootprintData) -> str:
        source = footprint.bbox.source_label if footprint.bbox else "missing"
        return (
            f"{footprint.reference or footprint.name}\n"
            f"Value: {footprint.value or '-'}\n"
            f"Side: {footprint.side}\n"
            f"Rotation: {footprint.rotation:.2f}°\n"
            f"BBox source: {source}\n"
            f"Warnings: {len(footprint.warnings_short)}"
        )

    def _scene_selection_changed(self) -> None:
        if self._suspend_selection_signal:
            return
        selected_items = self.scene.selectedItems()
        reference = None
        for item in selected_items:
            for ref, selectable in self.selectable_items.items():
                if item is selectable:
                    reference = ref
                    break
            if reference:
                break
        self._set_selected_reference(reference, emit_signal=True, center=False)

    def _set_selected_reference(self, reference: str | None, emit_signal: bool, center: bool) -> None:
        for ref, item in self.highlight_items.items():
            item.setVisible(ref == reference)
        if reference and center:
            self.center_on_reference(reference)
        self._populate_inspector(reference)
        if emit_signal and reference:
            self.footprintSelected.emit(reference)

    def _populate_inspector(self, reference: str | None) -> None:
        self.inspector.clear()
        if not reference or reference not in self.footprints_by_reference:
            self.inspector.addItem("Selecciona un componente.")
            return
        footprint = self.footprints_by_reference[reference]
        bbox = footprint.bbox
        self.inspector.addItems(
            [
                f"Reference: {footprint.reference or '-'}",
                f"Value: {footprint.value or '-'}",
                f"Cara: {footprint.side}",
                f"Posición X: {footprint.position[0]:.3f} mm",
                f"Posición Y: {footprint.position[1]:.3f} mm",
                f"Rotación: {footprint.rotation:.2f}°",
                f"BBox ancho: {bbox.width:.3f} mm" if bbox else "BBox ancho: -",
                f"BBox alto: {bbox.height:.3f} mm" if bbox else "BBox alto: -",
                f"Origen bbox: {bbox.source_label}" if bbox else "Origen bbox: missing",
                f"Factor de inflado: {bbox.inflation_factor:.3f}x" if bbox and bbox.inflation_factor else "Factor de inflado: -",
                f"Número de pads usados: {bbox.pad_count}" if bbox else f"Número de pads usados: {len(footprint.pads)}",
                f"YOLO X centro: {footprint.yolo['x_center']:.6f}" if footprint.yolo else "YOLO X centro: -",
                f"YOLO Y centro: {footprint.yolo['y_center']:.6f}" if footprint.yolo else "YOLO Y centro: -",
                f"YOLO ancho: {footprint.yolo['width']:.6f}" if footprint.yolo else "YOLO ancho: -",
                f"YOLO alto: {footprint.yolo['height']:.6f}" if footprint.yolo else "YOLO alto: -",
            ]
        )
        if footprint.warnings:
            self.inspector.addItem("Avisos:")
            for warning in footprint.warnings:
                self.inspector.addItem(f"- {warning}")

    def _populate_warnings(self) -> None:
        self.warning_list.clear()
        if not self.model:
            return
        added = False
        for footprint in self.model.footprints:
            for warning in footprint.warnings:
                item = QListWidgetItem(warning)
                item.setData(Qt.ItemDataRole.UserRole, footprint.reference)
                self.warning_list.addItem(item)
                added = True
        if not added:
            self.warning_list.addItem(QListWidgetItem("Sin avisos."))

    def _warning_clicked(self, item: QListWidgetItem) -> None:
        reference = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(reference, str) and reference:
            self.select_reference(reference, center=True)

    def _set_quick_bbox_filter(self, value: str) -> None:
        self.bbox_source_filter.setCurrentText(value)

    def _footprint_matches_filters(self, footprint: FootprintData) -> bool:
        ref_text = self.reference_filter.text().strip().lower()
        reference = (footprint.reference or "").lower()
        if ref_text and ref_text not in reference:
            return False
        value_text = self.value_filter.text().strip().lower()
        value = (footprint.value or "").lower()
        if value_text and value_text not in value:
            return False
        side_text = self.side_filter.currentText()
        if side_text != "All" and footprint.side != side_text:
            return False
        bbox_source = self.bbox_source_filter.currentText()
        actual_source = footprint.bbox.source if footprint.bbox else "missing"
        if bbox_source != "All" and actual_source != bbox_source:
            return False
        bbox_ok = self.bbox_ok_filter.currentText()
        if bbox_ok == "OK" and not footprint.bbox_ok:
            return False
        if bbox_ok == "Issues" and footprint.bbox_ok:
            return False
        return True

    def _layer_enabled(self, layer_name: str) -> bool:
        widgets = self.layer_widgets.get(layer_name)
        if not widgets:
            return False
        if self._export_layers_override is not None:
            return layer_name in self._export_layers_override
        return widgets.checkbox.isChecked()

    def _side_allowed_for_record(self, record: ItemRecord) -> bool:
        if self._export_layers_override is not None:
            return True
        if record.reference:
            return record.reference in self.visible_references
        side_filter = self.side_filter.currentText()
        if side_filter == "All":
            return True
        if record.side == "Both":
            return True
        return record.side == side_filter

    def _record_visible(self, record: ItemRecord) -> bool:
        if not self._layer_enabled(record.layer_name):
            return False
        if record.role == "bbox":
            if not (self.show_bboxes_cb.isChecked() or self.validate_mode_cb.isChecked()):
                return False
        if record.role == "pads-original-bbox" and not self.validate_mode_cb.isChecked():
            return False
        if record.role == "center" and not self.show_centers_cb.isChecked():
            return False
        if record.role == "reference" and not self.show_refs_cb.isChecked():
            return False
        if record.role == "courtyard" and not (self.show_courtyards_cb.isChecked() or self.validate_mode_cb.isChecked()):
            return False
        if record.role == "pad" and not (self.show_pads_cb.isChecked() or self.validate_mode_cb.isChecked()):
            return False
        if record.layer_name == "Axes" and not self.show_axes_cb.isChecked():
            return False
        if record.layer_name == "Dimensions" and not self.show_dimensions_cb.isChecked():
            return False
        return self._side_allowed_for_record(record)

    def _refresh_visibility(self) -> None:
        self.visible_references = set()
        if self.model:
            self.visible_references = {
                footprint.reference
                for footprint in self.model.footprints
                if footprint.reference and self._footprint_matches_filters(footprint)
            }
        selected_reference = self.current_reference()
        if selected_reference and selected_reference not in self.visible_references:
            selected_reference = None
            self._suspend_selection_signal = True
            self.scene.clearSelection()
            self._suspend_selection_signal = False
        for layer_name, records in self.layer_items.items():
            opacity = (self.layer_widgets.get(layer_name).slider.value() / 100.0) if layer_name in self.layer_widgets else 1.0
            for record in records:
                visible = self._record_visible(record)
                if record.role == "highlight" and record.reference != selected_reference:
                    visible = False
                record.item.setVisible(visible)
                record.item.setOpacity(1.0 if record.role == "selector" else opacity)
        self._populate_inspector(selected_reference)
        self.visibleReferencesChanged.emit(sorted(self.visible_references))

    def has_active_filters(self) -> bool:
        return any(
            (
                bool(self.reference_filter.text().strip()),
                bool(self.value_filter.text().strip()),
                self.side_filter.currentText() != "All",
                self.bbox_source_filter.currentText() != "All",
                self.bbox_ok_filter.currentText() != "All",
            )
        )

    def current_reference(self) -> str | None:
        for ref, item in self.highlight_items.items():
            if item.isVisible():
                return ref
        return None

    def select_reference(self, reference: str, center: bool = False) -> None:
        if reference not in self.selectable_items:
            self._set_selected_reference(None, emit_signal=False, center=False)
            return
        self._suspend_selection_signal = True
        self.scene.clearSelection()
        self.selectable_items[reference].setSelected(True)
        self._suspend_selection_signal = False
        self._set_selected_reference(reference, emit_signal=False, center=center)

    def center_on_reference(self, reference: str) -> None:
        if reference in self.highlight_items:
            self.view.centerOn(self.highlight_items[reference])
        elif reference in self.selectable_items:
            self.view.centerOn(self.selectable_items[reference])

    def center_on_selection(self) -> None:
        reference = self.current_reference()
        if reference:
            self.center_on_reference(reference)

    def zoom_to_fit(self) -> None:
        if not self.model:
            return
        dims = self.model.dimensions
        self.view.zoom_to_fit_rect(QRectF(dims.min_x, dims.min_y, dims.width, dims.height))

    def reset_view(self) -> None:
        self.view.resetTransform()
        self.zoom_to_fit()

    def apply_side_preset(self, side: str) -> None:
        front_layers = {"F.Cu", "F.SilkS", "F.Mask", "F.CrtYd"}
        back_layers = {"B.Cu", "B.SilkS", "B.Mask", "B.CrtYd"}
        for layer_name, widgets in self.layer_widgets.items():
            if side == "All":
                widgets.checkbox.setChecked(True)
            elif layer_name in front_layers:
                widgets.checkbox.setChecked(side != "Bottom")
            elif layer_name in back_layers:
                widgets.checkbox.setChecked(side != "Top")
            else:
                widgets.checkbox.setChecked(True)
        self.side_filter.setCurrentText(side if side in {"Top", "Bottom"} else "All")

    def board_rect(self) -> QRectF:
        if not self.model:
            return QRectF()
        dims = self.model.dimensions
        return QRectF(dims.min_x, dims.min_y, dims.width, dims.height)

    def capture_visibility_state(self) -> dict:
        return {
            "layers": {name: widgets.checkbox.isChecked() for name, widgets in self.layer_widgets.items()},
            "refs": self.show_refs_cb.isChecked(),
            "bboxes": self.show_bboxes_cb.isChecked(),
            "pads": self.show_pads_cb.isChecked(),
            "courtyards": self.show_courtyards_cb.isChecked(),
            "centers": self.show_centers_cb.isChecked(),
            "axes": self.show_axes_cb.isChecked(),
            "dimensions": self.show_dimensions_cb.isChecked(),
            "validate": self.validate_mode_cb.isChecked(),
            "selection": self.current_reference(),
        }

    def restore_visibility_state(self, state: dict) -> None:
        self._export_layers_override = None
        for name, checked in state.get("layers", {}).items():
            if name in self.layer_widgets:
                self.layer_widgets[name].checkbox.setChecked(checked)
        self.show_refs_cb.setChecked(state.get("refs", False))
        self.show_bboxes_cb.setChecked(state.get("bboxes", True))
        self.show_pads_cb.setChecked(state.get("pads", True))
        self.show_courtyards_cb.setChecked(state.get("courtyards", True))
        self.show_centers_cb.setChecked(state.get("centers", False))
        self.show_axes_cb.setChecked(state.get("axes", False))
        self.show_dimensions_cb.setChecked(state.get("dimensions", False))
        self.validate_mode_cb.setChecked(state.get("validate", False))
        self._refresh_visibility()
        selection = state.get("selection")
        if isinstance(selection, str) and selection:
            self.select_reference(selection)

    def apply_export_tokens(self, tokens: set[str]) -> None:
        layers: set[str] = set()
        export_tokens = tokens or set(EXPORT_TOKEN_MAP)
        for token in export_tokens:
            layers.update(EXPORT_TOKEN_MAP.get(token, {token}))
        self._export_layers_override = layers
        self._refresh_visibility()
