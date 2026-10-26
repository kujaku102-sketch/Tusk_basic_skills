#!/usr/bin/env python3
"""
/fvision runner — Unified CLI runner for FiumLLER Vision & Geometry Inspection.
Orchestrates rendering, visual diff comparison, and geometry anomaly detection.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RENDER_SCRIPT = HERE / "render_inspect.py"
COMPARE_SCRIPT = HERE / "compare_pages.py"
ANOMALY_SCRIPT = HERE / "detect_anomalies.py"
INSPECT_SCRIPT = HERE / "inspect_geometry.py"


def find_default_exe(name: str) -> str | None:
    # Common search paths
    candidates = [
        Path(f"Act4.1/target/release/{name}.exe"),
        Path(f"Act4.1/target/debug/{name}.exe"),
        Path(f"Act3/target/release/{name}.exe"),
        Path(f"Act3/target/debug/{name}.exe"),
        Path(f"bin/{name}.exe"),
        Path(f"{name}.exe"),
    ]
    for c in candidates:
        if c.exists():
            return str(c.resolve())
    return None


def run_command(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="fvision_runner.py",
        description="Unified runner for /fvision: Rendering, Diffing, and Geometry Inspection"
    )
    parser.add_argument("target", help="Input PDF or .folio file")
    parser.add_argument("--page", "-p", type=int, default=1, help="1-based page number (default: 1)")
    parser.add_argument("--compare", "-c", help="Original PDF file for visual diff comparison")
    parser.add_argument("--dpi", type=float, default=150.0, help="Render DPI (default: 150.0)")
    parser.add_argument("--out", "-o", help="Output directory or image path")
    parser.add_argument("--diff", action="store_true", help="Generate visual diff against --compare PDF")
    parser.add_argument("--anomalies", action="store_true", help="Run geometric anomaly detection")
    parser.add_argument("--geometry", action="store_true", help="Dump raw geometry structure to JSON")
    parser.add_argument("--legacy-rules", action="store_true", help="Enable legacy margin/CAD heuristics")
    parser.add_argument("--json", action="store_true", help="Output summary in JSON format")

    args = parser.parse_args()

    target_path = Path(args.target).resolve()
    if not target_path.exists():
        print(f"[ERROR] Target file not found: {target_path}", file=sys.stderr)
        return 1

    out_dir = Path(args.out) if args.out else Path("work/fvision_output")
    out_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, object] = {
        "target": str(target_path),
        "page": args.page,
        "artifacts": {},
        "status": "PASS",
        "warnings": [],
        "errors": []
    }

    # 1. Page Render
    render_img = out_dir / f"{target_path.stem}_p{args.page}.png"
    render_cmd = [
        sys.executable, str(RENDER_SCRIPT),
        str(target_path),
        "--page", str(args.page),
        "--dpi", str(args.dpi),
        "--output", str(render_img)
    ]
    proc = run_command(render_cmd)
    if proc.returncode == 0 and render_img.exists():
        results["artifacts"]["rendered_image"] = str(render_img)
    else:
        results["errors"].append(f"Render failed: {proc.stderr.strip() or proc.stdout.strip()}")
        results["status"] = "FAIL"

    # 2. Visual Diff (if --compare or --diff specified)
    if args.compare or args.diff:
        if not args.compare:
            results["warnings"].append("--diff requested but no --compare PDF provided. Skipping diff.")
        else:
            compare_path = Path(args.compare).resolve()
            if not compare_path.exists():
                results["errors"].append(f"Compare original PDF not found: {compare_path}")
            else:
                diff_img = out_dir / f"diff_{target_path.stem}_vs_{compare_path.stem}_p{args.page}.png"
                compare_cmd = [
                    sys.executable, str(COMPARE_SCRIPT),
                    str(compare_path),
                    str(target_path),
                    "--page", str(args.page),
                    "--dpi", str(args.dpi),
                    "--output", str(diff_img),
                    "--overwrite"
                ]
                proc = run_command(compare_cmd)
                if proc.returncode == 0 and diff_img.exists():
                    results["artifacts"]["diff_image"] = str(diff_img)
                else:
                    results["warnings"].append(f"Visual diff failed: {proc.stderr.strip()}")

    # 3. Geometric Anomaly Detection (if --anomalies specified)
    if args.anomalies:
        anomaly_work = out_dir / "anomalies_tmp"
        anomaly_cmd = [
            sys.executable, str(ANOMALY_SCRIPT),
            str(target_path),
            "--page", str(args.page),
            "--work-dir", str(anomaly_work),
            "--format", "json"
        ]
        if getattr(args, "legacy_rules", False):
            anomaly_cmd.append("--legacy-rules")
        act4_exe = find_default_exe("act4")
        if act4_exe:
            anomaly_cmd.extend(["--act4-exe", act4_exe])

        proc = run_command(anomaly_cmd)
        if proc.returncode == 0:
            try:
                results["anomalies"] = json.loads(proc.stdout)
            except Exception:
                results["anomalies"] = proc.stdout.strip()
        else:
            results["warnings"].append(f"Anomaly detection warning: {proc.stderr.strip()}")

    # 4. Geometry Structure Dump (if --geometry specified)
    if args.geometry:
        geom_json = out_dir / f"geometry_p{args.page}.json"
        geom_cmd = [
            sys.executable, str(INSPECT_SCRIPT),
            str(target_path),
            "--page", str(args.page),
            "--format", "json"
        ]
        proc = run_command(geom_cmd)
        if proc.returncode == 0:
            geom_json.write_text(proc.stdout, encoding="utf-8")
            results["artifacts"]["geometry_json"] = str(geom_json)
        else:
            results["warnings"].append(f"Geometry dump warning: {proc.stderr.strip()}")

    # Output representation
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print(f"=== /fvision Inspection Report: Page {args.page} ===")
        print(f"Target: {target_path.name}")
        print(f"Status: {results['status']}")
        if "rendered_image" in results["artifacts"]:
            print(f"Rendered Image: {results['artifacts']['rendered_image']}")
        if "diff_image" in results["artifacts"]:
            print(f"Diff Image:     {results['artifacts']['diff_image']}")
        if "geometry_json" in results["artifacts"]:
            print(f"Geometry JSON:  {results['artifacts']['geometry_json']}")
        if "anomalies" in results:
            print("Anomalies:", results["anomalies"])
        if results["warnings"]:
            print("Warnings:", results["warnings"])
        if results["errors"]:
            print("Errors:", results["errors"])

    return 0 if results["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
