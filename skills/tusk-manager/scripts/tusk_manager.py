from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


NAME_RE = re.compile(r"^Tusk_[A-Za-z0-9._-]+$")
WORK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SCOPE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
MAX_REWORK_COUNT = 3
CONTEXT_EXCLUDED_DIRS = {
    ".git", "__pycache__", ".pytest_cache", "node_modules", ".venv",
    "context_cache", "focus_cache", "recovery", "records", "evidence",
    "artifacts", "scratch", "tmp", "secrets", "credentials", "truststore",
}
CONTEXT_EXCLUDED_SUFFIXES = {".pem", ".pfx", ".p12", ".key", ".pyc"}
CONTEXT_EXCLUDED_FILES = {"token.json", "credentials.json", "developer.key.json"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get_default_workspace() -> Path:
    return Path.cwd()


def calculate_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


REPLACE_RETRIES = 20
REPLACE_RETRY_SEC = 0.05
_IS_WINDOWS = os.name == "nt"


def _replace_with_retry(src: Path, dst: Path) -> None:
    """os.replace。Windows では一時的な PermissionError (WinError 5) を短い間隔で上限つきリトライする。"""
    for attempt in range(REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if not _IS_WINDOWS or attempt == REPLACE_RETRIES - 1:
                raise
            time.sleep(REPLACE_RETRY_SEC)


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        _replace_with_retry(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


# tusk_manager 自身を収録している Extension。Core の必須道具なので常に有効で、無効化・削除できない
REQUIRED_EXTENSION = "Tusk_Basic_skills"


def _required_row(ws: Path) -> Optional[Dict[str, str]]:
    """ワークスペースに必須 Extension があれば、有効一覧の行を返す"""
    if not (ws / "extensions" / REQUIRED_EXTENSION).is_dir():
        return None
    return {
        "id": REQUIRED_EXTENSION.lower(),
        "manifest": f"../../extensions/{REQUIRED_EXTENSION}/EXTENSION-MANIFEST.json",
    }


# ---------------------------------------------------------
# 1. 初期導入 (Init / Bootstrap)
# ---------------------------------------------------------
def init_workspace(workspace: Path, core_source: Optional[Path] = None) -> Dict[str, Any]:
    ws = workspace.resolve()
    ws.mkdir(parents=True, exist_ok=True)

    # 1. 必須ディレクトリ構造作成
    (ws / "work" / "settings").mkdir(parents=True, exist_ok=True)
    (ws / "work" / "focus_cache").mkdir(parents=True, exist_ok=True)
    (ws / "work" / "context_cache").mkdir(parents=True, exist_ok=True)
    (ws / "work" / "recovery").mkdir(parents=True, exist_ok=True)
    (ws / "extensions").mkdir(parents=True, exist_ok=True)

    # 2. 初期設定ファイル
    enabled_json = ws / "work" / "settings" / "extensions.enabled.json"
    if not enabled_json.exists():
        required = _required_row(ws)
        atomic_write_json(
            enabled_json,
            {
                "schema_version": 1,
                "enabled_extensions": [required] if required else [],
                "disabled_extensions": [],
            },
        )

    scopes_json = ws / "work" / "settings" / "context_scopes.json"
    if not scopes_json.exists():
        atomic_write_json(
            scopes_json,
            {
                "schema_version": 1,
                "cache_root": "work/context_cache",
                "scopes": {
                    "core_reference": {
                        "source_root": "Tusk_core",
                        "inputs": ["AGENTS.md", "README.md", "Tusk_core"],
                    }
                },
            },
        )

    # 3. 初期LANDMINES.md
    landmines_md = ws / "work" / "focus_cache" / "LANDMINES.md"
    if not landmines_md.exists():
        landmines_md.write_text("# Focus Cache: 地雷一覧\n\n", encoding="utf-8")

    # 4. Coreの配置 (指定があればコピー)
    core_installed = False
    if core_source and core_source.is_dir() and core_source.resolve() != (ws / "Tusk_core").resolve():
        dest_core = ws / "Tusk_core"
        if not dest_core.exists():
            shutil.copytree(core_source, dest_core, ignore=shutil.ignore_patterns("__pycache__", ".git", "*.tmp"))
            core_installed = True

    return {
        "status": "success",
        "action": "init",
        "workspace": str(ws),
        "created_files": [
            str(enabled_json.relative_to(ws)),
            str(scopes_json.relative_to(ws)),
            str(landmines_md.relative_to(ws)),
        ],
        "core_installed": core_installed,
    }


# ---------------------------------------------------------
# 2. 拡張機能管理 (Extension Management)
# ---------------------------------------------------------
def list_extensions(workspace: Path) -> Dict[str, Any]:
    ws = workspace.resolve()
    ext_dir = ws / "extensions"
    enabled_json = ws / "work" / "settings" / "extensions.enabled.json"

    available: List[str] = []
    if ext_dir.is_dir():
        available = sorted(p.name for p in ext_dir.iterdir() if p.is_dir() and NAME_RE.fullmatch(p.name))

    enabled_ids: List[str] = []
    disabled_ids: List[str] = []
    if enabled_json.exists():
        try:
            data = json.loads(enabled_json.read_text(encoding="utf-8"))
            enabled_ids = [row["id"] for row in data.get("enabled_extensions", [])]
            disabled_ids = data.get("disabled_extensions", [])
        except Exception:
            pass

    return {
        "workspace": str(ws),
        "available_extensions": available,
        "enabled_extensions": enabled_ids,
        "disabled_extensions": disabled_ids,
    }


def validate_extension_manifest(ext_path: Path) -> Dict[str, Any]:
    name = ext_path.name
    if not NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid extension folder name: {name}")
    manifest_path = ext_path / "EXTENSION-MANIFEST.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("root") != name or manifest.get("algorithm") != "sha256":
        raise ValueError("Invalid extension manifest root or algorithm")

    for item in manifest.get("managed_files", []):
        rel = Path(item["path"])
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"Unsafe path in manifest: {item['path']}")
        target = ext_path / rel
        if not target.is_file():
            raise FileNotFoundError(f"Managed file missing: {target}")
        if calculate_sha256(target) != item["sha256"]:
            raise ValueError(f"SHA mismatch: {item['path']}")

    return manifest


def install_extension(workspace: Path, source_path: Path, enable: bool = True) -> Dict[str, Any]:
    ws = workspace.resolve()
    src = source_path.resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"Source directory not found: {src}")

    name = src.name
    if not NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid extension name: {name}")

    dest = ws / "extensions" / name
    if dest.exists():
        shutil.rmtree(dest)

    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", ".git", "*.tmp"))
    validate_extension_manifest(dest)

    if enable:
        set_extension_enabled(workspace, name, True)

    return {
        "status": "success",
        "action": "install",
        "extension": name,
        "path": str(dest),
        "enabled": enable,
    }


def set_extension_enabled(workspace: Path, name: str, enabled: bool) -> Dict[str, Any]:
    ws = workspace.resolve()
    ext_dir = ws / "extensions"
    enabled_json = ws / "work" / "settings" / "extensions.enabled.json"

    data: Dict[str, Any] = {"schema_version": 1, "enabled_extensions": [], "disabled_extensions": []}
    if enabled_json.exists():
        try:
            data = json.loads(enabled_json.read_text(encoding="utf-8"))
        except Exception:
            pass

    rows = {row["id"]: row for row in data.get("enabled_extensions", [])}
    disabled = set(data.get("disabled_extensions", []))
    ext_key = name.lower()
    if not enabled and ext_key == REQUIRED_EXTENSION.lower():
        raise PermissionError(f"{REQUIRED_EXTENSION} は tusk_manager を収録する必須 Extension なので無効にできない")

    if enabled:
        ext_path = ext_dir / name
        if not ext_path.is_dir():
            raise FileNotFoundError(f"Extension not found: {name}")
        validate_extension_manifest(ext_path)
        manifest_rel = f"../../extensions/{name}/EXTENSION-MANIFEST.json"
        rows[ext_key] = {"id": ext_key, "manifest": manifest_rel}
        disabled.discard(ext_key)
    else:
        rows.pop(ext_key, None)
        disabled.add(ext_key)

    data["schema_version"] = 1
    data["enabled_extensions"] = [rows[k] for k in sorted(rows)]
    data["disabled_extensions"] = sorted(disabled)

    atomic_write_json(enabled_json, data)

    return {
        "status": "success",
        "action": "enable" if enabled else "disable",
        "extension": name,
        "enabled": enabled,
    }


def remove_extension(workspace: Path, name: str) -> Dict[str, Any]:
    ws = workspace.resolve()
    dest = ws / "extensions" / name
    if name.lower() == REQUIRED_EXTENSION.lower():
        raise PermissionError(f"{REQUIRED_EXTENSION} は tusk_manager を収録する必須 Extension なので削除できない")

    try:
        set_extension_enabled(workspace, name, False)
    except Exception:
        pass

    if dest.exists():
        shutil.rmtree(dest)

    return {
        "status": "success",
        "action": "remove",
        "extension": name,
    }


# ---------------------------------------------------------
# 3. 作業中の変更リカバリー (In-Progress Session Recovery)
# ---------------------------------------------------------
def _normalize_scope_paths(ws: Path, scope: List[str]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for raw in scope:
        p = Path(raw)
        target = (ws / p).resolve() if not p.is_absolute() else p.resolve()
        try:
            rel = target.relative_to(ws).as_posix()
        except ValueError as exc:
            raise ValueError(f"Scope path outside workspace: {raw}") from exc
        if rel == "." or rel.startswith("work/recovery"):
            raise ValueError(f"Invalid scope path: {raw}")
        if rel not in seen:
            seen.add(rel)
            normalized.append(rel)
    if not normalized:
        raise ValueError("At least one valid scope path is required")
    return normalized


def _capture_snapshot(ws: Path, rel_paths: List[str], backup_dir: Path) -> Dict[str, Dict[str, Any]]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    snapshot: Dict[str, Dict[str, Any]] = {}
    for rel in rel_paths:
        target = ws / rel
        if target.is_dir():
            raise ValueError(f"Scope path must be a file path, not directory: {rel}")
        if target.is_file():
            sha = calculate_sha256(target)
            dest = backup_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, dest)
            snapshot[rel] = {"exists": True, "sha256": sha}
        else:
            snapshot[rel] = {"exists": False, "sha256": None}
    return snapshot


def _restore_snapshot(ws: Path, snapshot: Dict[str, Dict[str, Any]], backup_dir: Path) -> Dict[str, List[str]]:
    restored: List[str] = []
    removed_created: List[str] = []
    for rel, info in snapshot.items():
        target = ws / rel
        if info.get("exists"):
            src = backup_dir / rel
            if not src.is_file():
                raise FileNotFoundError(f"Backup file missing for rollback: {src}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            restored.append(rel)
        else:
            if target.is_file():
                target.unlink()
                removed_created.append(rel)
    return {"restored": restored, "removed_created": removed_created}


SESSION_SCHEMA_VERSION = 2
FINISHED_STATES = {"completed", "abandoned"}
RESERVED_WORK_IDS = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
LOCK_TIMEOUT_SEC = 15.0
LOCK_POLL_SEC = 0.05


def _recovery_dir(ws: Path) -> Path:
    return ws / "work" / "recovery"


def _legacy_session_path(ws: Path) -> Path:
    """旧単一ファイル形式 (legacy)。新規書き込みはしない。読み込み時に自動移行する。"""
    return _recovery_dir(ws) / "active_session.json"


def _sessions_dir(ws: Path) -> Path:
    return _recovery_dir(ws) / "sessions"


def _sessions_log_path(ws: Path) -> Path:
    return _recovery_dir(ws) / "sessions.jsonl"


def _lock_path(ws: Path) -> Path:
    return _recovery_dir(ws) / ".lock"


def _validate_work_id(work_id: Any) -> str:
    if not isinstance(work_id, str) or not WORK_ID_RE.fullmatch(work_id):
        raise ValueError(f"Invalid work_id: {work_id}")
    if work_id.split(".", 1)[0].lower() in RESERVED_WORK_IDS:
        raise ValueError(f"Invalid work_id (reserved name): {work_id}")
    return work_id


def _session_path(ws: Path, work_id: str) -> Path:
    return _sessions_dir(ws) / f"{_validate_work_id(work_id)}.json"


def _backup_dir(ws: Path, work_id: str) -> Path:
    return _recovery_dir(ws) / "backups" / _validate_work_id(work_id)


@contextmanager
def _recovery_lock(ws: Path) -> Iterator[None]:
    """work/recovery/.lock を O_EXCL で作成する排他ロック (タイムアウト付き)。"""
    path = _lock_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + LOCK_TIMEOUT_SEC
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except (FileExistsError, PermissionError) as exc:
            # Windows では削除中の lock への O_EXCL が PermissionError になる。それ以外の OS では本物の権限エラー。
            if isinstance(exc, PermissionError) and os.name != "nt":
                raise
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Session store is locked by another process: {path} "
                    f"(timeout {LOCK_TIMEOUT_SEC}s). If no tusk_manager is running, remove the stale lock file."
                )
            time.sleep(LOCK_POLL_SEC)
    token = uuid.uuid4().hex
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"pid": os.getpid(), "token": token, "created_at_utc": utc_now()}) + "\n")
        yield
    finally:
        _release_lock(path, token)


def _release_lock(path: Path, token: str) -> None:
    """自分が作った lock (pid と token が一致) のときだけ解放する。他者の lock は消さない。"""
    for _ in range(20):
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
            if info.get("pid") != os.getpid() or info.get("token") != token:
                return
            path.unlink()
            return
        except (FileNotFoundError, ValueError):
            return
        except PermissionError:
            if os.name != "nt":
                raise
            time.sleep(LOCK_POLL_SEC)


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(1, 1000):
        cand = path.with_name(f"{path.stem}.{i}{path.suffix}")
        if not cand.exists():
            return cand
    raise RuntimeError(f"Cannot find a free file name near: {path}")


def _migrate_legacy_locked(ws: Path) -> Optional[str]:
    """旧 active_session.json を sessions/<work_id>.json へ移行する (ロック保持中に呼ぶ)。

    旧ファイルは backups/_legacy/ へ退避して保持する (消去しない)。冪等。
    """
    legacy = _legacy_session_path(ws)
    if not legacy.is_file():
        return None
    legacy_dir = _recovery_dir(ws) / "backups" / "_legacy"
    stamp = utc_now().replace(":", "").replace("-", "")
    try:
        data = json.loads(legacy.read_text(encoding="utf-8"))
        work_id = _validate_work_id(data.get("work_id"))
    except (ValueError, AttributeError, OSError) as exc:
        # 不正な旧ファイルで write 系全体を止めない: 退避して警告・記録し、処理を続ける。
        legacy_dir.mkdir(parents=True, exist_ok=True)
        kept = _unique_path(legacy_dir / f"active_session.invalid.{stamp}.json")
        os.replace(legacy, kept)
        append_jsonl(
            _sessions_log_path(ws),
            {"event": "migrate_invalid", "source": "active_session.json", "reason": str(exc), "kept_as": kept.as_posix()},
        )
        print(f"[WARN] Invalid legacy active_session.json moved to {kept} ({exc}); not migrated.", file=sys.stderr)
        return None

    dest = _session_path(ws, work_id)
    migrated = False
    if not dest.exists():
        data["schema_version"] = SESSION_SCHEMA_VERSION
        data.setdefault("owner", None)
        data["migrated_from"] = "active_session.json"
        data["migrated_at_utc"] = utc_now()
        atomic_write_json(dest, data)
        append_jsonl(_sessions_log_path(ws), {"event": "migrate", "source": "active_session.json", **data})
        migrated = True
    else:
        append_jsonl(
            _sessions_log_path(ws),
            {"event": "migrate_skipped", "work_id": work_id, "reason": "sessions/<work_id>.json already exists"},
        )

    legacy_dir.mkdir(parents=True, exist_ok=True)
    os.replace(legacy, _unique_path(legacy_dir / f"active_session.{stamp}.json"))
    return work_id if migrated else None


@contextmanager
def _session_store_write(ws: Path) -> Iterator[None]:
    """書き込み系操作用: ロック取得 + 旧形式の自動移行。"""
    with _recovery_lock(ws):
        _migrate_legacy_locked(ws)
        yield


def _ensure_migrated(ws: Path) -> None:
    """読み取り系操作用: 旧形式が残っているときだけロックして移行する。"""
    if _legacy_session_path(ws).is_file():
        with _recovery_lock(ws):
            _migrate_legacy_locked(ws)


def _is_unfinished(session: Dict[str, Any]) -> bool:
    return session.get("state") not in FINISHED_STATES


def _read_session_file(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise RuntimeError(f"Session file is unreadable: {path} ({exc})") from exc


def _load_all_sessions(ws: Path) -> List[Dict[str, Any]]:
    sdir = _sessions_dir(ws)
    if not sdir.is_dir():
        return []
    return [_read_session_file(p) for p in sorted(sdir.glob("*.json"))]


def _save_session(ws: Path, session: Dict[str, Any]) -> None:
    atomic_write_json(_session_path(ws, session["work_id"]), session)


def _resolve_session(ws: Path, work_id: Optional[str], latest_fallback: bool = False) -> Dict[str, Any]:
    """work_id 指定ならその session。省略時は未完了が1つのときだけそれを返す。"""
    if work_id is not None:
        path = _session_path(ws, work_id)
        if not path.is_file():
            raise RuntimeError(f"No session found for work_id: {work_id}")
        return _read_session_file(path)

    sessions = _load_all_sessions(ws)
    unfinished = [s for s in sessions if _is_unfinished(s)]
    if len(unfinished) == 1:
        return unfinished[0]
    if len(unfinished) > 1:
        ids = ", ".join(sorted(str(s.get("work_id")) for s in unfinished))
        raise RuntimeError(f"Multiple unfinished sessions exist ({ids}). Specify --work-id.")
    if latest_fallback and sessions:
        return max(sessions, key=lambda s: str(s.get("updated_at_utc") or ""))
    raise RuntimeError("No active session found")


def _scope_key(rel: str) -> List[str]:
    key = os.path.normcase(rel).replace("\\", "/")
    return [part for part in key.split("/") if part]


def _scopes_overlap(a: str, b: str) -> bool:
    """同一パス、またはディレクトリ包含 (どちらかがもう一方の親) なら True。"""
    ka, kb = _scope_key(a), _scope_key(b)
    n = min(len(ka), len(kb))
    return ka[:n] == kb[:n]


def _check_scope_conflicts(ws: Path, rel_paths: List[str], exclude_work_id: Optional[str] = None) -> None:
    exclude = exclude_work_id.casefold() if exclude_work_id else None
    for other in _load_all_sessions(ws):
        if not _is_unfinished(other):
            continue
        other_id = str(other.get("work_id"))
        if exclude and other_id.casefold() == exclude:
            continue
        for mine in rel_paths:
            for theirs in other.get("scope", []):
                if _scopes_overlap(mine, theirs):
                    raise RuntimeError(
                        f"F203: Scope overlaps with active session {other_id} ({other.get('state')}): "
                        f"{mine} <-> {theirs}"
                    )


def session_start(workspace: Path, work_id: str, scope: List[str], owner: Optional[str] = None) -> Dict[str, Any]:
    ws = workspace.resolve()
    _validate_work_id(work_id)
    rel_paths = _normalize_scope_paths(ws, scope)

    with _session_store_write(ws):
        for other in _load_all_sessions(ws):
            if str(other.get("work_id")).casefold() == work_id.casefold() and _is_unfinished(other):
                raise RuntimeError(f"Active session already exists: {other.get('work_id')} ({other.get('state')})")
        _check_scope_conflicts(ws, rel_paths)

        base_dir = _backup_dir(ws, work_id)
        if base_dir.exists():
            shutil.rmtree(base_dir)

        baseline_snap = _capture_snapshot(ws, rel_paths, base_dir / "baseline")
        gen_snap = _capture_snapshot(ws, rel_paths, base_dir / "gen_0")

        now = utc_now()
        session_data: Dict[str, Any] = {
            "schema_version": SESSION_SCHEMA_VERSION,
            "work_id": work_id,
            "state": "running",
            "owner": owner,
            "generation": 0,
            "rework_count": 0,
            "scope": rel_paths,
            "baseline_snapshot": baseline_snap,
            "generation_snapshot": gen_snap,
            "wip_snapshot": None,
            "last_reason": None,
            "last_error_key": None,
            "started_at_utc": now,
            "updated_at_utc": now,
        }
        _save_session(ws, session_data)
        append_jsonl(_sessions_log_path(ws), {"event": "start", **session_data})
    return {"status": "success", "action": "session_start", "session": session_data}


def session_stop(
    workspace: Path, reason: str, error_key: Optional[str] = None, work_id: Optional[str] = None
) -> Dict[str, Any]:
    ws = workspace.resolve()
    with _session_store_write(ws):
        session = _resolve_session(ws, work_id)
        if session.get("state") != "running":
            raise RuntimeError(f"Cannot stop session in state: {session.get('state')}")

        base_dir = _backup_dir(ws, session["work_id"])
        wip_snap = _capture_snapshot(ws, session["scope"], base_dir / "wip")

        session["state"] = "waiting_human"
        session["wip_snapshot"] = wip_snap
        session["last_reason"] = reason
        session["last_error_key"] = error_key
        session["updated_at_utc"] = utc_now()

        _save_session(ws, session)
        append_jsonl(_sessions_log_path(ws), {"event": "stop", "reason": reason, "error_key": error_key, **session})
    return {"status": "success", "action": "session_stop", "session": session}


def session_resume(workspace: Path, work_id: Optional[str] = None) -> Dict[str, Any]:
    ws = workspace.resolve()
    with _session_store_write(ws):
        session = _resolve_session(ws, work_id)
        if session.get("state") != "waiting_human":
            raise RuntimeError(f"Cannot resume session in state: {session.get('state')}")

        expected = session.get("wip_snapshot") or session.get("generation_snapshot") or {}
        mismatches: List[str] = []
        for rel, info in expected.items():
            target = ws / rel
            if info.get("exists"):
                if not target.is_file() or calculate_sha256(target) != info.get("sha256"):
                    mismatches.append(rel)
            else:
                if target.exists():
                    mismatches.append(rel)

        if mismatches:
            raise RuntimeError(f"F204: Scope files modified outside session while stopped: {mismatches}. Use 'session revise' or 'session rollback'.")

        session["state"] = "running"
        session["updated_at_utc"] = utc_now()
        _save_session(ws, session)
        append_jsonl(_sessions_log_path(ws), {"event": "resume", **session})
    return {"status": "success", "action": "session_resume", "session": session}


def session_revise(
    workspace: Path, add_scope: Optional[List[str]] = None, work_id: Optional[str] = None
) -> Dict[str, Any]:
    ws = workspace.resolve()
    with _session_store_write(ws):
        session = _resolve_session(ws, work_id)
        if session.get("state") not in {"waiting_human", "running"}:
            raise RuntimeError(f"Cannot revise session in state: {session.get('state')}")

        rework_count = int(session.get("rework_count", 0)) + 1
        if rework_count > MAX_REWORK_COUNT:
            raise RuntimeError(f"F202: Rework limit exceeded (rework_count={rework_count} > MAX_REWORK_COUNT={MAX_REWORK_COUNT})")

        base_dir = _backup_dir(ws, session["work_id"])
        current_scope: List[str] = list(session["scope"])

        if add_scope:
            extra_paths = _normalize_scope_paths(ws, add_scope)
            new_only = [p for p in extra_paths if p not in current_scope]
            if new_only:
                _check_scope_conflicts(ws, new_only, exclude_work_id=session["work_id"])
                extra_base = _capture_snapshot(ws, new_only, base_dir / "baseline")
                session["baseline_snapshot"].update(extra_base)
                current_scope.extend(new_only)

        generation = int(session.get("generation", 0)) + 1
        gen_snap = _capture_snapshot(ws, current_scope, base_dir / f"gen_{generation}")

        session["scope"] = current_scope
        session["generation"] = generation
        session["rework_count"] = rework_count
        session["state"] = "running"
        session["generation_snapshot"] = gen_snap
        session["wip_snapshot"] = None
        session["updated_at_utc"] = utc_now()

        _save_session(ws, session)
        append_jsonl(_sessions_log_path(ws), {"event": "revise", **session})
    return {"status": "success", "action": "session_revise", "session": session}


def session_rollback(
    workspace: Path, to_target: str = "baseline", work_id: Optional[str] = None
) -> Dict[str, Any]:
    ws = workspace.resolve()
    with _session_store_write(ws):
        session = _resolve_session(ws, work_id)
        if not _is_unfinished(session):
            raise RuntimeError(
                f"Cannot rollback session in state: {session.get('state')} "
                f"(finished sessions cannot be restored; their files may now belong to another session)"
            )
        base_dir = _backup_dir(ws, session["work_id"])

        if to_target == "baseline":
            snap = session["baseline_snapshot"]
            backup_dir = base_dir / "baseline"
            next_state = "abandoned"
        elif to_target == "generation":
            gen = session.get("generation", 0)
            snap = session["generation_snapshot"]
            backup_dir = base_dir / f"gen_{gen}"
            next_state = "waiting_human"
        else:
            raise ValueError(f"Unsupported rollback target: {to_target}")

        # 復元先が他の未完了 session の scope と重なる場合は拒否 (他 session の作業を壊さない)
        _check_scope_conflicts(ws, list(snap.keys()), exclude_work_id=session["work_id"])
        restore_info = _restore_snapshot(ws, snap, backup_dir)
        session["state"] = next_state
        session["wip_snapshot"] = None
        session["updated_at_utc"] = utc_now()

        _save_session(ws, session)
        append_jsonl(_sessions_log_path(ws), {"event": "rollback", "to": to_target, "restore_info": restore_info, **session})
    return {
        "status": "success",
        "action": "session_rollback",
        "to": to_target,
        "restore_info": restore_info,
        "session": session,
    }


def session_complete(workspace: Path, work_id: Optional[str] = None) -> Dict[str, Any]:
    ws = workspace.resolve()
    with _session_store_write(ws):
        session = _resolve_session(ws, work_id)
        if session.get("state") != "running":
            raise RuntimeError(f"Cannot complete session in state: {session.get('state')}")

        base_dir = _backup_dir(ws, session["work_id"])
        final_snap = _capture_snapshot(ws, session["scope"], base_dir / "final")

        session["state"] = "completed"
        session["final_snapshot"] = final_snap
        session["updated_at_utc"] = utc_now()

        _save_session(ws, session)
        append_jsonl(_sessions_log_path(ws), {"event": "complete", **session})
    return {"status": "success", "action": "session_complete", "session": session}


def session_abandon(workspace: Path, work_id: str, reason: str) -> Dict[str, Any]:
    """放置 session の片付け。スナップショット/ファイルには触らず状態だけを abandoned にする (rollback はしない)。"""
    ws = workspace.resolve()
    _validate_work_id(work_id)
    if not reason or not reason.strip():
        raise ValueError("abandon requires a non-empty reason")
    with _session_store_write(ws):
        session = _resolve_session(ws, work_id)
        if not _is_unfinished(session):
            raise RuntimeError(f"Cannot abandon session in state: {session.get('state')}")

        previous_state = session.get("state")
        now = utc_now()
        session["state"] = "abandoned"
        session["abandon_reason"] = reason
        session["abandoned_at_utc"] = now
        session["updated_at_utc"] = now

        _save_session(ws, session)
        append_jsonl(
            _sessions_log_path(ws),
            {"event": "abandon", "reason": reason, "previous_state": previous_state, **session},
        )
    return {"status": "success", "action": "session_abandon", "previous_state": previous_state, "session": session}


def _parse_utc(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def session_list(
    workspace: Path, stale_hours: Optional[float] = None, include_finished: bool = False
) -> Dict[str, Any]:
    ws = workspace.resolve()
    _ensure_migrated(ws)
    now = datetime.now(timezone.utc)
    rows: List[Dict[str, Any]] = []
    for s in _load_all_sessions(ws):
        if not include_finished and not _is_unfinished(s):
            continue
        updated = _parse_utc(s.get("updated_at_utc"))
        age_seconds = int((now - updated).total_seconds()) if updated else None
        if stale_hours is not None and (age_seconds is None or age_seconds < stale_hours * 3600):
            continue
        rows.append({
            "work_id": s.get("work_id"),
            "state": s.get("state"),
            "scope": s.get("scope"),
            "owner": s.get("owner"),
            "started_at_utc": s.get("started_at_utc"),
            "updated_at_utc": s.get("updated_at_utc"),
            "age_seconds": age_seconds,
            "age_hours": round(age_seconds / 3600, 2) if age_seconds is not None else None,
            "last_reason": s.get("last_reason"),
            "abandon_reason": s.get("abandon_reason"),
        })
    return {
        "status": "success",
        "action": "session_list",
        "stale_hours": stale_hours,
        "include_finished": include_finished,
        "count": len(rows),
        "sessions": rows,
    }


def session_status(workspace: Path, work_id: Optional[str] = None) -> Dict[str, Any]:
    ws = workspace.resolve()
    _ensure_migrated(ws)
    if work_id is None and not _load_all_sessions(ws):
        return {"status": "success", "active": False, "session": None}
    session = _resolve_session(ws, work_id, latest_fallback=True)
    baseline = session.get("baseline_snapshot", {})
    modified: List[str] = []
    created: List[str] = []
    deleted: List[str] = []

    for rel in session.get("scope", []):
        target = ws / rel
        base_info = baseline.get(rel, {"exists": False, "sha256": None})
        if base_info.get("exists"):
            if not target.is_file():
                deleted.append(rel)
            elif calculate_sha256(target) != base_info.get("sha256"):
                modified.append(rel)
        else:
            if target.is_file():
                created.append(rel)

    return {
        "status": "success",
        "active": session.get("state") in {"running", "waiting_human"},
        "work_id": session.get("work_id"),
        "state": session.get("state"),
        "owner": session.get("owner"),
        "generation": session.get("generation"),
        "rework_count": session.get("rework_count"),
        "scope": session.get("scope"),
        "changes_since_baseline": {
            "modified": modified,
            "created": created,
            "deleted": deleted,
        },
        "session": session,
    }


# ---------------------------------------------------------
# 4. コンテキストキャッシュ (Context Cache Indexing)
# ---------------------------------------------------------
def _within_workspace(ws: Path, rel_or_path: str | Path) -> Path:
    candidate = (ws / rel_or_path) if not Path(rel_or_path).is_absolute() else Path(rel_or_path)
    for parent in (candidate, *candidate.parents):
        if parent == ws:
            break
        if parent.exists() and (parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction())):
            raise ValueError(f"Symlink or junction not allowed in context cache: {rel_or_path}")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(ws):
        raise ValueError(f"Path escapes workspace: {rel_or_path}")
    return resolved


def _collect_context_files(ws: Path, inputs: List[str]) -> List[Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    for raw in inputs:
        path = _within_workspace(ws, raw)
        rel_parts = path.relative_to(ws).parts
        if any(part in CONTEXT_EXCLUDED_DIRS or part.startswith(".env") for part in rel_parts):
            raise ValueError(f"Excluded input in context scope: {raw}")
        if not path.exists():
            raise FileNotFoundError(f"Missing context input: {raw}")
        pending = [path]
        while pending:
            item = pending.pop()
            if item.name in CONTEXT_EXCLUDED_DIRS or item.name.startswith(".env"):
                continue
            item = _within_workspace(ws, item.relative_to(ws))
            if item.is_dir():
                pending.extend(sorted(item.iterdir(), reverse=True))
                continue
            if item.suffix.lower() in CONTEXT_EXCLUDED_SUFFIXES or item.name in CONTEXT_EXCLUDED_FILES:
                continue
            stat_info = item.stat()
            sha = calculate_sha256(item)
            rel = item.relative_to(ws).as_posix()
            kind = (
                "code" if item.suffix in {".py", ".rs", ".ts", ".js"}
                else "rule" if item.name in {"AGENTS.md", "START-HERE.md", "GENERAL.md"}
                else "config" if item.suffix == ".json"
                else "document"
            )
            found[rel] = {
                "path": rel,
                "sha256": sha,
                "bytes": stat_info.st_size,
                "mtime_ns": stat_info.st_mtime_ns,
                "kind": kind,
            }
    return [found[k] for k in sorted(found)]


def build_context_cache(
    workspace: Path,
    scope_key: str,
    inputs_override: Optional[List[str]] = None,
    write: bool = False,
) -> Dict[str, Any]:
    ws = workspace.resolve()
    if not SCOPE_KEY_RE.fullmatch(scope_key):
        raise ValueError(f"Invalid scope_key: {scope_key}")

    scopes_file = ws / "work" / "settings" / "context_scopes.json"
    scopes_data: Dict[str, Any] = {"schema_version": 1, "cache_root": "work/context_cache", "scopes": {}}
    if scopes_file.is_file():
        scopes_data = json.loads(scopes_file.read_text(encoding="utf-8"))

    scopes = scopes_data.get("scopes", {})
    if inputs_override:
        inputs = inputs_override
        scopes[scope_key] = {"source_root": inputs[0], "inputs": inputs}
        if write:
            scopes_data["scopes"] = scopes
            atomic_write_json(scopes_file, scopes_data)
    elif scope_key in scopes:
        inputs = scopes[scope_key]["inputs"]
    else:
        raise ValueError(f"Unknown context scope '{scope_key}'. Register it in work/settings/context_scopes.json or pass --inputs.")

    target = _within_workspace(ws, f"work/context_cache/{scope_key}/context_manifest.json")
    common = _within_workspace(ws, "work/context_cache/common/workspace_manifest.json")

    files = _collect_context_files(ws, inputs)
    previous = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
    old_map = {item["path"]: item for item in previous.get("files", [])}

    for item in files:
        prior = old_map.get(item["path"])
        item["change"] = (
            "added" if prior is None
            else "unchanged" if prior.get("sha256") == item["sha256"]
            else "modified"
        )
    deleted = sorted(set(old_map) - {item["path"] for item in files})

    summary = {
        "status": "success",
        "action": "context",
        "scope": scope_key,
        "mode": "write" if write else "dry_run",
        "total_files": len(files),
        "added": [f["path"] for f in files if f["change"] == "added"],
        "modified": [f["path"] for f in files if f["change"] == "modified"],
        "unchanged_count": sum(1 for f in files if f["change"] == "unchanged"),
        "deleted": deleted,
    }

    if write:
        now = utc_now()
        atomic_write_json(
            target,
            {
                "schema_version": 1,
                "scope_key": scope_key,
                "workspace_root": str(ws),
                "generated_at_utc": now,
                "status": "indexed",
                "acceptance_evidence": False,
                "inputs": inputs,
                "files": files,
                "deleted": deleted,
            },
        )
        atomic_write_json(
            common,
            {
                "schema_version": 1,
                "workspace_root": str(ws),
                "generated_at_utc": now,
                "scopes": scopes,
                "acceptance_evidence": False,
            },
        )

    return summary


# ---------------------------------------------------------
# 5. アプデ確認・検証 (Update / Check)
# ---------------------------------------------------------
def check_updates(workspace: Path) -> Dict[str, Any]:
    ws = workspace.resolve()
    results: Dict[str, Any] = {"workspace": str(ws), "components": {}}

    core_dir = ws / "Tusk_core"
    core_manifest = core_dir / "DISTRIBUTION-MANIFEST.json"
    if core_manifest.is_file():
        try:
            m_data = json.loads(core_manifest.read_text(encoding="utf-8"))
            mismatches = []
            for item in m_data.get("managed_files", []):
                p = core_dir / item["path"]
                if not p.is_file() or calculate_sha256(p) != item["sha256"]:
                    mismatches.append(item["path"])
            results["components"]["Tusk_core"] = {
                "version": m_data.get("version", "unknown"),
                "status": "up_to_date" if not mismatches else "modified",
                "modified_files": mismatches,
            }
        except Exception as e:
            results["components"]["Tusk_core"] = {"status": "error", "error": str(e)}

    ext_list = list_extensions(ws)
    for ext_name in ext_list["available_extensions"]:
        ext_dir = ws / "extensions" / ext_name
        manifest_path = ext_dir / "EXTENSION-MANIFEST.json"
        if manifest_path.is_file():
            try:
                m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                mismatches = []
                for item in m_data.get("managed_files", []):
                    p = ext_dir / item["path"]
                    if not p.is_file() or calculate_sha256(p) != item["sha256"]:
                        mismatches.append(item["path"])
                results["components"][ext_name] = {
                    "status": "up_to_date" if not mismatches else "modified",
                    "modified_files": mismatches,
                }
            except Exception as e:
                results["components"][ext_name] = {"status": "error", "error": str(e)}

    return results


# ---------------------------------------------------------
# 6. アンインストーラ (Uninstall / Clean)
# ---------------------------------------------------------
def uninstall_workspace(workspace: Path, purge: bool = False) -> Dict[str, Any]:
    ws = workspace.resolve()
    removed = []

    if purge:
        if ws.exists():
            shutil.rmtree(ws)
            removed.append(str(ws))
    else:
        for target_dir in [ws / "work", ws / "tmp"]:
            if target_dir.exists():
                shutil.rmtree(target_dir)
                removed.append(str(target_dir))

    return {
        "status": "success",
        "action": "uninstall",
        "purge": purge,
        "removed_paths": removed,
    }


# ---------------------------------------------------------
# CLI エントリーポイント
# ---------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tusk Workspace, Extension, Session & Context Cache Manager")
    parser.add_argument("--workspace", type=Path, default=get_default_workspace(), help="Target workspace path")
    parser.add_argument("--json", action="store_true", help="Output in JSON format")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. init
    init_parser = subparsers.add_parser("init", help="Initialize workspace with Core baseline")
    init_parser.add_argument("--core-source", type=Path, help="Path to Tusk_core source to copy")

    # 2. ext
    ext_parser = subparsers.add_parser("ext", help="Manage extensions")
    ext_sub = ext_parser.add_subparsers(dest="ext_command", required=True)
    ext_sub.add_parser("list", help="List extensions")

    inst_p = ext_sub.add_parser("install", help="Install extension from directory")
    inst_p.add_argument("source", type=Path, help="Extension source folder")
    inst_p.add_argument("--no-enable", action="store_true", help="Do not enable after install")

    en_p = ext_sub.add_parser("enable", help="Enable an extension")
    en_p.add_argument("name", help="Extension folder name")

    dis_p = ext_sub.add_parser("disable", help="Disable an extension")
    dis_p.add_argument("name", help="Extension folder name")

    rem_p = ext_sub.add_parser("remove", help="Remove an extension")
    rem_p.add_argument("name", help="Extension folder name")

    # 3. session (In-progress change recovery)
    sess_parser = subparsers.add_parser("session", help="Manage in-progress work session & recovery")
    sess_sub = sess_parser.add_subparsers(dest="session_command", required=True)

    s_start = sess_sub.add_parser("start", help="Start a new work session and snapshot scope files")
    s_start.add_argument("--work-id", required=True, help="Unique work identifier")
    s_start.add_argument("--scope", nargs="+", required=True, help="Target file paths in scope")
    s_start.add_argument("--owner", help="Optional owner label (agent/person/terminal) recorded in the session")

    wid_help = "Target session (optional only when exactly one unfinished session exists)"

    s_stop = sess_sub.add_parser("stop", help="Stop active session and save WIP snapshot")
    s_stop.add_argument("--work-id", help=wid_help)
    s_stop.add_argument("--reason", required=True, help="Stop reason")
    s_stop.add_argument("--error-key", help="Optional error code/key")

    s_res = sess_sub.add_parser("resume", help="Verify WIP snapshot and resume stopped session")
    s_res.add_argument("--work-id", help=wid_help)

    s_rev = sess_sub.add_parser("revise", help="Increment generation/rework_count and continue from current WIP")
    s_rev.add_argument("--work-id", help=wid_help)
    s_rev.add_argument("--scope", nargs="*", help="Additional file paths to add to scope")

    s_roll = sess_sub.add_parser("rollback", help="Restore scoped files to baseline or generation snapshot")
    s_roll.add_argument("--work-id", help=wid_help)
    s_roll.add_argument("--to", choices=("baseline", "generation"), default="baseline", help="Rollback target")

    s_comp = sess_sub.add_parser("complete", help="Mark active session as completed")
    s_comp.add_argument("--work-id", help=wid_help)

    s_stat = sess_sub.add_parser("status", help="Show session status and scope diffs")
    s_stat.add_argument("--work-id", help=wid_help)

    s_list = sess_sub.add_parser("list", help="List sessions (unfinished by default)")
    s_list.add_argument("--stale-hours", type=float, help="Only sessions not updated for at least this many hours")
    s_list.add_argument("--all", action="store_true", help="Include completed/abandoned sessions")

    s_aband = sess_sub.add_parser("abandon", help="Mark a stale session abandoned without touching files or snapshots")
    s_aband.add_argument("--work-id", required=True, help="Session to abandon")
    s_aband.add_argument("--reason", required=True, help="Abandon reason")

    # 4. context (Context Cache diff/index)
    ctx_parser = subparsers.add_parser("context", help="Check or update scope Context Cache manifest")
    ctx_parser.add_argument("--scope", required=True, help="Scope key (e.g. core_reference)")
    ctx_parser.add_argument("--inputs", nargs="*", help="Optional input paths to register/override for this scope")
    ctx_parser.add_argument("--write", action="store_true", help="Write updated context_manifest.json (default is dry-run)")

    # 5. update
    up_parser = subparsers.add_parser("update", help="Check and apply updates")
    up_parser.add_argument("--check", action="store_true", help="Only check for updates without applying")

    # 6. uninstall
    un_parser = subparsers.add_parser("uninstall", help="Uninstall workspace / Clean temporary files")
    un_parser.add_argument("--purge", action="store_true", help="Purge entire workspace directory")
    un_parser.add_argument("--force", action="store_true", help="Force without confirmation")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    ws: Path = args.workspace

    try:
        if args.command == "init":
            res = init_workspace(ws, args.core_source)
        elif args.command == "ext":
            if args.ext_command == "list":
                res = list_extensions(ws)
            elif args.ext_command == "install":
                res = install_extension(ws, args.source, enable=not args.no_enable)
            elif args.ext_command == "enable":
                res = set_extension_enabled(ws, args.name, True)
            elif args.ext_command == "disable":
                res = set_extension_enabled(ws, args.name, False)
            elif args.ext_command == "remove":
                res = remove_extension(ws, args.name)
            else:
                parser.error("Unknown ext subcommand")
        elif args.command == "session":
            if args.session_command == "start":
                res = session_start(ws, args.work_id, args.scope, owner=args.owner)
            elif args.session_command == "stop":
                res = session_stop(ws, args.reason, args.error_key, work_id=args.work_id)
            elif args.session_command == "resume":
                res = session_resume(ws, work_id=args.work_id)
            elif args.session_command == "revise":
                res = session_revise(ws, args.scope, work_id=args.work_id)
            elif args.session_command == "rollback":
                res = session_rollback(ws, args.to, work_id=args.work_id)
            elif args.session_command == "complete":
                res = session_complete(ws, work_id=args.work_id)
            elif args.session_command == "status":
                res = session_status(ws, work_id=args.work_id)
            elif args.session_command == "list":
                res = session_list(ws, stale_hours=args.stale_hours, include_finished=args.all)
            elif args.session_command == "abandon":
                res = session_abandon(ws, args.work_id, args.reason)
            else:
                parser.error("Unknown session subcommand")
        elif args.command == "context":
            res = build_context_cache(ws, args.scope, inputs_override=args.inputs, write=args.write)
        elif args.command == "update":
            res = check_updates(ws)
        elif args.command == "uninstall":
            if not args.force and not args.json:
                confirm = input(f"Are you sure you want to uninstall {'(PURGE ENTIRE WORKSPACE)' if args.purge else ''} at {ws}? [y/N]: ")
                if confirm.lower() != "y":
                    print("Cancelled.")
                    return 1
            res = uninstall_workspace(ws, purge=args.purge)
        else:
            parser.error("Unknown command")

        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"[{res.get('status', 'OK').upper()}] {args.command} completed.")
            print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    except Exception as e:
        if args.json:
            print(json.dumps({"status": "error", "error": str(e)}, indent=2, ensure_ascii=False))
        else:
            print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
