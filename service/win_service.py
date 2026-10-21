# -*- coding: utf-8 -*-
"""最小 Windows 服务封装（ctypes，无需 pywin32）。

非 Windows 平台会退化为「直接运行 setup/run/stop」，方便在 Linux 上
测试 Web 核心与代理逻辑；真正的 SCM 行为只在 Windows 上生效。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes

from core import config

_IS_WINDOWS = os.name == 'nt'

DWORD = ctypes.c_ulong
BOOL = ctypes.c_int
HANDLE = ctypes.c_void_p

SERVICE_WIN32_OWN_PROCESS = 0x00000010
SERVICE_STOPPED = 0x00000001
SERVICE_START_PENDING = 0x00000002
SERVICE_STOP_PENDING = 0x00000003
SERVICE_RUNNING = 0x00000004
SERVICE_ACCEPT_STOP = 0x00000001
SERVICE_ACCEPT_SHUTDOWN = 0x00000004
SERVICE_CONTROL_STOP = 0x00000001
SERVICE_CONTROL_INTERROGATE = 0x00000004
SERVICE_CONTROL_SHUTDOWN = 0x00000005
ERROR_FAILED_SERVICE_CONTROLLER_CONNECT = 1063

_WINFUNCTYPE = getattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE)
HANDLER_EX = _WINFUNCTYPE(DWORD, DWORD, DWORD, ctypes.c_void_p, ctypes.c_void_p)
SERVICE_MAIN_FN = _WINFUNCTYPE(None, DWORD, ctypes.POINTER(wintypes.LPWSTR))


class SERVICE_STATUS(ctypes.Structure):
    _fields_ = [
        ('dwServiceType', DWORD),
        ('dwCurrentState', DWORD),
        ('dwControlsAccepted', DWORD),
        ('dwWin32ExitCode', DWORD),
        ('dwServiceSpecificExitCode', DWORD),
        ('dwCheckPoint', DWORD),
        ('dwWaitHint', DWORD),
    ]


class SERVICE_TABLE_ENTRYW(ctypes.Structure):
    _fields_ = [('lpServiceName', wintypes.LPWSTR),
                ('lpServiceProc', SERVICE_MAIN_FN)]


advapi32 = None
if _IS_WINDOWS:
    advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
    advapi32.StartServiceCtrlDispatcherW.argtypes = [ctypes.POINTER(SERVICE_TABLE_ENTRYW)]
    advapi32.StartServiceCtrlDispatcherW.restype = BOOL
    advapi32.RegisterServiceCtrlHandlerExW.argtypes = [wintypes.LPCWSTR, HANDLER_EX,
                                                       ctypes.c_void_p]
    advapi32.RegisterServiceCtrlHandlerExW.restype = HANDLE
    advapi32.SetServiceStatus.argtypes = [HANDLE, ctypes.POINTER(SERVICE_STATUS)]
    advapi32.SetServiceStatus.restype = BOOL

_NAME = ''
_SETUP = None
_RUN = None
_STOP = None
_STOP_EVENT: threading.Event | None = None
_STATUS_HANDLE: HANDLE | None = None
_STATUS = SERVICE_STATUS()
_HANDLER_CB = None
_MAIN_CB = None
_EXIT_CODE = 0


def _report(state: int, controls: int = 0, wait_hint: int = 0,
            checkpoint: int = 0) -> None:
    if not _IS_WINDOWS or not _STATUS_HANDLE:
        return
    _STATUS.dwServiceType = SERVICE_WIN32_OWN_PROCESS
    _STATUS.dwCurrentState = state
    _STATUS.dwControlsAccepted = controls
    _STATUS.dwWin32ExitCode = _EXIT_CODE
    _STATUS.dwServiceSpecificExitCode = 0
    _STATUS.dwCheckPoint = checkpoint
    _STATUS.dwWaitHint = wait_hint
    try:
        advapi32.SetServiceStatus(_STATUS_HANDLE, ctypes.byref(_STATUS))
    except Exception:
        pass


def _handler(control: int, event_type: int, event_data: ctypes.c_void_p,
             context: ctypes.c_void_p) -> int:
    global _EXIT_CODE
    if control in (SERVICE_CONTROL_STOP, SERVICE_CONTROL_SHUTDOWN):
        if _STOP_EVENT is not None and not _STOP_EVENT.is_set():
            _STOP_EVENT.set()
            _report(SERVICE_STOP_PENDING, wait_hint=20000, checkpoint=1)
    elif control == SERVICE_CONTROL_INTERROGATE:
        _report(SERVICE_RUNNING, controls=SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN)
    return 0


def _service_main(argc, argv) -> None:
    global _STATUS_HANDLE, _STOP_EVENT, _EXIT_CODE
    if not _IS_WINDOWS:
        return
    _STOP_EVENT = threading.Event()
    _STATUS_HANDLE = advapi32.RegisterServiceCtrlHandlerExW(_NAME, _HANDLER_CB, None)
    if not _STATUS_HANDLE:
        config.log(f'[service] RegisterServiceCtrlHandlerExW 失败: {ctypes.get_last_error()}')
        return
    _report(SERVICE_START_PENDING, wait_hint=20000, checkpoint=1)
    try:
        if _SETUP is not None:
            _SETUP()
        _report(SERVICE_RUNNING, controls=SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN)
        _RUN(_STOP_EVENT)
    except Exception as e:
        _EXIT_CODE = 1
        config.log(f'[service] {_NAME} 异常: {e}')
    finally:
        try:
            if _STOP is not None:
                _STOP()
        except Exception as e:
            config.log(f'[service] {_NAME} 停止清理异常: {e}')
        _report(SERVICE_STOPPED)
        config.log(f'[service] {_NAME} 已停止')


def _run_console(name: str, setup, run, stop) -> int:
    config.log(f'[service] {name} 控制台模式启动')
    ev = threading.Event()
    if setup:
        setup()
    try:
        run(ev)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        config.log(f'[service] {name} 异常: {e}')
        return 1
    finally:
        if stop:
            try:
                stop()
            except Exception as e:
                config.log(f'[service] {name} 停止清理异常: {e}')
    return 0


def run_service(name: str, setup, run, stop) -> int:
    """入口：由 SCM 调用；非 Windows 或 PCTL_SERVICE_CONSOLE=1 时直接运行。"""
    global _NAME, _SETUP, _RUN, _STOP, _HANDLER_CB, _MAIN_CB
    _NAME, _SETUP, _RUN, _STOP = name, setup, run, stop
    if not _IS_WINDOWS or os.environ.get('PCTL_SERVICE_CONSOLE', '') == '1':
        return _run_console(name, setup, run, stop)

    _HANDLER_CB = HANDLER_EX(_handler)
    _MAIN_CB = SERVICE_MAIN_FN(_service_main)
    table = (SERVICE_TABLE_ENTRYW * 2)()
    table[0].lpServiceName = name
    table[0].lpServiceProc = _MAIN_CB
    table[1].lpServiceName = None
    table[1].lpServiceProc = None
    ok = advapi32.StartServiceCtrlDispatcherW(table)
    if not ok:
        err = ctypes.get_last_error()
        config.log(f'[service] StartServiceCtrlDispatcherW 失败: {err}')
        if err == ERROR_FAILED_SERVICE_CONTROLLER_CONNECT:
            config.log('[service] 该进程不是由 SCM 启动的；调试可设 PCTL_SERVICE_CONSOLE=1')
        return 1
    return 0
