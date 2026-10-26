#!/usr/bin/env python3
"""
GamersEYE CLI Standalone Entrypoint
Provides portable execution regardless of working directory or installation method.
"""

import sys
from pathlib import Path

# Self-contained path resolution
skill_dir = Path(__file__).resolve().parent
core_dir = skill_dir / "gamerseye_core"

if str(skill_dir) not in sys.path:
    sys.path.insert(0, str(skill_dir))
if str(core_dir) not in sys.path:
    sys.path.insert(0, str(core_dir))

def _check_dependencies():
    missing = []
    try:
        import win32gui
        import win32process
    except ImportError:
        missing.append("pywin32")
    try:
        import mss
    except ImportError:
        missing.append("mss")
    try:
        import PIL
    except ImportError:
        missing.append("Pillow")

    if missing:
        print(f"[GamersEYE ERROR] Missing required dependencies: {', '.join(missing)}", file=sys.stderr)
        print(f"Please install them using:", file=sys.stderr)
        print(f"    pip install -r \"{skill_dir / 'requirements.txt'}\"", file=sys.stderr)
        sys.exit(1)

def main():
    _check_dependencies()
    from gamerseye_core import handoff
    if handoff.is_handoff_invocation(sys.argv[1:]):
        sys.exit(handoff.main(sys.argv[1:]))
    from gamerseye_core.runner import main as runner_main
    runner_main()

if __name__ == "__main__":
    main()
