# -*- coding: utf-8 -*-
"""Win32 原生能力：托盘图标、全局热键、单实例、多显示器、DPI。

只依赖 ctypes；非 Windows 平台可安全导入（功能降级为空实现），
方便在 Linux 上跑纯逻辑测试。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import ctypes
import os
import queue
import threading
from pathlib import Path

from core import hotkey as hk

_IS_WINDOWS = os.name == 'nt'

# ---------------------------------------------------------------- 基础类型

DWORD = ctypes.c_ulong
UINT = ctypes.c_uint
BOOL = ctypes.c_int
HWND = ctypes.c_void_p
HICON = ctypes.c_void_p
HMENU = ctypes.c_void_p
HINSTANCE = ctypes.c_void_p
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t
WCHAR = ctypes.c_wchar
WNDPROC = None
if _IS_WINDOWS:
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)
    MONITORENUMPROC = ctypes.WINFUNCTYPE(BOOL, ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_void_p, LPARAM)
else:
    MONITORENUMPROC = ctypes.CFUNCTYPE(BOOL, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_void_p, LPARAM)


class RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


class MSG(ctypes.Structure):
    _fields_ = [('hwnd', HWND), ('message', UINT), ('wParam', WPARAM),
                ('lParam', LPARAM), ('time', DWORD), ('pt', POINT)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [('style', UINT), ('lpfnWndProc', ctypes.c_void_p),
                ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int),
                ('hInstance', HINSTANCE), ('hIcon', HICON), ('hCursor', ctypes.c_void_p),
                ('hbrBackground', ctypes.c_void_p), ('lpszMenuName', ctypes.c_wchar_p),
                ('lpszClassName', ctypes.c_wchar_p)]


class GUID(ctypes.Structure):
    _fields_ = [('Data1', DWORD), ('Data2', ctypes.c_ushort),
                ('Data3', ctypes.c_ushort), ('Data4', ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ('cbSize', DWORD), ('hWnd', HWND), ('uID', UINT), ('uFlags', UINT),
        ('uCallbackMessage', UINT), ('hIcon', HICON), ('szTip', WCHAR * 128),
        ('dwState', DWORD), ('dwStateMask', DWORD), ('szInfo', WCHAR * 256),
        ('uTimeoutOrVersion', UINT), ('szInfoTitle', WCHAR * 64),
        ('dwInfoFlags', DWORD), ('guidItem', GUID), ('hBalloonIcon', HICON),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', DWORD), ('rcMonitor', RECT), ('rcWork', RECT), ('dwFlags', DWORD)]


# ---------------------------------------------------------------- 常量

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
WM_COMMAND = 0x0111
WM_NULL = 0x0000
WM_CONTEXTMENU = 0x007B
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_HOTKEY = 0x0312
WM_APP = 0x8000

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_INFO = 0x10
NIIF_INFO = 0x01
NIIF_WARNING = 0x02

TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
MF_STRING = 0x0000
MF_GRAYED = 0x0001
MF_CHECKED = 0x0008
MF_SEPARATOR = 0x0800

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
IDI_APPLICATION = 32512

MOD_NOREPEAT = 0x4000
ERROR_ALREADY_EXISTS = 183

GA_ROOT = 2
HWND_TOPMOST = -1
SWP_SHOWWINDOW = 0x0040
SW_SHOWNOACTIVATE = 4

#: 托盘回调消息（WM_APP + 20）
WM_TRAY = WM_APP + 20
TRAY_UID = 1


# ---------------------------------------------------------------- DLL

user32 = None
kernel32 = None
shell32 = None

if _IS_WINDOWS:
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    shell32 = ctypes.WinDLL('shell32', use_last_error=True)

    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = ctypes.c_ushort
    user32.CreateWindowExW.argtypes = [
        DWORD, ctypes.c_wchar_p, ctypes.c_wchar_p, DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        HWND, HMENU, HINSTANCE, ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = HWND
    user32.DefWindowProcW.argtypes = [HWND, UINT, WPARAM, LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.DestroyWindow.argtypes = [HWND]
    user32.DestroyWindow.restype = BOOL
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), HWND, UINT, UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), HWND, UINT, UINT, UINT]
    user32.PeekMessageW.restype = BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.PostMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
    user32.PostMessageW.restype = BOOL
    user32.PostThreadMessageW.argtypes = [DWORD, UINT, WPARAM, LPARAM]
    user32.PostThreadMessageW.restype = BOOL
    user32.LoadImageW.argtypes = [HINSTANCE, ctypes.c_wchar_p, UINT, ctypes.c_int,
                                  ctypes.c_int, UINT]
    user32.LoadImageW.restype = HICON
    user32.LoadIconW.argtypes = [HINSTANCE, ctypes.c_void_p]
    user32.LoadIconW.restype = HICON
    user32.DestroyIcon.argtypes = [HICON]
    user32.DestroyIcon.restype = BOOL
    user32.CreatePopupMenu.argtypes = []
    user32.CreatePopupMenu.restype = HMENU
    user32.AppendMenuW.argtypes = [HMENU, UINT, ctypes.c_size_t, ctypes.c_wchar_p]
    user32.AppendMenuW.restype = BOOL
    user32.DestroyMenu.argtypes = [HMENU]
    user32.DestroyMenu.restype = BOOL
    user32.TrackPopupMenu.argtypes = [HMENU, UINT, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, HWND, ctypes.c_void_p]
    user32.TrackPopupMenu.restype = UINT
    user32.SetForegroundWindow.argtypes = [HWND]
    user32.SetForegroundWindow.restype = BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorPos.restype = BOOL
    user32.RegisterHotKey.argtypes = [HWND, ctypes.c_int, UINT, UINT]
    user32.RegisterHotKey.restype = BOOL
    user32.UnregisterHotKey.argtypes = [HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = BOOL
    user32.EnumDisplayMonitors.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                           MONITORENUMPROC, LPARAM]
    user32.EnumDisplayMonitors.restype = BOOL
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
    user32.GetMonitorInfoW.restype = BOOL
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetAncestor.argtypes = [HWND, UINT]
    user32.GetAncestor.restype = HWND
    user32.SetWindowPos.argtypes = [HWND, HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, UINT]
    user32.SetWindowPos.restype = BOOL
    user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
    user32.ShowWindow.restype = BOOL

    kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetModuleHandleW.restype = HINSTANCE
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = DWORD
    kernel32.SetThreadExecutionState.argtypes = [UINT]
    kernel32.SetThreadExecutionState.restype = UINT
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, BOOL, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = BOOL

    shell32.Shell_NotifyIconW.argtypes = [DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = BOOL


# ---------------------------------------------------------------- 基础工具

_mutex_handle = None


def set_dpi_awareness() -> None:
    """尽量开启 Per-Monitor DPI 感知，避免高分屏下界面模糊。"""
    if not _IS_WINDOWS:
        return
    try:
        shcore = ctypes.WinDLL('shcore')
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.c_long
        shcore.SetProcessDpiAwareness(2)   # PROCESS_PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def acquire_single_instance(name: str = 'PCTimeLite_SingleInstance') -> bool:
    """创建命名互斥体；已有实例时返回 False。句柄需要活到进程结束。"""
    global _mutex_handle
    if not _IS_WINDOWS:
        return True
    _mutex_handle = kernel32.CreateMutexW(None, False, f'Local\\{name}')
    if not _mutex_handle:
        return True
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def primary_rect() -> tuple[int, int, int, int]:
    """主显示器矩形 (x, y, w, h)。"""
    if not _IS_WINDOWS:
        return 0, 0, 1280, 800
    w = max(1, int(user32.GetSystemMetrics(0)))
    h = max(1, int(user32.GetSystemMetrics(1)))
    return 0, 0, w, h


def place_topmost(widget, rect: tuple[int, int, int, int]) -> None:
    """把 Tk 顶层窗口放到指定显示器矩形（支持负坐标的副屏）。"""
    x, y, w, h = rect
    try:
        widget.update_idletasks()
    except Exception:
        pass
    if not _IS_WINDOWS:
        try:
            widget.geometry(f'{w}x{h}+{max(0, x)}+{max(0, y)}')
        except Exception:
            pass
        return
    try:
        hwnd = user32.GetAncestor(widget.winfo_id(), GA_ROOT)
        if hwnd:
            user32.SetWindowPos(hwnd, HWND_TOPMOST, int(x), int(y),
                                int(w), int(h), SWP_SHOWWINDOW)
            return
    except Exception:
        pass
    try:
        widget.geometry(f'{w}x{h}+{max(0, x)}+{max(0, y)}')
    except Exception:
        pass


ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def keep_awake(on: bool) -> None:
    """锁屏期间阻止系统休眠/关闭显示器，更像真的死机画面。"""
    if not _IS_WINDOWS:
        return
    try:
        flags = ES_CONTINUOUS
        if on:
            flags |= ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
        kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


def show_no_activate(widget) -> None:
    """显示窗口但不抢焦点（提醒横幅用）。"""
    try:
        widget.update_idletasks()
    except Exception:
        pass
    if not _IS_WINDOWS:
        try:
            widget.deiconify()
        except Exception:
            pass
        return
    try:
        hwnd = user32.GetAncestor(widget.winfo_id(), GA_ROOT)
        if hwnd:
            user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            return
    except Exception:
        pass
    try:
        widget.deiconify()
    except Exception:
        pass


def list_monitors() -> list[tuple[int, int, int, int]]:
    """枚举所有显示器矩形；失败时退回主屏。"""
    if not _IS_WINDOWS:
        return [primary_rect()]
    out: list[tuple[int, int, int, int]] = []

    def _cb(hmon, hdc, lprect, lparam):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            out.append((int(r.left), int(r.top),
                        max(1, int(r.right - r.left)),
                        max(1, int(r.bottom - r.top))))
        return True

    proc = MONITORENUMPROC(_cb)
    try:
        user32.EnumDisplayMonitors(None, None, proc, 0)
    except Exception:
        pass
    return out or [primary_rect()]


# ---------------------------------------------------------------- 全局热键

class HotkeyManager:
    """在独立线程里注册 RegisterHotKey，命中后向事件队列投递 ('hotkey',)。"""

    def __init__(self, events: queue.Queue, hotkey_id: int = 0xB001):
        self.events = events
        self.hotkey_id = int(hotkey_id)
        self._thread: threading.Thread | None = None
        self._tid: int | None = None
        self._ready = threading.Event()
        self._ok = False
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._ok)

    def start(self, text: str) -> bool:
        self.stop()
        parsed = hk.parse_hotkey(text)
        if parsed is None or not _IS_WINDOWS:
            return False
        mods, vk = parsed
        self._ready.clear()
        self._ok = False
        self._thread = threading.Thread(target=self._run, args=(mods, vk),
                                        daemon=True, name='HotkeyManager')
        self._thread.start()
        self._ready.wait(2.0)
        return bool(self._ok)

    def reconfigure(self, text: str) -> bool:
        return self.start(text)

    def stop(self) -> None:
        with self._lock:
            t, tid = self._thread, self._tid
            self._thread = None
            self._tid = None
            self._ok = False
        if t and t.is_alive():
            try:
                if tid:
                    user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
            except Exception:
                pass
            t.join(2.0)

    def _run(self, mods: int, vk: int) -> None:
        msg = MSG()
        ok = False
        try:
            self._tid = int(kernel32.GetCurrentThreadId())
            # 先创建线程消息队列，RegisterHotKey(hwnd=None) 才能把 WM_HOTKEY 投递进来
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
            ok = bool(user32.RegisterHotKey(None, self.hotkey_id,
                                            int(mods) | MOD_NOREPEAT, int(vk)))
        except Exception:
            ok = False
        self._ok = ok
        self._ready.set()
        if not ok:
            return
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    try:
                        self.events.put(('hotkey',))
                    except Exception:
                        pass
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            try:
                user32.UnregisterHotKey(None, self.hotkey_id)
            except Exception:
                pass


# ---------------------------------------------------------------- 托盘图标

class TrayIcon:
    """Windows 通知区域图标。

    菜单项由主线程通过 set_menu() 下发快照，托盘线程在右键时展示；
    菜单命令通过 events 队列回传 ('tray', id)，双击回传 ('tray_open',)。
    """

    def __init__(self, events: queue.Queue, icon_path: str | Path | None = None):
        self.events = events
        self.icon_path = str(icon_path) if icon_path else ''
        self._thread: threading.Thread | None = None
        self._hwnd = None
        self._tid: int | None = None
        self._ready = threading.Event()
        self._ok = False
        self._visible = False
        self._icon = None
        self._owned_icon = False
        self._nid = NOTIFYICONDATAW()
        self._nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self._nid.uID = TRAY_UID
        self._nid.uCallbackMessage = WM_TRAY
        self._lock = threading.RLock()
        self._menu: list[dict] = []
        self._menu_lock = threading.Lock()
        self._wndproc = None
        self._last_tip = None

    # ------------------------------------------------------------ 生命周期

    def start(self, visible: bool = True) -> bool:
        if not _IS_WINDOWS:
            return False
        self._ready.clear()
        self._ok = False
        self._thread = threading.Thread(target=self._run, daemon=True, name='TrayIcon')
        self._thread.start()
        self._ready.wait(3.0)
        if self._ok and visible:
            self.set_visible(True)
        return bool(self._ok)

    def stop(self) -> None:
        t = self._thread
        try:
            self.set_visible(False)
        except Exception:
            pass
        if t and t.is_alive():
            try:
                if self._hwnd:
                    user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass
            t.join(2.0)
        self._thread = None
        self._hwnd = None

    # ------------------------------------------------------------ 线程

    def _run(self) -> None:
        try:
            self._tid = int(kernel32.GetCurrentThreadId())
            hinst = kernel32.GetModuleHandleW(None)
            cls_name = f'PCTimeLiteTray_{os.getpid()}'

            self._wndproc = WNDPROC(self._wnd_proc)
            wc = WNDCLASSW()
            wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
            wc.hInstance = hinst
            wc.lpszClassName = cls_name
            if not user32.RegisterClassW(ctypes.byref(wc)):
                self._ok = False
                return

            self._hwnd = user32.CreateWindowExW(
                0, cls_name, 'PCTimeLiteTray', 0, 0, 0, 0, 0,
                None, None, hinst, None)
            if not self._hwnd:
                self._ok = False
                return

            self._nid.hWnd = self._hwnd
            self._icon = self._load_icon()
            self._nid.hIcon = self._icon or 0
            self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            self._nid.szTip = '儿童电脑使用时长控制'
            self._ok = True
        except Exception:
            self._ok = False
        finally:
            self._ready.set()
        if not self._ok:
            return

        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._owned_icon and self._icon:
            try:
                user32.DestroyIcon(self._icon)
            except Exception:
                pass
        self._icon = None

    def _load_icon(self):
        icon = None
        if self.icon_path and os.path.exists(self.icon_path):
            try:
                icon = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 0, 0,
                                         LR_LOADFROMFILE | LR_DEFAULTSIZE)
                self._owned_icon = bool(icon)
            except Exception:
                icon = None
        if not icon:
            try:
                icon = user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
                self._owned_icon = False
            except Exception:
                icon = None
        return icon

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                ev = int(lparam) & 0xFFFF
                if ev in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._show_menu(hwnd)
                    return 0
                if ev == WM_LBUTTONDBLCLK:
                    self.events.put(('tray_open',))
                    return 0
            elif msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            elif msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ------------------------------------------------------------ 菜单

    def set_menu(self, items: list[dict]) -> None:
        with self._menu_lock:
            self._menu = list(items or [])

    def _show_menu(self, hwnd) -> None:
        with self._menu_lock:
            items = list(self._menu)
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            for it in items:
                if it.get('sep'):
                    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                    continue
                flags = MF_STRING
                if not it.get('enabled', True):
                    flags |= MF_GRAYED
                if it.get('checked'):
                    flags |= MF_CHECKED
                user32.AppendMenuW(menu, flags, int(it.get('id', 0)),
                                   str(it.get('label', '')))
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            user32.SetForegroundWindow(hwnd)
            cmd = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                        pt.x, pt.y, 0, hwnd, None)
            user32.PostMessageW(hwnd, WM_NULL, 0, 0)
            if cmd:
                self.events.put(('tray', int(cmd)))
        finally:
            user32.DestroyMenu(menu)

    # ------------------------------------------------------------ 图标状态

    def set_visible(self, visible: bool) -> bool:
        if not _IS_WINDOWS or not self._hwnd:
            return False
        with self._lock:
            if visible:
                if self._visible:
                    return True
                ok = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid)))
                self._visible = ok
                return ok
            if not self._visible:
                return True
            ok = bool(shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid)))
            self._visible = False
            return ok

    @property
    def visible(self) -> bool:
        return self._visible

    def set_tooltip(self, text: str) -> None:
        if not _IS_WINDOWS:
            return
        tip = str(text or '')[:127]
        if tip == self._last_tip:
            return
        self._last_tip = tip
        with self._lock:
            self._nid.szTip = tip
            if self._visible:
                self._nid.uFlags = NIF_TIP
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))
                self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP

    def notify(self, title: str, text: str, timeout_ms: int = 8000,
               warning: bool = False) -> bool:
        if not _IS_WINDOWS or not self._visible:
            return False
        with self._lock:
            self._nid.szInfoTitle = str(title or '')[:63]
            self._nid.szInfo = str(text or '')[:255]
            self._nid.uTimeoutOrVersion = int(timeout_ms)
            self._nid.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
            self._nid.uFlags = NIF_INFO
            ok = bool(shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid)))
            self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            return ok
