# -*- coding: utf-8 -*-
"""服务控制管理器（SCM）查询/启动/停止。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from core import config
from .win_service import SERVICE_STATUS, SERVICE_STOPPED, SERVICE_RUNNING

_IS_WINDOWS = os.name == 'nt'
SC_MANAGER_CONNECT = 0x0001
SC_MANAGER_CREATE_SERVICE = 0x0002
SC_MANAGER_ALL_ACCESS = 0xF003F
SERVICE_QUERY_STATUS = 0x0004
SERVICE_START = 0x0010
SERVICE_STOP = 0x0020
SERVICE_ALL_ACCESS = 0xF01FF
SERVICE_WIN32_OWN_PROCESS = 0x00000010
SERVICE_AUTO_START = 0x00000002
SERVICE_ERROR_NORMAL = 0x00000001
DELETE = 0x00010000
SERVICE_CONTROL_STOP = 0x00000001
SERVICE_CONTROL_INTERROGATE = 0x00000004
SERVICE_CONFIG_DESCRIPTION = 1
SERVICE_CONFIG_FAILURE_ACTIONS = 2
SC_ACTION_RESTART = 1


class SERVICE_DESCRIPTIONW(ctypes.Structure):
    _fields_ = [('lpDescription', wintypes.LPWSTR)]


class SC_ACTION(ctypes.Structure):
    _fields_ = [('Type', wintypes.DWORD), ('Delay', wintypes.DWORD)]


class SERVICE_FAILURE_ACTIONSW(ctypes.Structure):
    _fields_ = [
        ('dwResetPeriod', wintypes.DWORD),
        ('lpRebootMsg', wintypes.LPWSTR),
        ('lpCommand', wintypes.LPWSTR),
        ('cActions', wintypes.DWORD),
        ('lpsaActions', ctypes.POINTER(SC_ACTION)),
    ]

advapi32 = None
if _IS_WINDOWS:
    advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
    advapi32.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                                        wintypes.DWORD]
    advapi32.OpenSCManagerW.restype = ctypes.c_void_p
    advapi32.OpenServiceW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                      wintypes.DWORD]
    advapi32.OpenServiceW.restype = ctypes.c_void_p
    advapi32.CloseServiceHandle.argtypes = [ctypes.c_void_p]
    advapi32.CloseServiceHandle.restype = wintypes.BOOL
    advapi32.QueryServiceStatus.argtypes = [ctypes.c_void_p,
                                            ctypes.POINTER(SERVICE_STATUS)]
    advapi32.QueryServiceStatus.restype = wintypes.BOOL
    advapi32.StartServiceW.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.c_void_p]
    advapi32.StartServiceW.restype = wintypes.BOOL
    advapi32.ControlService.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.POINTER(SERVICE_STATUS)]
    advapi32.ControlService.restype = wintypes.BOOL
    advapi32.CreateServiceW.argtypes = [
        ctypes.c_void_p, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR,
        wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD), wintypes.LPCWSTR,
        wintypes.LPCWSTR, wintypes.LPCWSTR,
    ]
    advapi32.CreateServiceW.restype = ctypes.c_void_p
    advapi32.DeleteService.argtypes = [ctypes.c_void_p]
    advapi32.DeleteService.restype = wintypes.BOOL
    advapi32.ChangeServiceConfig2W.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                               ctypes.c_void_p]
    advapi32.ChangeServiceConfig2W.restype = wintypes.BOOL


def _open(name: str, access: int):
    scm = advapi32.OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
    if not scm:
        return None, None
    svc = advapi32.OpenServiceW(scm, name, access)
    if not svc:
        advapi32.CloseServiceHandle(scm)
        return None, None
    return scm, svc


def service_state(name: str) -> int | None:
    if not _IS_WINDOWS:
        return None
    scm, svc = _open(name, SERVICE_QUERY_STATUS)
    if not svc:
        return None
    try:
        st = SERVICE_STATUS()
        if not advapi32.QueryServiceStatus(svc, ctypes.byref(st)):
            return None
        return int(st.dwCurrentState)
    finally:
        advapi32.CloseServiceHandle(svc)
        advapi32.CloseServiceHandle(scm)


def is_running(name: str) -> bool:
    return service_state(name) == SERVICE_RUNNING


def wait_stopped(name: str, timeout: float = 20.0) -> bool:
    """等待服务进入 STOPPED；服务不存在也视为已停止。"""
    if not _IS_WINDOWS:
        return True
    import time as _time
    end = _time.time() + timeout
    while _time.time() < end:
        st = service_state(name)
        if st is None or st == SERVICE_STOPPED:
            return True
        _time.sleep(0.5)
    return False


def start(name: str) -> bool:
    if not _IS_WINDOWS:
        return False
    scm, svc = _open(name, SERVICE_START | SERVICE_QUERY_STATUS)
    if not svc:
        return False
    try:
        ok = bool(advapi32.StartServiceW(svc, 0, None))
        err = ctypes.get_last_error()
        if not ok and err not in (0, 1056):   # 1056 = already running
            config.log(f'[scm] 启动 {name} 失败: {err}')
        return ok or err == 1056
    finally:
        advapi32.CloseServiceHandle(svc)
        advapi32.CloseServiceHandle(scm)


def create_service(name: str, display: str, bin_path: str,
                   account: str | None = None,
                   dependency: str | None = None) -> bool:
    """创建自动启动服务；account=None 表示 LocalSystem。"""
    if not _IS_WINDOWS:
        return False
    scm = advapi32.OpenSCManagerW(None, None,
                                  SC_MANAGER_CONNECT | SC_MANAGER_CREATE_SERVICE)
    if not scm:
        config.log(f'[scm] OpenSCManager 失败: {ctypes.get_last_error()}')
        return False
    svc = None
    try:
        deps = None
        if dependency:
            deps = ctypes.create_unicode_buffer(dependency + '\0')
        svc = advapi32.CreateServiceW(
            scm, name, display, SERVICE_ALL_ACCESS,
            SERVICE_WIN32_OWN_PROCESS, SERVICE_AUTO_START, SERVICE_ERROR_NORMAL,
            bin_path, None, None, deps, account, None)
        if not svc:
            config.log(f'[scm] CreateService {name} 失败: {ctypes.get_last_error()}')
            return False
        desc = SERVICE_DESCRIPTIONW()
        desc.lpDescription = display
        advapi32.ChangeServiceConfig2W(svc, SERVICE_CONFIG_DESCRIPTION,
                                       ctypes.byref(desc))
        actions = (SC_ACTION * 3)()
        for i in range(3):
            actions[i].Type = SC_ACTION_RESTART
            actions[i].Delay = 1000
        fa = SERVICE_FAILURE_ACTIONSW()
        fa.dwResetPeriod = 86400
        fa.cActions = 3
        fa.lpsaActions = actions
        advapi32.ChangeServiceConfig2W(svc, SERVICE_CONFIG_FAILURE_ACTIONS,
                                       ctypes.byref(fa))
        return True
    except Exception as e:
        config.log(f'[scm] 创建服务 {name} 异常: {e}')
        return False
    finally:
        if svc:
            advapi32.CloseServiceHandle(svc)
        advapi32.CloseServiceHandle(scm)


def delete_service(name: str) -> bool:
    if not _IS_WINDOWS:
        return False
    scm, svc = _open(name, DELETE | SERVICE_STOP | SERVICE_QUERY_STATUS)
    if not svc:
        return False
    try:
        st = SERVICE_STATUS()
        advapi32.ControlService(svc, SERVICE_CONTROL_STOP, ctypes.byref(st))
        ok = bool(advapi32.DeleteService(svc))
        if not ok:
            config.log(f'[scm] DeleteService {name} 失败: {ctypes.get_last_error()}')
        return ok
    finally:
        advapi32.CloseServiceHandle(svc)
        advapi32.CloseServiceHandle(scm)


def stop(name: str) -> bool:
    if not _IS_WINDOWS:
        return False
    scm, svc = _open(name, SERVICE_STOP | SERVICE_QUERY_STATUS)
    if not svc:
        return False
    try:
        st = SERVICE_STATUS()
        ok = bool(advapi32.ControlService(svc, SERVICE_CONTROL_STOP,
                                          ctypes.byref(st)))
        err = ctypes.get_last_error()
        if not ok and err not in (0, 1062):   # 1062 = not started
            config.log(f'[scm] 停止 {name} 失败: {err}')
        return ok or err == 1062
    finally:
        advapi32.CloseServiceHandle(svc)
        advapi32.CloseServiceHandle(scm)
