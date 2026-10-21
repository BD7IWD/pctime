# -*- coding: utf-8 -*-
"""对非管理员用户的策略：禁用任务管理器。

写入 HKEY_USERS\\<SID>\\...\\Policies\\System\\DisableTaskMgr。
策略只是提高门槛，不隐藏进程；守护服务会周期性恢复。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import os
from pathlib import Path

from core import config

_IS_WINDOWS = os.name == 'nt'
_POLICY_PATH = r'Software\Microsoft\Windows\CurrentVersion\Policies\System'


def policy_path(sid: str) -> str:
    return f'{sid}\\{_POLICY_PATH}'


def set_taskmgr(sid: str, disabled: bool) -> bool:
    if not _IS_WINDOWS or not sid:
        return False
    try:
        import winreg
        key = policy_path(sid)
        with winreg.CreateKeyEx(winreg.HKEY_USERS, key, 0,
                                winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, 'DisableTaskMgr', 0, winreg.REG_DWORD,
                              1 if disabled else 0)
        return True
    except Exception as e:
        config.log(f'[policy] 设置任务管理器策略失败 sid={sid}: {e}')
        return False


def _lookup_sid(username: str) -> str:
    if not _IS_WINDOWS or not username:
        return ''
    try:
        import ctypes
        from ctypes import wintypes
        advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
        advapi32.LookupAccountNameW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.LookupAccountNameW.restype = wintypes.BOOL
        advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                                    ctypes.POINTER(ctypes.c_void_p)]
        advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        n = wintypes.DWORD(0)
        d = wintypes.DWORD(0)
        use = wintypes.DWORD(0)
        advapi32.LookupAccountNameW(None, username, None, ctypes.byref(n),
                                    None, ctypes.byref(d), ctypes.byref(use))
        if not n.value:
            return ''
        sid = ctypes.create_string_buffer(n.value)
        dom = ctypes.create_unicode_buffer(max(1, d.value))
        if not advapi32.LookupAccountNameW(None, username, sid, ctypes.byref(n),
                                           dom, ctypes.byref(d), ctypes.byref(use)):
            return ''
        s = ctypes.c_void_p()
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(s)):
            return ''
        try:
            return ctypes.wstring_at(s.value)
        finally:
            ctypes.WinDLL('kernel32').LocalFree(s)
    except Exception:
        return ''


def clear_all() -> None:
    """卸载时清零所有已加载用户，并尝试加载磁盘上的 NTUSER.DAT。"""
    if not _IS_WINDOWS:
        return
    import winreg
    loaded: set[str] = set()
    try:
        with winreg.OpenKey(winreg.HKEY_USERS, '', 0, winreg.KEY_READ) as root:
            i = 0
            while True:
                try:
                    sid = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if not sid.startswith('S-1-'):
                    continue
                loaded.add(sid)
                try:
                    set_taskmgr(sid, False)
                except Exception:
                    pass
    except Exception as e:
        config.log(f'[policy] 清理已加载用户策略失败: {e}')

    # 未登录用户的策略仍在 NTUSER.DAT 里，卸载前临时加载清理
    try:
        from .wts import _enable_privilege
        _enable_privilege('SeRestorePrivilege')
        _enable_privilege('SeBackupPrivilege')
    except Exception:
        pass
    try:
        users = Path(os.environ.get('SystemDrive', 'C:') + '\\Users')
        for prof in users.iterdir():
            if not prof.is_dir() or prof.name in ('Public', 'Default', 'Default User', 'All Users'):
                continue
            hive = prof / 'NTUSER.DAT'
            if not hive.exists():
                continue
            sid = _lookup_sid(prof.name)
            if not sid or sid in loaded:
                continue
            try:
                winreg.LoadKey(winreg.HKEY_USERS, sid, str(hive))
            except Exception:
                continue
            try:
                set_taskmgr(sid, False)
            finally:
                try:
                    winreg.UnloadKey(winreg.HKEY_USERS, sid)
                except Exception:
                    pass
    except Exception as e:
        config.log(f'[policy] 清理磁盘用户策略失败: {e}')
