"""Track the exact Windows helper launched by the GUI, without PID guessing."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.DWORD), ('fMask', wintypes.ULONG),
        ('hwnd', wintypes.HWND), ('lpVerb', wintypes.LPCWSTR),
        ('lpFile', wintypes.LPCWSTR), ('lpParameters', wintypes.LPCWSTR),
        ('lpDirectory', wintypes.LPCWSTR), ('nShow', ctypes.c_int),
        ('hInstApp', wintypes.HINSTANCE), ('lpIDList', ctypes.c_void_p),
        ('lpClass', wintypes.LPCWSTR), ('hkeyClass', wintypes.HKEY),
        ('dwHotKey', wintypes.DWORD), ('hIcon', wintypes.HANDLE),
        ('hProcess', wintypes.HANDLE),
    ]


class HelperProcess:
    def __init__(self, handle, kernel):
        if not handle:
            raise ValueError('missing helper process handle')
        self._handle = handle
        self._kernel = kernel

    @classmethod
    def launch(cls, executable: Path, params: str):
        shell = ctypes.WinDLL('shell32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        shell.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
        shell.ShellExecuteExW.restype = wintypes.BOOL
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        info = _ShellExecuteInfo()
        info.cbSize = ctypes.sizeof(info)
        info.fMask = 0x40 | 0x100  # NOCLOSEPROCESS | NOASYNC
        info.lpVerb = 'open'
        info.lpFile = str(executable)
        info.lpParameters = params
        info.lpDirectory = str(executable.parent)
        info.nShow = 0
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            error = ctypes.get_last_error()
            if error == 1223:  # UAC cancelled
                return None
            raise ctypes.WinError(error)
        if not info.hProcess:
            raise OSError('helper process handle unavailable')
        return cls(info.hProcess, kernel)

    def exited(self) -> bool:
        if self._handle is None:
            raise RuntimeError('helper handle closed')
        result = self._kernel.WaitForSingleObject(self._handle, 0)
        if result == 0:
            return True
        if result == 258:
            return False
        raise OSError('helper process wait failed')

    def close(self) -> None:
        if self._handle is not None:
            handle, self._handle = self._handle, None
            self._kernel.CloseHandle(handle)


def exit_status(terminal_status: str | None, backup_saved: bool) -> str:
    if terminal_status is not None:
        return terminal_status
    if backup_saved:
        return 'CSV 저장 완료 · 캡처 도구 비정상 종료 · 게임 종료 미확인'
    return '캡처 도구 비정상 종료 · 백업 확인 필요'
