"""Load Act4.1 UDR from one input without reusing another input's extraction."""

import json
from pathlib import Path
import shutil
import subprocess
from tempfile import mkdtemp


WORKSPACE = Path(__file__).resolve().parents[4]


def find_act4(explicit: str | None = None) -> Path:
    if explicit:
        path = Path(explicit).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Act4.1 executable not found: {path}")
        return path
    for path in (
        WORKSPACE / "Act4.1" / "target" / "release" / "act4.exe",
        WORKSPACE / "Act4.1" / "target" / "debug" / "act4.exe",
        Path("D:/Tuskfolio-portable/bin/act4.exe"),
    ):
        if path.is_file():
            return path
    found = shutil.which("act4")
    if found:
        return Path(found).resolve()
    raise FileNotFoundError("act4.exe not found; pass --act4-exe")


def load_udr(source_arg: str, work_dir_arg: str, act4_exe_arg: str | None = None):
    source = Path(source_arg).resolve()
    if not source.is_file() or source.suffix.lower() not in (".pdf", ".folio"):
        raise ValueError(f"PDF or .folio input not found: {source}")
    act4 = find_act4(act4_exe_arg)
    work_dir = Path(work_dir_arg).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(mkdtemp(prefix="udr_", dir=work_dir))
    command = "extract" if source.suffix.lower() == ".pdf" else "unpack"
    result = subprocess.run([str(act4), command, str(source), "-o", str(run_dir)],
                            cwd=WORKSPACE, timeout=300, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-600:]
        raise RuntimeError(f"act4 {command} exited {result.returncode}: {detail}")
    udr_path = run_dir / "udr.json"
    if not udr_path.is_file():
        raise FileNotFoundError(f"Act4.1 produced no udr.json: {run_dir}")
    with udr_path.open(encoding="utf-8") as stream:
        udr = json.load(stream)
    if not isinstance(udr, dict) or not isinstance(udr.get("pages"), list):
        raise ValueError(f"Invalid UDR pages in {udr_path}")
    return udr, udr_path
