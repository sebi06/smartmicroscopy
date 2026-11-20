"""Test output collection without importing or connecting to microscope services."""

import ast
import shutil
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
from skimage.filters import threshold_otsu

from examples.control_zen_via_zenapi.processing_tools import ArrayProcessor


class WellPositionOutputTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "zen"
        self.output.mkdir()
        self.target = self.root / "target"
        self.channel = MagicMock()
        self.service = SimpleNamespace(
            get_image_output_path=AsyncMock(return_value=SimpleNamespace(image_output_path=str(self.output))),
            load=AsyncMock(return_value=SimpleNamespace(experiment_id="original")),
            clone=AsyncMock(return_value=SimpleNamespace(experiment_id="clone")),
            run_experiment=AsyncMock(side_effect=self.acquire),
            export=AsyncMock(return_value=SimpleNamespace(xml="<Experiment />")),
        )
        self.sources = []
        self.namespace = {
            "Path": Path,
            "datetime": datetime,
            "UTC": UTC,
            "shutil": shutil,
            "logger": MagicMock(),
            "run_experiment": True,
            "max_objects_per_well": 100,
            "save_cloned_experiment": True,
            "target_folder": self.target,
            "exp_name": Path("plate.czexp"),
            "config_path": self.root / "config.ini",
            "count_czi_objects": MagicMock(return_value=7),
            "print_positions": MagicMock(),
            "positions": [
                SimpleNamespace(x=1.0, y=2.0, z=3.0, well_id="A1"),
                SimpleNamespace(x=1.0, y=2.0, z=3.0, well_id="C3"),
            ],
            "initialize_zenapi": MagicMock(return_value=(self.channel, {})),
            "ExperimentServiceStub": MagicMock(return_value=self.service),
            "TilesServiceStub": MagicMock(return_value=SimpleNamespace(clear=AsyncMock(), add_positions=AsyncMock())),
        }
        for name in (
            "ExperimentServiceGetImageOutputPathRequest",
            "ExperimentServiceLoadRequest",
            "ExperimentServiceCloneRequest",
            "ExperimentServiceExportRequest",
            "ExperimentServiceRunExperimentRequest",
            "TilesServiceClearRequest",
            "TilesServiceAddPositionsRequest",
            "Position3D",
        ):
            self.namespace[name] = SimpleNamespace
        self.namespace["extract_positions"] = MagicMock(side_effect=lambda path: self.namespace["positions"])
        source = (
            Path(__file__).resolve().parents[1]
            / "examples"
            / "control_zen_via_zenapi"
            / "run_experiment_well_positions.py"
        )
        tree = ast.parse(source.read_text(encoding="utf-8"))
        functions = [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in {"main", "save_diagnostic_experiment", "move_acquired_files"}
        ]
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), self.namespace)

    async def acquire(self, request):
        self.assertTrue(all(source.exists() for source in self.sources))
        self.assertFalse(any(self.target.rglob("*.czi")))
        index = int(request.output_name.rsplit("_P", 1)[1])
        diagnostic = next(self.target.iterdir()) / f"position_{index:04d}.czexp"
        self.assertEqual(diagnostic.read_text(encoding="utf-8"), "<Experiment />")
        source = self.output / f"zen_actual_{len(self.sources)}.czi"
        source.write_bytes(f"acquired {len(self.sources)}".encode())
        self.sources.append(source)
        return SimpleNamespace(output_name=source.stem)

    async def test_moves_only_run_files_after_all_acquisitions(self):
        unrelated = self.output / "unrelated.czi"
        unrelated.write_bytes(b"keep")
        await self.namespace["main"]()
        self.namespace["extract_positions"].assert_called_once_with(Path("plate.czexp"))
        self.namespace["print_positions"].assert_called_once_with(self.namespace["positions"], title="plate.czexp")
        folders = list(self.target.iterdir())
        self.assertEqual(len(folders), 1)
        self.assertTrue(folders[0].name.startswith("plate_wellpositions_"))
        datetime.strptime(folders[0].name.removeprefix("plate_wellpositions_"), "%Y-%m-%d_%H-%M-%S")
        self.assertEqual(
            sorted(path.name for path in folders[0].glob("*.czi")), [source.name for source in self.sources]
        )
        self.assertEqual(len(list(folders[0].glob("*.czexp"))), 2)
        for index, source in enumerate(self.sources):
            self.assertFalse(source.exists())
            self.assertEqual((folders[0] / source.name).read_bytes(), f"acquired {index}".encode())
        self.assertEqual(unrelated.read_bytes(), b"keep")
        requests = [call.args[0] for call in self.service.run_experiment.await_args_list]
        self.assertEqual(
            [request.output_name for request in requests],
            ["plate_wellpositions_A1_P001", "plate_wellpositions_C3_P002"],
        )
        self.channel.close.assert_called_once()
        self.assertEqual([call.args[0] for call in self.namespace["count_czi_objects"].call_args_list], self.sources)
        self.namespace["logger"].info.assert_any_call(f"Object count: 7 | Well A1 | P001 | CZI: {self.sources[0]}")

    async def test_acquisition_failure_leaves_files_in_zen_output(self):
        async def fail_second(request):
            if self.sources:
                raise RuntimeError("Acquisition failed")
            return await self.acquire(request)

        self.service.run_experiment.side_effect = fail_second
        with self.assertRaisesRegex(RuntimeError, "Acquisition failed"):
            await self.namespace["main"]()
        self.assertTrue(self.sources[0].exists())
        self.assertFalse(any(self.target.rglob("*.czi")))
        self.assertEqual(len(list(self.target.rglob("*.czexp"))), 2)
        self.channel.close.assert_called_once()

    async def test_missing_output_is_detected_before_any_moves(self):
        async def missing_second(request):
            result = await self.acquire(request)
            if len(self.sources) == 2:
                self.sources[-1].unlink()
            return result

        self.service.run_experiment.side_effect = missing_second
        with self.assertRaisesRegex(FileNotFoundError, "Acquired CZI not found"):
            await self.namespace["main"]()
        self.assertTrue(self.sources[0].exists())
        self.assertFalse(any(self.target.rglob("*.czi")))

    async def test_existing_run_folder_is_not_reused(self):
        fixed_time = datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC)
        self.namespace["datetime"] = MagicMock()
        self.namespace["datetime"].now.return_value = fixed_time
        existing = self.target / "plate_wellpositions_2026-11-19_12-00-00"
        existing.mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            await self.namespace["main"]()
        self.namespace["initialize_zenapi"].assert_not_called()

    async def test_disabled_acquisition_does_not_create_or_move_outputs(self):
        self.namespace["run_experiment"] = False
        self.namespace["save_cloned_experiment"] = False
        await self.namespace["main"]()
        self.service.run_experiment.assert_not_awaited()
        self.service.get_image_output_path.assert_not_awaited()
        self.assertFalse(self.target.exists())
        self.namespace["count_czi_objects"].assert_not_called()
        self.channel.close.assert_called_once()

    async def test_position_coordinates_are_converted_from_micrometers_to_meters(self):
        self.namespace["positions"] = [SimpleNamespace(x=4500.0, y=8500.0, z=-8000.0, well_id="A1")]
        await self.namespace["main"]()
        tile_service = self.namespace["TilesServiceStub"].return_value
        request = tile_service.add_positions.await_args.args[0]
        position = request.positions[0]
        self.assertAlmostEqual(position.x, 0.0045)
        self.assertAlmostEqual(position.y, 0.0085)
        self.assertAlmostEqual(position.z, -0.008)

    async def test_diagnostics_can_be_saved_without_running_acquisition(self):
        self.namespace["run_experiment"] = False
        await self.namespace["main"]()
        self.assertEqual(len(list(self.target.rglob("*.czexp"))), 2)
        self.service.run_experiment.assert_not_awaited()
        self.service.get_image_output_path.assert_not_awaited()
        self.channel.close.assert_called_once()

    async def test_export_failure_stops_before_acquisition(self):
        self.service.export.side_effect = RuntimeError("Export failed")
        with self.assertRaisesRegex(RuntimeError, "Export failed"):
            await self.namespace["main"]()
        self.service.run_experiment.assert_not_awaited()
        self.channel.close.assert_called_once()

    async def test_fourth_position_uses_three_digit_index_without_timestamp(self):
        self.namespace["positions"] = [SimpleNamespace(x=1.0, y=2.0, z=3.0, well_id="D5")] * 4
        await self.namespace["main"]()
        request = self.service.run_experiment.await_args.args[0]
        self.assertEqual(request.output_name, "plate_wellpositions_D5_P004")

    async def test_unassigned_well_has_explicit_filename_label(self):
        self.namespace["positions"] = [SimpleNamespace(x=1.0, y=2.0, z=3.0, well_id=None)]
        await self.namespace["main"]()
        request = self.service.run_experiment.await_args.args[0]
        self.assertEqual(request.output_name, "plate_wellpositions_Unassigned_P001")

    async def test_existing_source_filename_is_not_overwritten(self):
        existing = self.output / "plate_wellpositions_A1_P001.czi"
        existing.write_bytes(b"previous run")
        with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite an existing acquisition"):
            await self.namespace["main"]()
        self.service.run_experiment.assert_not_awaited()
        self.assertEqual(existing.read_bytes(), b"previous run")
        self.channel.close.assert_called_once()

    async def test_counting_failure_leaves_acquired_file_in_place_and_closes_channel(self):
        self.namespace["count_czi_objects"].side_effect = ValueError("Invalid image")
        with self.assertRaisesRegex(ValueError, "Invalid image"):
            await self.namespace["main"]()
        self.assertTrue(self.sources[0].exists())
        self.assertFalse(any(self.target.rglob("*.czi")))
        self.channel.close.assert_called_once()

    async def test_cumulative_target_skips_remaining_well_positions_and_continues_in_order(self):
        self.namespace["positions"] = [
            SimpleNamespace(x=1.0, y=2.0, z=3.0, well_id=well) for well in ("A1", "A1", "A1", "B2", "A1", "B2", "B2")
        ]
        self.namespace["count_czi_objects"].side_effect = [40, 60, 110]
        await self.namespace["main"]()
        names = [call.args[0].output_name for call in self.service.run_experiment.await_args_list]
        self.assertEqual(
            names,
            ["plate_wellpositions_A1_P001", "plate_wellpositions_A1_P002", "plate_wellpositions_B2_P004"],
        )
        tile_service = self.namespace["TilesServiceStub"].return_value
        self.assertEqual(tile_service.clear.await_count, 3)
        self.assertEqual(tile_service.add_positions.await_count, 3)
        self.assertEqual(self.service.export.await_count, 3)
        self.assertEqual(self.namespace["count_czi_objects"].call_count, 3)
        self.assertEqual(len(list(self.target.rglob("*.czi"))), 3)
        self.assertEqual(
            sorted(path.name for path in self.target.rglob("*.czexp")),
            ["position_0001.czexp", "position_0002.czexp", "position_0004.czexp"],
        )
        self.namespace["logger"].info.assert_any_call("Well A1: cumulative object count 100 / 100")
        self.namespace["logger"].info.assert_any_call("Well B2: cumulative object count 110 / 100")

    async def test_positions_without_well_assignment_do_not_share_a_limit(self):
        self.namespace["positions"] = [SimpleNamespace(x=1.0, y=2.0, z=3.0, well_id=None)] * 2
        self.namespace["count_czi_objects"].return_value = 150
        await self.namespace["main"]()
        self.assertEqual(self.service.run_experiment.await_count, 2)

    async def test_invalid_well_target_fails_before_connecting(self):
        for target in (0, -1):
            with self.subTest(target=target):
                self.namespace["max_objects_per_well"] = target
                with self.assertRaisesRegex(ValueError, "max_objects_per_well must be positive"):
                    await self.namespace["main"]()
        self.namespace["initialize_zenapi"].assert_not_called()
        self.assertFalse(self.target.exists())


class CziObjectCountingTests(unittest.TestCase):
    def setUp(self):
        self.reader = MagicMock()
        source = (
            Path(__file__).resolve().parents[1]
            / "examples"
            / "control_zen_via_zenapi"
            / "run_experiment_well_positions.py"
        )
        tree = ast.parse(source.read_text(encoding="utf-8"))
        helper = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "count_czi_objects"
        )
        namespace = {
            "Path": Path,
            "np": np,
            "threshold_otsu": threshold_otsu,
            "ArrayProcessor": ArrayProcessor,
            "read_tools": self.reader,
        }
        exec(compile(ast.Module(body=[helper], type_ignores=[]), str(source), "exec"), namespace)
        self.count = namespace["count_czi_objects"]

    def test_counts_bright_objects_excluding_noise_and_border(self):
        image = np.zeros((40, 40), dtype=np.uint16)
        image[5:10, 5:10] = 1000
        image[20:25, 20:25] = 1000
        image[15, 15] = 1000
        image[:5, 30:35] = 1000
        for sample_axis in (False, True):
            with self.subTest(sample_axis=sample_axis):
                data = image.reshape((1, 1, 1, 1, 40, 40))
                if sample_axis:
                    data = data[..., np.newaxis]
                self.reader.read_6darray.return_value = (data, None)
                self.assertEqual(self.count(Path("acquired.czi")), 2)
        self.reader.read_6darray.assert_called_with(
            Path("acquired.czi"),
            planes={"S": (0, 0), "T": (0, 0), "C": (0, 0), "Z": (0, 0)},
            use_dask=False,
            use_xarray=False,
        )

    def test_constant_image_has_no_objects(self):
        self.reader.read_6darray.return_value = (np.full((1, 1, 1, 1, 20, 20), 50, dtype=np.uint16), None)
        self.assertEqual(self.count(Path("constant.czi")), 0)

    def test_custom_plane_selection_keeps_defaults_for_other_axes(self):
        self.reader.read_6darray.return_value = (np.zeros((1, 1, 1, 1, 20, 20), dtype=np.uint16), None)
        planes = {"C": (1, 1), "Z": (2, 2)}
        self.assertEqual(self.count(Path("selected.czi"), planes=planes), 0)
        self.reader.read_6darray.assert_called_once_with(
            Path("selected.czi"),
            planes={"S": (0, 0), "T": (0, 0), "C": (1, 1), "Z": (2, 2)},
            use_dask=False,
            use_xarray=False,
        )
        self.assertEqual(planes, {"C": (1, 1), "Z": (2, 2)})

    def test_multiple_selected_planes_are_rejected(self):
        self.reader.read_6darray.return_value = (np.zeros((1, 1, 2, 1, 20, 20), dtype=np.uint16), None)
        with self.assertRaisesRegex(ValueError, "Expected a single STCZ plane"):
            self.count(Path("channels.czi"), planes={"C": (0, 1)})

    def test_missing_image_data_and_rgb_are_rejected(self):
        self.reader.read_6darray.return_value = (None, None)
        with self.assertRaisesRegex(TypeError, "Expected a NumPy image array"):
            self.count(Path("missing.czi"))
        self.reader.read_6darray.return_value = (np.zeros((1, 1, 1, 1, 20, 20, 3), dtype=np.uint8), None)
        with self.assertRaisesRegex(ValueError, "Expected a grayscale YX plane"):
            self.count(Path("rgb.czi"))


if __name__ == "__main__":
    unittest.main()
