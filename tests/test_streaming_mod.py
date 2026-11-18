"""Test streaming threshold analysis without loading GUI or microscope services."""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from skimage.filters import threshold_otsu


class StreamingInversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        examples = Path(__file__).resolve().parents[1] / "examples" / "control_zen_via_zenapi"
        processor_tree = ast.parse((examples / "processing_tools.py").read_text(encoding="utf-8"))
        processor = next(node for node in processor_tree.body if isinstance(node, ast.ClassDef)
                         and node.name == "ArrayProcessor")
        processor.body = [node for node in processor.body if isinstance(node, ast.FunctionDef)
                          and node.name in ("__init__", "apply_otsu_threshold")]
        source = examples / "zenapi_streaming_mod.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
        reader = next(node for node in window.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "read")
        otsu = next(node for node in ast.walk(reader) if isinstance(node, ast.If)
                    and ast.unparse(node.test) == "processing is Processing.SEG_THRESHOLD_OTSU")
        function = ast.parse(
            "def analyze(image, invert=False):\n"
            "    ap = ArrayProcessor(image)\n"
            "    processing = Processing.SEG_THRESHOLD_OTSU\n"
            "    pass\n"
            "    return pro2d\n"
        ).body[0]
        function.body[2] = otsu
        namespace = {"np": np, "threshold_otsu": threshold_otsu,
                     "Processing": SimpleNamespace(SEG_THRESHOLD_OTSU=object())}
        module = ast.Module(body=[processor, function], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), str(source), "exec"), namespace)
        cls.analyze = staticmethod(namespace["analyze"])

    def test_inversion_detects_dark_objects_without_changing_original(self):
        for dtype, background in ((np.uint8, 200), (np.uint16, 3000), (np.float32, 200)):
            with self.subTest(dtype=dtype):
                image = np.full((20, 20), background, dtype=dtype)
                image[5:15, 5:15] = 20
                original = image.copy()
                mask = self.analyze(image, invert=True)
                expected = np.zeros(image.shape, dtype=bool)
                expected[5:15, 5:15] = True
                np.testing.assert_array_equal(mask, expected)
                np.testing.assert_array_equal(image, original)

    def test_default_preserves_bright_object_detection(self):
        image = np.full((20, 20), 20, dtype=np.uint16)
        image[5:15, 5:15] = 3000
        expected = image >= threshold_otsu(image)
        np.testing.assert_array_equal(self.analyze(image), expected)
