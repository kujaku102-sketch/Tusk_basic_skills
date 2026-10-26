"""
GUI 試験を Antigravity CLI (agy) へ受け渡すモード + agy が呼ぶ単発の窓操作CLI。
- --handoff spec.json : agy(Gemini Flash)に操作・連射撮影させ、固定schemaのJSONを受け取る
- --shot/--burst/--place/--launch/--click/--key/--type/--close-window : agy が run.py 経由で使う道具
"""

import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

DEFAULT_AGY = "C:/Users/Masaya.Futamata/AppData/Local/agy/bin/agy"
DEFAULT_MODEL = "gemini-3.8-flash-medium"
RESULTS = ("pass", "fail", "unknown")

RESULT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["items", "steps", "captures", "anomalies"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "result", "evidence", "note"],
                "properties": {
                    "id": {"type": "string"},
                    "result": {"type": "string", "enum": list(RESULTS)},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                },
            },
        },
        "steps": {"type": "array", "items": {"type": "string"}},
        "captures": {"type": "array", "items": {"type": "string"}},
        "anomalies": {"type": "array", "items": {"type": "string"}},
    },
}


def check_model(model: str) -> str:
    """Flash 以外は拒否。"""
    if "flash" not in model.lower() or not model.lower().startswith("gemini"):
        raise ValueError(f"Flash 以外のモデルは使えない: {model}")
    return model


def _rel(p: Path) -> str:
    return str(p.resolve()).replace("\\", "/")


def build_prompt(spec: Dict[str, Any], run_py: Path, out_dir: Path) -> str:
    target = spec["target"]
    # 窓は PID でだけ捕まえる。タイトルだけだと、同じ名前の他人の窓 (別のセッションが起動したアプリ) を掴んで閉じうる
    if not target.get("pid"):
        raise ValueError("target には pid か exe が要る (タイトルだけでは捕まえない)")
    sel = f"--pid {target['pid']}"
    exe_started = bool(target.get("exe"))
    place = spec.get("place")
    lines: List[str] = []
    lines.append("あなたはGUI試験の操作役。下の道具(python run.py)だけでアプリを操作し、キャプチャを撮り、項目ごとに判定して、指定schemaのJSONだけを返す。")
    if exe_started:
        lines.append("アプリは起動済み・窓配置済み(自分で起動しない。--launch は使わない)。")
    lines.append(f"道具: python \"{_rel(run_py)}\"")
    lines.append(f"  窓の捕捉: {sel}  (以下の各コマンドに付ける)")
    lines.append("  配置: --place X,Y,W,H   単発撮影: --shot out.png   連射(15FPS): --burst DIR [--duration 1.5]")
    lines.append("  操作: --click X,Y (窓内相対px) / --key 名前 / --type 文字列 / --close-window")
    lines.append("決まり:")
    lines.append("- 窓の位置とサイズは先に決めてある。ずれていたら --place で直す(起動直後 1280x960 は下端がタスクバーに隠れる)。")
    lines.append("- 閉じるのは --close-window だけ。Alt+F4 は使わない(安全装置で止まる)。試験の最後に必ず閉じる。")
    lines.append("- 窓は必ず上の --pid で捕まえる。--title は使わない(他の窓を掴まないため)。")
    lines.append(f"- キャプチャは PNG で {_rel(out_dir)} の下にだけ保存する。他の場所に書かない。")
    lines.append("- run.py のソースを読まない・書き換えない・別スクリプトを作らない。上の道具のコマンドだけ使う。各コマンドは数秒で終わる。")
    lines.append("- 操作の前後に --shot を撮り、画像ファイルを開いて確かめてから次へ進む。")
    lines.append("- 判定は撮った画像を自分で見て行う。見えなければ unknown。推測で pass にしない。evidence には実在するPNGのパスだけ書く。")
    if place:
        lines.append(f"窓の位置とサイズ(X,Y,幅,高さ): {place}")
    if spec.get("document"):
        lines.append(f"開く文書: {spec['document']}")
    lines.append("確かめる項目:")
    for it in spec["items"]:
        lines.append(f"- [{it['id']}] {it['check']}")
    lines.append(f"時間上限: {spec.get('timeout', 300)} 秒")
    lines.append("返すJSON: items[{id,result(pass|fail|unknown),evidence[PNGパス],note}], steps[実行した手順], captures[保存した全PNGパス], anomalies[異常・想定外]。")
    return "\n".join(lines)


def extract_json(text: str) -> Dict[str, Any]:
    """agy の出力(包みJSON/コードフェンス付き等)から結果オブジェクトを取り出す。"""
    def want(o: Any) -> bool:
        return isinstance(o, dict) and isinstance(o.get("items"), list) and isinstance(o.get("steps"), list)

    def walk(o: Any) -> Optional[Dict[str, Any]]:
        if want(o):
            return o
        if isinstance(o, dict):
            for v in o.values():
                r = walk(v)
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = walk(v)
                if r:
                    return r
        elif isinstance(o, str):
            return from_str(o)
        return None

    def from_str(s: str) -> Optional[Dict[str, Any]]:
        s = s.strip()
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
        try:
            return walk(json.loads(s))
        except (ValueError, TypeError):
            pass
        i, j = s.find("{"), s.rfind("}")
        if 0 <= i < j:
            try:
                return walk(json.loads(s[i:j + 1]))
            except (ValueError, TypeError):
                return None
        return None

    r = from_str(text)
    if not r:
        raise ValueError("agy の出力から結果JSONを取れなかった")
    return r


def validate_result(res: Dict[str, Any], spec: Dict[str, Any], out_dir: Path) -> Dict[str, Any]:
    """schema適合を直し、存在しない根拠パスを異常として記録(捏造の機械的検出)。"""
    anomalies = list(res.get("anomalies") or [])
    ids = {it["id"] for it in spec["items"]}
    seen = set()
    for it in [x for x in res.get("items", []) if isinstance(x, dict)]:
        seen.add(it.get("id"))
        if it.get("result") not in RESULTS:
            anomalies.append(f"項目 {it.get('id')}: 不正な結果値 {it.get('result')!r} -> unknown")
            it["result"] = "unknown"
        it.setdefault("evidence", [])
        it.setdefault("note", "")
        missing = [e for e in it["evidence"] if not Path(e).is_file()]
        if missing:
            anomalies.append(f"項目 {it['id']}: 根拠PNGが実在しない {missing}")
            it["evidence"] = [e for e in it["evidence"] if e not in missing]
        if it["result"] == "pass" and not it["evidence"]:
            anomalies.append(f"項目 {it['id']}: 根拠無しの pass -> unknown")
            it["result"] = "unknown"
    for miss in sorted(ids - seen):
        res.setdefault("items", []).append({"id": miss, "result": "unknown", "evidence": [], "note": "agy が返さなかった"})
    res.setdefault("steps", [])
    res["captures"] = [c for c in (res.get("captures") or []) if Path(c).is_file()]
    res["anomalies"] = anomalies
    return res


def run_handoff(spec_path: str, runner=subprocess.run) -> Dict[str, Any]:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    model = check_model(spec.get("model") or os.environ.get("GAMERSEYE_AGY_MODEL", DEFAULT_MODEL))
    if not spec.get("items") or not spec.get("target"):
        raise ValueError("spec には target と items が要る")
    out_dir = Path(spec.get("out_dir") or (Path(spec_path).resolve().parent / "handoff_out"))
    out_dir.mkdir(parents=True, exist_ok=True)
    run_py = Path(__file__).resolve().parent.parent / "run.py"
    timeout = int(spec.get("timeout", 300))
    target = spec["target"]
    if not target.get("pid") and not target.get("exe"):
        raise ValueError("target には pid か exe が要る (タイトルだけでは捕まえない)")
    started = None
    if target.get("exe") and not target.get("pid"):
        started = launch_and_place(target["exe"], spec.get("document"), spec.get("place", "40,40,1200,800"))
        target = dict(target, pid=started["pid"])
        spec = dict(spec, target=target)
    cmd = [
        spec.get("agy") or os.environ.get("AGY_BIN", DEFAULT_AGY),
        "-p", build_prompt(spec, run_py, out_dir),
        "--model", model,
        "--output-format", "json",
        "--json-schema", json.dumps(RESULT_SCHEMA),
        "--sandbox",
        "--add-dir", str(out_dir),
        "--add-dir", str(run_py.parent),
        "--print-timeout", f"{timeout}s",
    ]
    try:
        proc = runner(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout + 60)
    finally:
        left_open = bool(started) and close_if_alive(started["pid"])
    (out_dir / "agy_raw.txt").write_text((proc.stdout or "") + "\n--stderr--\n" + (proc.stderr or ""), encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"agy が失敗 rc={proc.returncode}: {(proc.stderr or '')[:500]}")
    res = validate_result(extract_json(proc.stdout), spec, out_dir)
    res["model"] = model
    if left_open:
        res["anomalies"].append("agy が閉じ忘れたので窓を閉じた")
    (out_dir / "handoff_result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return res


# ---- 単発の道具 ----------------------------------------------------------

def _bind(args):
    from .binder import WindowBinder
    b = WindowBinder(pid=args.pid, title_regex=args.title)
    b.find_and_bind()
    return b


def _place(b, x: int, y: int, w: int, h: int) -> None:
    import win32con
    import win32gui
    if win32gui.IsIconic(b.hwnd) or ctypes.windll.user32.IsZoomed(b.hwnd):
        win32gui.ShowWindow(b.hwnd, win32con.SW_RESTORE)
    win32gui.MoveWindow(b.hwnd, x, y, w, h, True)
    time.sleep(0.3)


def launch_and_place(exe: str, doc: Optional[str], place: Optional[str]) -> Dict[str, Any]:
    """アプリを agy の sandbox の外で起動し窓を配置する(sandbox 内で起動すると命令の終了で落ちる)。"""
    from .binder import WindowBinder
    proc = subprocess.Popen([exe] + ([doc] if doc else []), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, close_fds=True, creationflags=0x00000008 | 0x00000200)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            b = WindowBinder(pid=proc.pid)
            b.find_and_bind()
            if place:
                _place(b, *[int(v) for v in place.split(",")])
            return {"pid": proc.pid, "title": b.bound_title}
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("窓が20秒で出なかった")


def close_if_alive(pid: int) -> bool:
    """残っていたら閉じるボタン相当(WM_CLOSE)で閉じる。閉じたら True。"""
    try:
        from .binder import WindowBinder
        import win32con
        import win32gui
        b = WindowBinder(pid=pid)
        b.find_and_bind()
        win32gui.PostMessage(b.hwnd, win32con.WM_CLOSE, 0, 0)
        return True
    except Exception:
        return False


def tool_cli(argv: List[str]) -> int:
    p = argparse.ArgumentParser(description="GamersEYE 単発道具")
    p.add_argument("--title")
    p.add_argument("--pid", type=int)
    p.add_argument("--launch")
    p.add_argument("--open", dest="doc")
    p.add_argument("--place")
    p.add_argument("--shot")
    p.add_argument("--burst")
    p.add_argument("--duration", type=float, default=1.5)
    p.add_argument("--fps", type=int, default=15)
    p.add_argument("--click")
    p.add_argument("--key")
    p.add_argument("--type", dest="text")
    p.add_argument("--close-window", action="store_true")
    a = p.parse_args(argv)

    if a.launch:
        cmd = [a.launch] + ([a.doc] if a.doc else [])
        # 親のpipeを子に継がせない(継ぐとagy側のコマンドが終わらない)
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                close_fds=True, creationflags=0x00000008 | 0x00000200)
        from .binder import WindowBinder
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                b = WindowBinder(pid=proc.pid)
                b.find_and_bind()
                break
            except Exception:
                time.sleep(0.5)
        else:
            print(json.dumps({"error": "窓が20秒で出なかった", "pid": proc.pid}))
            return 1
        a.pid = proc.pid
        if a.place:
            _place(b, *[int(v) for v in a.place.split(",")])
        print(json.dumps({"pid": proc.pid, "title": b.bound_title}, ensure_ascii=False))
        return 0

    if a.pid is None and not a.title:
        print("--pid か --title が要る", file=sys.stderr)
        return 2
    if a.close_window and a.pid is None:
        # 閉じるのは PID を名指ししたときだけ (タイトルで他人の窓を閉じない)
        print("--close-window には --pid が要る", file=sys.stderr)
        return 2
    b = _bind(a)
    if a.place:
        _place(b, *[int(v) for v in a.place.split(",")])
    if a.click or a.key or a.text:
        from .actuator import WindowActuator
        act = WindowActuator(b)
        if a.click:
            x, y = [float(v) for v in a.click.split(",")]
            act.click(x, y)
        if a.key:
            act.press_key(a.key)
        if a.text:
            act.type_text(a.text)
    if a.close_window:
        import win32con
        import win32gui
        win32gui.PostMessage(b.hwnd, win32con.WM_CLOSE, 0, 0)  # 閉じるボタン相当
        print(json.dumps({"closed": b.bound_pid}))
        return 0
    if a.shot or a.burst:
        from .vision import WindowVision
        v = WindowVision(b)
        try:
            if a.shot:
                Path(a.shot).parent.mkdir(parents=True, exist_ok=True)
                v.capture(ensure_foreground=True).save(a.shot, "PNG")
                print(json.dumps({"shot": _rel(Path(a.shot))}))
            if a.burst:
                d = Path(a.burst)
                d.mkdir(parents=True, exist_ok=True)
                frames = v.capture_burst(duration=a.duration, fps=a.fps, mode="full")
                paths = []
                for i, f in enumerate(frames):
                    fp = d / f"frame_{i:03d}.png"
                    f.save(fp, "PNG")
                    paths.append(_rel(fp))
                print(json.dumps({"burst": paths}))
        finally:
            v.close()
    return 0


HANDOFF_FLAGS = ("--handoff", "--shot", "--burst", "--place", "--launch", "--click", "--key", "--type", "--close-window")


def is_handoff_invocation(argv: List[str]) -> bool:
    return any(x in HANDOFF_FLAGS for x in argv)


def main(argv: List[str]) -> int:
    if "--handoff" in argv:
        i = argv.index("--handoff")
        res = run_handoff(argv[i + 1])
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    return tool_cli(argv)
