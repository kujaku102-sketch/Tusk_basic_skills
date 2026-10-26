from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import tusk_manager  # noqa: E402


def _sample_extension(root: Path, name: str) -> Path:
    ext_src = root / name
    ext_src.mkdir(parents=True)
    agent_md = ext_src / "AGENTS.md"
    agent_md.write_text(f"# {name}\n", encoding="utf-8")
    sha = hashlib.sha256(agent_md.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "root": name,
        "algorithm": "sha256",
        "managed_files": [{"path": "AGENTS.md", "sha256": sha}],
    }
    (ext_src / "EXTENSION-MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    return ext_src


class TestTuskManager(unittest.TestCase):
    def test_init_and_extension_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            res = tusk_manager.init_workspace(ws)
            self.assertEqual(res["status"], "success")
            self.assertFalse((ws / "developer.key.json").exists())
            self.assertTrue((ws / "work" / "settings" / "extensions.enabled.json").is_file())
            self.assertTrue((ws / "work" / "settings" / "context_scopes.json").is_file())
            self.assertTrue((ws / "work" / "focus_cache" / "LANDMINES.md").is_file())

            ext_src = _sample_extension(Path(tmp), "Tusk_Sample")
            inst = tusk_manager.install_extension(ws, ext_src, enable=True)
            self.assertTrue(inst["enabled"])
            listed = tusk_manager.list_extensions(ws)
            self.assertIn("Tusk_Sample", listed["available_extensions"])
            self.assertIn("tusk_sample", listed["enabled_extensions"])

            tusk_manager.set_extension_enabled(ws, "Tusk_Sample", False)
            listed_after = tusk_manager.list_extensions(ws)
            self.assertNotIn("tusk_sample", listed_after["enabled_extensions"])
            self.assertIn("tusk_sample", listed_after["disabled_extensions"])

    def test_required_extension_is_enabled_on_init_and_cannot_be_disabled(self) -> None:
        """tusk_manager を収録する Tusk_Basic_skills は、初期化で有効になり、無効化・削除できない"""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            _sample_extension(ws / "extensions", tusk_manager.REQUIRED_EXTENSION)
            tusk_manager.init_workspace(ws)
            listed = tusk_manager.list_extensions(ws)
            self.assertIn("tusk_basic_skills", listed["enabled_extensions"])
            with self.assertRaises(PermissionError):
                tusk_manager.set_extension_enabled(ws, tusk_manager.REQUIRED_EXTENSION, False)
            with self.assertRaises(PermissionError):
                tusk_manager.remove_extension(ws, tusk_manager.REQUIRED_EXTENSION)
            self.assertTrue((ws / "extensions" / tusk_manager.REQUIRED_EXTENSION).is_dir())
            self.assertIn("tusk_basic_skills", tusk_manager.list_extensions(ws)["enabled_extensions"])

        with tempfile.TemporaryDirectory() as tmp:
            # 必須 Extension が無いワークスペースでは、今までどおり空の一覧で始まる
            ws = Path(tmp) / "ws"
            tusk_manager.init_workspace(ws)
            self.assertEqual(tusk_manager.list_extensions(ws)["enabled_extensions"], [])

    def test_context_cache_indexing_and_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            tusk_manager.init_workspace(ws)
            src_dir = ws / "app"
            src_dir.mkdir()
            f1 = src_dir / "main.py"
            f1.write_text("x = 1\n", encoding="utf-8")

            res1 = tusk_manager.build_context_cache(ws, "app_scope", inputs_override=["app"], write=True)
            self.assertEqual(res1["added"], ["app/main.py"])
            self.assertEqual(res1["unchanged_count"], 0)

            res2 = tusk_manager.build_context_cache(ws, "app_scope", write=False)
            self.assertEqual(res2["added"], [])
            self.assertEqual(res2["modified"], [])
            self.assertEqual(res2["unchanged_count"], 1)

            f1.write_text("x = 2\n", encoding="utf-8")
            res3 = tusk_manager.build_context_cache(ws, "app_scope", write=False)
            self.assertEqual(res3["modified"], ["app/main.py"])

    def test_session_in_progress_recovery_and_scoped_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            tusk_manager.init_workspace(ws)

            tracked = ws / "src" / "app.py"
            tracked.parent.mkdir(parents=True, exist_ok=True)
            tracked.write_text("print('v1')\n", encoding="utf-8")

            unrelated = ws / "src" / "user_notes.txt"
            unrelated.write_text("user secret note\n", encoding="utf-8")

            new_scoped = ws / "src" / "new_helper.py"

            start_res = tusk_manager.session_start(ws, "task-01", ["src/app.py", "src/new_helper.py"])
            self.assertEqual(start_res["session"]["state"], "running")

            tracked.write_text("print('wip v2')\n", encoding="utf-8")
            new_scoped.write_text("def helper(): pass\n", encoding="utf-8")
            unrelated.write_text("user edited note during task\n", encoding="utf-8")

            stop_res = tusk_manager.session_stop(ws, reason="Wait for spec confirmation", error_key="F205")
            self.assertEqual(stop_res["session"]["state"], "waiting_human")

            resume_res = tusk_manager.session_resume(ws)
            self.assertEqual(resume_res["session"]["state"], "running")

            tusk_manager.session_stop(ws, reason="Pause again")
            tracked.write_text("print('external tamper')\n", encoding="utf-8")
            with self.assertRaises(RuntimeError) as ctx:
                tusk_manager.session_resume(ws)
            self.assertIn("F204", str(ctx.exception))

            rev_res = tusk_manager.session_revise(ws, add_scope=["src/extra.py"])
            self.assertEqual(rev_res["session"]["state"], "running")
            self.assertEqual(rev_res["session"]["generation"], 1)
            self.assertEqual(rev_res["session"]["rework_count"], 1)
            self.assertIn("src/extra.py", rev_res["session"]["scope"])

            rb_res = tusk_manager.session_rollback(ws, to_target="baseline")
            self.assertEqual(rb_res["session"]["state"], "abandoned")
            self.assertEqual(tracked.read_text(encoding="utf-8"), "print('v1')\n")
            self.assertFalse(new_scoped.exists())
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "user edited note during task\n")

    def test_session_rework_limit_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            tusk_manager.init_workspace(ws)
            f = ws / "a.txt"
            f.write_text("0", encoding="utf-8")
            tusk_manager.session_start(ws, "rework-test", ["a.txt"])

            for i in range(1, tusk_manager.MAX_REWORK_COUNT + 1):
                res = tusk_manager.session_revise(ws)
                self.assertEqual(res["session"]["rework_count"], i)

            with self.assertRaises(RuntimeError) as ctx:
                tusk_manager.session_revise(ws)
            self.assertIn("F202", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
