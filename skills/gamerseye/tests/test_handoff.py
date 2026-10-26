import json, subprocess, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gamerseye_core import handoff as h


class T(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.png = self.d / "a.png"
        self.png.write_bytes(b"x")
        self.spec = {"target": {"pid": 1}, "items": [{"id": "A", "check": "menu opens"}, {"id": "B", "check": "closes"}],
                     "out_dir": str(self.d / "o"), "timeout": 5}
        self.sp = self.d / "spec.json"
        self.sp.write_text(json.dumps(self.spec), encoding="utf-8")

    def fake(self, payload, rc=0):
        calls = []
        def run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, rc, stdout=payload, stderr="err")
        return run, calls

    def test_model_flash_only(self):
        self.assertEqual(h.check_model("gemini-3.8-flash-low"), "gemini-3.8-flash-low")
        for m in ("gemini-4-argon-high", "claude-opus-5-5-low", "gpt-oss-120b-medium"):
            with self.assertRaises(ValueError):
                h.check_model(m)

    def test_cmd_flags_and_result(self):
        body = {"items": [{"id": "A", "result": "pass", "evidence": [str(self.png)], "note": ""}],
                "steps": ["s"], "captures": [str(self.png), "nope.png"], "anomalies": []}
        run, calls = self.fake(json.dumps({"response": "```json\n" + json.dumps(body) + "\n```"}))
        r = h.run_handoff(str(self.sp), runner=run)
        c = calls[0]
        self.assertIn("--sandbox", c)
        self.assertNotIn("--dangerously-skip-permissions", c)
        self.assertEqual(c[c.index("--model") + 1], h.DEFAULT_MODEL)
        self.assertEqual(c[c.index("--output-format") + 1], "json")
        self.assertEqual(c[c.index("--print-timeout") + 1], "5s")
        self.assertEqual(r["captures"], [str(self.png)])
        self.assertEqual([i["result"] for i in r["items"]], ["pass", "unknown"])  # B は未返却
        self.assertTrue((self.d / "o" / "handoff_result.json").is_file())

    def test_fabricated_evidence_downgraded(self):
        body = {"items": [{"id": "A", "result": "pass", "evidence": ["ghost.png"], "note": ""},
                          {"id": "B", "result": "maybe", "evidence": [], "note": ""}], "steps": [], "captures": [], "anomalies": []}
        run, _ = self.fake(json.dumps(body))
        r = h.run_handoff(str(self.sp), runner=run)
        self.assertEqual([i["result"] for i in r["items"]], ["unknown", "unknown"])
        self.assertEqual(len(r["anomalies"]), 3)

    def test_rejects_non_flash_and_failure(self):
        self.spec["model"] = "claude-opus-5-5-low"
        self.sp.write_text(json.dumps(self.spec), encoding="utf-8")
        with self.assertRaises(ValueError):
            h.run_handoff(str(self.sp), runner=self.fake("{}")[0])
        self.spec.pop("model"); self.sp.write_text(json.dumps(self.spec), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            h.run_handoff(str(self.sp), runner=self.fake("", rc=1)[0])

    def test_target_needs_pid_or_exe(self):
        # タイトルだけの spec は断る (他人が起動した同じ名前の窓を掴んで閉じないため)
        self.spec["target"] = {"title": "TuskFolio"}
        self.sp.write_text(json.dumps(self.spec), encoding="utf-8")
        run, calls = self.fake("{}")
        with self.assertRaises(ValueError):
            h.run_handoff(str(self.sp), runner=run)
        self.assertEqual(calls, [], "agy は呼ばない")
        with self.assertRaises(ValueError):
            h.build_prompt(self.spec, Path("run.py"), self.d)
        # PID ならプロンプトは --pid だけを使う
        self.spec["target"] = {"pid": 4242, "title": "TuskFolio"}
        p = h.build_prompt(self.spec, Path("run.py"), self.d)
        self.assertIn("--pid 4242", p)
        self.assertNotIn('--title "', p)

    def test_close_window_needs_pid(self):
        self.assertEqual(h.tool_cli(["--title", "TuskFolio", "--close-window"]), 2)

    def test_prompt_rules(self):
        p = h.build_prompt(self.spec, Path("run.py"), self.d)
        for s in ("--place", "Alt+F4", "--close-window", "タスクバー", "[A]"):
            self.assertIn(s, p)


if __name__ == "__main__":
    unittest.main()
