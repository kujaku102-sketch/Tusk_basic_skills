#!/usr/bin/env python3
"""Render a PDF through FiumLLER/PDFium or capture a .folio through Act3 View."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4


WORKSPACE = Path(__file__).resolve().parents[4]


def find_executable(name: str, explicit: str | None) -> Path:
    if explicit:
        executable = Path(explicit).resolve()
        if not executable.is_file():
            raise FileNotFoundError(f"{name} executable not found: {executable}")
        return executable
    candidates = [
        WORKSPACE / "Act3" / "target" / "release" / f"{name}.exe",
        WORKSPACE / "Act3" / "target" / "debug" / f"{name}.exe",
    ]
    if name == "act3_view":
        candidates.append(Path("D:/Tuskfolio-portable/bin/act3_view.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    found = shutil.which(name)
    if found:
        return Path(found).resolve()
    raise FileNotFoundError(f"{name} executable not found; pass --{name.replace('_', '-')}-exe")


def render_pdf(source: Path, page: int, dpi: float, output: Path, executable: Path) -> None:
    command = [
        str(executable), str(source), "--render-page", str(page),
        "--dpi", str(dpi), "--render-out", str(output),
    ]
    subprocess.run(command, cwd=WORKSPACE, check=True, timeout=120)


def capture_folio(
    source: Path, page: int, zoom: float, language: str, output: Path, executable: Path,
    inactive_desktop: bool = False,
) -> None:
    command = [
        str(executable), str(source), "--page", str(page), "--zoom", str(zoom),
        "--lang", language, "--screenshot", str(output),
    ]
    if inactive_desktop:
        from inactive_desktop import run_on_inactive_desktop
        run_on_inactive_desktop(command, executable.parent)
        return
    startupinfo = None
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    subprocess.run(command, cwd=executable.parent, startupinfo=startupinfo, check=True, timeout=120)


def crop_pdf_render(output: Path, bbox: list[float], dpi: float) -> None:
    from PIL import Image

    x0, y0, x1, y1 = bbox
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
        raise ValueError("bbox must be positive DTP points: x0 y0 x1 y1")
    scale = dpi / 72.0
    with Image.open(output) as image:
        box = (round(x0 * scale), round(y0 * scale), round(x1 * scale), round(y1 * scale))
        if box[2] > image.width or box[3] > image.height:
            raise ValueError(f"bbox exceeds rendered page: {image.width}x{image.height} px")
        image.crop(box).save(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="PDF or .folio input")
    parser.add_argument("--page", "-p", type=int, required=True, help="1-based page number")
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"),
                        help="PDF crop in displayed-page top-left points after rotation; PDF only")
    parser.add_argument("--dpi", type=float, default=150.0, help="PDF render DPI")
    parser.add_argument("--zoom", type=float, default=1.0, help=".folio GUI zoom scale")
    parser.add_argument("--lang", choices=("en", "jp"), default="jp", help=".folio GUI language")
    parser.add_argument("--fiumller-exe", help="FiumLLER CLI path")
    parser.add_argument("--act3-view-exe", help="Act3 View executable path")
    parser.add_argument("--inactive-desktop", action="store_true",
                        help="Capture .folio on a non-active Win32 desktop (Windows only)")
    parser.add_argument("--output", "-o", help="Output PNG path")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing an existing output")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.is_file():
        parser.error(f"input file not found: {source}")
    if args.page < 1 or args.dpi <= 0 or args.zoom <= 0:
        parser.error("page, dpi, and zoom must be positive")
    extension = source.suffix.lower()
    if extension not in (".pdf", ".folio"):
        parser.error("input must be a PDF or .folio")
    if extension == ".folio" and args.bbox:
        parser.error("--bbox is only supported for PDF renders")
    if args.inactive_desktop and (extension != ".folio" or sys.platform != "win32"):
        parser.error("--inactive-desktop requires a .folio input on Windows")
    if args.bbox:
        x0, y0, x1, y1 = args.bbox
        if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
            parser.error("bbox must be positive DTP points: x0 y0 x1 y1")

    out_dir = WORKSPACE / "work" / "fiumller_vision_renders"
    output = Path(args.output).resolve() if args.output else out_dir / f"{source.stem}_p{args.page}.png"
    if output.exists() and not args.overwrite:
        parser.error(f"output already exists: {output}; pass --overwrite to replace it")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.parent / f".{output.stem}.{uuid4().hex}.tmp.png"

    try:
        if extension == ".pdf":
            executable = find_executable("fiumller", args.fiumller_exe)
            render_pdf(source, args.page, args.dpi, temporary, executable)
            if args.bbox:
                crop_pdf_render(temporary, args.bbox, args.dpi)
            mode = "FiumLLER/PDFium"
        else:
            executable = find_executable("act3_view", args.act3_view_exe)
            capture_folio(source, args.page, args.zoom, args.lang, temporary, executable,
                          args.inactive_desktop)
            mode = "Act3 View GUI capture"
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise ValueError(f"no image produced at {temporary}")
        from PIL import Image
        with Image.open(temporary) as image:
            image.verify()
        if output.exists() and not args.overwrite:
            raise FileExistsError(f"output appeared during render: {output}")
        temporary.replace(output)
    except (FileNotFoundError, ImportError, OSError, ValueError,
            subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(f"Render failed: {error}", file=sys.stderr)
        return 1
    finally:
        temporary.unlink(missing_ok=True)

    print(f"{mode}: {output.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
