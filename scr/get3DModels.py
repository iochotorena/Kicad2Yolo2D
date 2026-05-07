#!/usr/bin/env python3
"""
get3DModels.py - Extract 3D model names and locations from KiCad PCB files

This script reads a .kicad_pcb file and extracts the 3D models associated
with each footprint. It writes the footprint reference, footprint name,
model name, raw KiCad model path, and resolved model path to a CSV file.
"""

import os
import csv
import argparse
import re
from pathlib import Path


def parse_pcb_3d_models(filepath):
    """
    Parse a KiCad PCB file and extract footprint 3D model data.

    Args:
        filepath: Path to the .kicad_pcb file

    Returns:
        List of dictionaries containing footprint and model data
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    footprints = []
    in_footprint = False
    in_property = False
    paren_depth = 0
    footprint_depth = 0
    property_depth = 0
    current_footprint = {}

    for line in lines:
        stripped = line.strip()
        paren_depth += line.count('(') - line.count(')')

        if stripped.startswith('(footprint'):
            in_footprint = True
            footprint_depth = paren_depth
            match = re.search(r'\(footprint\s+"([^"]+)"', line)
            current_footprint = {
                'footprint_name': match.group(1) if match else '',
                'reference': '',
                'models': [],
            }

        elif in_footprint and stripped.startswith('(property'):
            in_property = True
            property_depth = paren_depth
            match = re.search(r'\(property\s+"([^"]+)"\s+"([^"]*)"', line)
            if match and match.group(1) == 'Reference':
                current_footprint['reference'] = match.group(2)

        elif in_property and paren_depth < property_depth:
            in_property = False

        elif in_footprint and stripped.startswith('(model'):
            match = re.search(r'\(model\s+"([^"]+)"', line)
            if match:
                current_footprint['models'].append(match.group(1))

        if in_footprint and paren_depth < footprint_depth:
            if current_footprint.get('models'):
                footprints.append(current_footprint)
            in_footprint = False
            in_property = False
            current_footprint = {}

    return footprints


def resolve_model_path(model_path, pcb_path):
    """
    Resolve a KiCad model path to a filesystem path when possible.

    Args:
        model_path: Raw model path from the PCB file
        pcb_path: Path to the source PCB file

    Returns:
        Resolved path string when variables can be expanded, otherwise the raw path
    """
    variables = {'KIPRJMOD': str(pcb_path.parent), **os.environ}

    def replace_var(match):
        var_name = match.group(1)
        if var_name in variables:
            return variables[var_name]
        return match.group(0)

    expanded = re.sub(r'\$\{([^}]+)\}', replace_var, model_path)

    if '${' in expanded:
        return expanded

    resolved_path = Path(expanded)
    if not resolved_path.is_absolute():
        resolved_path = pcb_path.parent / resolved_path

    return str(resolved_path.resolve())


def flatten_footprint_models(footprints, pcb_path):
    """
    Convert parsed footprint data into CSV rows.

    Args:
        footprints: List of footprint dictionaries
        pcb_path: Path to the source PCB file

    Returns:
        List of flat dictionaries for CSV output
    """
    rows = []

    for footprint in footprints:
        for index, model_path in enumerate(footprint['models'], start=1):
            rows.append({
                'reference': footprint['reference'],
                'footprint_name': footprint['footprint_name'],
                'model_index': index,
                'model_name': Path(model_path).stem,
                'model_path': model_path,
                'resolved_model_path': resolve_model_path(model_path, pcb_path),
            })

    return rows


def write_models_csv(rows, output_path):
    """
    Write footprint 3D model data to CSV.

    Args:
        rows: List of row dictionaries
        output_path: Path to output CSV file
    """
    fieldnames = [
        'reference',
        'footprint_name',
        'model_index',
        'model_name',
        'model_path',
        'resolved_model_path',
    ]

    with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written {len(rows)} 3D models to {output_path}")


def main():
    """Main function to extract 3D model data from a KiCad PCB."""
    parser = argparse.ArgumentParser(
        description='Extract 3D model names and locations from a KiCad PCB file'
    )
    parser.add_argument(
        '--pcb',
        type=str,
        help='Path to .kicad_pcb file (default: first .kicad_pcb in scr/input/)'
    )
    parser.add_argument(
        '--output',
        type=str,
        help='Path to output CSV file (default: scr/input/footprint_3d_models.csv)'
    )
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    input_dir = script_dir / 'input'

    if args.pcb:
        pcb_path = Path(args.pcb)
    else:
        pcb_files = list(input_dir.glob('*.kicad_pcb'))
        if not pcb_files:
            print(f"No .kicad_pcb files found in {input_dir}")
            return
        pcb_path = pcb_files[0]

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_dir / 'footprint_3d_models.csv'

    print(f"Processing: {pcb_path.name}")

    footprints = parse_pcb_3d_models(pcb_path)
    rows = flatten_footprint_models(footprints, pcb_path)
    write_models_csv(rows, output_path)

    print(f"Found {len(footprints)} footprints with 3D models")

    if rows:
        print("\nSample of extracted data (first 5 models):")
        print("-" * 160)
        print(f"{'Ref':<10} {'Footprint':<55} {'Model':<35} {'Path':<55}")
        print("-" * 160)
        for row in rows[:5]:
            print(
                f"{row['reference']:<10} "
                f"{row['footprint_name']:<55} "
                f"{row['model_name']:<35} "
                f"{row['model_path']:<55}"
            )
        print("-" * 160)


if __name__ == '__main__':
    main()
