import unittest

from scr.kicad_extract import calculate_bounding_box, validate_result
from scr.kicad_parser import PCBDimensions, transform_footprint_point
from scr.kicad_repair import calculate_bounding_box_from_pads
from scr.pcb_viewer_model import _parse_primitive


class GeometryTests(unittest.TestCase):
    def test_footprint_rotations(self):
        origin = (10.0, 20.0)
        expected = {
            0: (11.0, 22.0),
            90: (12.0, 19.0),
            -90: (8.0, 21.0),
            180: (9.0, 18.0),
            270: (8.0, 21.0),
        }
        for rotation, point in expected.items():
            self.assertEqual(transform_footprint_point(1.0, 2.0, *origin, rotation), point)

    def test_u4_courtyard_bbox(self):
        component = {
            "position": (177.0761, 111.4806),
            "rotation": -90.0,
            "fp_lines": [
                {"start": (-1.55, -1.60), "end": (9.15, -1.60)},
                {"start": (9.15, -1.60), "end": (9.15, 34.65)},
                {"start": (9.15, 34.65), "end": (-1.55, 34.65)},
                {"start": (-1.55, 34.65), "end": (-1.55, -1.60)},
            ],
        }
        bbox = calculate_bounding_box(component)
        self.assertAlmostEqual(bbox["bbox_center_x"], 160.5511, places=4)
        self.assertAlmostEqual(bbox["bbox_center_y"], 115.2806, places=4)
        self.assertAlmostEqual(bbox["width"], 36.25, places=4)
        self.assertAlmostEqual(bbox["height"], 10.70, places=4)

    def test_viewer_geometry_matches_extractor_rotations(self):
        origin = (10.0, 20.0)
        point = (1.0, 2.0)
        block = """(fp_line
            (start 1.0 2.0)
            (end 1.0 2.0)
            (stroke (width 0.1) (type default))
            (layer "F.CrtYd")
        )"""
        for rotation in (0.0, 90.0, -90.0, 180.0):
            primitive = _parse_primitive(block, origin, rotation)
            self.assertIsNotNone(primitive)
            expected = transform_footprint_point(*point, *origin, rotation)
            self.assertEqual(primitive.points, [expected, expected])

    def test_viewer_u4_geometry_matches_extractor_bbox(self):
        origin = (177.0761, 111.4806)
        rotation = -90.0
        courtyard = [
            ((-1.55, -1.60), (9.15, -1.60)),
            ((9.15, -1.60), (9.15, 34.65)),
            ((9.15, 34.65), (-1.55, 34.65)),
            ((-1.55, 34.65), (-1.55, -1.60)),
        ]
        points = [
            transform_footprint_point(x, y, *origin, rotation)
            for start, end in courtyard
            for x, y in (start, end)
        ]
        viewer_bbox = (
            min(x for x, _ in points),
            min(y for _, y in points),
            max(x for x, _ in points),
            max(y for _, y in points),
        )
        self.assertEqual(viewer_bbox, (142.4261, 109.9306, 178.6761, 120.6306))

    def test_pad_bbox_uses_footprint_and_pad_rotations(self):
        component = {
            "position": (10.0, 20.0),
            "rotation": 90.0,
            "pads": [{"position": (2.0, 0.0), "size": (2.0, 1.0), "rotation": 90.0}],
        }
        bbox = calculate_bounding_box_from_pads(component, margin=0.0)
        self.assertAlmostEqual(bbox["bbox_center_x"], 10.0)
        self.assertAlmostEqual(bbox["bbox_center_y"], 18.0)
        self.assertAlmostEqual(bbox["width"], 2.0)
        self.assertAlmostEqual(bbox["height"], 1.0)

    def test_outside_edge_cuts_warning_contains_details(self):
        component = {
            "name": "Package:DIP",
            "reference": "U4",
            "value": "ATMEGA328P-PU",
            "bbox_center_x": 160.5511,
            "bbox_center_y": 115.2806,
            "width": 36.25,
            "height": 10.70,
        }
        dimensions = PCBDimensions(0, 30, 0, 30, 15, 15, 30, 30)
        warnings = validate_result([component], dimensions, type("Stats", (), {"skipped_components": 0})())
        self.assertIn("U4", warnings[-1])
        self.assertIn("ATMEGA328P-PU", warnings[-1])
        self.assertIn("Edge.Cuts", warnings[-1])
        self.assertIn("142.4261", warnings[-1])


if __name__ == "__main__":
    unittest.main()
