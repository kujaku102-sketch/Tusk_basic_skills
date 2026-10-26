from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List


EXCLUDED_DIRS = {
    ".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv",
    "target", "dist", "build", "work", "archive", "tmp", "_internal",
}
CODE_EXTS = {".py", ".rs", ".ts", ".tsx", ".js", ".jsx", ".cs"}

# Regex patterns for Axis 1 (Error Codes)
VALID_MARKER_RE = re.compile(r"\[TUSK_(FORCE_STOP|MINOR_ISSUE|PROGRESS)\]")
LEGACY_MARKER_RE = re.compile(r"\[IDTASK_(FORCE_STOP|MINOR_ISSUE|PROGRESS)\]")
OLD_0XX_CODE_RE = re.compile(r"(?<![\\{])\b[FM]0[0-9]{2}\b")
FIXED_CODE_RE = re.compile(r"\b([FM][1-5][0-9]{2})\b")
PRODUCT_CODE_RE = re.compile(r"\b([FM][6-9][0-9]{2})\b")
LEGACY_STRING_CODES = (
    "STALL_30M", "LOOP_DETECTED", "PROCESS_LOST", "PIPELINE_FATAL",
    "ARTIFACT_INVALID", "TRANSLATION_FATAL", "TRANSLATION_SKIP",
)

# Heuristics for Axis 2 (Microkernel) & Axis 3 (CUI/GUI Separation)
GUI_KEYWORDS = re.compile(
    r"\b(tkinter|PyQt|PySide|wx|tauri|electron|win32gui| SendInput| CreateWindow|React|useState|useEffect|DOM|window\.)\b"
)
CLI_KEYWORDS = re.compile(
    r"\b(argparse|click|typer|clap| StructOpt|subparsers|sys\.argv|std::env::args)\b"
)
HEAVY_LOGIC_IN_GUI_KEYWORDS = re.compile(
    r"\b(sqlite3|sqlalchemy|requests\.|urllib|pdfium|fitz|openpyxl|pandas| hashlib\.sha256|subprocess\.Popen)\b"
)


def scan_target(target_dir: Path, bloated_line_threshold: int = 600) -> Dict[str, Any]:
    root = target_dir.resolve()
    if not root.exists():
        raise FileNotFoundError(f"Target not found: {root}")

    files_scanned = 0
    axis1_findings: List[Dict[str, Any]] = []
    fixed_codes_used: set[str] = set()
    product_codes_used: set[str] = set()

    bloated_files: List[Dict[str, Any]] = []
    entrypoint_candidates: List[Dict[str, Any]] = []
    cli_files: List[str] = []
    gui_files: List[str] = []
    gui_logic_leaks: List[Dict[str, Any]] = []

    pending = [root]
    while pending:
        current = pending.pop()
        if current.name in EXCLUDED_DIRS:
            continue
        if current.is_dir():
            pending.extend(sorted(current.iterdir(), reverse=True))
            continue
        if current.suffix.lower() not in CODE_EXTS and current.suffix.lower() != ".md":
            continue

        try:
            text = current.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        rel = current.relative_to(root).as_posix() if current != root else current.name
        lines = text.splitlines()
        line_count = len(lines)
        files_scanned += 1

        # --- Axis 1: Error Code Compliance ---
        for idx, line in enumerate(lines, start=1):
            if LEGACY_MARKER_RE.search(line):
                axis1_findings.append({
                    "file": rel, "line": idx, "issue": "legacy_idtask_marker",
                    "snippet": line.strip()[:120],
                })
            if OLD_0XX_CODE_RE.search(line):
                axis1_findings.append({
                    "file": rel, "line": idx, "issue": "deprecated_0xx_code",
                    "snippet": line.strip()[:120],
                })
            for leg in LEGACY_STRING_CODES:
                if leg in line:
                    axis1_findings.append({
                        "file": rel, "line": idx, "issue": f"legacy_string_code:{leg}",
                        "snippet": line.strip()[:120],
                    })
            for m in FIXED_CODE_RE.finditer(line):
                fixed_codes_used.add(m.group(1))
            for m in PRODUCT_CODE_RE.finditer(line):
                product_codes_used.add(m.group(1))

        if current.suffix.lower() not in CODE_EXTS:
            continue

        # --- Axis 2: Microkernel & Runtime Size ---
        if line_count >= bloated_line_threshold:
            bloated_files.append({"file": rel, "lines": line_count})
        if current.name in {"main.py", "cli.py", "app.py", "main.rs", "lib.rs", "index.ts"}:
            entrypoint_candidates.append({"file": rel, "lines": line_count})

        # --- Axis 3: CUI vs GUI Separation ---
        has_gui = bool(GUI_KEYWORDS.search(text)) or any(
            part in {"ui", "gui", "frontend", "views", "components"} for part in current.parts
        )
        has_cli = bool(CLI_KEYWORDS.search(text))

        if has_cli:
            cli_files.append(rel)
        if has_gui:
            gui_files.append(rel)
            # Check if GUI file directly embeds heavy data/IO/engine logic
            leaks = sorted(set(m.group(0) for m in HEAVY_LOGIC_IN_GUI_KEYWORDS.finditer(text)))
            if leaks:
                gui_logic_leaks.append({
                    "file": rel,
                    "lines": line_count,
                    "suspected_direct_io_or_engine_calls": leaks,
                })

    return {
        "status": "ok",
        "target": str(root),
        "files_scanned": files_scanned,
        "skim_signals": {
            "axis1_error_codes": {
                "status": "suspect" if axis1_findings else "pass",
                "fixed_codes_detected": sorted(fixed_codes_used),
                "product_codes_detected": sorted(product_codes_used),
                "violations": axis1_findings,
            },
            "axis2_microkernel": {
                "status": "suspect" if bloated_files else "pass",
                "bloated_threshold_lines": bloated_line_threshold,
                "bloated_files": sorted(bloated_files, key=lambda x: -x["lines"]),
                "entrypoints": entrypoint_candidates,
            },
            "axis3_cui_gui_separation": {
                "status": "suspect" if (gui_files and not cli_files) or gui_logic_leaks else "pass",
                "cli_entrypoints_detected": cli_files,
                "gui_files_detected": gui_files,
                "gui_direct_logic_suspects": gui_logic_leaks,
            },
        },
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tuskfact Stage-1 Skim Scanner")
    parser.add_argument("--target", type=Path, required=True, help="Target product/extension directory")
    parser.add_argument("--threshold", type=int, default=600, help="Line threshold for monolith/bloat warning")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args(argv)

    result = scan_target(args.target, bloated_line_threshold=args.threshold)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
