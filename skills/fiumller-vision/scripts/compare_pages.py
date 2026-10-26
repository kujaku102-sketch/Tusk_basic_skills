#!/usr/bin/env python3
"""Compare FiumLLER/PDFium-rendered PDF pages with a red pixel-diff overlay."""

import argparse
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from render_inspect import find_executable, render_pdf

def main():
    parser = argparse.ArgumentParser(description="Generate visual pixel diff between two PDF pages.")
    parser.add_argument("pdf_orig", help="Original PDF file")
    parser.add_argument("pdf_gen", help="Generated/Translated PDF file")
    parser.add_argument("--page", "-p", type=int, required=True, help="1-indexed page number")
    parser.add_argument("--dpi", type=float, default=150.0, help="Rendering resolution (DPI)")
    parser.add_argument("--output", "-o", help="Output diff PNG path")
    parser.add_argument("--fiumller-exe", help="FiumLLER CLI path")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing an existing output")
    args = parser.parse_args()

    if args.page < 1 or args.dpi <= 0:
        parser.error("page and dpi must be positive")
    originals = [Path(args.pdf_orig).resolve(), Path(args.pdf_gen).resolve()]
    for source in originals:
        if not source.is_file() or source.suffix.lower() != ".pdf":
            parser.error(f"PDF input not found: {source}")
    out_dir = Path(__file__).resolve().parents[4] / "work" / "fiumller_vision_diffs"
    out_path = Path(args.output).resolve() if args.output else out_dir / f"diff_p{args.page}.png"
    if out_path.exists() and not args.overwrite:
        parser.error(f"output already exists: {out_path}; pass --overwrite to replace it")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from PIL import Image, ImageChops
    except ImportError:
        print("Error: Pillow is required for the diff overlay", file=sys.stderr)
        return 1

    try:
        executable = find_executable("fiumller", args.fiumller_exe)
        with TemporaryDirectory(prefix="fiumller_diff_") as temp:
            paths = [Path(temp) / "reference.png", Path(temp) / "candidate.png"]
            for source, image_path in zip(originals, paths):
                render_pdf(source, args.page, args.dpi, image_path, executable)
            with Image.open(paths[0]) as image:
                img_orig = image.convert("RGB")
            with Image.open(paths[1]) as image:
                img_gen = image.convert("RGB")
    except (FileNotFoundError, OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(f"FiumLLER render failed: {error}", file=sys.stderr)
        return 1

    # Align dimensions
    max_w = max(img_orig.width, img_gen.width)
    max_h = max(img_orig.height, img_gen.height)

    canvas_orig = Image.new("RGB", (max_w, max_h), (255, 255, 255))
    canvas_gen = Image.new("RGB", (max_w, max_h), (255, 255, 255))
    canvas_orig.paste(img_orig, (0, 0))
    canvas_gen.paste(img_gen, (0, 0))

    # Calculate diff
    diff = ImageChops.difference(canvas_orig, canvas_gen)
    diff_gray = diff.convert("L")
    mask = diff_gray.point(lambda p: 255 if p > 25 else 0)

    # Highlight diff in red over original background
    highlight = Image.new("RGB", (max_w, max_h), (255, 0, 0))
    diff_overlay = Image.composite(highlight, canvas_gen, mask)
    diff_overlay.save(out_path)

    print(f"FiumLLER/PDFium visual diff: {out_path.as_posix()}")
    return 0

if __name__ == '__main__':
    sys.exit(main())
