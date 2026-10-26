#!/usr/bin/env python3
"""Summarize Act4.1 UDR geometry for a PDF or .folio page."""

import argparse
import json
import subprocess
import sys
from udr_source import load_udr

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="Path to PDF or .folio file")
    parser.add_argument("--page", "-p", type=int, required=True, help="1-indexed page number to inspect")
    parser.add_argument("--bbox", nargs=4, type=float, metavar=('X0', 'Y0', 'X1', 'Y1'), help="Optional bounding box filter [x0, y0, x1, y1]")
    parser.add_argument("--format", "-f", choices=['summary', 'json'], default='summary', help="Output format")
    parser.add_argument("--work-dir", default="work/fiumller_vision_tmp", help="Parent directory for isolated extraction outputs")
    parser.add_argument("--act4-exe", help="Act4.1 CLI executable path")
    args = parser.parse_args()

    if args.page < 1:
        parser.error("page must be positive")
    try:
        udr, udr_path = load_udr(args.target, args.work_dir, args.act4_exe)
    except (FileNotFoundError, OSError, RuntimeError, ValueError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        print(f"UDR extraction failed: {error}", file=sys.stderr)
        return 2

    target_page = None
    for p in udr.get('pages', []):
        if p.get('page_number') == args.page:
            target_page = p
            break

    if not target_page:
        print(f"Page {args.page} not found in document.", file=sys.stderr)
        return 2

    frames = target_page.get('frames', [])
    page_w = target_page.get('width_pt', 595.28)
    page_h = target_page.get('height_pt', 841.89)

    # Filter by bbox if specified
    filtered_frames = []
    for fr in frames:
        r = fr.get('rect_pt', [0, 0, 0, 0])
        if args.bbox:
            bx0, by0, bx1, by1 = args.bbox
            # Overlap check
            if not (r[2] < bx0 or r[0] > bx1 or r[3] < by0 or r[1] > by1):
                filtered_frames.append(fr)
        else:
            filtered_frames.append(fr)

    if args.format == 'json':
        print(json.dumps({
            "udr_path": udr_path.as_posix(),
            "page": args.page,
            "dimensions": [page_w, page_h],
            "frame_count": len(filtered_frames),
            "frames": filtered_frames
        }, ensure_ascii=False, indent=2))
        return 0

    # Summary output for LLM vision
    print(f"=== Act4.1 UDR Geometry: Page {args.page} ({page_w:.1f} x {page_h:.1f} pt) ===")
    print(f"UDR: {udr_path.as_posix()}")
    print(f"Total Frames: {len(filtered_frames)}")
    
    kinds = {}
    for fr in filtered_frames:
        k = fr.get('kind', {})
        k_name = list(k.keys())[0] if isinstance(k, dict) else str(k)
        kinds[k_name] = kinds.get(k_name, 0) + 1
    print(f"Components: {kinds}\n")

    # Story index (stories is a dict mapping story_id -> story object)
    raw_stories = udr.get('stories', {})
    if isinstance(raw_stories, dict):
        story_map = raw_stories
    else:
        story_map = {st.get('id', ''): st for st in raw_stories if isinstance(st, dict)}

    print(f"{'Type':<16} | {'Rect [l, t, r, b]':<32} | Details")
    print("-" * 80)
    for fr in filtered_frames[:100]:
        k = fr.get('kind', '')
        k_name = list(k.keys())[0] if isinstance(k, dict) else str(k)
        r = fr.get('rect_pt', [0, 0, 0, 0])
        r_str = f"[{r[0]:.1f}, {r[1]:.1f}, {r[2]:.1f}, {r[3]:.1f}]"
        
        detail = ""
        if k_name == 'Text':
            st_id = fr.get('story_ref', '')
            st_obj = story_map.get(st_id, {})
            raw_txt = "".join(p.get('text', '') + " " for p in st_obj.get('paragraphs', [])).strip()
            detail = f"Text: \"{raw_txt[:40]}\""
        elif k_name == 'Image':
            img_obj = k.get('Image', {}) if isinstance(k, dict) else {}
            detail = f"Image: {img_obj.get('asset_path')}"
        elif k_name == 'HorizontalRule':
            hr_obj = k.get('HorizontalRule', {}) if isinstance(k, dict) else {}
            detail = f"H-Rule: width={hr_obj.get('stroke_width')}, color=#{hr_obj.get('color', 0):06x}"
        elif k_name == 'VerticalRule':
            vr_obj = k.get('VerticalRule', {}) if isinstance(k, dict) else {}
            detail = f"V-Rule: width={vr_obj.get('stroke_width')}, color=#{vr_obj.get('color', 0):06x}"
        elif k_name == 'VectorPath':
            detail = f"Path: w={r[2]-r[0]:.1f}, h={r[3]-r[1]:.1f}"
        elif k_name == 'FilledRegion':
            fr_obj = k.get('FilledRegion', {}) if isinstance(k, dict) else {}
            detail = f"Fill: color=#{fr_obj.get('color', 0):06x}"
        elif k_name == 'Table':
            t_obj = k.get('Table', {}) if isinstance(k, dict) else {}
            detail = f"Table: {len(t_obj.get('rows', []))} rows x {len(t_obj.get('cols', []))} cols"

        print(f"{k_name:<16} | {r_str:<32} | {detail}")

    if len(filtered_frames) > 100:
        print(f"... and {len(filtered_frames) - 100} more frames.")
    return 0

if __name__ == '__main__':
    sys.exit(main())
