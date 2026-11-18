"""Exercise overview selection without importing or connecting to microscope services."""

import ast
import shutil
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


class OverviewSelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / "data"
        self.output = self.root / "zen"
        self.data.mkdir()
        self.output.mkdir()
        self.existing = self.data / "OverViewScan.czi"
        self.existing.write_bytes(b"existing overview")
        self.acquired = self.output / "actual_overview_OV.czi"
        self.acquired.write_bytes(b"acquired overview")

        self.service = SimpleNamespace(
            get_image_output_path=AsyncMock(
                return_value=SimpleNamespace(image_output_path=str(self.output))
            )
        )
        self.scan = AsyncMock(return_value=SimpleNamespace(output_name=self.acquired.stem))
        results = MagicMock()
        results.shape = (0,)

        def segment(source, destination, **kwargs):
            Path(destination).mkdir()
            return None, results, None, {"Number": SimpleNamespace(values=[0])}

        self.segment = MagicMock(side_effect=segment)
        self.planetable = MagicMock()
        self.planetable.get_planetable.return_value = (MagicMock(), None)
        self.channel = MagicMock()
        self.namespace = {
            "__file__": str(self.root / "zenapi_guidedacq.py"),
            "Path": Path,
            "datetime": datetime,
            "UTC": UTC,
            "shutil": shutil,
            "data_dir": self.data,
            "logger": MagicMock(),
            "initialize_zenapi": MagicMock(return_value=(self.channel, {})),
            "ExperimentServiceStub": MagicMock(return_value=self.service),
            "ExperimentServiceGetImageOutputPathRequest": MagicMock(),
            "gca_run_overviewscan": self.scan,
            "zen_tcpip_commands": MagicMock(),
            "ZenCommands": MagicMock(),
            "planetable": self.planetable,
            "np": MagicMock(),
            "segment_czi": self.segment,
            "show_segmentation": MagicMock(),
            "TilesServiceStub": MagicMock(),
            "trange": range,
        }
        source = self.root_source()
        tree = ast.parse(source.read_text(encoding="utf-8"))
        main = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "main")
        module = ast.Module(
            body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), main],
            type_ignores=[],
        )
        exec(compile(ast.fix_missing_locations(module), str(source), "exec"), self.namespace)

    @staticmethod
    def root_source():
        return Path(__file__).resolve().parents[1] / "examples" / "control_zen_via_zenapi" / "zenapi_guidedacq.py"

    async def test_default_loads_existing_overview_without_acquiring(self):
        await self.namespace["main"]()
        self.scan.assert_not_awaited()
        self.assert_overview_used(self.existing)

    async def test_disabled_loading_acquires_before_reading_and_segmenting(self):
        def read_planetable(path, **kwargs):
            self.scan.assert_awaited_once_with("00000_Overview_15x8_DAPI_5X", self.service)
            self.assertEqual(path, self.acquired)
            return MagicMock(), None

        self.planetable.get_planetable.side_effect = read_planetable
        await self.namespace["main"](load_overview=False)
        self.assert_overview_used(self.acquired)

    async def test_acquisition_failure_does_not_process_existing_overview(self):
        self.scan.side_effect = RuntimeError("Acquisition failed")
        with self.assertRaisesRegex(RuntimeError, "Acquisition failed"):
            await self.namespace["main"](load_overview=False)
        self.planetable.get_planetable.assert_not_called()
        self.segment.assert_not_called()

    def assert_overview_used(self, expected):
        self.assertEqual(self.planetable.get_planetable.call_args.args[0], expected)
        self.assertEqual(self.segment.call_args.args[0], str(expected))
        destination = Path(self.segment.call_args.args[1]) / f"{expected.stem}_OV.czi"
        self.assertEqual(destination.read_bytes(), expected.read_bytes())
        self.namespace["zen_tcpip_commands"].add_image.assert_called_once_with(str(expected))
        self.channel.close.assert_called_once()
