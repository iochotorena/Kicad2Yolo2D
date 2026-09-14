#!/usr/bin/env python3
"""CLI wrapper for CSV to YOLO conversion."""

from __future__ import annotations

import argparse
from pathlib import Path

from kicad_extract import (
    convert_to_yolo_format,
    read_components_csv,
    write_class_mapping,
    write_yolo_annotation,
)
from kicad_parser import parse_pcb_dimensions


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert component coordinates from CSV to YOLO format")
    parser.add_argument("--csv", type=str, help="Path to components.csv file (default: scr/input/components.csv)")
    parser.add_argument("--pcb", type=str, help="Path to .kicad_pcb file (default: first .kicad_pcb in scr/input/)")
    parser.add_argument("--output", type=str, help="Path to output YOLO annotation file (default: scr/input/annotations.txt)")
    parser.add_argument("--classes", type=str, help="Path to output class mapping file (default: scr/input/classes.txt)")
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    input_dir = script_dir / "input"

    csv_path = Path(args.csv) if args.csv else input_dir / "components.csv"
    if args.pcb:
        pcb_path = Path(args.pcb)
    else:
        pcb_files = sorted(input_dir.glob("*.kicad_pcb"))
        if not pcb_files:
            print(f"Error: No .kicad_pcb files found in {input_dir}")
            return
        pcb_path = pcb_files[0]

    output_path = Path(args.output) if args.output else input_dir / "annotations.txt"
    classes_path = Path(args.classes) if args.classes else input_dir / "classes.txt"

    print(f"Reading PCB file: {pcb_path.name}")
    try:
        pcb = parse_pcb_dimensions(pcb_path)
        print(f"PCB Bounding Box: ({pcb.min_x:.2f}, {pcb.min_y:.2f}) -> ({pcb.max_x:.2f}, {pcb.max_y:.2f})")
        print(f"PCB Size: {pcb.width:.2f} x {pcb.height:.2f} mm")
    except Exception as exc:
        print(f"Error parsing PCB file: {exc}")
        return

    print(f"\nReading components from: {csv_path.name}")
    try:
        components = read_components_csv(csv_path)
        print(f"Found {len(components)} components")
    except Exception as exc:
        print(f"Error reading CSV file: {exc}")
        return

    print("\nConverting to YOLO format...")
    annotations, class_mapping = convert_to_yolo_format(
        components,
        pcb.width,
        pcb.height,
        pcb.center_x,
        pcb.center_y,
    )

    write_yolo_annotation(annotations, output_path)
    write_class_mapping(class_mapping, classes_path)

    print("\nConversion complete!")
    print(f"YOLO Annotations: {output_path}")
    print(f"Class Mapping:    {classes_path}")


if __name__ == "__main__":
    main()
