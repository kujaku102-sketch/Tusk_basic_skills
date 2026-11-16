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
# 所有者 2026-11-16「全て Flash-lite でいこう」。Flash-Lite は agy の一覧に無いので、直の道 (google-genai) で回す。
DEFAULT_MODEL = "gemini-3.5-flash-lite"
# agy が受けるモデル (agy models の一覧、2026-11-16)。これ以外は直の道で回す。
AGY_MODELS = {"gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low", "gemini-4-hydrogen-low"}
DIRECT_MAX_STEPS = 12  # 直の道で 1 項目あたりに使う手数の上限
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


ALLOWED_NON_FLASH = {"gemini-4-hydrogen-low"}  # 所有者 2026-11-16「モデルは 4-hydrogen-low の方がいい」


def check_model(model: str) -> str:
    """Flash と、所有者が許した 1 つ (gemini-4-hydrogen-low) 以外は拒否。"""
    if model.lower() in ALLOWED_NON_FLASH:
        return model
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
    lines.append("  操作: --click X,Y (窓内相対px) / --double-click X,Y / --key 名前 / --type 文字列 / --close-window")
    lines.append("決まり:")
    lines.append("- 窓の位置とサイズは先に決めてある。ずれていたら --place で直す(起動直後 1280x960 は下端がタスクバーに隠れる)。")
    lines.append("- 閉じるのは --close-window だけ。Alt+F4 は使わない(安全装置で止まる)。試験の最後に必ず閉じる。")
    lines.append("- 窓は必ず上の --pid で捕まえる。--title は使わない(他の窓を掴まないため)。")
    lines.append(f"- キャプチャは PNG で {_rel(out_dir)} の下にだけ保存する。他の場所に書かない。")
    lines.append("- run.py のソースを読まない・書き換えない・別スクリプトを作らない。上の道具のコマンドだけ使う。各コマンドは数秒で終わる。")
    lines.append("- トークンを節約する: 撮影は項目ごとに必要最小限(1項目あたり原則1〜2枚)。確認済みの画面を撮り直さない。連射は動きの確認が要る項目だけ。")
    lines.append("- 時間上限が近づいたら操作を止め、今わかっている結果で返す(未確認は unknown)。")
    lines.append("- 操作の前後に --shot を撮り、画像ファイルを開いて確かめてから次へ進む。")
    lines.append("- 判定は撮った画像を自分で見て行う。見えなければ unknown。推測で pass にしない。evidence には実在するPNGのパスだけ書く。")
    if place:
        lines.append(f"窓の位置とサイズ(X,Y,幅,高さ): {place}")
    if spec.get("document"):
        lines.append(f"開く文書: {spec['document']}")
    lines.append("確かめる項目:")
    for it in spec["items"]:
        lines.append(f"- [{it['id']}] {it['check']}")
    lines.append(f"時間上限: {spec.get('timeout') or default_timeout(len(spec['items']))} 秒")
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


DEFAULT_CHUNK = 6  # 1回の受け渡しで見る項目数の上限(超えたら分割)


def default_timeout(n_items: int) -> int:
    """項目数に応じた時間上限(秒)。1項目あたり約150秒、最低300秒。"""
    return max(300, 150 * n_items)


def _pid_alive(pid: int) -> bool:
    h = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not h:
        return False
    try:
        return ctypes.windll.kernel32.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT = まだ生きてる
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def _kill_pid(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)


def finish_started(pid: int, wait: float = 5.0, alive=_pid_alive, kill=_kill_pid, close=None) -> List[str]:
    """handoff 自身が起動した PID だけを片付ける。WM_CLOSE 後 wait 秒待ち、残れば強制終了。"""
    notes: List[str] = []
    if not alive(pid):
        return notes
    if (close or close_if_alive)(pid):
        notes.append("agy が閉じ忘れたので窓を閉じた")
    end = time.time() + wait
    while time.time() < end and alive(pid):
        time.sleep(0.3)
    if alive(pid):
        kill(pid)
        notes.append(f"WM_CLOSE 後も PID {pid} が残ったので強制終了した(エラー小窓/未保存確認の疑い)")
    return notes


def _agy_cmd(spec, model, prompt, out_dir, run_py, timeout, conversation=None):
    cmd = [spec.get("agy") or os.environ.get("AGY_BIN", DEFAULT_AGY)]
    if conversation:
        cmd += ["--conversation", conversation]
    cmd += ["-p", prompt, "--model", model, "--output-format", "json", "--json-schema", json.dumps(RESULT_SCHEMA),
            "--sandbox", "--add-dir", str(out_dir), "--add-dir", str(run_py.parent), "--print-timeout", f"{timeout}s"]
    return cmd


def _conversation_id(stdout: str) -> Optional[str]:
    try:
        return json.loads(stdout).get("conversation_id")
    except (ValueError, AttributeError):
        m = re.search(r'"conversation_id"\s*:\s*"([^"]+)"', stdout or "")
        return m.group(1) if m else None


RECOVER_PROMPT = "時間切れだ。これ以上操作せず、今わかっている結果をschemaどおりJSONだけで返せ。確かめていない項目は unknown。"
RECOVER_TIMEOUT = 90


def uses_agy(model: str) -> bool:
    """agy が受けるモデルなら agy で、それ以外 (Flash-Lite など) は直の道で回す。"""
    return model.lower() in AGY_MODELS


DIRECT_GOAL = (
    "GUI 試験の 1 項目を確かめる。確かめること: {check}\n"
    "決まり: 窓を閉じない。Alt+F4 を押さない。確かめに要らない操作をしない。"
    "ダイアログの設定 (チェック・選択) を変えない。読み込み中の画面は wait で待つ。"
    "編集モードに入るには、対象の枠をダブルクリックする。"
    "動き (ドラッグ・ホバー・アニメーション) を確かめる項目は、操作の直後に burst_capture (15FPS の連射) を使う。\n"
    "今の画面で確かめられるなら、操作せずにすぐ finish_task を呼ぶ。手数の残りが 2 になったら、必ず finish_task を呼ぶ。"
    "finish_task の summary の先頭は 'PASS:'・'FAIL:'・'UNKNOWN:' のどれかにして、"
    "見えた事実を 1 行で書く。画面で確かめられなければ UNKNOWN。推測で PASS にしない。"
)


def parse_direct_summary(summary: str) -> str:
    """finish_task の summary の先頭から結果を取る。読めなければ unknown。"""
    head = (summary or "").strip().upper()
    for r in RESULTS:
        if head.startswith(r.upper() + ":"):
            return r
    return "unknown"


def _make_direct_runner(pid: int, model: str):
    from .runner import ComputerUseRunner
    return ComputerUseRunner(pid=pid, model_name=model)


def _run_once_direct(spec: Dict[str, Any], model: str, out_dir: Path, make_runner=_make_direct_runner,
                     launch=None, finish=None) -> Dict[str, Any]:
    """agy を使わずに、直の道 (runner.py の google-genai のループ) で項目ごとに確かめる。結果の形は agy と同じ。"""
    target = spec["target"]
    started = None
    notes: List[str] = []
    res: Dict[str, Any] = {"items": [], "steps": [], "captures": [], "anomalies": []}
    if target.get("exe") and not target.get("pid"):
        started = (launch or launch_and_place)(target["exe"], spec.get("document"), spec.get("place", "40,40,1200,800"),
                                               spec.get("args"), float(spec.get("load_wait", DEFAULT_LOAD_WAIT)),
                                               spec.get("open_keys"))
        target = dict(target, pid=started["pid"])
        if started.get("loaded") is False:
            notes.append(f"文書の読み込みが {spec.get('load_wait', DEFAULT_LOAD_WAIT)} 秒で済まなかった (窓の題に文書の名前が出ない)")
    max_steps = int(spec.get("max_steps") or DIRECT_MAX_STEPS)
    try:
        for it in spec["items"]:
            item = {"id": it["id"], "result": "unknown", "evidence": [], "note": ""}
            try:
                r = make_runner(target["pid"], model)
                before = out_dir / f"{it['id']}_00_before.png"
                r.vision.capture(ensure_foreground=True).save(before, "PNG")
                res["captures"].append(_rel(before))
                r.run_autonomous_loop(goal=DIRECT_GOAL.format(check=it["check"]), max_steps=max_steps)
                after = out_dir / f"{it['id']}_99_after.png"
                (r.vision.last_capture or r.vision.capture()).save(after, "PNG")
                res["captures"].append(_rel(after))
                for h in r.history:
                    res["steps"].append(f"{it['id']} step {h.step}: {h.action} {json.dumps(h.args, ensure_ascii=False)}"
                                        + (f" ERR {h.error}" if h.error else ""))
                burst_paths = []
                for k, frames in enumerate(getattr(r, "burst_history", []) or []):
                    from .runner import sample_frames
                    for i, f in enumerate(sample_frames(frames)):
                        p = out_dir / f"{it['id']}_burst{k}_{i:02d}.png"
                        f.save(p, "PNG")
                        burst_paths.append(_rel(p))
                res["captures"] += burst_paths
                item["result"] = parse_direct_summary(r.final_summary)
                item["note"] = (r.final_summary or "").strip()[:500]
                item["evidence"] = [_rel(before), _rel(after)] + burst_paths
            except Exception as e:  # 1 項目の失敗は unknown にして次へ
                item["note"] = f"直の道で失敗: {type(e).__name__}: {e}"[:500]
            res["items"].append(item)
    finally:
        if started:
            notes += (finish or finish_started)(started["pid"])
    res = validate_result(res, spec, out_dir)
    res["anomalies"] += notes
    res["timed_out"] = False
    res["recovered"] = False
    return res


def _run_once(spec: Dict[str, Any], model: str, out_dir: Path, runner) -> Dict[str, Any]:
    run_py = Path(__file__).resolve().parent.parent / "run.py"
    timeout = int(spec.get("timeout") or default_timeout(len(spec["items"])))
    target = spec["target"]
    started = None
    if target.get("exe") and not target.get("pid"):
        started = launch_and_place(target["exe"], spec.get("document"), spec.get("place", "40,40,1200,800"),
                                   spec.get("args"), float(spec.get("load_wait", DEFAULT_LOAD_WAIT)),
                                   spec.get("open_keys"))
        spec = dict(spec, target=dict(target, pid=started["pid"]))
    cmd = _agy_cmd(spec, model, build_prompt(dict(spec, timeout=timeout), run_py, out_dir), out_dir, run_py, timeout)
    notes: List[str] = []
    timed_out = False
    recovered = False
    res: Optional[Dict[str, Any]] = None
    raw = ""
    try:
        try:
            proc = runner(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout + 120)
        except subprocess.TimeoutExpired as e:
            # agy が --print-timeout を過ぎても戻らなかった。subprocess が agy を止めた後の部分出力で続行する
            def _s(v):
                return v.decode("utf-8", "replace") if isinstance(v, bytes) else (v or "")
            proc = subprocess.CompletedProcess(cmd, 0, stdout=_s(e.stdout), stderr=_s(e.stderr) + "\nprint timeout (subprocess TimeoutExpired)")
            notes.append(f"agy が {timeout + 120}s 戻らず subprocess 側で打ち切った")
        raw = (proc.stdout or "") + "\n--stderr--\n" + (proc.stderr or "")
        if proc.returncode != 0:
            raise RuntimeError(f"agy が失敗 rc={proc.returncode}: {(proc.stderr or '')[:500]}")
        timed_out = "print timeout" in (proc.stderr or "")
        try:
            res = extract_json(proc.stdout)
        except ValueError:
            if not timed_out:
                raise
        if timed_out:
            notes.append(f"agy が時間上限 {timeout}s で打ち切られた(print timeout)")
            if res is None:
                cid = _conversation_id(proc.stdout)
                if cid:
                    try:
                        p2 = runner(_agy_cmd(spec, model, RECOVER_PROMPT, out_dir, run_py, RECOVER_TIMEOUT, conversation=cid),
                                    capture_output=True, text=True, encoding="utf-8", timeout=RECOVER_TIMEOUT + 30)
                        raw += "\n--recover--\n" + (p2.stdout or "") + "\n--stderr--\n" + (p2.stderr or "")
                        res = extract_json(p2.stdout)
                        recovered = True
                        notes.append("時間切れ後に conversation で結果を回収した")
                    except Exception as e:  # 回収失敗: 全項目 unknown
                        notes.append(f"結果の回収に失敗: {e}")
                else:
                    notes.append("conversation_id が無く回収できなかった")
    finally:
        (out_dir / "agy_raw.txt").write_text(raw, encoding="utf-8")
        if started:
            notes += finish_started(started["pid"])
    if res is None:
        res = {"items": [], "steps": [], "captures": [], "anomalies": []}
    res = validate_result(res, spec, out_dir)
    res["anomalies"] += notes
    res["timed_out"] = timed_out
    res["recovered"] = recovered
    return res


def run_handoff(spec_path: str, runner=subprocess.run) -> Dict[str, Any]:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    model = check_model(spec.get("model") or os.environ.get("GAMERSEYE_AGY_MODEL", DEFAULT_MODEL))
    if not spec.get("items") or not spec.get("target"):
        raise ValueError("spec には target と items が要る")
    target = spec["target"]
    if not target.get("pid") and not target.get("exe"):
        raise ValueError("target には pid か exe が要る (タイトルだけでは捕まえない)")
    out_dir = Path(spec.get("out_dir") or (Path(spec_path).resolve().parent / "handoff_out"))
    out_dir.mkdir(parents=True, exist_ok=True)
    chunk = int(spec.get("chunk", DEFAULT_CHUNK))
    items = spec["items"]
    parts = [items[i:i + chunk] for i in range(0, len(items), chunk)]
    if len(parts) > 1 and not target.get("exe"):
        raise ValueError("項目が多く分割が要るが、pid 指定では毎回起動し直せない。chunk を増やすか exe 指定にする")
    merged: Dict[str, Any] = {"items": [], "steps": [], "captures": [], "anomalies": [], "timed_out": False, "recovered": False}
    if len(parts) > 1:
        merged["anomalies"].append(f"項目{len(items)}件を{len(parts)}回に分けて実行した(chunk={chunk})")
    for n, part in enumerate(parts, 1):
        d = out_dir if len(parts) == 1 else out_dir / f"part{n}"
        d.mkdir(parents=True, exist_ok=True)
        if uses_agy(model):
            r = _run_once(dict(spec, items=part), model, d, runner)
        else:
            r = _run_once_direct(dict(spec, items=part), model, d)
        for k in ("items", "steps", "captures", "anomalies"):
            merged[k] += r[k]
        merged["timed_out"] |= r["timed_out"]
        merged["recovered"] |= r["recovered"]
    merged["model"] = model
    merged["route"] = "agy" if uses_agy(model) else "direct"
    (out_dir / "handoff_result.json").write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


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


DEFAULT_LOAD_WAIT = 60.0  # 文書の読み込みを待つ上限 (秒)


def wait_document_loaded(title_of, doc: Optional[str], load_wait: float, poll: float = 0.5,
                         clock=time.time, sleep=time.sleep) -> bool:
    """窓の題に文書の名前が出るまで待つ (読み込み中の画面やダイアログを操作役に触らせないため)。出たら True。"""
    if not doc:
        return True
    name = Path(doc).name.lower()
    end = clock() + load_wait
    while True:
        try:
            if name in (title_of() or "").lower():
                return True
        except Exception:
            pass
        if clock() >= end:
            return False
        sleep(poll)


def launch_and_place(exe: str, doc: Optional[str], place: Optional[str],
                     args: Optional[List[str]] = None, load_wait: float = DEFAULT_LOAD_WAIT,
                     open_keys: Optional[List[str]] = None) -> Dict[str, Any]:
    """アプリを agy の sandbox の外で起動し窓を配置する(sandbox 内で起動すると命令の終了で落ちる)。
    args は文書の後ろに付ける起動の引数 (例: act3_view の ["--page", "6"])。
    open_keys は、窓が出た直後に押すキー (例: act3_view の開くときの確かめの画面を通す ["enter"])。
    文書があれば、窓の題に文書の名前が出るまで (読み込みが済むまで) load_wait 秒まで待つ。"""
    from .binder import WindowBinder
    import win32gui
    cmd = [exe] + ([doc] if doc else []) + [str(a) for a in (args or [])]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, close_fds=True, creationflags=0x00000008 | 0x00000200)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            b = WindowBinder(pid=proc.pid)
            b.find_and_bind()
            break
        except Exception:
            time.sleep(0.5)
    else:
        proc.kill()
        raise RuntimeError("窓が20秒で出なかった")
    if open_keys:
        from .actuator import WindowActuator
        time.sleep(1.0)  # 確かめの画面が描かれるまで
        b.focus()
        act = WindowActuator(b)
        for k in open_keys:
            act.press_key(k)
            time.sleep(0.3)
    def _main_title() -> str:
        # 開くときの確かめの画面は別の窓のことがあるので、毎回 PID から主の窓を捕まえ直して題を読む
        nb = WindowBinder(pid=proc.pid)
        nb.find_and_bind()
        return win32gui.GetWindowText(nb.hwnd) or nb.bound_title

    loaded = wait_document_loaded(_main_title, doc, load_wait)
    b = WindowBinder(pid=proc.pid)
    b.find_and_bind()
    if place:
        _place(b, *[int(v) for v in place.split(",")])
    b.update_rect()
    return {"pid": proc.pid, "title": b.bound_title, "loaded": loaded}


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
    p.add_argument("--double-click", dest="double_click")
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
    if a.click or a.double_click or a.key or a.text:
        from .actuator import WindowActuator
        act = WindowActuator(b)
        if a.click:
            x, y = [float(v) for v in a.click.split(",")]
            act.click(x, y)
        if a.double_click:
            x, y = [float(v) for v in a.double_click.split(",")]
            act.double_click(x, y)
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


HANDOFF_FLAGS = ("--handoff", "--shot", "--burst", "--place", "--launch", "--click", "--double-click", "--key", "--type",
                 "--close-window")


def is_handoff_invocation(argv: List[str]) -> bool:
    return any(x in HANDOFF_FLAGS for x in argv)


def main(argv: List[str]) -> int:
    if "--handoff" in argv:
        i = argv.index("--handoff")
        res = run_handoff(argv[i + 1])
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    return tool_cli(argv)
