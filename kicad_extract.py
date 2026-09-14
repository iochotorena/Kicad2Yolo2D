#!/usr/bin/env python3
"""Component extraction, validation and output helpers."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from kicad_parser import PCBDimensions, parse_pcb_dimensions, parse_pcb_file, rotate_point
from kicad_repair import calculate_bounding_box_from_pads


@dataclass
class ExtractionStats:
    total_footprints: int
    exported_components: int
    components_with_courtyard: int
    components_repaired_from_pads: int
    skipped_components: int


@dataclass
class ProcessingResult:
    pcb_path: Path
    output_dir: Path | None
    components: list[dict]
    annotations: list[str]
    class_mapping: dict[str, int]
    pcb_dimensions: PCBDimensions
    stats: ExtractionStats
    warnings: list[str] = field(default_factory=list)


def calculate_bounding_box(component: dict) -> dict | None:
    """Calculate the bounding box for a component based on courtyard fp_lines."""
    if not component["fp_lines"]:
        return None

    fp_x, fp_y = component["position"]
    rotation = component["rotation"]

    all_points = []
    for fp_line in component["fp_lines"]:
        start_rot = rotate_point(fp_line["start"][0], fp_line["start"][1], rotation)
        end_rot = rotate_point(fp_line["end"][0], fp_line["end"][1], rotation)
        all_points.append((fp_x + start_rot[0], fp_y + start_rot[1]))
        all_points.append((fp_x + end_rot[0], fp_y + end_rot[1]))

    x_coords = [point[0] for point in all_points]
    y_coords = [point[1] for point in all_points]

    min_x = min(x_coords)
    max_x = max(x_coords)
    min_y = min(y_coords)
    max_y = max(y_coords)

    return {
        "bbox_center_x": (min_x + max_x) / 2,
        "bbox_center_y": (min_y + max_y) / 2,
        "width": max_x - min_x,
        "height": max_y - min_y,
    }


def extract_components(filepath: str | Path) -> tuple[list[dict], ExtractionStats]:
    """Extract components and bounding boxes from a PCB file."""
    components = parse_pcb_file(filepath)
    component_rows = []
    components_with_courtyard = 0
    components_repaired_from_pads = 0

    for component in components:
        bbox_data = None
        bbox_source = None

        if component.get("fp_lines"):
            bbox_data = calculate_bounding_box(component)
            if bbox_data:
                bbox_source = "courtyard"
                components_with_courtyard += 1

        if bbox_data is None and component.get("pads"):
            bbox_data = calculate_bounding_box_from_pads(component)
            if bbox_data:
                bbox_source = "pads"
                components_repaired_from_pads += 1

        if bbox_data:
            fp_x, fp_y = component["position"]
            component_rows.append(
                {
                    "name": component["name"],
                    "reference": component.get("reference") or "",
                    "value": component.get("value") or "",
                    "center_x": fp_x,
                    "center_y": fp_y,
                    "bbox_center_x": bbox_data["bbox_center_x"],
                    "bbox_center_y": bbox_data["bbox_center_y"],
                    "width": bbox_data["width"],
                    "height": bbox_data["height"],
                    "bbox_source": bbox_source or "",
                }
            )

    stats = ExtractionStats(
        total_footprints=len(components),
        exported_components=len(component_rows),
        components_with_courtyard=components_with_courtyard,
        components_repaired_from_pads=components_repaired_from_pads,
        skipped_components=len(components) - len(component_rows),
    )
    return component_rows, stats


def write_components_csv(components_data: list[dict], output_path: str | Path) -> None:
    """Write component data to a CSV file."""
    fieldnames = [
        "name",
        "reference",
        "value",
        "center_x",
        "center_y",
        "bbox_center_x",
        "bbox_center_y",
        "width",
        "height",
        "bbox_source",
    ]
    with Path(output_path).open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for component in components_data:
            writer.writerow(component)


def read_components_csv(csv_path: str | Path) -> list[dict]:
    """Read component data from CSV file."""
    components = []
    with Path(csv_path).open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            components.append(
                {
                    "name": row["name"],
                    "reference": row.get("reference", ""),
                    "value": row.get("value", ""),
                    "center_x": float(row["center_x"]),
                    "center_y": float(row["center_y"]),
                    "bbox_center_x": float(row["bbox_center_x"]),
                    "bbox_center_y": float(row["bbox_center_y"]),
                    "width": float(row["width"]),
                    "height": float(row["height"]),
                    "bbox_source": row.get("bbox_source", ""),
                }
            )
    return components


def get_reference_prefix(reference: str) -> str:
    """Extract the letter prefix from a component reference."""
    if not reference:
        return "UNKNOWN"
    match = re.match(r"^([A-Za-z]+)", reference)
    return match.group(1).upper() if match else "UNKNOWN"


def convert_to_yolo_format(
    components: list[dict],
    pcb_width: float,
    pcb_height: float,
    pcb_center_x: float,
    pcb_center_y: float,
    class_mapping: dict[str, int] | None = None,
) -> tuple[list[str], dict[str, int]]:
    """Convert component coordinates to YOLO format."""
    yolo_annotations = []

    if class_mapping is None:
        unique_prefixes = sorted({get_reference_prefix(component["reference"]) for component in components})
        class_mapping = {prefix: idx for idx, prefix in enumerate(unique_prefixes)}

    pcb_origin_x = pcb_center_x - pcb_width / 2
    pcb_origin_y = pcb_center_y - pcb_height / 2

    for component in components:
        class_id = class_mapping.get(get_reference_prefix(component["reference"]), 0)
        norm_center_x = (component["bbox_center_x"] - pcb_origin_x) / pcb_width
        norm_center_y = (component["bbox_center_y"] - pcb_origin_y) / pcb_height
        norm_width = component["width"] / pcb_width
        norm_height = component["height"] / pcb_height

        norm_center_x = max(0.0, min(1.0, norm_center_x))
        norm_center_y = max(0.0, min(1.0, norm_center_y))
        norm_width = max(0.0, min(1.0, norm_width))
        norm_height = max(0.0, min(1.0, norm_height))

        yolo_annotations.append(
            f"{class_id} {norm_center_x:.6f} {norm_center_y:.6f} {norm_width:.6f} {norm_height:.6f}"
        )

    return yolo_annotations, class_mapping


def write_yolo_annotation(annotations: list[str], output_path: str | Path) -> None:
    """Write YOLO annotations to a file."""
    with Path(output_path).open("w", encoding="utf-8") as handle:
        for annotation in annotations:
            handle.write(annotation + "\n")


def write_class_mapping(class_mapping: dict[str, int], output_path: str | Path) -> None:
    """Write class mapping to a file."""
    with Path(output_path).open("w", encoding="utf-8") as handle:
        handle.write("# Class ID to Component Name Mapping\n")
        handle.write("# Format: class_id: component_name\n\n")
        for name, class_id in sorted(class_mapping.items(), key=lambda item: item[1]):
            handle.write(f"{class_id}: {name}\n")


def default_output_directory(source_path: str | Path) -> Path:
    """Return the default output directory for a file or folder source."""
    source = Path(source_path)
    return source.with_suffix("") if source.is_file() else source / "results"


def find_pcb_files(source_path: str | Path) -> list[Path]:
    """Return PCB files to process from a file or directory source."""
    source = Path(source_path)
    if source.is_file():
        return [source]
    return sorted(source.glob("*.kicad_pcb"))


def validate_result(components: list[dict], pcb_dimensions: PCBDimensions, stats: ExtractionStats) -> list[str]:
    """Generate validation messages without changing geometry."""
    warnings = []
    if pcb_dimensions.width <= 0 or pcb_dimensions.height <= 0:
        warnings.append("Las dimensiones de la PCB no son válidas.")
    if not components:
        warnings.append("No se extrajeron componentes exportables.")
    if stats.skipped_components:
        warnings.append(f"Se omitieron {stats.skipped_components} footprints sin bbox exportable.")

    missing_reference = sum(1 for component in components if not component.get("reference"))
    missing_value = sum(1 for component in components if not component.get("value"))
    if missing_reference:
        warnings.append(f"Hay {missing_reference} componentes sin Reference.")
    if missing_value:
        warnings.append(f"Hay {missing_value} componentes sin Value.")

    for component in components:
        if component["width"] <= 0 or component["height"] <= 0:
            warnings.append(f"{component['name']} tiene una bbox con dimensiones no positivas.")
        if not (pcb_dimensions.min_x <= component["bbox_center_x"] <= pcb_dimensions.max_x):
            warnings.append(f"{component['reference'] or component['name']} queda fuera del ancho de la PCB.")
        if not (pcb_dimensions.min_y <= component["bbox_center_y"] <= pcb_dimensions.max_y):
            warnings.append(f"{component['reference'] or component['name']} queda fuera del alto de la PCB.")

    return warnings


def process_pcb_file(pcb_path: str | Path, output_dir: str | Path | None = None) -> ProcessingResult:
    """Process one PCB file into CSV and YOLO data."""
    pcb_file = Path(pcb_path)
    pcb_dimensions = parse_pcb_dimensions(pcb_file)
    components, stats = extract_components(pcb_file)
    annotations, class_mapping = convert_to_yolo_format(
        components,
        pcb_dimensions.width,
        pcb_dimensions.height,
        pcb_dimensions.center_x,
        pcb_dimensions.center_y,
    )
    warnings = validate_result(components, pcb_dimensions, stats)
    result = ProcessingResult(
        pcb_path=pcb_file,
        output_dir=Path(output_dir) if output_dir else None,
        components=components,
        annotations=annotations,
        class_mapping=class_mapping,
        pcb_dimensions=pcb_dimensions,
        stats=stats,
        warnings=warnings,
    )
    return result


def save_processing_result(result: ProcessingResult, output_dir: str | Path) -> dict[str, Path]:
    """Persist extracted CSV and YOLO outputs."""
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    components_path = destination / "components.csv"
    annotations_path = destination / "annotations.txt"
    classes_path = destination / "classes.txt"

    write_components_csv(result.components, components_path)
    write_yolo_annotation(result.annotations, annotations_path)
    write_class_mapping(result.class_mapping, classes_path)

    result.output_dir = destination
    return {
        "components_csv": components_path,
        "annotations": annotations_path,
        "classes": classes_path,
    }


def process_source(source_path: str | Path, output_root: str | Path | None = None) -> list[ProcessingResult]:
    """Process a single PCB or a directory of PCB files."""
    source = Path(source_path)
    pcb_files = find_pcb_files(source)
    if not pcb_files:
        raise FileNotFoundError(f"No se encontraron ficheros .kicad_pcb en {source}")

    base_output = Path(output_root) if output_root else default_output_directory(source)
    results = []
    for pcb_file in pcb_files:
        target_dir = base_output if source.is_file() else base_output / pcb_file.stem
        result = process_pcb_file(pcb_file, target_dir)
        save_processing_result(result, target_dir)
        results.append(result)
    return results
