#!/usr/bin/env python3
"""CLI wrapper for component extraction."""

from __future__ import annotations

import argparse
from pathlib import Path

from kicad_extract import default_output_directory, process_pcb_file, save_processing_result


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract component information from KiCad PCB files")
    parser.add_argument("--pcb", type=str, help="Path to .kicad_pcb file")
    parser.add_argument("--output", type=str, help="Output directory for components.csv")
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    input_dir = script_dir / "input"

    if args.pcb:
        pcb_file = Path(args.pcb)
    else:
        pcb_files = sorted(input_dir.glob("*.kicad_pcb"))
        if not pcb_files:
            print(f"No .kicad_pcb files found in {input_dir}")
            return
        pcb_file = pcb_files[0]

    output_dir = Path(args.output) if args.output else (input_dir if not args.pcb else default_output_directory(pcb_file))

    print(f"Processing: {pcb_file.name}")
    result = process_pcb_file(pcb_file, output_dir)
    save_processing_result(result, output_dir)

    print(f"Found {result.stats.total_footprints} footprints")
    print(f"Written {result.stats.exported_components} components to {output_dir / 'components.csv'}")
    print(f"Components with courtyard: {result.stats.components_with_courtyard}")
    print(f"Components using pads: {result.stats.components_repaired_from_pads}")

    if result.components:
        print("\nSample of extracted data (first 5 components):")
        print("-" * 120)
        print(f"{'Name':<45} {'Reference':<12} {'Value':<18} {'BBox Center':<20} {'Size':<18} {'Source':<10}")
        print("-" * 120)
        for component in result.components[:5]:
            bbox = f"({component['bbox_center_x']:.2f}, {component['bbox_center_y']:.2f})"
            size = f"{component['width']:.2f} x {component['height']:.2f}"
            print(
                f"{component['name']:<45} "
                f"{component['reference']:<12} "
                f"{component.get('value', ''):<18} "
                f"{bbox:<20} "
                f"{size:<18} "
                f"{component.get('bbox_source', ''):<10}"
            )
        print("-" * 120)


if __name__ == "__main__":
    main()
