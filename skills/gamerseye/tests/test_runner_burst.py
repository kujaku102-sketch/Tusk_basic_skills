"""直の道の 15FPS 連射の道具 (burst_capture) の試験。窓・API には触らない。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image  # noqa: E402

from gamerseye_core import runner as rn  # noqa: E402


class SampleFramesTest(unittest.TestCase):
    def test_keeps_first_last_and_order(self):
        frames = list(range(23))  # 1.5 秒 x 15FPS
        s = rn.sample_frames(frames, 6)
        self.assertEqual(len(s), 6)
        self.assertEqual(s[0], 0)
        self.assertEqual(s[-1], 22)
        self.assertEqual(s, sorted(s))

    def test_short_and_empty(self):
        self.assertEqual(rn.sample_frames([1, 2, 3], 6), [1, 2, 3])
        self.assertEqual(rn.sample_frames([], 6), [])
        self.assertEqual(rn.sample_frames([1, 2, 3], 1), [3])


class FakeVision:
    def __init__(self):
        self.last_capture = None
        self.burst_calls = []

    def capture(self, ensure_foreground=False):
        self.last_capture = Image.new("RGB", (4, 4), "white")
        return self.last_capture

    def capture_burst(self, duration=1.5, fps=15, mode="full"):
        self.burst_calls.append((duration, fps, mode))
        return [Image.new("RGB", (4, 4), "white") for _ in range(int(round(duration * fps)))]

    def calculate_diff_metrics(self, a, b):
        return {"ui_changed": False, "changed_pixel_ratio": 0.0, "changed_bbox": None}


class BurstToolTest(unittest.TestCase):
    def _runner(self):
        r = object.__new__(rn.ComputerUseRunner)
        r.vision = FakeVision()
        r.history = []
        r.final_summary = ""
        r.pending_burst = []
        r.burst_history = []
        return r

    def test_burst_capture_keeps_frames_for_next_turn(self):
        r = self._runner()
        finished, rec = r.execute_tool_call("burst_capture", {"seconds": 1.0, "reasoning": "drag feedback"}, 1)
        self.assertFalse(finished)
        self.assertIsNone(rec.error)
        self.assertEqual(r.vision.burst_calls, [(1.0, 15, "full")])
        self.assertEqual(len(r.pending_burst), 15)
        self.assertEqual(len(r.burst_history), 1)
        self.assertEqual(rec.args["frames"], 15)

    def test_burst_seconds_are_clamped(self):
        r = self._runner()
        r.execute_tool_call("burst_capture", {"seconds": 30, "reasoning": "x"}, 1)
        r.execute_tool_call("burst_capture", {"seconds": 0.01, "reasoning": "x"}, 2)
        self.assertEqual([c[0] for c in r.vision.burst_calls], [3.0, 0.5])


if __name__ == "__main__":
    unittest.main()
