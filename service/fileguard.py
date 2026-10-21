# -*- coding: utf-8 -*-
"""防删除句柄：守护服务持有 exe / 数据目录 / 数据库的打开句柄。

共享模式只允许读写、不允许 delete，因此标准用户即使拿到文件路径，
在守护服务运行期间也无法删除/重命名这些对象（ACL 仍是第一层保护）。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path

from core import config

_IS_WINDOWS = os.name == 'nt'

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

kernel32 = None
if _IS_WINDOWS:
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
        ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int


class FileGuard:
    def __init__(self, paths: list[Path]):
        self.paths = [Path(p) for p in paths]
        self.handles: dict[str, int] = {}

    def open_all(self) -> None:
        if not _IS_WINDOWS:
            return
        for p in self.paths:
            self._open(p)

    def _open(self, path: Path) -> None:
        key = str(path)
        if key in self.handles:
            return
        try:
            if not path.exists():
                return
            is_dir = path.is_dir()
        except OSError:
            is_dir = False
        flags = FILE_FLAG_BACKUP_SEMANTICS if is_dir else FILE_ATTRIBUTE_NORMAL
        try:
            h = kernel32.CreateFileW(
                str(path), GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_WRITE,   # 故意不含 FILE_SHARE_DELETE
                None, OPEN_EXISTING, flags, None)
            if h and h != INVALID_HANDLE_VALUE:
                self.handles[key] = int(h)
            else:
                config.log(f'[fileguard] 打不开 {path}: {ctypes.get_last_error()}')
        except Exception as e:
            config.log(f'[fileguard] 打开 {path} 异常: {e}')

    def close(self) -> None:
        for h in list(self.handles.values()):
            try:
                if kernel32:
                    kernel32.CloseHandle(h)
            except Exception:
                pass
        self.handles.clear()

    @property
    def count(self) -> int:
        return len(self.handles)
