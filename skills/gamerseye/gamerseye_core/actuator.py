"""
Window-Relative Action Dispatcher (Production Hardened)
- Uses Win32 SendInput (hardware scan codes + KEYEVENTF_UNICODE) instead of legacy keybd_event/mouse_event.
- Always focuses and refreshes DWM bounds BEFORE computing clamped screen coordinates.
- Verifies target window ownership before dispatching mouse clicks.
- Implements full action suite required by SPEC-UNIVERSAL-COMPUTER-USE-001:
  click, double_click, drag, scroll, type_text, press_key.
"""

import ctypes
from ctypes import wintypes
import time
from typing import Tuple, List, Dict

import win32api
import win32con

from .binder import WindowBinder

# Win32 SendInput C-Structures
PUL = ctypes.POINTER(ctypes.c_ulong)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79

MAPVK_VK_TO_VSC = 0


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUT_UNION),
    ]


VK_MAP: Dict[str, int] = {
    "escape": win32con.VK_ESCAPE,
    "esc": win32con.VK_ESCAPE,
    "enter": win32con.VK_RETURN,
    "return": win32con.VK_RETURN,
    "tab": win32con.VK_TAB,
    "space": win32con.VK_SPACE,
    "backspace": win32con.VK_BACK,
    "back": win32con.VK_BACK,
    "delete": win32con.VK_DELETE,
    "del": win32con.VK_DELETE,
    "insert": win32con.VK_INSERT,
    "home": win32con.VK_HOME,
    "end": win32con.VK_END,
    "pageup": win32con.VK_PRIOR,
    "pgup": win32con.VK_PRIOR,
    "pagedown": win32con.VK_NEXT,
    "pgdn": win32con.VK_NEXT,
    "up": win32con.VK_UP,
    "down": win32con.VK_DOWN,
    "left": win32con.VK_LEFT,
    "right": win32con.VK_RIGHT,
    "ctrl": win32con.VK_CONTROL,
    "control": win32con.VK_CONTROL,
    "alt": win32con.VK_MENU,
    "menu": win32con.VK_MENU,
    "shift": win32con.VK_SHIFT,
    "win": win32con.VK_LWIN,
    "f1": win32con.VK_F1,
    "f2": win32con.VK_F2,
    "f3": win32con.VK_F3,
    "f4": win32con.VK_F4,
    "f5": win32con.VK_F5,
    "f6": win32con.VK_F6,
    "f7": win32con.VK_F7,
    "f8": win32con.VK_F8,
    "f9": win32con.VK_F9,
    "f10": win32con.VK_F10,
    "f11": win32con.VK_F11,
    "f12": win32con.VK_F12,
    "plus": 0xBB,      # VK_OEM_PLUS
    "minus": 0xBD,     # VK_OEM_MINUS
    "comma": 0xBC,     # VK_OEM_COMMA
    "period": 0xBE,    # VK_OEM_PERIOD
    "slash": 0xBF,     # VK_OEM_2
}

EXTENDED_VKS = {
    win32con.VK_UP,
    win32con.VK_DOWN,
    win32con.VK_LEFT,
    win32con.VK_RIGHT,
    win32con.VK_HOME,
    win32con.VK_END,
    win32con.VK_PRIOR,
    win32con.VK_NEXT,
    win32con.VK_INSERT,
    win32con.VK_DELETE,
    win32con.VK_LWIN,
}


class WindowActuator:
    def __init__(self, binder: WindowBinder):
        self.binder = binder
        self._user32 = ctypes.windll.user32

    def _send_inputs(self, inputs: List[INPUT]) -> int:
        if not inputs:
            return 0
        arr = (INPUT * len(inputs))(*inputs)
        return self._user32.SendInput(len(inputs), ctypes.byref(arr), ctypes.sizeof(INPUT))

    def _make_mouse_input(self, flags: int, data: int = 0, dx: int = 0, dy: int = 0) -> INPUT:
        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp.union.mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=data, dwFlags=flags, time=0, dwExtraInfo=0)
        return inp

    def _absolute_move_input(self, abs_x: int, abs_y: int) -> INPUT:
        """仮想デスクトップ全体を 0..65535 にした絶対座標の「マウスが動いた」入力"""
        vx = self._user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = self._user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = max(1, self._user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) - 1)
        vh = max(1, self._user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) - 1)
        nx = int(round((abs_x - vx) * 65535 / vw))
        ny = int(round((abs_y - vy) * 65535 / vh))
        return self._make_mouse_input(
            MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, dx=nx, dy=ny
        )

    def _point_to(self, abs_x: int, abs_y: int, settle: float = 0.05) -> None:
        """カーソルを置き、アプリに「マウスが動いた」を必ず届けてから戻る。
        SetCursorPos だけだと移動の知らせがボタンより遅れることがあり、アプリ (egui など) は
        押した位置を前のカーソル位置と取り違える (ボタンが押せない・選択がずれて動く)。
        SendInput の移動はボタンと同じ入力の列に入るので、押す前に必ず届く"""
        win32api.SetCursorPos((abs_x, abs_y))
        self._send_inputs([self._absolute_move_input(abs_x, abs_y)])
        time.sleep(settle)

    def _make_vk_input(self, vk: int, key_up: bool = False) -> INPUT:
        scan = self._user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
        flags = 0
        if key_up:
            flags |= KEYEVENTF_KEYUP
        if vk in EXTENDED_VKS:
            flags |= KEYEVENTF_EXTENDEDKEY

        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.union.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
        return inp

    def _make_unicode_input(self, code_unit: int, key_up: bool = False) -> INPUT:
        flags = KEYEVENTF_UNICODE
        if key_up:
            flags |= KEYEVENTF_KEYUP
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.union.ki = KEYBDINPUT(wVk=0, wScan=code_unit, dwFlags=flags, time=0, dwExtraInfo=0)
        return inp

    def _prepare_target_point(
        self, rel_x: float, rel_y: float, normalized: bool = False
    ) -> Tuple[int, int]:
        """
        Focus window FIRST (restoring if minimized), then compute clamped screen coordinates
        and verify sandbox ownership.
        """
        self.binder.focus()
        abs_x, abs_y = self.binder.to_screen_coords(rel_x, rel_y, normalized=normalized)

        # Ensure target is still on top if another window popped up
        if not self.binder.verify_point_ownership(abs_x, abs_y):
            self.binder.focus()
            abs_x, abs_y = self.binder.to_screen_coords(rel_x, rel_y, normalized=normalized)

        return abs_x, abs_y

    def move_to(
        self, rel_x: float, rel_y: float, normalized: bool = False
    ) -> Tuple[int, int]:
        """Move cursor to clamped window-relative coordinate and return (rel_x_clamped, rel_y_clamped)."""
        abs_x, abs_y = self._prepare_target_point(rel_x, rel_y, normalized=normalized)
        self._point_to(abs_x, abs_y, settle=0.03)
        return self.binder.to_relative_coords(abs_x, abs_y)

    def click(
        self,
        rel_x: float,
        rel_y: float,
        button: str = "left",
        normalized: bool = False,
        delay_after: float = 0.25,
    ) -> Tuple[int, int]:
        """
        Click at window-relative (rel_x, rel_y) strictly inside target window bounds.
        Returns actual clamped relative (rel_x, rel_y) clicked.
        """
        abs_x, abs_y = self._prepare_target_point(rel_x, rel_y, normalized=normalized)
        self._point_to(abs_x, abs_y)

        btn = button.lower().strip()
        if btn == "left":
            down_flag, up_flag = MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
        elif btn == "right":
            down_flag, up_flag = MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
        elif btn == "middle":
            down_flag, up_flag = MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP
        else:
            raise ValueError(f"Unsupported mouse button: {button}")

        self._send_inputs([self._make_mouse_input(down_flag)])
        time.sleep(0.04)
        self._send_inputs([self._make_mouse_input(up_flag)])
        time.sleep(delay_after)

        return self.binder.to_relative_coords(abs_x, abs_y)

    def double_click(
        self,
        rel_x: float,
        rel_y: float,
        normalized: bool = False,
        delay_after: float = 0.25,
    ) -> Tuple[int, int]:
        """Double-click at window-relative (rel_x, rel_y) within OS double-click time threshold."""
        abs_x, abs_y = self._prepare_target_point(rel_x, rel_y, normalized=normalized)
        self._point_to(abs_x, abs_y)

        for _ in range(2):
            self._send_inputs([self._make_mouse_input(MOUSEEVENTF_LEFTDOWN)])
            time.sleep(0.03)
            self._send_inputs([self._make_mouse_input(MOUSEEVENTF_LEFTUP)])
            time.sleep(0.06)

        time.sleep(delay_after)
        return self.binder.to_relative_coords(abs_x, abs_y)

    def drag(
        self,
        from_x: float,
        from_y: float,
        to_x: float,
        to_y: float,
        steps: int = 15,
        normalized: bool = False,
        delay_after: float = 0.3,
    ) -> Tuple[Tuple[int, int], Tuple[int, int]]:
        """
        Smoothly drag from relative (from_x, from_y) to (to_x, to_y),
        clamping every intermediate step strictly inside the target window.
        """
        self.binder.focus()
        rect = self.binder.update_rect()
        abs_from_x, abs_from_y = self.binder.to_screen_coords(from_x, from_y, normalized=normalized)
        abs_to_x, abs_to_y = self.binder.to_screen_coords(to_x, to_y, normalized=normalized)

        self._point_to(abs_from_x, abs_from_y)
        self._send_inputs([self._make_mouse_input(MOUSEEVENTF_LEFTDOWN)])
        time.sleep(0.05)

        total_steps = max(2, int(steps))
        for i in range(1, total_steps + 1):
            t = i / float(total_steps)
            interp_x = int(round(abs_from_x + (abs_to_x - abs_from_x) * t))
            interp_y = int(round(abs_from_y + (abs_to_y - abs_from_y) * t))
            clamped_x, clamped_y = rect.clamp(interp_x, interp_y, margin=self.binder.clamp_margin)
            self._point_to(clamped_x, clamped_y, settle=0.015)

        time.sleep(0.04)
        self._send_inputs([self._make_mouse_input(MOUSEEVENTF_LEFTUP)])
        time.sleep(delay_after)

        return (
            self.binder.to_relative_coords(abs_from_x, abs_from_y),
            self.binder.to_relative_coords(abs_to_x, abs_to_y),
        )

    def scroll(
        self,
        rel_x: float,
        rel_y: float,
        clicks: int,
        normalized: bool = False,
        delay_after: float = 0.2,
    ) -> Tuple[int, int]:
        """Scroll mouse wheel at clamped window-relative position (positive = up, negative = down)."""
        abs_x, abs_y = self._prepare_target_point(rel_x, rel_y, normalized=normalized)
        self._point_to(abs_x, abs_y)

        wheel_delta = int(clicks) * 120
        # Convert signed wheel delta to unsigned DWORD for MOUSEINPUT.mouseData
        unsigned_delta = wheel_delta & 0xFFFFFFFF
        self._send_inputs([self._make_mouse_input(MOUSEEVENTF_WHEEL, data=unsigned_delta)])
        time.sleep(delay_after)
        return self.binder.to_relative_coords(abs_x, abs_y)

    def type_text(
        self,
        text: str,
        press_enter: bool = False,
        clear_first: bool = False,
        delay_after: float = 0.2,
    ) -> None:
        """
        Type arbitrary Unicode text (ASCII, Japanese, symbols) into the focused target window
        via SendInput + KEYEVENTF_UNICODE.
        """
        self.binder.focus()

        if clear_first:
            self.press_key("ctrl+a", delay_after=0.05)
            self.press_key("backspace", delay_after=0.05)

        for ch in text:
            if ch == "\r":
                continue
            if ch == "\n":
                self.press_key("enter", delay_after=0.04)
                continue
            if ch == "\t":
                self.press_key("tab", delay_after=0.04)
                continue

            # Encode character as UTF-16LE code units to support full BMP & surrogate pairs
            utf16_bytes = ch.encode("utf-16-le")
            code_units = [
                int.from_bytes(utf16_bytes[i : i + 2], "little")
                for i in range(0, len(utf16_bytes), 2)
            ]
            events: List[INPUT] = []
            for cu in code_units:
                events.append(self._make_unicode_input(cu, key_up=False))
                events.append(self._make_unicode_input(cu, key_up=True))
            self._send_inputs(events)
            time.sleep(0.01)

        if press_enter:
            self.press_key("enter", delay_after=0.05)

        time.sleep(delay_after)

    def _resolve_vk(self, token: str) -> int:
        t = token.lower().strip()
        if t in VK_MAP:
            return VK_MAP[t]
        if len(t) == 1:
            ch = t.upper()
            if "A" <= ch <= "Z" or "0" <= ch <= "9":
                return ord(ch)
            vk_scan = win32api.VkKeyScan(t)
            if vk_scan != -1:
                return vk_scan & 0xFF
        raise ValueError(f"Unrecognized key token: '{token}'")

    def press_key(self, key_name: str, delay_after: float = 0.2) -> None:
        """
        Press a single key or modifier chord (e.g., 'esc', 'enter', 'ctrl+z', 'ctrl+shift+s', 'alt+f4').
        Note: Blocks 'alt+f4' and 'win+...' by default unless explicitly needed to protect target session.
        """
        self.binder.focus()
        normalized = key_name.lower().strip()
        if normalized in ("alt+f4", "win+d", "win+l", "alt+tab"):
            raise PermissionError(f"Safety Sandbox blocked dangerous system key chord: '{key_name}'")

        tokens = [tok.strip() for tok in normalized.split("+") if tok.strip()]
        if not tokens:
            return

        vks = [self._resolve_vk(tok) for tok in tokens]

        # Press all keys in sequence
        for vk in vks:
            self._send_inputs([self._make_vk_input(vk, key_up=False)])
            time.sleep(0.02)

        time.sleep(0.03)

        # Release in reverse order
        for vk in reversed(vks):
            self._send_inputs([self._make_vk_input(vk, key_up=True)])
            time.sleep(0.02)

        time.sleep(delay_after)
