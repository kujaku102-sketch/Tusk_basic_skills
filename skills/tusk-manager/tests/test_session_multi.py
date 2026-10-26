from __future__ import annotations

import contextlib
import io
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import tusk_manager as tm  # noqa: E402


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _log_events(ws: Path) -> list:
    log = ws / "work" / "recovery" / "sessions.jsonl"
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestMultiSession(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = Path(self._tmp.name)
        tm.init_workspace(self.ws)
        self.a = _write(self.ws / "src" / "a.py", "a0\n")
        self.b = _write(self.ws / "src" / "b.py", "b0\n")

    def test_two_sessions_run_concurrently(self) -> None:
        tm.session_start(self.ws, "task-a", ["src/a.py"], owner="agent-1")
        tm.session_start(self.ws, "task-b", ["src/b.py"], owner="agent-2")
        sdir = self.ws / "work" / "recovery" / "sessions"
        self.assertTrue((sdir / "task-a.json").is_file())
        self.assertTrue((sdir / "task-b.json").is_file())
        self.assertFalse((self.ws / "work" / "recovery" / "active_session.json").exists())

        # 省略時は複数の未完了があるのでエラー
        for call in (
            lambda: tm.session_complete(self.ws),
            lambda: tm.session_stop(self.ws, "r"),
            lambda: tm.session_resume(self.ws),
            lambda: tm.session_revise(self.ws),
            lambda: tm.session_rollback(self.ws),
            lambda: tm.session_status(self.ws),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                call()
            self.assertIn("--work-id", str(ctx.exception))

        st = tm.session_status(self.ws, work_id="task-a")
        self.assertEqual(st["work_id"], "task-a")
        self.assertEqual(st["owner"], "agent-1")

    def test_scope_overlap_rejected_with_other_work_id(self) -> None:
        tm.session_start(self.ws, "task-a", ["src/a.py"])
        with self.assertRaises(RuntimeError) as ctx:
            tm.session_start(self.ws, "task-x", ["src/a.py"])
        self.assertIn("task-a", str(ctx.exception))
        # ディレクトリ包含 (親ディレクトリ / 子パス)
        with self.assertRaises(RuntimeError) as ctx:
            tm.session_start(self.ws, "task-dir", ["src"])
        self.assertIn("task-a", str(ctx.exception))
        tm.session_start(self.ws, "task-dir2", ["docs/sub/x.md"])
        with self.assertRaises(RuntimeError) as ctx:
            tm.session_start(self.ws, "task-docs", ["docs"])
        self.assertIn("task-dir2", str(ctx.exception))
        # 同名 work_id の重複
        with self.assertRaises(RuntimeError):
            tm.session_start(self.ws, "task-a", ["src/b.py"])
        # 拒否された start は状態を残さない
        self.assertFalse((self.ws / "work" / "recovery" / "sessions" / "task-x.json").exists())
        # revise による scope 追加も他 session と重なれば拒否
        tm.session_start(self.ws, "task-b", ["src/b.py"])
        with self.assertRaises(RuntimeError) as ctx:
            tm.session_revise(self.ws, add_scope=["src/a.py"], work_id="task-b")
        self.assertIn("task-a", str(ctx.exception))

    def test_finished_session_scope_can_be_reused(self) -> None:
        tm.session_start(self.ws, "task-a", ["src/a.py"])
        tm.session_complete(self.ws)
        tm.session_start(self.ws, "task-a2", ["src/a.py"])
        tm.session_start(self.ws, "task-b", ["src/b.py"])
        tm.session_abandon(self.ws, "task-b", "no longer needed")
        tm.session_start(self.ws, "task-b2", ["src/b.py"])

    def test_invalid_work_ids_rejected(self) -> None:
        for bad in ("../x", "a/b", "a\\b", "", ".hidden", "a b", "nul", "CON.txt", "x" * 65):
            with self.assertRaises(ValueError, msg=bad):
                tm.session_start(self.ws, bad, ["src/a.py"])
        with self.assertRaises(ValueError):
            tm.session_status(self.ws, work_id="../evil")

    def test_complete_and_rollback_with_work_id_leave_other_session_alone(self) -> None:
        tm.session_start(self.ws, "task-a", ["src/a.py"])
        tm.session_start(self.ws, "task-b", ["src/b.py", "src/new_b.py"])
        self.a.write_text("a-wip\n", encoding="utf-8")
        self.b.write_text("b-wip\n", encoding="utf-8")
        new_b = _write(self.ws / "src" / "new_b.py", "created\n")

        # b を baseline へ rollback: a の変更は残る
        rb = tm.session_rollback(self.ws, "baseline", work_id="task-b")
        self.assertEqual(rb["session"]["state"], "abandoned")
        self.assertEqual(self.b.read_text(encoding="utf-8"), "b0\n")
        self.assertFalse(new_b.exists())
        self.assertEqual(self.a.read_text(encoding="utf-8"), "a-wip\n")
        self.assertEqual(tm.session_status(self.ws, work_id="task-a")["state"], "running")

        # b が片付いたので未完了は a だけ -> 省略可
        comp = tm.session_complete(self.ws)
        self.assertEqual(comp["session"]["work_id"], "task-a")
        self.assertEqual(self.a.read_text(encoding="utf-8"), "a-wip\n")

        # 逆順: complete 指定で他方に影響しない
        tm.session_start(self.ws, "task-c", ["src/c.py"])
        tm.session_start(self.ws, "task-d", ["src/d.py"])
        _write(self.ws / "src" / "c.py", "c-wip\n")
        _write(self.ws / "src" / "d.py", "d-wip\n")
        tm.session_complete(self.ws, work_id="task-c")
        self.assertEqual(tm.session_status(self.ws, work_id="task-c")["state"], "completed")
        self.assertEqual(tm.session_status(self.ws, work_id="task-d")["state"], "running")
        # c は完了済みなので d だけが未完了 -> 省略時は d に効く
        tm.session_stop(self.ws, "pause")
        self.assertEqual(tm.session_status(self.ws, work_id="task-d")["state"], "waiting_human")
        self.assertEqual(tm.session_status(self.ws, work_id="task-c")["state"], "completed")

    def test_stop_resume_revise_target_only_given_work_id(self) -> None:
        tm.session_start(self.ws, "task-a", ["src/a.py"])
        tm.session_start(self.ws, "task-b", ["src/b.py"])
        tm.session_stop(self.ws, "wait", work_id="task-a")
        self.assertEqual(tm.session_status(self.ws, work_id="task-b")["state"], "running")
        tm.session_resume(self.ws, work_id="task-a")
        rev = tm.session_revise(self.ws, work_id="task-b")
        self.assertEqual(rev["session"]["generation"], 1)
        self.assertEqual(tm.session_status(self.ws, work_id="task-a")["session"]["generation"], 0)
        with self.assertRaises(RuntimeError):
            tm.session_status(self.ws, work_id="nope")

    def test_legacy_active_session_is_migrated(self) -> None:
        rec = self.ws / "work" / "recovery"
        legacy_running = {
            "schema_version": 1,
            "work_id": "old-task",
            "state": "running",
            "generation": 0,
            "rework_count": 0,
            "scope": ["src/a.py"],
            "baseline_snapshot": {"src/a.py": {"exists": True, "sha256": tm.calculate_sha256(self.a)}},
            "generation_snapshot": {"src/a.py": {"exists": True, "sha256": tm.calculate_sha256(self.a)}},
            "wip_snapshot": None,
            "last_reason": None,
            "last_error_key": None,
            "updated_at_utc": "2026-10-01T00:00:00Z",
        }
        legacy_path = rec / "active_session.json"
        legacy_path.write_text(json.dumps(legacy_running), encoding="utf-8")
        legacy_text = legacy_path.read_text(encoding="utf-8")

        # 読み取り (status) だけで自動移行される
        st = tm.session_status(self.ws)
        self.assertEqual(st["work_id"], "old-task")
        self.assertTrue(st["active"])
        self.assertFalse(legacy_path.exists())
        self.assertTrue((rec / "sessions" / "old-task.json").is_file())
        kept = list((rec / "backups" / "_legacy").glob("active_session*.json"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_text(encoding="utf-8"), legacy_text)
        self.assertEqual(_log_events(self.ws)[-1]["event"], "migrate")

        # 移行後も旧 session のスコープは保護される
        with self.assertRaises(RuntimeError) as ctx:
            tm.session_start(self.ws, "new-task", ["src/a.py"])
        self.assertIn("old-task", str(ctx.exception))
        tm.session_start(self.ws, "new-task", ["src/b.py"])
        tm.session_complete(self.ws, work_id="old-task")

    def test_legacy_completed_is_migrated_as_history(self) -> None:
        rec = self.ws / "work" / "recovery"
        legacy = {
            "schema_version": 1, "work_id": "done-task", "state": "completed", "generation": 0,
            "rework_count": 0, "scope": ["src/a.py"], "baseline_snapshot": {}, "generation_snapshot": {},
            "wip_snapshot": None, "updated_at_utc": "2026-10-01T00:00:00Z",
        }
        (rec / "active_session.json").write_text(json.dumps(legacy), encoding="utf-8")
        # 書き込み系 (別 session の start) でも移行され、完了済みなので scope は衝突しない
        tm.session_start(self.ws, "fresh", ["src/a.py"])
        self.assertFalse((rec / "active_session.json").exists())
        listing = tm.session_list(self.ws, include_finished=True)
        states = {s["work_id"]: s["state"] for s in listing["sessions"]}
        self.assertEqual(states, {"done-task": "completed", "fresh": "running"})
        self.assertEqual([s["work_id"] for s in tm.session_list(self.ws)["sessions"]], ["fresh"])

    def test_rollback_of_finished_session_is_rejected(self) -> None:
        tm.session_start(self.ws, "task-a", ["src/a.py"])
        self.a.write_text("a-done\n", encoding="utf-8")
        tm.session_complete(self.ws)
        # 同じファイルを別 session が使い始める
        tm.session_start(self.ws, "task-a2", ["src/a.py"])
        self.a.write_text("a2-wip\n", encoding="utf-8")
        for target in ("baseline", "generation"):
            with self.assertRaises(RuntimeError) as ctx:
                tm.session_rollback(self.ws, target, work_id="task-a")
            self.assertIn("completed", str(ctx.exception))
        self.assertEqual(self.a.read_text(encoding="utf-8"), "a2-wip\n")
        self.assertEqual(tm.session_status(self.ws, work_id="task-a")["state"], "completed")

        # abandoned も同様
        tm.session_abandon(self.ws, "task-a2", "drop")
        tm.session_start(self.ws, "task-a3", ["src/a.py"])
        self.a.write_text("a3-wip\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            tm.session_rollback(self.ws, "baseline", work_id="task-a2")
        self.assertEqual(self.a.read_text(encoding="utf-8"), "a3-wip\n")

    def test_rollback_rejected_when_restore_target_overlaps_other_session(self) -> None:
        tm.session_start(self.ws, "task-a", ["src/a.py"])
        tm.session_start(self.ws, "task-b", ["src/b.py"])
        self.a.write_text("a-wip\n", encoding="utf-8")
        # 壊れた/手編集された状態を模擬: task-b の snapshot が task-a の scope を含む
        p = self.ws / "work" / "recovery" / "sessions" / "task-b.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["baseline_snapshot"]["src/a.py"] = {"exists": True, "sha256": "0" * 64}
        data["generation_snapshot"]["src/a.py"] = {"exists": True, "sha256": "0" * 64}
        p.write_text(json.dumps(data), encoding="utf-8")
        for target in ("baseline", "generation"):
            with self.assertRaises(RuntimeError) as ctx:
                tm.session_rollback(self.ws, target, work_id="task-b")
            self.assertIn("F203", str(ctx.exception))
            self.assertIn("task-a", str(ctx.exception))
        self.assertEqual(self.a.read_text(encoding="utf-8"), "a-wip\n")
        self.assertEqual(tm.session_status(self.ws, work_id="task-b")["state"], "running")

    def test_invalid_legacy_work_id_is_quarantined_and_work_continues(self) -> None:
        rec = self.ws / "work" / "recovery"
        for i, bad_id in enumerate(("../evil", "nul", "has space", None)):
            legacy = {"schema_version": 1, "work_id": bad_id, "state": "running", "scope": ["src/a.py"]}
            (rec / "active_session.json").write_text(json.dumps(legacy), encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                tm.session_start(self.ws, f"ok-{i}", [f"src/f{i}.py"])
            self.assertIn("WARN", err.getvalue())
            self.assertFalse((rec / "active_session.json").exists())
            tm.session_complete(self.ws, work_id=f"ok-{i}")
        kept = list((rec / "backups" / "_legacy").glob("active_session.invalid.*.json"))
        self.assertEqual(len(kept), 4)
        self.assertEqual(len([e for e in _log_events(self.ws) if e["event"] == "migrate_invalid"]), 4)
        # 不正 JSON も同様
        (rec / "active_session.json").write_text("{not json", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(tm.session_list(self.ws)["count"], 0)
        self.assertFalse((rec / "active_session.json").exists())
        self.assertEqual(len(list((rec / "backups" / "_legacy").glob("active_session.invalid.*.json"))), 5)

    def test_lock_release_only_when_owned(self) -> None:
        lock = self.ws / "work" / "recovery" / ".lock"
        with tm._recovery_lock(self.ws):
            self.assertTrue(lock.exists())
            lock.write_text(json.dumps({"pid": -1, "token": "other"}), encoding="utf-8")
        self.assertTrue(lock.exists())  # 他者の lock に置き換わっていたら消さない
        lock.unlink()
        with tm._recovery_lock(self.ws):
            pass
        self.assertFalse(lock.exists())

    def test_abandon_and_list(self) -> None:
        tm.session_start(self.ws, "task-a", ["src/a.py"], owner="alice")
        tm.session_start(self.ws, "task-b", ["src/b.py"], owner="bob")
        self.a.write_text("a-wip\n", encoding="utf-8")
        before_snapshot = json.loads(
            (self.ws / "work" / "recovery" / "sessions" / "task-a.json").read_text(encoding="utf-8")
        )["baseline_snapshot"]

        listing = tm.session_list(self.ws)
        self.assertEqual(listing["count"], 2)
        row = {r["work_id"]: r for r in listing["sessions"]}["task-a"]
        for key in ("state", "scope", "updated_at_utc", "age_seconds", "owner"):
            self.assertIn(key, row)
        self.assertEqual(row["owner"], "alice")

        # stale 抽出: 直後は 0 件、task-b だけ古くすると task-b のみ
        self.assertEqual(tm.session_list(self.ws, stale_hours=1)["count"], 0)
        p = self.ws / "work" / "recovery" / "sessions" / "task-b.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["updated_at_utc"] = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat().replace("+00:00", "Z")
        p.write_text(json.dumps(data), encoding="utf-8")
        stale = tm.session_list(self.ws, stale_hours=24)
        self.assertEqual([r["work_id"] for r in stale["sessions"]], ["task-b"])
        self.assertGreaterEqual(stale["sessions"][0]["age_hours"], 30)

        with self.assertRaises(ValueError):
            tm.session_abandon(self.ws, "task-b", "  ")
        res = tm.session_abandon(self.ws, "task-b", "owner gone")
        self.assertEqual(res["session"]["state"], "abandoned")
        self.assertEqual(res["session"]["abandon_reason"], "owner gone")
        # ファイルは触らない (rollback しない)、a は無影響
        self.assertEqual(self.b.read_text(encoding="utf-8"), "b0\n")
        self.assertEqual(self.a.read_text(encoding="utf-8"), "a-wip\n")
        self.assertTrue((self.ws / "work" / "recovery" / "backups" / "task-b" / "baseline" / "src" / "b.py").is_file())
        a_after = json.loads(
            (self.ws / "work" / "recovery" / "sessions" / "task-a.json").read_text(encoding="utf-8")
        )
        self.assertEqual(a_after["state"], "running")
        self.assertEqual(a_after["baseline_snapshot"], before_snapshot)
        ev = _log_events(self.ws)[-1]
        self.assertEqual((ev["event"], ev["work_id"], ev["reason"]), ("abandon", "task-b", "owner gone"))
        self.assertEqual(ev["previous_state"], "running")

        self.assertEqual([r["work_id"] for r in tm.session_list(self.ws)["sessions"]], ["task-a"])
        self.assertEqual(tm.session_list(self.ws, include_finished=True)["count"], 2)
        with self.assertRaises(RuntimeError):
            tm.session_abandon(self.ws, "task-b", "again")
        with self.assertRaises(RuntimeError):
            tm.session_abandon(self.ws, "missing", "x")

    def test_concurrent_start_threads_only_one_wins_on_same_scope(self) -> None:
        results: list = []

        def worker(i: int) -> None:
            try:
                tm.session_start(self.ws, f"race-{i}", ["src/a.py"])
                results.append(("ok", i))
            except RuntimeError as exc:
                results.append(("err", str(exc)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len([r for r in results if r[0] == "ok"]), 1)
        self.assertEqual(len(tm.session_list(self.ws)["sessions"]), 1)
        self.assertFalse((self.ws / "work" / "recovery" / ".lock").exists())

    def test_lock_timeout_and_release(self) -> None:
        lock = self.ws / "work" / "recovery" / ".lock"
        lock.write_text("held\n", encoding="utf-8")
        old = tm.LOCK_TIMEOUT_SEC
        tm.LOCK_TIMEOUT_SEC = 0.3
        self.addCleanup(setattr, tm, "LOCK_TIMEOUT_SEC", old)
        with self.assertRaises(RuntimeError) as ctx:
            tm.session_start(self.ws, "task-a", ["src/a.py"])
        self.assertIn("locked", str(ctx.exception))
        self.assertTrue(lock.exists())  # 他者のロックは消さない
        lock.unlink()
        tm.session_start(self.ws, "task-a", ["src/a.py"])
        self.assertFalse(lock.exists())

    def test_atomic_write_retries_permission_error_on_windows(self) -> None:
        target = self.ws / "out.json"
        real_replace = tm.os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] <= 3:
                raise PermissionError(5, "Access is denied")
            return real_replace(src, dst)

        with mock.patch.object(tm, "_IS_WINDOWS", True), mock.patch.object(tm, "REPLACE_RETRY_SEC", 0.001), \
                mock.patch.object(tm.os, "replace", flaky):
            tm.atomic_write_json(target, {"k": 1})
        self.assertEqual(calls["n"], 4)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"k": 1})
        self.assertEqual(list(self.ws.glob("out.json.*.tmp")), [])

    def test_atomic_write_gives_up_and_cleans_temp(self) -> None:
        target = self.ws / "out2.json"
        calls = {"n": 0}

        def always_denied(src, dst):
            calls["n"] += 1
            raise PermissionError(5, "Access is denied")

        with mock.patch.object(tm, "_IS_WINDOWS", True), mock.patch.object(tm, "REPLACE_RETRY_SEC", 0.001), \
                mock.patch.object(tm.os, "replace", always_denied):
            with self.assertRaises(PermissionError):
                tm.atomic_write_json(target, {"k": 1})
        self.assertEqual(calls["n"], tm.REPLACE_RETRIES)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.ws.glob("out2.json.*.tmp")), [])

        # 非 Windows では即座に例外 (リトライしない)
        calls["n"] = 0
        with mock.patch.object(tm, "_IS_WINDOWS", False), mock.patch.object(tm.os, "replace", always_denied):
            with self.assertRaises(PermissionError):
                tm.atomic_write_json(target, {"k": 1})
        self.assertEqual(calls["n"], 1)
        self.assertEqual(list(self.ws.glob("out2.json.*.tmp")), [])

    def test_atomic_write_leaves_no_temp_files(self) -> None:
        tm.session_start(self.ws, "task-a", ["src/a.py"])
        tm.session_stop(self.ws, "x")
        leftovers = list((self.ws / "work" / "recovery").rglob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_cli_options(self) -> None:
        def run(*argv: str) -> dict:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                code = tm.main(["--workspace", str(self.ws), "--json", *argv])
            out = json.loads(buf.getvalue())
            out["_code"] = code
            return out

        self.assertEqual(run("session", "start", "--work-id", "c-a", "--scope", "src/a.py", "--owner", "me")["_code"], 0)
        self.assertEqual(run("session", "start", "--work-id", "c-b", "--scope", "src/b.py")["_code"], 0)
        clash = run("session", "start", "--work-id", "c-c", "--scope", "src/a.py")
        self.assertEqual(clash["_code"], 1)
        self.assertIn("c-a", clash["error"])
        self.assertEqual(run("session", "complete")["_code"], 1)
        self.assertEqual(run("session", "complete", "--work-id", "c-a")["_code"], 0)
        lst = run("session", "list", "--stale-hours", "0")
        self.assertEqual([s["work_id"] for s in lst["sessions"]], ["c-b"])
        self.assertEqual(run("session", "abandon", "--work-id", "c-b", "--reason", "cleanup")["_code"], 0)
        self.assertEqual(run("session", "list")["count"], 0)


if __name__ == "__main__":
    unittest.main()
