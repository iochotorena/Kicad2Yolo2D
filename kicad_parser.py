#!/usr/bin/env python3
"""Parsing and shared geometry helpers for KiCad PCB files."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PCBDimensions:
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    center_x: float
    center_y: float
    width: float
    height: float


def rotate_point(x: float, y: float, angle_deg: float) -> tuple[float, float]:
    """Rotate a point around the origin."""
    angle_rad = math.radians(angle_deg)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    return x * cos_a - y * sin_a, x * sin_a + y * cos_a


def parse_pcb_file(filepath: str | Path) -> list[dict]:
    """Parse a KiCad PCB file and extract footprint information."""
    path = Path(filepath)
    content = path.read_text(encoding="utf-8")

    components = []
    lines = content.split("\n")

    in_footprint = False
    in_fp_line = False
    in_property = False
    in_pad = False
    current_footprint = {}
    paren_depth = 0
    footprint_depth = 0
    property_depth = 0
    pad_depth = 0
    fp_line_data = {}
    pad_data = {}
    current_property_name = None

    for line in lines:
        stripped = line.strip()
        paren_depth += line.count("(") - line.count(")")

        if stripped.startswith("(footprint"):
            in_footprint = True
            footprint_depth = paren_depth
            match = re.search(r'\(footprint\s+"([^"]+)"', line)
            if match:
                current_footprint = {
                    "name": match.group(1),
                    "reference": None,
                    "value": None,
                    "fp_lines": [],
                    "pads": [],
                    "position": None,
                    "rotation": 0.0,
                }

        elif in_footprint and stripped.startswith("(property"):
            in_property = True
            property_depth = paren_depth
            match = re.search(r'\(property\s+"([^"]+)"\s+"([^"]*)"', line)
            if match:
                current_property_name = match.group(1)
                if current_property_name == "Reference":
                    current_footprint["reference"] = match.group(2)
                elif current_property_name == "Value":
                    current_footprint["value"] = match.group(2)

        elif in_property and paren_depth < property_depth:
            in_property = False
            current_property_name = None

        elif in_footprint and not in_property and not in_pad and stripped.startswith("(at"):
            match = re.search(r"\(at\s+([\d.-]+)\s+([\d.-]+)(?:\s+([\d.-]+))?\)", line)
            if match and current_footprint["position"] is None:
                current_footprint["position"] = (float(match.group(1)), float(match.group(2)))
                current_footprint["rotation"] = float(match.group(3)) if match.group(3) else 0.0

        elif in_footprint and stripped.startswith("(fp_line"):
            in_fp_line = True
            fp_line_data = {}

        elif in_fp_line and stripped.startswith("(start"):
            match = re.search(r"\(start\s+([\d.-]+)\s+([\d.-]+)\)", line)
            if match:
                fp_line_data["start"] = (float(match.group(1)), float(match.group(2)))

        elif in_fp_line and stripped.startswith("(end"):
            match = re.search(r"\(end\s+([\d.-]+)\s+([\d.-]+)\)", line)
            if match:
                fp_line_data["end"] = (float(match.group(1)), float(match.group(2)))

        elif in_fp_line and stripped.startswith("(layer"):
            match = re.search(r'\(layer\s+"([^"]+)"\)', line)
            if match:
                layer = match.group(1)
                if layer in {"F.CrtYd", "B.CrtYd"} and "start" in fp_line_data and "end" in fp_line_data:
                    current_footprint["fp_lines"].append(fp_line_data.copy())
                in_fp_line = False
                fp_line_data = {}

        elif in_footprint and stripped.startswith("(pad"):
            in_pad = True
            pad_depth = paren_depth
            pad_data = {}

        elif in_pad and stripped.startswith("(at"):
            match = re.search(r"\(at\s+([\d.-]+)\s+([\d.-]+)(?:\s+([\d.-]+))?\)", line)
            if match:
                pad_data["position"] = (float(match.group(1)), float(match.group(2)))
                pad_data["rotation"] = float(match.group(3)) if match.group(3) else 0.0

        elif in_pad and stripped.startswith("(size"):
            match = re.search(r"\(size\s+([\d.-]+)\s+([\d.-]+)\)", line)
            if match:
                pad_data["size"] = (float(match.group(1)), float(match.group(2)))

        if in_pad and paren_depth < pad_depth:
            if "position" in pad_data and "size" in pad_data:
                current_footprint["pads"].append(pad_data.copy())
            in_pad = False
            pad_data = {}

        if in_footprint and paren_depth < footprint_depth:
            if current_footprint.get("position") and (
                current_footprint.get("fp_lines") or current_footprint.get("pads")
            ):
                components.append(current_footprint)
            in_footprint = False
            in_property = False
            current_footprint = {}

    return components


def parse_pcb_dimensions(filepath: str | Path) -> PCBDimensions:
    """Parse a KiCad PCB file to extract board dimensions from Edge.Cuts."""
    path = Path(filepath)
    content = path.read_text(encoding="utf-8")

    edge_cuts_pattern = r'\(gr_(?:line|arc|rect|circle|poly)\s+.*?\(layer\s+"Edge\.Cuts"\).*?\)'
    edge_elements = re.findall(edge_cuts_pattern, content, re.DOTALL)

    all_points: list[tuple[float, float]] = []
    for element in edge_elements:
        start_matches = re.findall(r"\(start\s+([\d.-]+)\s+([\d.-]+)\)", element)
        end_matches = re.findall(r"\(end\s+([\d.-]+)\s+([\d.-]+)\)", element)
        mid_matches = re.findall(r"\(mid\s+([\d.-]+)\s+([\d.-]+)\)", element)

        for match in start_matches + end_matches + mid_matches:
            all_points.append((float(match[0]), float(match[1])))

        if re.findall(r"\(center\s+([\d.-]+)\s+([\d.-]+)\)", element):
            pass

    if not all_points:
        raise ValueError("No Edge.Cuts elements found in PCB file")

    x_coords = [point[0] for point in all_points]
    y_coords = [point[1] for point in all_points]

    min_x = min(x_coords)
    max_x = max(x_coords)
    min_y = min(y_coords)
    max_y = max(y_coords)

    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2
    width = max_x - min_x
    height = max_y - min_y

    return PCBDimensions(min_x, max_x, min_y, max_y, center_x, center_y, width, height)
