# -*- coding: utf-8 -*-
"""Windows 底层守卫：全局键盘钩子、进程存活检测。

锁屏期间吞掉**所有**按键，模拟死机；显示解锁密码框时由 UI 临时
set_active(False)，让家长可以输入密码。

全部使用 ctypes，无需 pywin32。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes

from . import config

_IS_WINDOWS = os.name == 'nt'

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12          # Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ('vkCode', wintypes.DWORD),
        ('scanCode', wintypes.DWORD),
        ('flags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]


_CALLBACK = getattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE)
HOOKPROC = _CALLBACK(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32 = None
kernel32 = None

if _IS_WINDOWS:
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

    # ⚠️ 64 位下必须显式声明 restype/argtypes，否则句柄被截断成 32 位，
    #    SetWindowsHookExW 会返回 126 (ERROR_MOD_NOT_FOUND) 而静默失败。
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.GetMessageW.restype = ctypes.c_int
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), ctypes.c_void_p, wintypes.UINT, wintypes.UINT]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]

    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


class KeyboardGuard(threading.Thread):
    """低级键盘钩子。

    - 普通锁屏模式：吞掉所有按键，模拟死机；
    - 密码输入模式（set_typing(True)）：只放行普通输入，仍吞掉
      Win / Ctrl / Alt 及其组合键（Alt+Tab、Ctrl+Shift+Esc 等）。
    （Ctrl+Alt+Del 是系统安全序列，用户态无法屏蔽）
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name='KeyboardGuard')
        self._active = False
        self._typing = False
        self._stop_flag = False
        self._hook = None
        self._tid: int | None = None
        self._proc = HOOKPROC(self._callback)  # 保持引用，防止被 GC

    # ---- 外部接口 ----
    def set_active(self, on: bool) -> None:
        self._active = bool(on)

    def set_typing(self, on: bool) -> None:
        """密码框显示期间放行普通字符，但不能用系统快捷键。"""
        self._typing = bool(on)
        if on:
            self._active = True

    def stop(self) -> None:
        self._stop_flag = True
        if self._tid and user32 is not None:
            try:
                user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            except Exception:
                pass

    # ---- 线程体 ----
    def run(self) -> None:
        if user32 is None or kernel32 is None:
            config.log('[guard] 非 Windows 环境，键盘钩子不启用')
            return
        try:
            self._tid = kernel32.GetCurrentThreadId()
            hmod = kernel32.GetModuleHandleW(None)
            self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, hmod, 0)
            if not self._hook:
                config.log(f'[guard] 键盘钩子安装失败 err={ctypes.get_last_error()}')
                return
            config.log('[guard] 键盘钩子已安装')
            msg = wintypes.MSG()
            while not self._stop_flag:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret is None or ret == 0 or ret == -1:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as e:
            config.log(f'[guard] 异常: {e}')
        finally:
            if self._hook:
                try:
                    user32.UnhookWindowsHookEx(self._hook)
                except Exception:
                    pass
                self._hook = None

    # ---- 钩子回调 ----
    def _callback(self, n_code, w_param, l_param):
        if user32 is None:
            return 0
        try:
            if n_code == 0 and self._active:
                if not self._typing:
                    return 1   # 锁屏期间吞掉所有按键（含 keyup），模拟死机
                # 密码输入模式：Win/Ctrl/Alt 本身及其任何组合都吞掉，
                # 其余普通按键放行（字母、数字、Shift、退格、回车、Esc 等）。
                for vk_mod in (VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN):
                    if user32.GetAsyncKeyState(vk_mod) & 0x8000:
                        return 1
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if int(kb.vkCode) in (VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN):
                    return 1
        except Exception:
            return 1
        return user32.CallNextHookEx(None, n_code, w_param, l_param)


# ---------------------------------------------------------------- 进程工具

def pid_alive(pid: int) -> bool:
    """判断进程是否还活着（不用 tasklist，避免弹黑框）。"""
    if not _IS_WINDOWS:
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    try:
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        kernel32.CloseHandle(h)
        return bool(ok) and code.value == STILL_ACTIVE
    except Exception:
        return False
