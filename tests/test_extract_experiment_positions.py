"""Offline position and rectangular tile-region extraction tests."""

import copy
import csv
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from zen_api_utils.extract_czexp_plan import extract_plan

from examples.control_zen_via_zenapi.extract_experiment_positions import (
    CSV_FIELDS,
    DEFAULT_EXPERIMENT,
    WORKSHOP_ROOT,
    Position,
    TileRegion,
    extract_positions,
    extract_positions_dataframe,
    main,
)


TILE_EXPERIMENT = WORKSHOP_ROOT / "data" / "ZEN_API_OMEZARR_Stream_Wellplate_Tiles.czexp"


class ExperimentPositionTests(unittest.TestCase):
    def test_dataframe_preserves_rows_and_missing_dimensions_without_printing(self):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            table = extract_positions_dataframe(str(DEFAULT_EXPERIMENT))
        self.assertIsInstance(table, pd.DataFrame)
        self.assertEqual(list(table.columns), list(CSV_FIELDS))
        records = table.astype(object).where(table.notna(), None).to_dict("records")
        self.assertEqual(records, [position.to_record() for position in extract_positions(DEFAULT_EXPERIMENT)])
        self.assertEqual(str(table["well_index"].dtype), "Int64")
        self.assertEqual(str(table["tile_width_um"].dtype), "Float64")
        self.assertEqual(str(table["position_id"].dtype), "string")
        self.assertTrue(table[["tile_width_um", "tile_height_um"]].isna().all().all())
        self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", ""))

    def test_dataframe_preserves_tile_dimensions_and_coordinate_precision(self):
        table = extract_positions_dataframe(TILE_EXPERIMENT)
        self.assertEqual(
            table.to_dict("records"), [position.to_record() for position in extract_positions(TILE_EXPERIMENT)]
        )
        self.assertEqual(
            table.loc[0, ["tile_width_um", "tile_height_um"]].tolist(),
            [548.8, 396.48],
        )

    def test_rectangular_region_dimensions(self):
        positions = extract_positions(TILE_EXPERIMENT)
        self.assertTrue(all(isinstance(position, Position) for position in positions))
        self.assertEqual(positions[0].tile_region, TileRegion("Rectangle", 548.8, 396.48))
        self.assertEqual((positions[0].x, positions[0].y, positions[0].z), (4500.0, 8500.0, -8000.0))
        rows = [position.to_record() for position in positions]
        self.assertEqual([row["well_id"] for row in rows], ["A1", "C3", "D1"])
        self.assertEqual([row["well_index"] for row in rows], [0, 1, 2])
        for row in rows:
            self.assertEqual((row["tile_width_um"], row["tile_height_um"]), (548.8, 396.48))
        self.assertEqual(
            (rows[0]["stage_x_um"], rows[0]["stage_y_um"], rows[0]["stage_z_um"]),
            (4500.0, 8500.0, -8000.0),
        )

    def test_single_positions_have_no_tile_dimensions(self):
        plan = extract_plan(DEFAULT_EXPERIMENT)
        positions = [position for block in plan["acquisition_blocks"] for position in block["positions"]]
        extracted = extract_positions(str(DEFAULT_EXPERIMENT))
        self.assertEqual(len(extracted), len(positions))
        for field, position in zip(extracted, positions):
            self.assertIsInstance(field, Position)
            self.assertEqual(field.position_id, position["position_id"])
            self.assertEqual(field.x, position["stage_position"]["x"])
            self.assertEqual(field.y, position["stage_position"]["y"])
            self.assertEqual(field.z, position["stage_position"]["z"])
            self.assertIsNone(field.tile_region)

    def test_nonrectangular_and_disabled_regions(self):
        tree = ET.parse(TILE_EXPERIMENT)
        regions = tree.findall(".//TileRegions/TileRegion")
        regions[0].find("Contour").set("Type", "Ellipse")
        regions[0].remove(regions[0].find("ContourSize"))
        regions[1].find("IsUsedForAcquisition").text = "false"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "regions.czexp"
            tree.write(source, encoding="utf-8")
            positions = extract_positions(source)
        self.assertEqual(positions[0].tile_region, TileRegion("Ellipse"))
        rows = [position.to_record() for position in positions]
        self.assertEqual([row["well_id"] for row in rows], ["A1", "D1"])
        self.assertIsNone(rows[0]["tile_width_um"])
        self.assertIsNone(rows[0]["tile_height_um"])
        self.assertEqual(rows[1]["tile_width_um"], 548.8)

    def test_unassigned_well_stays_missing_in_model_and_dataframe(self):
        plan = extract_plan(DEFAULT_EXPERIMENT)
        position = plan["acquisition_blocks"][0]["positions"][0]
        for key in ("well_id", "well_row", "well_column"):
            position[key] = None
        with patch(
            "examples.control_zen_via_zenapi.extract_experiment_positions.extract_plan",
            return_value=plan,
        ):
            field = extract_positions(DEFAULT_EXPERIMENT)[0]
            table = extract_positions_dataframe(DEFAULT_EXPERIMENT)
        self.assertEqual((field.well_id, field.well_row, field.well_column, field.well_index), (None,) * 4)
        self.assertTrue(table.loc[0, ["well_id", "well_row", "well_column", "well_index"]].isna().all())
        self.assertEqual(str(table["well_index"].dtype), "Int64")

    def test_invalid_rectangular_dimensions_raise(self):
        for size in ("", "548.8", "1,2,3", "bad,396.48", "nan,1", "1,inf", "-1,2", "1,0"):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as directory:
                tree = ET.parse(TILE_EXPERIMENT)
                tree.find(".//TileRegions/TileRegion/ContourSize").text = size
                source = Path(directory) / "invalid.czexp"
                tree.write(source, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Invalid rectangular tile-region ContourSize"):
                    extract_positions(source)

    def test_dimensions_are_scoped_to_active_block_and_setup(self):
        tree = ET.parse(TILE_EXPERIMENT)
        blocks = tree.find("ExperimentBlocks")
        original = blocks.find("AcquisitionBlock")
        inactive = copy.deepcopy(original)
        inactive.set("IsActivated", "false")
        blocks.insert(0, inactive)
        second = copy.deepcopy(original)
        second.find(".//TileRegions/TileRegion/ContourSize").text = "100,200"
        blocks.append(second)
        setup = original.find(".//RegionsSetup")
        inactive_setup = copy.deepcopy(setup)
        inactive_setup.set("IsActivated", "false")
        inactive_setup.find(".//TileRegions/TileRegion/ContourSize").text = "999,999"
        parent = next(element for element in original.iter() if setup in list(element))
        parent.insert(0, inactive_setup)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "blocks.czexp"
            tree.write(source, encoding="utf-8")
            rows = [position.to_record() for position in extract_positions(source)]
        self.assertEqual([row["block_index"] for row in rows], [1, 1, 1, 2, 2, 2])
        self.assertEqual((rows[0]["tile_width_um"], rows[0]["tile_height_um"]), (548.8, 396.48))
        self.assertEqual((rows[3]["tile_width_um"], rows[3]["tile_height_um"]), (100.0, 200.0))

    def test_cli_exports_dimensions_to_table_and_csv(self):
        output = StringIO()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "positions.csv"
            with (
                patch("sys.argv", ["extract", str(TILE_EXPERIMENT), "--output", str(destination)]),
                patch.dict("os.environ", {"COLUMNS": "300"}),
                redirect_stdout(output),
                redirect_stderr(StringIO()),
            ):
                main()
            with destination.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["tile_width_um"], "548.8")
        self.assertEqual(rows[0]["tile_height_um"], "396.48")
        self.assertIn("Tile width (um)", output.getvalue())
        self.assertIn("548.800", output.getvalue())
        self.assertIn("396.480", output.getvalue())


if __name__ == "__main__":
    unittest.main()
