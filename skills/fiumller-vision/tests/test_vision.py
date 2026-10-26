"""Regression checks for skill routing and geometry findings."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
TEST_ROOT = Path(__file__).resolve().parents[4] / "work" / "test_runs" / "fiumller_skill_completion"
sys.path.insert(0, str(SCRIPTS))

from detect_anomalies import detect_anomalies
import render_inspect
from udr_source import load_udr


class GeometryTests(unittest.TestCase):
    def test_missing_page_is_not_clear(self):
        with self.assertRaises(ValueError):
            detect_anomalies({"pages": [{"page_number": 1, "width_pt": 100,
                                          "height_pt": 100, "frames": []}]}, 2)

    def test_legacy_margin_is_opt_in(self):
        udr = {"pages": [{"page_number": 7, "width_pt": 100, "height_pt": 100,
                          "frames": [{"id": "t", "kind": "Text",
                                      "rect_pt": [10, 20, 90, 30]}]}], "stories": {}}
        self.assertEqual(detect_anomalies(udr, 7), [])
        self.assertEqual(detect_anomalies(udr, 7, True)[0]["type"], "LEGACY_RIGHT_MARGIN")

    def test_invalid_rect_and_outside_text_are_candidates(self):
        udr = {"pages": [{"page_number": 1, "width_pt": 100, "height_pt": 100,
                          "frames": [
                              {"id": "bad", "kind": "Text", "rect_pt": [20, 0, 10, 10]},
                              {"id": "outside", "kind": "Text", "rect_pt": [1, 1, 101, 10]},
                          ]}], "stories": {}}
        self.assertEqual([v["type"] for v in detect_anomalies(udr, 1)],
                         ["INVALID_RECT", "TEXT_OUTSIDE_PAGE"])

    def test_two_inputs_do_not_share_udr(self):
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            root = Path(temp)
            self.assertTrue(root.resolve().is_relative_to(TEST_ROOT.resolve()))
            first = root / "first.folio"
            second = root / "second.folio"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            engine = root / "act4.exe"
            engine.write_bytes(b"test")

            def fake_run(command, **_kwargs):
                output = Path(command[command.index("-o") + 1])
                source = Path(command[2])
                (output / "udr.json").write_text(json.dumps({"pages": [], "source": source.name}),
                                                 encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("udr_source.subprocess.run", side_effect=fake_run):
                one, path_one = load_udr(str(first), str(root / "outputs"), str(engine))
                two, path_two = load_udr(str(second), str(root / "outputs"), str(engine))
            self.assertNotEqual(path_one.parent, path_two.parent)
            self.assertEqual(one["source"], "first.folio")
            self.assertEqual(two["source"], "second.folio")

    def test_failed_render_preserves_existing_output(self):
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as temp:
            root = Path(temp)
            self.assertTrue(root.resolve().is_relative_to(TEST_ROOT.resolve()))
            source = root / "input.pdf"
            source.write_bytes(b"test")
            output = root / "result.png"
            output.write_bytes(b"original")

            def corrupt_render(_source, _page, _dpi, temporary, _executable):
                temporary.write_bytes(b"not a PNG")

            argv = ["render_inspect.py", str(source), "--page", "1", "--output",
                    str(output), "--overwrite"]
            with patch.object(sys, "argv", argv), patch.object(render_inspect, "find_executable",
                                                       return_value=root / "fiumller.exe"), \
                    patch.object(render_inspect, "render_pdf", side_effect=corrupt_render):
                self.assertEqual(render_inspect.main(), 1)
            self.assertEqual(output.read_bytes(), b"original")
            self.assertEqual(list(root.glob("*.tmp.png")), [])


if __name__ == "__main__":
    unittest.main()
