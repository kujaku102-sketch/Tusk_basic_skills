from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import re
from pathlib import Path
from typing import Any, Dict, List


TOOL_PATH_HINTS = ("tool", "script", "util", "helper", "cli", "check", "scan", "guard", "runner")


def _parse_landmines_file(path: Path, source_label: str) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    items: List[Dict[str, Any]] = []
    current_key: str | None = None
    fields: Dict[str, str] = {}

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            if current_key and fields:
                items.append({"error_key": current_key, "source": source_label, **fields})
            current_key = line[3:].strip()
            fields = {}
        elif current_key and line.startswith("- ") and ": " in line:
            k, v = line[2:].split(": ", 1)
            fields[k.strip()] = v.strip()

    if current_key and fields:
        items.append({"error_key": current_key, "source": source_label, **fields})

    results: List[Dict[str, Any]] = []
    for item in items:
        count_str = item.get("発生回数", "1")
        count = int(count_str) if count_str.isdigit() else 1
        results.append({
            "error_key": item["error_key"],
            "source": item["source"],
            "count": count,
            "landmine": item.get("地雷", ""),
            "cause": item.get("原因", ""),
            "correct_pattern": item.get("正解パターン", ""),
            "target": item.get("対象", ""),
            "confirmed_on": item.get("最終確認", ""),
        })
    return results


def harvest_focus_caches(ws: Path) -> List[Dict[str, Any]]:
    all_records: List[Dict[str, Any]] = []
    ws_landmine = ws / "work" / "focus_cache" / "LANDMINES.md"
    all_records.extend(_parse_landmines_file(ws_landmine, "work/focus_cache/LANDMINES.md"))

    ext_root = ws / "extensions"
    if ext_root.is_dir():
        for ext_dir in sorted(ext_root.iterdir()):
            if ext_dir.is_dir():
                lm = ext_dir / "focus_cache" / "LANDMINES.md"
                rel = lm.relative_to(ws).as_posix()
                all_records.extend(_parse_landmines_file(lm, rel))

    return sorted(all_records, key=lambda x: (-x["count"], x["error_key"]))


def harvest_context_caches(ws: Path) -> Dict[str, Any]:
    ctx_root = ws / "work" / "context_cache"
    active_files: List[Dict[str, Any]] = []
    tool_candidates: List[Dict[str, Any]] = []
    basename_locations: Dict[str, List[str]] = defaultdict(list)

    if ctx_root.is_dir():
        for scope_dir in sorted(ctx_root.iterdir()):
            if not scope_dir.is_dir() or scope_dir.name == "common":
                continue
            manifest_path = scope_dir / "context_manifest.json"
            if not manifest_path.is_file():
                continue
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            scope_key = data.get("scope_key", scope_dir.name)
            for f in data.get("files", []):
                path_str = f.get("path", "")
                change = f.get("change", "unchanged")
                kind = f.get("kind", "")
                name_lower = Path(path_str).name.lower()

                if kind == "code":
                    basename_locations[Path(path_str).name].append(path_str)

                if change in {"added", "modified"}:
                    active_files.append({"scope": scope_key, "path": path_str, "change": change, "kind": kind})

                if kind == "code" and any(h in path_str.lower() or h in name_lower for h in TOOL_PATH_HINTS):
                    tool_candidates.append({
                        "scope": scope_key,
                        "path": path_str,
                        "change": change,
                        "bytes": f.get("bytes", 0),
                    })

    duplicated_scripts = {
        name: sorted(set(paths))
        for name, paths in basename_locations.items()
        if len(set(paths)) > 1 and name != "__init__.py"
    }

    return {
        "active_changed_files": active_files,
        "reusable_tool_candidates": tool_candidates,
        "duplicated_script_basenames": duplicated_scripts,
    }


def harvest_session_logs(ws: Path) -> Dict[str, Any]:
    log_path = ws / "work" / "recovery" / "sessions.jsonl"
    scope_touch_counts: Counter[str] = Counter()
    stop_reasons: List[Dict[str, Any]] = []
    rework_hotspots: Counter[str] = Counter()

    if log_path.is_file():
        for raw in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not raw.strip():
                continue
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            event_type = ev.get("event")
            for sp in ev.get("scope", []):
                if event_type == "start":
                    scope_touch_counts[sp] += 1
                elif event_type in {"revise", "rollback"}:
                    rework_hotspots[sp] += 1
            if event_type == "stop":
                stop_reasons.append({
                    "work_id": ev.get("work_id"),
                    "error_key": ev.get("error_key"),
                    "reason": ev.get("reason"),
                    "scope": ev.get("scope", []),
                })

    return {
        "frequently_scoped_files": [
            {"path": p, "session_count": c} for p, c in scope_touch_counts.most_common(15)
        ],
        "rework_hotspots": [
            {"path": p, "rework_or_rollback_count": c} for p, c in rework_hotspots.most_common(15)
        ],
        "recent_stops": stop_reasons[-15:],
    }


def classify_candidates(
    focus_items: List[Dict[str, Any]],
    ctx_info: Dict[str, Any],
    sess_info: Dict[str, Any],
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    idx = 1

    # 1. Focus Cache recurring landmines -> Route A or Route B
    target_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in focus_items:
        target_groups[item["target"]].append(item)
        if item["count"] >= 2:
            route = "Route B (New Basic Skill)" if item["count"] >= 3 else "Route A (Update Existing Skill)"
            candidates.append({
                "candidate_id": f"CAND-{idx:02d}",
                "source": "focus_cache",
                "target": item["target"],
                "error_key": item["error_key"],
                "score": item["count"],
                "recommended_route": route,
                "reason": f"地雷 '{item['error_key']}' が {item['count']} 回発生。正解パターン '{item['correct_pattern']}' の自動化・Skill手順化を推奨。",
            })
            idx += 1

    # If a single domain target has >= 3 distinct landmines, suggest Route C (Domain Extension)
    for target, group in target_groups.items():
        if len(group) >= 3:
            candidates.append({
                "candidate_id": f"CAND-{idx:02d}",
                "source": "focus_cache_cluster",
                "target": target,
                "error_key": ",".join(g["error_key"] for g in group),
                "score": sum(g["count"] for g in group),
                "recommended_route": "Route C (New Extension: Tusk_<Domain>)",
                "reason": f"対象 '{target}' に {len(group)} 種類の固有地雷が集中。専用Focus CacheとSkillを持つ独立Extension化を推奨。",
            })
            idx += 1

    # 2. Duplicated scripts across scopes -> Route B
    for basename, paths in ctx_info.get("duplicated_script_basenames", {}).items():
        candidates.append({
            "candidate_id": f"CAND-{idx:02d}",
            "source": "context_cache_duplication",
            "target": basename,
            "paths": paths,
            "score": len(paths),
            "recommended_route": "Route B (New Basic Skill)",
            "reason": f"同名スクリプト '{basename}' が複数箇所 ({len(paths)}箇所) に重複配置。Tusk_Basic_skills への共通化を推奨。",
        })
        idx += 1

    # 3. Session rework hotspots -> Route A or B
    for item in sess_info.get("rework_hotspots", []):
        if item["rework_or_rollback_count"] >= 2:
            candidates.append({
                "candidate_id": f"CAND-{idx:02d}",
                "source": "session_recovery_rework",
                "target": item["path"],
                "score": item["rework_or_rollback_count"],
                "recommended_route": "Route A (Update Existing Skill)",
                "reason": f"ファイル '{item['path']}' で手戻り/ロールバックが {item['rework_or_rollback_count']} 回発生。事前検証スクリプトまたはSkill手順の強化を推奨。",
            })
            idx += 1

    return candidates


def harvest_workspace(workspace: Path) -> Dict[str, Any]:
    ws = workspace.resolve()
    if not ws.is_dir():
        raise FileNotFoundError(f"Workspace not found: {ws}")

    focus_items = harvest_focus_caches(ws)
    ctx_info = harvest_context_caches(ws)
    sess_info = harvest_session_logs(ws)
    skim_candidates = classify_candidates(focus_items, ctx_info, sess_info)

    return {
        "status": "ok",
        "workspace": str(ws),
        "summary": {
            "focus_cache_entries": len(focus_items),
            "context_changed_files": len(ctx_info["active_changed_files"]),
            "context_tool_candidates": len(ctx_info["reusable_tool_candidates"]),
            "session_rework_hotspots": len(sess_info["rework_hotspots"]),
            "skim_recommended_candidates": len(skim_candidates),
        },
        "skim_candidates": skim_candidates,
        "raw_signals": {
            "focus_cache": focus_items,
            "context_cache": ctx_info,
            "session_recovery": sess_info,
        },
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="InQ Stage-1 Skim Harvester")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Target workspace root")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args(argv)

    result = harvest_workspace(args.workspace)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
