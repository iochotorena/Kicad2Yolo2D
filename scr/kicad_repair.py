#!/usr/bin/env python3
"""Bounding-box repair helpers."""

from __future__ import annotations

try:
    from .kicad_parser import rotate_point, transform_footprint_point
except ImportError:  # pragma: no cover - script fallback
    from kicad_parser import rotate_point, transform_footprint_point


def calculate_bounding_box_from_pads(component: dict, margin: float = 0.5) -> dict | None:
    """Calculate the bounding box for a component based on its pads."""
    if not component["pads"]:
        return None

    fp_x, fp_y = component["position"]
    fp_rotation = component["rotation"]

    all_points = []
    for pad in component["pads"]:
        pad_x, pad_y = pad["position"]
        pad_width, pad_height = pad["size"]
        pad_rotation = pad.get("rotation", 0.0)

        corners = [
            (-pad_width / 2, -pad_height / 2),
            (pad_width / 2, -pad_height / 2),
            (pad_width / 2, pad_height / 2),
            (-pad_width / 2, pad_height / 2),
        ]

        for corner_x, corner_y in corners:
            rotated_corner = rotate_point(corner_x, corner_y, -pad_rotation)
            pad_corner_x = pad_x + rotated_corner[0]
            pad_corner_y = pad_y + rotated_corner[1]
            all_points.append(
                transform_footprint_point(pad_corner_x, pad_corner_y, fp_x, fp_y, fp_rotation)
            )

    x_coords = [point[0] for point in all_points]
    y_coords = [point[1] for point in all_points]

    min_x = min(x_coords) - margin
    max_x = max(x_coords) + margin
    min_y = min(y_coords) - margin
    max_y = max(y_coords) + margin

    return {
        "bbox_center_x": (min_x + max_x) / 2,
        "bbox_center_y": (min_y + max_y) / 2,
        "width": max_x - min_x,
        "height": max_y - min_y,
    }
