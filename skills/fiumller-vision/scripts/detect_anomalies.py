#!/usr/bin/env python3
"""Find geometry candidates in Act4.1 UDR; findings require visual review."""

import argparse
import json
import math
import subprocess
import sys

from udr_source import load_udr


def frame_text(frame: dict, stories: dict) -> str:
    story = stories.get(frame.get("story_ref"), {})
    if not isinstance(story, dict):
        return ""
    return " ".join(p.get("text", "") for p in story.get("paragraphs", [])
                    if isinstance(p, dict)).strip()


def detect_anomalies(udr: dict, page_num: int | None = None,
                     legacy_rules: bool = False) -> list[dict]:
    pages = udr.get("pages", [])
    selected = [p for p in pages if page_num is None or p.get("page_number") == page_num]
    if not selected:
        raise ValueError(f"page {page_num} not found in UDR")
    raw_stories = udr.get("stories", {})
    stories = (raw_stories if isinstance(raw_stories, dict) else
               {s.get("id"): s for s in raw_stories if isinstance(s, dict)})
    findings = []

    for page in selected:
        number = page.get("page_number")
        width = page.get("width_pt")
        height = page.get("height_pt")
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0
                   for v in (width, height)):
            raise ValueError(f"page {number} has invalid dimensions")

        for frame in page.get("frames", []):
            rect = frame.get("rect_pt")
            kind = frame.get("kind", "")
            name = next(iter(kind), "") if isinstance(kind, dict) else str(kind)
            if (not isinstance(rect, list) or len(rect) != 4 or
                    not all(isinstance(v, (int, float)) and math.isfinite(v) for v in rect) or
                    rect[2] < rect[0] or rect[3] < rect[1]):
                findings.append({"page": number, "type": "INVALID_RECT", "severity": "ERROR",
                                 "frame_id": frame.get("id"), "rect": rect})
                continue

            left, top, right, bottom = rect
            text = frame_text(frame, stories) if name == "Text" else ""
            if name == "Text" and (left < -0.5 or top < -0.5 or
                                   right > width + 0.5 or bottom > height + 0.5):
                findings.append({"page": number, "type": "TEXT_OUTSIDE_PAGE", "severity": "WARNING",
                                 "frame_id": frame.get("id"), "rect": rect, "text": text[:60]})
            if name in ("VerticalRule", "VectorPath") and top <= 5 and bottom >= 150 and right - left <= 2.5:
                findings.append({"page": number, "type": "POSSIBLE_TOP_GHOST_RULE", "severity": "WARNING",
                                 "frame_id": frame.get("id"), "rect": rect})

            if not legacy_rules:
                continue
            if name == "Text" and right > width - 15:
                findings.append({"page": number, "type": "LEGACY_RIGHT_MARGIN", "severity": "WARNING",
                                 "frame_id": frame.get("id"), "rect": rect, "text": text[:60]})
            if (name in ("VectorPath", "FilledRegion", "HorizontalRule", "VerticalRule") and
                    300 <= top and bottom <= 378 and (right - left >= 30 or bottom - top >= 15)):
                findings.append({"page": number, "type": "LEGACY_STRAY_CAD_CANDIDATE", "severity": "WARNING",
                                 "frame_id": frame.get("id"), "rect": rect})
            if name == "Table" and isinstance(kind, dict):
                table = kind.get("Table", {})
                if len(table.get("rows", [])) * len(table.get("cols", [])) >= 6:
                    findings.append({"page": number, "type": "LEGACY_TABLE_REVIEW", "severity": "WARNING",
                                     "frame_id": frame.get("id"), "rect": rect})
            if number > 6 and name == "Text" and ("....." in text or "……" in text):
                findings.append({"page": number, "type": "LEGACY_TOC_LEADER_REVIEW", "severity": "WARNING",
                                 "frame_id": frame.get("id"), "rect": rect, "text": text[:60]})

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="PDF or .folio input")
    parser.add_argument("--page", "-p", type=int, help="1-based page number; omitted scans every page")
    parser.add_argument("--work-dir", default="work/fiumller_vision_tmp",
                        help="Parent directory for isolated extraction outputs")
    parser.add_argument("--act4-exe", help="Act4.1 CLI executable path")
    parser.add_argument("--legacy-rules", action="store_true",
                        help="Enable document-specific 15pt margin, CAD band, table and TOC heuristics")
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    args = parser.parse_args()
    if args.page is not None and args.page < 1:
        parser.error("page must be positive")

    try:
        udr, udr_path = load_udr(args.target, args.work_dir, args.act4_exe)
        findings = detect_anomalies(udr, args.page, args.legacy_rules)
    except (FileNotFoundError, OSError, RuntimeError, ValueError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        print(f"Geometry scan failed: {error}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps({"udr_path": udr_path.as_posix(), "findings": findings}, ensure_ascii=False))
    else:
        print(f"UDR: {udr_path.as_posix()}")
        if not findings:
            print("NO_HEURISTIC_FINDINGS: Visual review is still required.")
        for item in findings:
            print(f"[{item['severity']}] page={item['page']} {item['type']} rect={item['rect']}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
