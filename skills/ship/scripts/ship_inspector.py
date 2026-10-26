from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List


IGNORED_BUILD_DIRS = {".git", "node_modules", "target", ".venv", "venv", "dist", "build"}
CACHE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
CODE_AND_CONFIG_EXTS = {".py", ".rs", ".ts", ".tsx", ".js", ".jsx", ".json", ".toml", ".yaml", ".yml", ".ps1", ".bat", ".sh"}

# Portability & Secret patterns
ABS_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\(?:Users|Documents|Desktop|work|temp|tmp)\\[^\s\"'`]+|"
    r"[A-Za-z]:/(?:Users|Documents|Desktop|work|temp|tmp)/[^\s\"'`]+|"
    r"/Users/[A-Za-z0-9._-]+/[^\s\"'`]+|"
    r"/home/[A-Za-z0-9._-]+/[^\s\"'`]+)",
    re.IGNORECASE,
)
SECRET_FILE_NAMES = {".env", "credentials.json", "token.json", "developer.key.json", "id_rsa", "id_ed25519"}
SECRET_EXTS = {".pem", ".pfx", ".p12", ".key"}
SECRET_CONTENT_RE = re.compile(
    r"(?:\b(?:sk|sess|pat)-[A-Za-z0-9_-]{16,}\b|"
    r"\b(?:api[_-]?key|client[_-]?secret|access[_-]?token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{12,}['\"])",
    re.IGNORECASE,
)

# Cruft patterns
CRUFT_DIR_NAMES = {"scratch", "tmp"}
CRUFT_FILE_PATTERNS = (
    re.compile(r".*\.bak.*$", re.IGNORECASE),
    re.compile(r".*\.(?:tmp|orig|swp|dmp)$", re.IGNORECASE),
    re.compile(r"^(?:crash|untranslated|runtime_copy|debug)\.log$", re.IGNORECASE),
    re.compile(r"^tmp_.*$", re.IGNORECASE),
)


def calculate_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def update_manifest_if_present(root: Path) -> Dict[str, Any]:
    updated: List[str] = []
    for manifest_name in ("DISTRIBUTION-MANIFEST.json", "EXTENSION-MANIFEST.json"):
        m_path = root / manifest_name
        if not m_path.is_file():
            continue
        try:
            data = json.loads(m_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        managed = []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if (
                rel == manifest_name
                or any(part in IGNORED_BUILD_DIRS | CACHE_DIRS | {"work"} for part in p.relative_to(root).parts)
                or rel.endswith(".pyc")
            ):
                continue
            managed.append({"path": rel, "sha256": calculate_sha256(p)})
        data["managed_files"] = managed
        m_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        updated.append(manifest_name)
    return {"updated_manifests": updated}


def inspect_target(
    target_dir: Path,
    clean_cache: bool = False,
    update_manifest: bool = False,
) -> Dict[str, Any]:
    root = target_dir.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Target directory not found: {root}")

    hardcoded_paths_in_code: List[Dict[str, Any]] = []
    hardcoded_paths_in_docs_or_tests: List[Dict[str, Any]] = []
    secret_findings: List[Dict[str, Any]] = []
    cruft_findings: List[Dict[str, Any]] = []
    cache_items: List[str] = []
    cleaned_cache_items: List[str] = []

    # 1. Walk tree
    for current_root, dirs, files in root.walk(top_down=True):
        rel_root = current_root.relative_to(root)

        # Skip heavy build/VCS dirs
        dirs[:] = [d for d in dirs if d not in IGNORED_BUILD_DIRS]

        # Handle cache directories
        for d in list(dirs):
            if d in CACHE_DIRS:
                c_path = current_root / d
                rel_c = c_path.relative_to(root).as_posix()
                cache_items.append(rel_c)
                if clean_cache:
                    shutil.rmtree(c_path, ignore_errors=True)
                    cleaned_cache_items.append(rel_c)
                dirs.remove(d)
            elif d.lower() in CRUFT_DIR_NAMES and rel_root != Path("work"):
                c_path = current_root / d
                cruft_findings.append({
                    "kind": "cruft_directory",
                    "path": c_path.relative_to(root).as_posix(),
                })

        for fname in files:
            fpath = current_root / fname
            rel_str = fpath.relative_to(root).as_posix()

            # Check .pyc / .tmp cache files
            if fpath.suffix.lower() == ".pyc":
                cache_items.append(rel_str)
                if clean_cache:
                    fpath.unlink(missing_ok=True)
                    cleaned_cache_items.append(rel_str)
                continue

            # Check secret files
            if fname.lower() in SECRET_FILE_NAMES or fpath.suffix.lower() in SECRET_EXTS:
                secret_findings.append({"kind": "secret_file", "path": rel_str})
                continue

            # Check cruft files
            if any(pat.match(fname) for pat in CRUFT_FILE_PATTERNS):
                cruft_findings.append({"kind": "cruft_file", "path": rel_str})

            # Inspect content for hardcoded paths & secrets
            if fpath.suffix.lower() in CODE_AND_CONFIG_EXTS or fpath.suffix.lower() == ".md":
                if fname in {"DISTRIBUTION-MANIFEST.json", "EXTENSION-MANIFEST.json", "Cargo.lock", "package-lock.json"}:
                    continue
                try:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue

                is_test_or_doc = (
                    fpath.suffix.lower() == ".md"
                    or "test" in rel_str.lower()
                    or "docs/" in rel_str.lower()
                    or "specs/" in rel_str.lower()
                )

                for line_no, line in enumerate(text.splitlines(), start=1):
                    for m in ABS_PATH_RE.finditer(line):
                        matched_path = m.group(0)
                        # Ignore regex pattern definitions themselves
                        if "Users|Documents" in matched_path:
                            continue
                        entry = {
                            "file": rel_str,
                            "line": line_no,
                            "matched_path": matched_path,
                        }
                        if is_test_or_doc:
                            hardcoded_paths_in_docs_or_tests.append(entry)
                        else:
                            hardcoded_paths_in_code.append(entry)

                    if SECRET_CONTENT_RE.search(line):
                        secret_findings.append({
                            "kind": "secret_in_content",
                            "file": rel_str,
                            "line": line_no,
                        })

    manifest_res = update_manifest_if_present(root) if update_manifest else {"updated_manifests": []}

    if hardcoded_paths_in_code or secret_findings:
        readiness = "BLOCKED"
    elif cruft_findings or (cache_items and not clean_cache):
        readiness = "NEEDS_CLEANUP"
    else:
        readiness = "READY"

    return {
        "status": "ok",
        "target": str(root),
        "readiness": readiness,
        "summary": {
            "hardcoded_paths_in_production_code": len(hardcoded_paths_in_code),
            "hardcoded_paths_in_docs_or_tests": len(hardcoded_paths_in_docs_or_tests),
            "secret_findings": len(secret_findings),
            "cruft_findings": len(cruft_findings),
            "cache_items_found": len(cache_items),
            "cache_items_cleaned": len(cleaned_cache_items),
            "updated_manifests": manifest_res["updated_manifests"],
        },
        "findings": {
            "production_hardcoded_paths": hardcoded_paths_in_code,
            "doc_or_test_hardcoded_paths": hardcoded_paths_in_docs_or_tests[:25],
            "secrets": secret_findings,
            "cruft": cruft_findings,
            "uncleaned_cache": [] if clean_cache else cache_items,
        },
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="/ship Pre-Release & Handoff Inspector")
    parser.add_argument("--target", type=Path, default=Path.cwd(), help="Target directory to inspect")
    parser.add_argument("--clean-cache", action="store_true", help="Remove __pycache__ and .pyc files automatically")
    parser.add_argument("--update-manifest", action="store_true", help="Recalculate SHA-256 in DISTRIBUTION/EXTENSION-MANIFEST.json")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args(argv)

    res = inspect_target(
        args.target,
        clean_cache=args.clean_cache,
        update_manifest=args.update_manifest,
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
