"""
Window Binder & Safety Sandbox (Production Hardened)
- Enforces Per-Monitor DPI Awareness V2 at import time.
- Uses DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS) for pixel-exact visible bounds.
- Filters out Cloaked / Invisible / Tool windows.
- Bypasses Windows Foreground Lock via AttachThreadInput + Alt key state trick.
"""

import ctypes
from ctypes import wintypes
import re
import time
from dataclasses import dataclass
from typing import Optional, Tuple, List

import win32api
import win32con
import win32gui
import win32process
import win32service


_WINSTA0_HANDLE = None
_DEFAULT_DESK_HANDLE = None


def _attach_interactive_desktop() -> None:
    """
    Ensure the current thread is attached to an interactive desktop.
    agy の exebox デスクトップは対象窓が居ない別デスクトップなので、Default へ付け替える。
    """
    global _WINSTA0_HANDLE, _DEFAULT_DESK_HANDLE
    try:
        tid = win32api.GetCurrentThreadId()
        cur_desk = win32service.GetThreadDesktop(tid)
        cur_desk_name = win32service.GetUserObjectInformation(cur_desk, 2)
        if cur_desk_name and cur_desk_name != "Default" and not str(cur_desk_name).startswith("exebox"):
            return
        _WINSTA0_HANDLE = win32service.OpenWindowStation("WinSta0", False, 0xF037F)
        _WINSTA0_HANDLE.SetProcessWindowStation()
        _DEFAULT_DESK_HANDLE = win32service.OpenDesktop("Default", 0, False, 0x01FF)
        _DEFAULT_DESK_HANDLE.SetThreadDesktop()
    except Exception:
        pass


_attach_interactive_desktop()


def _enable_per_monitor_dpi_awareness() -> None:
    """Force Per-Monitor DPI Awareness V2 so GetWindowRect and mss share physical pixel space."""
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_per_monitor_dpi_awareness()

# DWM Constants
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14


@dataclass(frozen=True)
class WindowRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    def contains(self, x: int, y: int, margin: int = 0) -> bool:
        return (
            (self.left + margin) <= x <= (self.right - margin)
            and (self.top + margin) <= y <= (self.bottom - margin)
        )

    def clamp(self, x: int, y: int, margin: int = 4) -> Tuple[int, int]:
        """Clamp absolute screen coordinates strictly inside the visible window rectangle."""
        safe_margin_x = min(margin, max(0, (self.width // 2) - 1))
        safe_margin_y = min(margin, max(0, (self.height // 2) - 1))

        min_x = self.left + safe_margin_x
        max_x = max(min_x, self.right - 1 - safe_margin_x)
        min_y = self.top + safe_margin_y
        max_y = max(min_y, self.bottom - 1 - safe_margin_y)

        clamped_x = max(min_x, min(max_x, int(x)))
        clamped_y = max(min_y, min(max_y, int(y)))
        return clamped_x, clamped_y


class WindowBinder:
    def __init__(
        self,
        pid: Optional[int] = None,
        title_regex: Optional[str] = None,
        clamp_margin: int = 4,
    ):
        if pid is None and not title_regex:
            raise ValueError("WindowBinder requires either 'pid' or 'title_regex'.")
        self.pid = pid
        self.title_regex = re.compile(title_regex, re.IGNORECASE) if title_regex else None
        self.clamp_margin = clamp_margin
        self.hwnd: Optional[int] = None
        self.bound_pid: Optional[int] = None
        self.bound_title: str = ""
        self.last_rect: Optional[WindowRect] = None

        self._dwmapi = ctypes.windll.dwmapi
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32

    def _is_window_cloaked(self, hwnd: int) -> bool:
        """Check if DWM has cloaked the window (e.g., suspended UWP or other virtual desktop)."""
        cloaked = ctypes.c_int(0)
        hr = self._dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(DWMWA_CLOAKED),
            ctypes.byref(cloaked),
            ctypes.sizeof(cloaked),
        )
        return hr == 0 and cloaked.value != 0

    def _get_visible_rect(self, hwnd: int) -> WindowRect:
        """
        Retrieve true visible frame bounds via DWMWA_EXTENDED_FRAME_BOUNDS,
        stripping Windows 10/11 invisible 7-8px resize borders.
        Falls back to GetWindowRect if DWM query fails.
        """
        rect = wintypes.RECT()
        hr = self._dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if hr == 0:
            w_rect = WindowRect(rect.left, rect.top, rect.right, rect.bottom)
            if w_rect.width > 0 and w_rect.height > 0:
                return w_rect

        l, t, r, b = win32gui.GetWindowRect(hwnd)
        return WindowRect(l, t, r, b)

    def find_and_bind(self) -> int:
        """Locate best matching top-level visible window and bind its HWND."""
        candidates: List[Tuple[int, int, str, int, int]] = []

        def enum_cb(hwnd: int, _) -> None:
            try:
                if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                    return
                if self._is_window_cloaked(hwnd):
                    return

                # Skip pure tool windows unless explicitly matched by title
                ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                is_tool = bool(ex_style & win32con.WS_EX_TOOLWINDOW)

                title = win32gui.GetWindowText(hwnd) or ""
                _, w_pid = win32process.GetWindowThreadProcessId(hwnd)

                pid_match = self.pid is not None and w_pid == self.pid
                title_match = bool(self.title_regex and title and self.title_regex.search(title))

                if self.pid is not None and self.title_regex is not None:
                    matched = pid_match and title_match
                elif self.pid is not None:
                    matched = pid_match
                else:
                    matched = title_match

                if not matched:
                    return

                rect = self._get_visible_rect(hwnd)
                area = rect.width * rect.height
                # Score candidates: prefer non-minimized, titled, non-tool, larger windows
                score = 0
                if title.strip():
                    score += 1000
                if not is_tool:
                    score += 500
                if not win32gui.IsIconic(hwnd):
                    score += 300
                score += min(area // 1000, 500)

                candidates.append((score, hwnd, title, w_pid, area))
            except Exception:
                return

        try:
            win32gui.EnumWindows(enum_cb, None)
        except Exception:
            pass

        if not candidates and _DEFAULT_DESK_HANDLE is not None:
            try:
                for h in _DEFAULT_DESK_HANDLE.EnumDesktopWindows():
                    enum_cb(int(h), None)
            except Exception:
                pass

        if not candidates:
            desc = []
            if self.pid is not None:
                desc.append(f"PID={self.pid}")
            if self.title_regex is not None:
                desc.append(f"Title~='{self.title_regex.pattern}'")
            raise RuntimeError(f"Target window not found ({', '.join(desc)})")

        candidates.sort(key=lambda item: item[0], reverse=True)
        _, best_hwnd, best_title, best_pid, _ = candidates[0]

        self.hwnd = best_hwnd
        self.bound_pid = best_pid
        self.bound_title = best_title

        # Ensure restored if minimized prior to initial rect capture
        if win32gui.IsIconic(self.hwnd):
            self.focus()
        else:
            self.update_rect()

        return self.hwnd

    def ensure_alive(self) -> int:
        """Verify bound HWND still exists; re-bind if window handle was recreated."""
        if self.hwnd and win32gui.IsWindow(self.hwnd):
            return self.hwnd
        return self.find_and_bind()

    def update_rect(self) -> WindowRect:
        """Query latest DWM-accurate window bounds on screen."""
        hwnd = self.ensure_alive()
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.15)

        self.bound_title = win32gui.GetWindowText(hwnd) or self.bound_title
        rect = self._get_visible_rect(hwnd)
        if rect.width <= 1 or rect.height <= 1:
            raise RuntimeError(
                f"Target window (HWND={hwnd}) has invalid dimensions: {rect.width}x{rect.height}"
            )
        self.last_rect = rect
        return rect

    def focus(self, timeout: float = 1.0) -> WindowRect:
        """
        Deterministically restore and bring the bound window to the foreground,
        bypassing Win32 foreground lock restrictions.
        """
        hwnd = self.ensure_alive()

        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.2)

        fg_hwnd = win32gui.GetForegroundWindow()
        if fg_hwnd != hwnd:
            fg_tid, _ = win32process.GetWindowThreadProcessId(fg_hwnd) if fg_hwnd else (0, 0)
            target_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
            cur_tid = self._kernel32.GetCurrentThreadId()

            attached_fg = False
            attached_target = False
            try:
                if fg_tid and fg_tid != cur_tid:
                    attached_fg = bool(self._user32.AttachThreadInput(cur_tid, fg_tid, True))
                if target_tid and target_tid != cur_tid:
                    attached_target = bool(self._user32.AttachThreadInput(cur_tid, target_tid, True))

                # Unlock SetForegroundWindow restriction with dummy Alt key state if needed
                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_EXTENDEDKEY, 0)
                win32api.keybd_event(
                    win32con.VK_MENU,
                    0,
                    win32con.KEYEVENTF_EXTENDEDKEY | win32con.KEYEVENTF_KEYUP,
                    0,
                )

                self._user32.BringWindowToTop(wintypes.HWND(hwnd))
                self._user32.SetForegroundWindow(wintypes.HWND(hwnd))
                self._user32.SetActiveWindow(wintypes.HWND(hwnd))
            except Exception:
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                except Exception:
                    pass
            finally:
                if attached_fg:
                    self._user32.AttachThreadInput(cur_tid, fg_tid, False)
                if attached_target:
                    self._user32.AttachThreadInput(cur_tid, target_tid, False)

            deadline = time.time() + timeout
            while time.time() < deadline:
                if win32gui.GetForegroundWindow() == hwnd:
                    break
                time.sleep(0.03)

        return self.update_rect()

    def to_screen_coords(
        self,
        rel_x: float,
        rel_y: float,
        normalized: bool = False,
        margin: Optional[int] = None,
    ) -> Tuple[int, int]:
        """
        Convert window-relative coordinates to strictly clamped screen coordinates.
        Supports:
          - Pixel relative coordinates: (0..width, 0..height)
          - Normalized coordinates (0.0..1.0 or 0..1000 when normalized=True)
        """
        rect = self.update_rect()
        use_margin = self.clamp_margin if margin is None else margin

        if normalized:
            # Support both [0.0, 1.0] and Gemini's [0, 1000] normalized scale
            if 0.0 <= rel_x <= 1.0 and 0.0 <= rel_y <= 1.0 and (isinstance(rel_x, float) or isinstance(rel_y, float)):
                px_x = int(round(rel_x * rect.width))
                px_y = int(round(rel_y * rect.height))
            else:
                px_x = int(round((rel_x / 1000.0) * rect.width))
                px_y = int(round((rel_y / 1000.0) * rect.height))
        else:
            px_x = int(round(rel_x))
            px_y = int(round(rel_y))

        abs_x = rect.left + px_x
        abs_y = rect.top + px_y
        return rect.clamp(abs_x, abs_y, margin=use_margin)

    def to_relative_coords(self, abs_x: int, abs_y: int) -> Tuple[int, int]:
        """Convert absolute screen coordinates to window-relative pixel coordinates."""
        rect = self.update_rect()
        return abs_x - rect.left, abs_y - rect.top

    def verify_point_ownership(self, abs_x: int, abs_y: int) -> bool:
        """
        Verify at OS level that the screen point (abs_x, abs_y) is inside our WindowRect
        and is not occluded by a foreign process window.
        """
        rect = self.last_rect or self.update_rect()
        if not rect.contains(abs_x, abs_y):
            return False

        pt_hwnd = win32gui.WindowFromPoint((abs_x, abs_y))
        if not pt_hwnd:
            return True

        if pt_hwnd == self.hwnd or win32gui.IsChild(self.hwnd, pt_hwnd):
            return True

        _, pt_pid = win32process.GetWindowThreadProcessId(pt_hwnd)
        return pt_pid == self.bound_pid
