#!/usr/bin/env python3
"""
getComponents.py - Extract component information from KiCad PCB files

This script reads a .kicad_pcb file from the input folder and extracts
component information based on the F.CrtYd (Front Courtyard) layer.
It calculates bounding boxes and centers for each component and saves
the data to a components.csv file.
"""

import os
import re
import csv
import math
from pathlib import Path


def parse_pcb_file(filepath):
    """
    Parse a KiCad PCB file and extract footprint information.
    
    Args:
        filepath: Path to the .kicad_pcb file
        
    Returns:
        List of dictionaries containing component data
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    components = []
    lines = content.split('\n')
    
    in_footprint = False
    in_fp_line = False
    in_property = False
    current_footprint = {}
    paren_depth = 0
    footprint_depth = 0
    property_depth = 0
    fp_line_data = {}
    
    for line in lines:
        stripped = line.strip()
        
        # Track parenthesis depth
        paren_depth += line.count('(') - line.count(')')
        
        # Start of footprint
        if stripped.startswith('(footprint'):
            in_footprint = True
            footprint_depth = paren_depth
            match = re.search(r'\(footprint\s+"([^"]+)"', line)
            if match:
                current_footprint = {
                    'name': match.group(1),
                    'fp_lines': [],
                    'position': None,
                    'rotation': 0.0
                }
        
        # Track property blocks to avoid parsing (at ...) inside them
        elif in_footprint and stripped.startswith('(property'):
            in_property = True
            property_depth = paren_depth
        
        # End of property block
        elif in_property and paren_depth < property_depth:
            in_property = False
        
        # Get footprint position and rotation (only if not in property block)
        elif in_footprint and not in_property and stripped.startswith('(at'):
            match = re.search(r'\(at\s+([\d.-]+)\s+([\d.-]+)(?:\s+([\d.-]+))?\)', line)
            if match and current_footprint['position'] is None:
                x = float(match.group(1))
                y = float(match.group(2))
                rot = float(match.group(3)) if match.group(3) else 0.0
                current_footprint['position'] = (x, y)
                current_footprint['rotation'] = rot
        
        # Start of fp_line
        elif in_footprint and stripped.startswith('(fp_line'):
            in_fp_line = True
            fp_line_data = {}
        
        # Extract start coordinates
        elif in_fp_line and stripped.startswith('(start'):
            match = re.search(r'\(start\s+([\d.-]+)\s+([\d.-]+)\)', line)
            if match:
                fp_line_data['start'] = (float(match.group(1)), float(match.group(2)))
        
        # Extract end coordinates
        elif in_fp_line and stripped.startswith('(end'):
            match = re.search(r'\(end\s+([\d.-]+)\s+([\d.-]+)\)', line)
            if match:
                fp_line_data['end'] = (float(match.group(1)), float(match.group(2)))
        
        # Check layer and save if F.CrtYd
        elif in_fp_line and stripped.startswith('(layer'):
            match = re.search(r'\(layer\s+"([^"]+)"\)', line)
            if match:
                layer = match.group(1)
                if layer == 'F.CrtYd' and 'start' in fp_line_data and 'end' in fp_line_data:
                    current_footprint['fp_lines'].append(fp_line_data.copy())
                in_fp_line = False
                fp_line_data = {}
        
        # End of footprint
        if in_footprint and paren_depth < footprint_depth:
            if current_footprint.get('fp_lines') and current_footprint.get('position'):
                components.append(current_footprint)
            in_footprint = False
            in_property = False
            current_footprint = {}
    
    return components


def rotate_point(x, y, angle_deg):
    """
    Rotate a point around the origin.
    
    Args:
        x, y: Point coordinates
        angle_deg: Rotation angle in degrees
        
    Returns:
        Tuple of rotated (x, y) coordinates
    """
    angle_rad = math.radians(angle_deg)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    
    x_rot = x * cos_a - y * sin_a
    y_rot = x * sin_a + y * cos_a
    
    return x_rot, y_rot


def calculate_bounding_box(component):
    """
    Calculate the bounding box for a component based on its F.CrtYd fp_lines.
    
    Args:
        component: Dictionary containing component data
        
    Returns:
        Dictionary with center, bbox_center, width, height, and name
    """
    if not component['fp_lines']:
        return None
    
    # Get footprint position and rotation
    fp_x, fp_y = component['position']
    rotation = component['rotation']
    
    # Collect all points from fp_lines
    all_points = []
    for fp_line in component['fp_lines']:
        start = fp_line['start']
        end = fp_line['end']
        
        # Rotate points according to footprint rotation
        start_rot = rotate_point(start[0], start[1], rotation)
        end_rot = rotate_point(end[0], end[1], rotation)
        
        # Add footprint offset
        all_points.append((fp_x + start_rot[0], fp_y + start_rot[1]))
        all_points.append((fp_x + end_rot[0], fp_y + end_rot[1]))
    
    # Calculate bounding box
    x_coords = [p[0] for p in all_points]
    y_coords = [p[1] for p in all_points]
    
    min_x = min(x_coords)
    max_x = max(x_coords)
    min_y = min(y_coords)
    max_y = max(y_coords)
    
    # Calculate dimensions
    width = max_x - min_x
    height = max_y - min_y
    
    # Bounding box center
    bbox_center_x = (min_x + max_x) / 2
    bbox_center_y = (min_y + max_y) / 2
    
    # Component center (footprint position)
    center_x = fp_x
    center_y = fp_y
    
    return {
        'name': component['name'],
        'center_x': center_x,
        'center_y': center_y,
        'bbox_center_x': bbox_center_x,
        'bbox_center_y': bbox_center_y,
        'width': width,
        'height': height
    }


def write_components_csv(components_data, output_path):
    """
    Write component data to a CSV file.
    
    Args:
        components_data: List of component dictionaries
        output_path: Path to output CSV file
    """
    fieldnames = ['name', 'center_x', 'center_y', 'bbox_center_x', 
                  'bbox_center_y', 'width', 'height']
    
    with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        for component in components_data:
            writer.writerow(component)
    
    print(f"Written {len(components_data)} components to {output_path}")


def main():
    """Main function to process PCB files and generate component CSV."""
    # Define paths
    script_dir = Path(__file__).parent
    input_dir = script_dir / 'input'
    
    # Find .kicad_pcb files in the input folder
    pcb_files = list(input_dir.glob('*.kicad_pcb'))
    
    if not pcb_files:
        print(f"No .kicad_pcb files found in {input_dir}")
        return
    
    # Process the first PCB file found
    pcb_file = pcb_files[0]
    print(f"Processing: {pcb_file.name}")
    
    # Parse the PCB file
    components = parse_pcb_file(pcb_file)
    print(f"Found {len(components)} footprints with F.CrtYd layer")
    
    # Calculate bounding boxes
    components_data = []
    for component in components:
        bbox_data = calculate_bounding_box(component)
        if bbox_data:
            components_data.append(bbox_data)
    
    # Write to CSV
    output_csv = input_dir / 'components.csv'
    write_components_csv(components_data, output_csv)
    
    # Display sample data
    print("\nSample of extracted data (first 5 components):")
    print("-" * 100)
    print(f"{'Name':<50} {'Center':<20} {'BBox Center':<20} {'Size':<20}")
    print("-" * 100)
    for component in components_data[:5]:
        center = f"({component['center_x']:.2f}, {component['center_y']:.2f})"
        bbox = f"({component['bbox_center_x']:.2f}, {component['bbox_center_y']:.2f})"
        size = f"{component['width']:.2f} x {component['height']:.2f}"
        print(f"{component['name']:<50} {center:<20} {bbox:<20} {size:<20}")
    print("-" * 100)


if __name__ == '__main__':
    main()
