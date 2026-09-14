from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .kicad_extract import ProcessingResult
from .kicad_parser import PCBDimensions, rotate_point

PAD_REPAIR_MARGIN_MM = 0.5

PRIMARY_LAYER_ORDER = [
    "Edge.Cuts",
    "F.Cu",
    "B.Cu",
    "F.SilkS",
    "B.SilkS",
    "F.Mask",
    "B.Mask",
    "F.CrtYd",
    "B.CrtYd",
    "Pads",
    "Vias",
    "Tracks",
    "Bounding Boxes",
    "Component Centers",
    "References",
    "Axes",
    "Dimensions",
]


@dataclass(frozen=True)
class Rect:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    @property
    def center_x(self) -> float:
        return (self.min_x + self.max_x) / 2

    @property
    def center_y(self) -> float:
        return (self.min_y + self.max_y) / 2


@dataclass
class GraphicPrimitive:
    kind: str
    layer: str
    points: list[tuple[float, float]] = field(default_factory=list)
    width: float = 0.15
    fill: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass
class PadData:
    number: str
    shape: str
    layers: list[str]
    position: tuple[float, float]
    size: tuple[float, float]
    rotation: float
    board_rect: Rect
    board_center: tuple[float, float]
    side: str


@dataclass
class BBoxData:
    rect: Rect
    source: str
    source_label: str
    center_x: float
    center_y: float
    width: float
    height: float
    original_rect: Rect | None = None
    inflation_factor: float | None = None
    pad_count: int = 0


@dataclass
class FootprintData:
    name: str
    reference: str
    value: str
    side: str
    layer: str
    position: tuple[float, float]
    rotation: float
    courtyard: list[GraphicPrimitive]
    silkscreen: list[GraphicPrimitive]
    mask: list[GraphicPrimitive]
    copper: list[GraphicPrimitive]
    other_layers: list[GraphicPrimitive]
    pads: list[PadData]
    bbox: BBoxData | None
    bbox_missing: bool
    bbox_ok: bool
    yolo: dict[str, float] | None
    warnings: list[str]
    warnings_short: list[str]
    row_status: str


@dataclass
class BoardModel:
    pcb_path: Path
    dimensions: PCBDimensions
    footprints: list[FootprintData]
    edge_cuts: list[GraphicPrimitive]
    board_layers: dict[str, list[GraphicPrimitive]]
    tracks: list[GraphicPrimitive]
    vias: list[PadData]
    warnings: list[str]
    layer_names: list[str]


def _extract_blocks(lines: list[str], starters: tuple[str, ...]) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    depth = 0
    in_block = False
    for line in lines:
        stripped = line.strip()
        if not in_block and stripped.startswith(starters):
            in_block = True
            current = [line]
            depth = line.count("(") - line.count(")")
            if depth <= 0:
                blocks.append("\n".join(current))
                in_block = False
            continue
        if in_block:
            current.append(line)
            depth += line.count("(") - line.count(")")
            if depth <= 0:
                blocks.append("\n".join(current))
                current = []
                in_block = False
    return blocks


def _extract_nested_blocks(block_text: str, starters: tuple[str, ...]) -> list[str]:
    return _extract_blocks(block_text.splitlines(), starters)


def _match_float_pair(pattern: str, text: str) -> tuple[float, float] | None:
    match = re.search(pattern, text)
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def _match_at(text: str) -> tuple[float, float, float] | None:
    match = re.search(r"\(at\s+([\d.-]+)\s+([\d.-]+)(?:\s+([\d.-]+))?\)", text)
    if not match:
        return None
    return (
        float(match.group(1)),
        float(match.group(2)),
        float(match.group(3)) if match.group(3) else 0.0,
    )


def _match_layer(text: str) -> str | None:
    match = re.search(r'\(layer\s+"([^"]+)"\)', text)
    return match.group(1) if match else None


def _match_layers(text: str) -> list[str]:
    match = re.search(r"\(layers\s+([^\)]+)\)", text)
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


def _match_stroke_width(text: str) -> float:
    match = re.search(r"\(stroke\s+\([^\)]*\)\s*\(width\s+([\d.-]+)\)", text, re.DOTALL)
    if match:
        return float(match.group(1))
    match = re.search(r"\(width\s+([\d.-]+)\)", text)
    return float(match.group(1)) if match else 0.15


def _match_property_value(text: str, property_name: str) -> str:
    match = re.search(rf'\(property\s+"{re.escape(property_name)}"\s+"([^"]*)"', text)
    return match.group(1) if match else ""


def _match_footprint_name(text: str) -> str:
    match = re.search(r'\(footprint\s+"([^"]+)"', text)
    return match.group(1) if match else ""


def _parse_point_list(text: str) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in re.findall(r"\(xy\s+([\d.-]+)\s+([\d.-]+)\)", text)]


def _transform_point(point: tuple[float, float], origin: tuple[float, float], rotation: float) -> tuple[float, float]:
    rx, ry = rotate_point(point[0], point[1], rotation)
    return origin[0] + rx, origin[1] + ry


def _rect_from_points(points: list[tuple[float, float]]) -> Rect:
    x_values = [point[0] for point in points]
    y_values = [point[1] for point in points]
    return Rect(min(x_values), min(y_values), max(x_values), max(y_values))


def _parse_primitive(block_text: str, origin: tuple[float, float] | None = None, rotation: float = 0.0) -> GraphicPrimitive | None:
    header = block_text.lstrip().splitlines()[0].strip()
    layer = _match_layer(block_text)
    if not layer:
        return None
    width = _match_stroke_width(block_text)
    origin = origin or (0.0, 0.0)

    def to_board(point: tuple[float, float]) -> tuple[float, float]:
        return _transform_point(point, origin, rotation)

    if header.startswith("(gr_line") or header.startswith("(fp_line"):
        start = _match_float_pair(r"\(start\s+([\d.-]+)\s+([\d.-]+)\)", block_text)
        end = _match_float_pair(r"\(end\s+([\d.-]+)\s+([\d.-]+)\)", block_text)
        if start and end:
            points = [to_board(start), to_board(end)]
            return GraphicPrimitive("line", layer, points, width)
    if header.startswith("(gr_arc") or header.startswith("(fp_arc"):
        start = _match_float_pair(r"\(start\s+([\d.-]+)\s+([\d.-]+)\)", block_text)
        mid = _match_float_pair(r"\(mid\s+([\d.-]+)\s+([\d.-]+)\)", block_text)
        end = _match_float_pair(r"\(end\s+([\d.-]+)\s+([\d.-]+)\)", block_text)
        if start and mid and end:
            points = [to_board(start), to_board(mid), to_board(end)]
            return GraphicPrimitive("arc", layer, points, width)
    if header.startswith("(gr_rect") or header.startswith("(fp_rect"):
        start = _match_float_pair(r"\(start\s+([\d.-]+)\s+([\d.-]+)\)", block_text)
        end = _match_float_pair(r"\(end\s+([\d.-]+)\s+([\d.-]+)\)", block_text)
        if start and end:
            local_points = [
                start,
                (end[0], start[1]),
                end,
                (start[0], end[1]),
            ]
            return GraphicPrimitive("poly", layer, [to_board(point) for point in local_points], width)
    if header.startswith("(gr_circle") or header.startswith("(fp_circle"):
        center = _match_float_pair(r"\(center\s+([\d.-]+)\s+([\d.-]+)\)", block_text)
        end = _match_float_pair(r"\(end\s+([\d.-]+)\s+([\d.-]+)\)", block_text)
        if center and end:
            return GraphicPrimitive("circle", layer, [to_board(center), to_board(end)], width)
    if header.startswith("(gr_poly") or header.startswith("(fp_poly"):
        points = _parse_point_list(block_text)
        if points:
            return GraphicPrimitive("poly", layer, [to_board(point) for point in points], width)
    return None


def _pad_rect(center: tuple[float, float], size: tuple[float, float], rotation: float, shape: str) -> Rect:
    if shape == "circle":
        radius = max(size) / 2
        return Rect(center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius)
    if shape == "oval":
        width, height = size
        angle = math.radians(rotation)
        if width >= height:
            half_segment = (width - height) / 2
            radius = height / 2
            extent_x = abs(math.cos(angle)) * half_segment + radius
            extent_y = abs(math.sin(angle)) * half_segment + radius
        else:
            half_segment = (height - width) / 2
            radius = width / 2
            extent_x = abs(math.sin(angle)) * half_segment + radius
            extent_y = abs(math.cos(angle)) * half_segment + radius
        return Rect(center[0] - extent_x, center[1] - extent_y, center[0] + extent_x, center[1] + extent_y)
    half_width = size[0] / 2
    half_height = size[1] / 2
    corners = [
        (-half_width, -half_height),
        (half_width, -half_height),
        (half_width, half_height),
        (-half_width, half_height),
    ]
    rotated = []
    for corner in corners:
        rx, ry = rotate_point(corner[0], corner[1], rotation)
        rotated.append((center[0] + rx, center[1] + ry))
    return _rect_from_points(rotated)


def _calculate_pad_bbox(pads: list[PadData], margin: float = 0.0) -> Rect | None:
    if not pads:
        return None
    points: list[tuple[float, float]] = []
    for pad in pads:
        rect = pad.board_rect
        points.extend(
            [
                (rect.min_x, rect.min_y),
                (rect.max_x, rect.min_y),
                (rect.max_x, rect.max_y),
                (rect.min_x, rect.max_y),
            ]
        )
    rect = _rect_from_points(points)
    return Rect(rect.min_x - margin, rect.min_y - margin, rect.max_x + margin, rect.max_y + margin)


def _bbox_from_component_row(component: dict) -> BBoxData:
    width = float(component["width"])
    height = float(component["height"])
    center_x = float(component["bbox_center_x"])
    center_y = float(component["bbox_center_y"])
    rect = Rect(
        center_x - width / 2,
        center_y - height / 2,
        center_x + width / 2,
        center_y + height / 2,
    )
    source = component.get("bbox_source", "")
    source_label = {
        "courtyard": "courtyard",
        "pads": "pads_repaired",
    }.get(source, source or "missing")
    return BBoxData(
        rect=rect,
        source=source,
        source_label=source_label,
        center_x=center_x,
        center_y=center_y,
        width=width,
        height=height,
    )


def _normalize_bbox(rect: Rect, dimensions: PCBDimensions) -> dict[str, float]:
    if dimensions.width <= 0 or dimensions.height <= 0:
        return {"x_center": 0.0, "y_center": 0.0, "width": 0.0, "height": 0.0}
    return {
        "x_center": (rect.center_x - dimensions.min_x) / dimensions.width,
        "y_center": (rect.center_y - dimensions.min_y) / dimensions.height,
        "width": rect.width / dimensions.width,
        "height": rect.height / dimensions.height,
    }


def _board_contains_rect(rect: Rect, dimensions: PCBDimensions) -> bool:
    return (
        rect.min_x >= dimensions.min_x
        and rect.max_x <= dimensions.max_x
        and rect.min_y >= dimensions.min_y
        and rect.max_y <= dimensions.max_y
    )


def _issue_entries(footprint: FootprintData, dimensions: PCBDimensions) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    short: list[str] = []
    if not footprint.reference:
        issues.append(f"{footprint.name}: referencia inexistente.")
        short.append("sin reference")
    if not footprint.pads:
        issues.append(f"{footprint.reference or footprint.name}: footprint sin pads.")
        short.append("sin pads")
    if footprint.bbox is None:
        issues.append(f"{footprint.reference or footprint.name}: componente sin bbox.")
        short.append("sin bbox")
        return issues, short

    bbox = footprint.bbox
    if bbox.width <= 0:
        issues.append(f"{footprint.reference or footprint.name}: bbox con width <= 0.")
        short.append("bbox width <= 0")
    if bbox.height <= 0:
        issues.append(f"{footprint.reference or footprint.name}: bbox con height <= 0.")
        short.append("bbox height <= 0")
    if not _board_contains_rect(bbox.rect, dimensions):
        issues.append(f"{footprint.reference or footprint.name}: bbox fuera de Edge.Cuts.")
        short.append("bbox fuera de Edge.Cuts")
    yolo = footprint.yolo
    if yolo and not all(0.0 <= yolo[key] <= 1.0 for key in ("x_center", "y_center", "width", "height")):
        issues.append(f"{footprint.reference or footprint.name}: coordenadas YOLO fuera de 0..1.")
        short.append("YOLO fuera de rango")
    if bbox.width > dimensions.width * 0.5 or bbox.height > dimensions.height * 0.5:
        issues.append(f"{footprint.reference or footprint.name}: posible bbox anormalmente grande.")
        short.append("bbox anormalmente grande")
    return issues, short


def _make_status(footprint: FootprintData) -> str:
    if footprint.bbox is None:
        return "Missing"
    if footprint.warnings_short:
        return "Warning"
    if footprint.bbox.source == "pads":
        return "Repaired"
    return "OK"


def _parse_pad(block_text: str, footprint_origin: tuple[float, float], footprint_rotation: float, side: str) -> PadData | None:
    header = block_text.lstrip().splitlines()[0].strip()
    header_match = re.match(r'\(pad\s+"?([^"\s]*)"?\s+[^\s]+\s+([^\s\)]+)', header)
    if not header_match:
        return None
    number = header_match.group(1)
    shape = header_match.group(2)
    at_data = _match_at(block_text)
    size = _match_float_pair(r"\(size\s+([\d.-]+)\s+([\d.-]+)\)", block_text)
    if not at_data or not size:
        return None
    local_x, local_y, local_rotation = at_data
    board_center = _transform_point((local_x, local_y), footprint_origin, footprint_rotation)
    board_rotation = footprint_rotation + local_rotation
    board_rect = _pad_rect(board_center, size, board_rotation, shape)
    return PadData(
        number=number,
        shape=shape,
        layers=_match_layers(block_text),
        position=(local_x, local_y),
        size=size,
        rotation=board_rotation,
        board_rect=board_rect,
        board_center=board_center,
        side=side,
    )


def _parse_footprint(
    block_text: str,
    dimensions: PCBDimensions,
    exported_map: dict[tuple[str, str, float, float], dict],
    exported_by_reference: dict[str, dict],
) -> FootprintData | None:
    name = _match_footprint_name(block_text)
    layer = _match_layer(block_text) or ""
    at_data = _match_at(block_text)
    if not at_data:
        return None
    pos_x, pos_y, rotation = at_data
    reference = _match_property_value(block_text, "Reference")
    value = _match_property_value(block_text, "Value")
    side = "Bottom" if layer.startswith("B.") else "Top"

    nested = _extract_nested_blocks(block_text, ("(fp_line", "(fp_arc", "(fp_rect", "(fp_circle", "(fp_poly", "(pad", "(fp_text"))
    courtyard: list[GraphicPrimitive] = []
    silkscreen: list[GraphicPrimitive] = []
    mask: list[GraphicPrimitive] = []
    copper: list[GraphicPrimitive] = []
    other_layers: list[GraphicPrimitive] = []
    pads: list[PadData] = []

    for nested_block in nested:
        stripped = nested_block.lstrip()
        if stripped.startswith("(pad"):
            pad = _parse_pad(nested_block, (pos_x, pos_y), rotation, side)
            if pad:
                pads.append(pad)
            continue
        primitive = _parse_primitive(nested_block, (pos_x, pos_y), rotation)
        if primitive is None:
            continue
        if primitive.layer.endswith("CrtYd"):
            courtyard.append(primitive)
        elif primitive.layer.endswith("SilkS"):
            silkscreen.append(primitive)
        elif primitive.layer.endswith("Mask"):
            mask.append(primitive)
        elif primitive.layer.endswith("Cu"):
            copper.append(primitive)
        else:
            other_layers.append(primitive)

    key = (reference, name, round(pos_x, 6), round(pos_y, 6))
    exported = exported_map.get(key)
    if exported is None and reference:
        exported = exported_by_reference.get(reference)
    bbox = _bbox_from_component_row(exported) if exported else None
    if bbox and bbox.source == "pads":
        original_rect = _calculate_pad_bbox(pads, margin=0.0)
        if original_rect is not None:
            width_factor = bbox.width / original_rect.width if original_rect.width > 0 else None
            height_factor = bbox.height / original_rect.height if original_rect.height > 0 else None
            bbox.original_rect = original_rect
            valid_factors = [factor for factor in (width_factor, height_factor) if factor is not None]
            bbox.inflation_factor = sum(valid_factors) / len(valid_factors) if valid_factors else None
            bbox.pad_count = len(pads)
    elif bbox:
        bbox.pad_count = len(pads)

    yolo = _normalize_bbox(bbox.rect, dimensions) if bbox else None
    footprint = FootprintData(
        name=name,
        reference=reference,
        value=value,
        side=side,
        layer=layer,
        position=(pos_x, pos_y),
        rotation=rotation,
        courtyard=courtyard,
        silkscreen=silkscreen,
        mask=mask,
        copper=copper,
        other_layers=other_layers,
        pads=pads,
        bbox=bbox,
        bbox_missing=bbox is None,
        bbox_ok=True,
        yolo=yolo,
        warnings=[],
        warnings_short=[],
        row_status="",
    )
    warnings, warnings_short = _issue_entries(footprint, dimensions)
    footprint.warnings = warnings
    footprint.warnings_short = warnings_short
    footprint.bbox_ok = not warnings_short and bbox is not None
    footprint.row_status = _make_status(footprint)
    return footprint


def _parse_top_level_primitive(block_text: str) -> GraphicPrimitive | None:
    primitive = _parse_primitive(block_text)
    if primitive is None:
        return None
    return primitive


def _parse_segment(block_text: str) -> GraphicPrimitive | None:
    layer = _match_layer(block_text)
    start = _match_float_pair(r"\(start\s+([\d.-]+)\s+([\d.-]+)\)", block_text)
    end = _match_float_pair(r"\(end\s+([\d.-]+)\s+([\d.-]+)\)", block_text)
    width_match = re.search(r"\(width\s+([\d.-]+)\)", block_text)
    width = float(width_match.group(1)) if width_match else 0.15
    if not (layer and start and end):
        return None
    return GraphicPrimitive("line", layer, [start, end], width)


def _parse_via(block_text: str) -> PadData | None:
    at_data = _match_at(block_text)
    size_match = re.search(r"\(size\s+([\d.-]+)\)", block_text)
    if not at_data or not size_match:
        return None
    center = (at_data[0], at_data[1])
    diameter = float(size_match.group(1))
    rect = Rect(center[0] - diameter / 2, center[1] - diameter / 2, center[0] + diameter / 2, center[1] + diameter / 2)
    return PadData(
        number="via",
        shape="circle",
        layers=_match_layers(block_text),
        position=center,
        size=(diameter, diameter),
        rotation=0.0,
        board_rect=rect,
        board_center=center,
        side="Both",
    )


def build_board_model(result: ProcessingResult) -> BoardModel:
    pcb_text = result.pcb_path.read_text(encoding="utf-8")
    lines = pcb_text.splitlines()

    exported_by_reference: dict[str, dict] = {}
    exported_map: dict[tuple[str, str, float, float], dict] = {}
    for component in result.components:
        reference = component.get("reference", "")
        if reference:
            exported_by_reference[reference] = component
        key = (
            reference,
            component.get("name", ""),
            round(float(component.get("center_x", 0.0)), 6),
            round(float(component.get("center_y", 0.0)), 6),
        )
        exported_map[key] = component

    footprint_blocks = _extract_blocks(lines, ("(footprint",))
    board_blocks = _extract_blocks(lines, ("(gr_line", "(gr_arc", "(gr_rect", "(gr_circle", "(gr_poly"))
    segment_blocks = _extract_blocks(lines, ("(segment",))
    via_blocks = _extract_blocks(lines, ("(via",))

    footprints = [
        footprint
        for block in footprint_blocks
        if (footprint := _parse_footprint(block, result.pcb_dimensions, exported_map, exported_by_reference)) is not None
    ]

    edge_cuts: list[GraphicPrimitive] = []
    board_layers: dict[str, list[GraphicPrimitive]] = {}
    for block in board_blocks:
        primitive = _parse_top_level_primitive(block)
        if primitive is None:
            continue
        board_layers.setdefault(primitive.layer, []).append(primitive)
        if primitive.layer == "Edge.Cuts":
            edge_cuts.append(primitive)

    tracks = [track for block in segment_blocks if (track := _parse_segment(block)) is not None]
    vias = [via for block in via_blocks if (via := _parse_via(block)) is not None]

    warnings = list(result.warnings)
    for footprint in footprints:
        warnings.extend(footprint.warnings)
    warnings = list(dict.fromkeys(warnings))

    layer_names = list(PRIMARY_LAYER_ORDER)
    discovered_layers = set(board_layers)
    for footprint in footprints:
        for primitive in (
            footprint.courtyard
            + footprint.silkscreen
            + footprint.mask
            + footprint.copper
            + footprint.other_layers
        ):
            discovered_layers.add(primitive.layer)
    if tracks:
        discovered_layers.add("Tracks")
    if vias:
        discovered_layers.add("Vias")
    discovered_layers.add("Pads")
    extras = sorted(name for name in discovered_layers if name not in layer_names)
    layer_names.extend(extras)

    return BoardModel(
        pcb_path=result.pcb_path,
        dimensions=result.pcb_dimensions,
        footprints=footprints,
        edge_cuts=edge_cuts,
        board_layers=board_layers,
        tracks=tracks,
        vias=vias,
        warnings=warnings,
        layer_names=layer_names,
    )
