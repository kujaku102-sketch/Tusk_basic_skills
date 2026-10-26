"""Launch one GUI process on a non-active Win32 desktop without switching desktops."""

import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
    ]


def run_on_inactive_desktop(command: list[str], cwd: Path, timeout_seconds: int = 120) -> None:
    if sys.platform != "win32":
        raise OSError("inactive desktop capture is Windows-only")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p]
    user32.CreateDesktopW.restype = wintypes.HANDLE
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.CloseDesktop.restype = wintypes.BOOL
    kernel32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR,
                                        ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
                                        wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                                        ctypes.POINTER(STARTUPINFOW),
                                        ctypes.POINTER(PROCESS_INFORMATION)]
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    name = "TuskVision_" + uuid4().hex[:12]
    desktop = user32.CreateDesktopW(name, None, None, 0, 0xF01FF, None)
    if not desktop:
        raise OSError(ctypes.get_last_error(), "CreateDesktopW failed")
    process = PROCESS_INFORMATION()
    started = False
    try:
        startup = STARTUPINFOW()
        startup.cb = ctypes.sizeof(STARTUPINFOW)
        startup.lpDesktop = "WinSta0\\" + name
        command_buffer = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        started = bool(kernel32.CreateProcessW(command[0], command_buffer, None, None,
                                                False, 0, None, str(cwd),
                                                ctypes.byref(startup), ctypes.byref(process)))
        if not started:
            raise OSError(ctypes.get_last_error(), "CreateProcessW failed")
        wait = kernel32.WaitForSingleObject(process.hProcess, timeout_seconds * 1000)
        if wait != 0:
            kernel32.TerminateProcess(process.hProcess, 124)
            kernel32.WaitForSingleObject(process.hProcess, 5000)
            if wait == 0x102:
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            raise OSError(f"WaitForSingleObject failed: {wait}")
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(process.hProcess, ctypes.byref(exit_code)):
            raise OSError(ctypes.get_last_error(), "GetExitCodeProcess failed")
        if exit_code.value:
            raise subprocess.CalledProcessError(exit_code.value, command)
    finally:
        if started:
            kernel32.CloseHandle(process.hThread)
            kernel32.CloseHandle(process.hProcess)
        user32.CloseDesktop(desktop)
