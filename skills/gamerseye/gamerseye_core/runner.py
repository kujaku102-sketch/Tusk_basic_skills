"""
Universal ComputerUse Runner & Autonomous Multimodal Loop (Production Hardened)
- Orchestrates WindowBinder, WindowVision, WindowActuator, and Gemini Multimodal Function Calling.
- Zero disk I/O during vision capture & model streaming.
- Supports both pixel-relative coordinates and normalized [0..1000] coordinates.
- Automatically compiles and outputs `UI_OPERATION_MATRIX.md` at run completion.
"""

import argparse
import base64
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional

from .actuator import WindowActuator
from .binder import WindowBinder
from .vision import WindowVision


@dataclass
class StepRecord:
    step: int
    action: str
    args: Dict[str, Any]
    reasoning: str
    ui_changed: bool
    diff_ratio: float
    changed_bbox: Optional[tuple]
    window_size: Tuple[int, int] = (0, 0)
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


SYSTEM_INSTRUCTION = """You are the Antigravity Universal Target-Bound ComputerUse Agent.
You are operating strictly inside a single captured Windows application window.

COORDINATE PROTOCOL:
- The image provided to you is cropped EXACTLY to the target window's visible boundaries.
- Top-left of the window image is (0, 0).
- Bottom-right of the window image is (window_width, window_height).
- All coordinates you pass to tools (`rel_x`, `rel_y`, `from_x`, `from_y`, `to_x`, `to_y`) MUST be pixel coordinates relative to this window image (0 <= rel_x < window_width, 0 <= rel_y < window_height), unless you explicitly set `normalized=True` (which uses a 0..1000 scale).
- Every action is hardware-clamped inside the window bounds. Never attempt to interact outside the window.

EXECUTION STRATEGY:
1. Carefully inspect the current window screenshot and its pixel dimensions.
2. Review previous step results, specifically whether `ui_changed` was True or False.
   - If a menu or modal dialog opened, explore or close it (`press_key("escape")`) before clicking occluded elements.
   - If an action resulted in `ui_changed=False`, adjust coordinates or try an alternative control.
3. Call ONE tool per turn to advance toward the user's goal.
4. Always provide a concise `reasoning` string explaining what UI control you are targeting and why.
5. When the goal is satisfied, call `finish_task` with a comprehensive summary and structured findings.
"""


def _build_gemini_tool_declarations():
    """Build FunctionDeclarations for google-genai SDK."""
    from google.genai import types

    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="click",
                    description="Click at window-relative pixel coordinates (rel_x, rel_y).",
                    parameters=types.Schema(
                        type="OBJECT",
                        properties={
                            "rel_x": types.Schema(type="INTEGER", description="X coordinate relative to window left"),
                            "rel_y": types.Schema(type="INTEGER", description="Y coordinate relative to window top"),
                            "button": types.Schema(
                                type="STRING",
                                enum=["left", "right", "middle"],
                                description="Mouse button to click (default: left)",
                            ),
                            "normalized": types.Schema(
                                type="BOOLEAN",
                                description="Set True only if rel_x/rel_y are in 0..1000 normalized scale instead of pixels.",
                            ),
                            "reasoning": types.Schema(
                                type="STRING",
                                description="Which UI element is being clicked and expected visual response.",
                            ),
                        },
                        required=["rel_x", "rel_y", "reasoning"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="double_click",
                    description="Double-click at window-relative pixel coordinates (rel_x, rel_y).",
                    parameters=types.Schema(
                        type="OBJECT",
                        properties={
                            "rel_x": types.Schema(type="INTEGER", description="X coordinate relative to window left"),
                            "rel_y": types.Schema(type="INTEGER", description="Y coordinate relative to window top"),
                            "normalized": types.Schema(type="BOOLEAN", description="True if using 0..1000 scale"),
                            "reasoning": types.Schema(type="STRING", description="Target control and expected effect"),
                        },
                        required=["rel_x", "rel_y", "reasoning"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="drag",
                    description="Drag mouse from relative (from_x, from_y) to (to_x, to_y) inside the window.",
                    parameters=types.Schema(
                        type="OBJECT",
                        properties={
                            "from_x": types.Schema(type="INTEGER", description="Start X relative to window left"),
                            "from_y": types.Schema(type="INTEGER", description="Start Y relative to window top"),
                            "to_x": types.Schema(type="INTEGER", description="End X relative to window left"),
                            "to_y": types.Schema(type="INTEGER", description="End Y relative to window top"),
                            "normalized": types.Schema(type="BOOLEAN", description="True if using 0..1000 scale"),
                            "reasoning": types.Schema(type="STRING", description="What is being dragged and why"),
                        },
                        required=["from_x", "from_y", "to_x", "to_y", "reasoning"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="scroll",
                    description="Scroll mouse wheel at (rel_x, rel_y). Positive clicks scroll UP, negative scroll DOWN.",
                    parameters=types.Schema(
                        type="OBJECT",
                        properties={
                            "rel_x": types.Schema(type="INTEGER", description="X coordinate relative to window left"),
                            "rel_y": types.Schema(type="INTEGER", description="Y coordinate relative to window top"),
                            "clicks": types.Schema(type="INTEGER", description="Wheel notches (+ up, - down)"),
                            "normalized": types.Schema(type="BOOLEAN", description="True if using 0..1000 scale"),
                            "reasoning": types.Schema(type="STRING", description="Which scrollable area is targeted"),
                        },
                        required=["rel_x", "rel_y", "clicks", "reasoning"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="type_text",
                    description="Type Unicode text into the currently focused input field.",
                    parameters=types.Schema(
                        type="OBJECT",
                        properties={
                            "text": types.Schema(type="STRING", description="Text string to type"),
                            "press_enter": types.Schema(
                                type="BOOLEAN", description="Whether to press Enter after typing"
                            ),
                            "clear_first": types.Schema(
                                type="BOOLEAN", description="Whether to Ctrl+A and Backspace before typing"
                            ),
                            "reasoning": types.Schema(type="STRING", description="Why this text is being entered"),
                        },
                        required=["text", "reasoning"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="press_key",
                    description="Press a key or shortcut chord (e.g. 'escape', 'enter', 'tab', 'ctrl+z', 'ctrl+s').",
                    parameters=types.Schema(
                        type="OBJECT",
                        properties={
                            "key_name": types.Schema(type="STRING", description="Key name or chord"),
                            "reasoning": types.Schema(type="STRING", description="Purpose of pressing this key"),
                        },
                        required=["key_name", "reasoning"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="wait",
                    description="Wait for UI animation or background loading to settle.",
                    parameters=types.Schema(
                        type="OBJECT",
                        properties={
                            "seconds": types.Schema(type="NUMBER", description="Seconds to wait (0.2 to 5.0)"),
                            "reasoning": types.Schema(type="STRING", description="Reason for waiting"),
                        },
                        required=["seconds", "reasoning"],
                    ),
                ),
                types.FunctionDeclaration(
                    name="finish_task",
                    description="Call when the goal has been completed or all target UI elements have been verified.",
                    parameters=types.Schema(
                        type="OBJECT",
                        properties={
                            "summary": types.Schema(
                                type="STRING",
                                description="Detailed markdown summary of findings and verified UI controls.",
                            ),
                            "reasoning": types.Schema(
                                type="STRING", description="Why the goal is considered complete."
                            ),
                        },
                        required=["summary", "reasoning"],
                    ),
                ),
            ]
        )
    ]


class ComputerUseRunner:
    def __init__(
        self,
        title_regex: Optional[str] = None,
        pid: Optional[int] = None,
        model_name: str = "gemini-2.5-flash",
    ):
        self.binder = WindowBinder(pid=pid, title_regex=title_regex)
        self.binder.find_and_bind()
        self.vision = WindowVision(self.binder)
        self.actuator = WindowActuator(self.binder)
        self.model_name = model_name
        self.history: List[StepRecord] = []
        self.final_summary: str = ""

    def get_status(self) -> Dict[str, Any]:
        rect = self.binder.update_rect()
        return {
            "hwnd": self.binder.hwnd,
            "pid": self.binder.bound_pid,
            "title": self.binder.bound_title,
            "bounds": [rect.left, rect.top, rect.right, rect.bottom],
            "size": [rect.width, rect.height],
        }

    def capture_base64(self) -> str:
        png_bytes = self.vision.to_png_bytes()
        return base64.b64encode(png_bytes).decode("utf-8")

    def execute_tool_call(self, name: str, args: Dict[str, Any], step_num: int) -> Tuple[bool, StepRecord]:
        """
        Execute a single tool call inside the safety sandbox, measure before/after visual diff,
        and record a StepRecord. Returns (is_finished, step_record).
        """
        pre_img = self.vision.last_capture or self.vision.capture()
        reasoning = str(args.get("reasoning", ""))
        normalized = bool(args.get("normalized", False))
        error_msg: Optional[str] = None
        is_finished = False

        try:
            if name == "click":
                clamped = self.actuator.click(
                    rel_x=float(args["rel_x"]),
                    rel_y=float(args["rel_y"]),
                    button=str(args.get("button", "left")),
                    normalized=normalized,
                )
                args["clamped_rel"] = list(clamped)

            elif name == "double_click":
                clamped = self.actuator.double_click(
                    rel_x=float(args["rel_x"]),
                    rel_y=float(args["rel_y"]),
                    normalized=normalized,
                )
                args["clamped_rel"] = list(clamped)

            elif name == "drag":
                c_from, c_to = self.actuator.drag(
                    from_x=float(args["from_x"]),
                    from_y=float(args["from_y"]),
                    to_x=float(args["to_x"]),
                    to_y=float(args["to_y"]),
                    normalized=normalized,
                )
                args["clamped_from"] = list(c_from)
                args["clamped_to"] = list(c_to)

            elif name == "scroll":
                clamped = self.actuator.scroll(
                    rel_x=float(args["rel_x"]),
                    rel_y=float(args["rel_y"]),
                    clicks=int(args["clicks"]),
                    normalized=normalized,
                )
                args["clamped_rel"] = list(clamped)

            elif name == "type_text":
                self.actuator.type_text(
                    text=str(args["text"]),
                    press_enter=bool(args.get("press_enter", False)),
                    clear_first=bool(args.get("clear_first", False)),
                )

            elif name == "press_key":
                self.actuator.press_key(key_name=str(args["key_name"]))

            elif name == "wait":
                wait_sec = max(0.1, min(5.0, float(args.get("seconds", 1.0))))
                time.sleep(wait_sec)

            elif name == "finish_task":
                self.final_summary = str(args.get("summary", ""))
                is_finished = True

            else:
                error_msg = f"Unknown tool: {name}"

        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"

        post_img = self.vision.capture()
        diff_metrics = self.vision.calculate_diff_metrics(post_img, pre_img)

        record = StepRecord(
            step=step_num,
            action=name,
            args={k: v for k, v in args.items() if k != "reasoning"},
            reasoning=reasoning,
            ui_changed=bool(diff_metrics["ui_changed"]),
            diff_ratio=float(diff_metrics["changed_pixel_ratio"]),
            changed_bbox=diff_metrics["changed_bbox"],
            window_size=post_img.size,
            error=error_msg,
        )
        self.history.append(record)
        return is_finished, record

    def run_autonomous_loop(
        self,
        goal: str,
        max_steps: int = 15,
        report_path: Optional[Path] = None,
    ) -> List[StepRecord]:
        """
        Execute the autonomous Vision -> Gemini Function Calling -> Actuator -> Diff Verification loop.
        """
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError(
                "The 'google-genai' package is required for autonomous execution. "
                "Install it via: pip install google-genai"
            ) from exc

        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            # Check local settings file fallback
            candidates = [
                Path.cwd() / "work" / "settings" / "gemini_api_key.txt",
                Path.home() / ".config" / "gemini" / "api_key.txt",
            ]
            for c in candidates:
                if c.exists():
                    val = c.read_text(encoding="utf-8").strip()
                    if val:
                        api_key = val
                        break

        if not api_key:
            raise RuntimeError(
                "Gemini API key not found. Please set GEMINI_API_KEY or GOOGLE_API_KEY environment variable, "
                "or place your key in 'work/settings/gemini_api_key.txt'."
            )

        client = genai.Client(api_key=api_key)
        tools = _build_gemini_tool_declarations()

        self.binder.focus()
        current_img = self.vision.capture()

        for step_num in range(1, max_steps + 1):
            rect = self.binder.update_rect()
            png_bytes = self.vision.to_png_bytes(current_img)

            history_lines = []
            for h in self.history[-8:]:
                status_tag = "ERR" if h.error else ("CHANGED" if h.ui_changed else "NO_DIFF")
                history_lines.append(
                    f"Step {h.step}: {h.action}({json.dumps(h.args, ensure_ascii=False)}) "
                    f"-> [{status_tag}, diff={h.diff_ratio:.4f}, bbox={h.changed_bbox}] | {h.reasoning}"
                )
            history_text = "\n".join(history_lines) if history_lines else "(No actions taken yet)"

            prompt_text = (
                f"TARGET WINDOW: '{self.binder.bound_title}' (HWND={self.binder.hwnd}, PID={self.binder.bound_pid})\n"
                f"CURRENT WINDOW IMAGE SIZE: width={rect.width}px, height={rect.height}px\n"
                f"STEP: {step_num} of {max_steps}\n"
                f"GOAL: {goal}\n\n"
                f"RECENT ACTION HISTORY:\n{history_text}\n\n"
                "Inspect the attached window capture and invoke the next tool call."
            )

            contents = [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
                        types.Part.from_text(text=prompt_text),
                    ],
                )
            ]

            response = client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    tools=tools,
                    temperature=0.1,
                ),
            )

            func_calls = getattr(response, "function_calls", None)
            if not func_calls:
                # Fallback: check parts for function_call
                func_calls = []
                if response.candidates and response.candidates[0].content:
                    for part in response.candidates[0].content.parts:
                        if part.function_call:
                            func_calls.append(part.function_call)

            if not func_calls:
                text_reply = getattr(response, "text", "") or "Model returned no tool call."
                print(f"[STEP {step_num}] Model finished without tool call: {text_reply}")
                self.final_summary = text_reply
                break

            fc = func_calls[0]
            fn_name = fc.name
            fn_args = dict(fc.args) if fc.args else {}

            print(
                f"[STEP {step_num}/{max_steps}] Action: {fn_name} | "
                f"Args: {json.dumps({k: v for k, v in fn_args.items() if k != 'reasoning'}, ensure_ascii=False)} | "
                f"Reason: {fn_args.get('reasoning', '')}"
            )

            is_finished, record = self.execute_tool_call(fn_name, fn_args, step_num)
            current_img = self.vision.last_capture

            if record.error:
                print(f"  -> [ERROR] {record.error}")
            else:
                print(
                    f"  -> [VISION DIFF] ui_changed={record.ui_changed}, "
                    f"diff_ratio={record.diff_ratio:.4%}, changed_bbox={record.changed_bbox}"
                )

            if is_finished:
                print(f"\n[COMPLETE] Goal achieved at step {step_num}.")
                break

        if report_path:
            self.export_operation_matrix(goal=goal, output_path=report_path)

        return self.history

    def export_operation_matrix(self, goal: str, output_path: Path) -> Path:
        """Generate `UI_OPERATION_MATRIX.md` documenting all executed actions and visual responses."""
        status = self.get_status()
        lines = [
            "# UI Operation & Visual Verification Matrix",
            "",
            f"- **Generated At**: `{datetime.now().isoformat(timespec='seconds')}`",
            f"- **Target Window**: `{status['title']}` (HWND: `{status['hwnd']}`, PID: `{status['pid']}`)",
            f"- **Window Resolution**: `{status['size'][0]}x{status['size'][1]}` (Screen Bounds: `{status['bounds']}`)",
            f"- **Model**: `{self.model_name}`",
            f"- **Goal**: {goal}",
            "",
            "## 1. Step-by-Step Execution Matrix",
            "",
            "| Step | Action | Parameters (Window-Relative) | Visual Diff (%) | UI Changed | Altered Region (BBox) | Reasoning / Verification |",
            "|---:|---|---|---:|:---:|---|---|",
        ]

        for rec in self.history:
            params_str = json.dumps(rec.args, ensure_ascii=False).replace("|", "\\|")
            reason_str = (rec.error if rec.error else rec.reasoning).replace("|", "\\|").replace("\n", " ")
            changed_badge = "YES" if rec.ui_changed else "NO"
            bbox_str = str(rec.changed_bbox) if rec.changed_bbox else "-"
            lines.append(
                f"| {rec.step} | `{rec.action}` | `{params_str}` | {rec.diff_ratio * 100:.2f}% | "
                f"**{changed_badge}** | `{bbox_str}` | {reason_str} |"
            )

        if self.final_summary:
            lines.extend(
                [
                    "",
                    "## 2. Multimodal Agent Summary",
                    "",
                    self.final_summary.strip(),
                    "",
                ]
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[REPORT] Exported UI operation matrix to: {output_path}")
        return output_path

    def close(self) -> None:
        self.vision.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Universal Target-Bound ComputerUse Agent")
    parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="Window title regex to bind (e.g. 'TuskFolio', 'Unity', 'InDesign')",
    )
    parser.add_argument("--pid", type=int, default=None, help="Process ID to bind")
    parser.add_argument(
        "--goal",
        type=str,
        default="Explore visible UI controls and generate UI_OPERATION_MATRIX.md",
        help="Task goal for multimodal model",
    )
    parser.add_argument("--max-steps", type=int, default=15, help="Maximum autonomous action steps")
    parser.add_argument(
        "--model",
        type=str,
        default=os.environ.get("COMPUTER_USE_MODEL", "gemini-2.5-flash"),
        help="Gemini multimodal model name (default: gemini-2.5-flash)",
    )
    parser.add_argument(
        "--report",
        type=str,
        default="UI_OPERATION_MATRIX.md",
        help="Output path for the generated UI operation markdown matrix",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Bind target window and verify in-memory capture without executing actions",
    )
    # 15FPS Pseudo-Video Burst Options
    parser.add_argument(
        "-uicheck",
        "--uicheck",
        action="store_true",
        help="Run 15FPS burst capture tracking mouse cursor in 256x256 crop window (default 1.5s)",
    )
    parser.add_argument(
        "-fullview",
        "--fullview",
        action="store_true",
        help="Run 15FPS burst capture of the full target window (default 1.5s)",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=15,
        help="Target frames per second for burst capture (default: 15)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=1.5,
        help="Duration in seconds for burst capture (default: 1.5)",
    )
    parser.add_argument(
        "--save-anim",
        type=str,
        default=None,
        help="Optional path to save burst frames as animated WebP or GIF (e.g. output.webp)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.title and args.pid is None:
        print("[ERROR] Must specify either --title or --pid")
        sys.exit(1)

    print(">>> Initializing Universal Target-Bound ComputerUse...")
    runner: Optional[ComputerUseRunner] = None
    try:
        runner = ComputerUseRunner(
            title_regex=args.title,
            pid=args.pid,
            model_name=args.model,
        )
        status = runner.get_status()
        print(
            f"[BOUND] HWND={status['hwnd']}, PID={status['pid']}, "
            f"Title='{status['title']}', Size={status['size'][0]}x{status['size'][1]}, "
            f"VisibleBounds={status['bounds']}"
        )

        # Handle 15FPS Pseudo-Video Burst Mode (-uicheck / -fullview)
        if args.uicheck or args.fullview:
            mode = "uicheck" if args.uicheck else "full"
            mode_desc = "Cursor Tracking 256x256" if mode == "uicheck" else "Full Window"
            print(f"\n[BURST VISION] Starting {mode_desc} capture at {args.fps} FPS for {args.duration}s...")
            frames = runner.vision.capture_burst(
                duration=args.duration,
                fps=args.fps,
                mode=mode,
                uicheck_size=256,
            )
            print(f"[BURST VISION] Captured {len(frames)} frames in RAM (Zero Disk I/O).")
            if frames:
                print(f"[BURST VISION] Frame resolution: {frames[0].width}x{frames[0].height}")

            if args.save_anim:
                out_path = Path(args.save_anim)
                fmt = "GIF" if out_path.suffix.lower() == ".gif" else "WEBP"
                anim_bytes = runner.vision.build_animated_bytes(frames, fps=args.fps, format=fmt)
                out_path.write_bytes(anim_bytes)
                print(f"[BURST VISION] Saved animation ({len(anim_bytes)} bytes) to: {out_path.resolve()}")

            print("[BURST VISION] Completed successfully.")
            return

        img = runner.vision.capture()
        print(f"[VISION] Captured initial frame: {img.size[0]}x{img.size[1]} (zero-disk in-memory RGB)")

        if args.dry_run:
            print("[DRY RUN] Target bound and DWM frame captured successfully. Exiting.")
            return

        print(f"\n[READY] Bound to target window. Goal: '{args.goal}'")
        print("Action sandbox active: coordinates strictly clamped inside visible DWM window bounds.\n")

        runner.run_autonomous_loop(
            goal=args.goal,
            max_steps=args.max_steps,
            report_path=Path(args.report),
        )

    except KeyboardInterrupt:
        print("\n[ABORTED] Interrupted by user.")
        if runner and runner.history:
            runner.export_operation_matrix(goal=args.goal, output_path=Path(args.report))
        sys.exit(130)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)
    finally:
        if runner is not None:
            runner.close()


if __name__ == "__main__":
    main()

