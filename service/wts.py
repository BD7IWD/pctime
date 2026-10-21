# -*- coding: utf-8 -*-
"""Windows 会话/令牌工具：

- 枚举活动会话，判断用户是否为管理员；
- 以 SYSTEM 身份在用户会话桌面启动代理（CreateProcessAsUser）；
- 结束某个会话中的代理进程；
- 代理心跳文件。

只在守护服务（LocalSystem）中使用。非 Windows 平台全部降级为空实现。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from pathlib import Path

from core import config
from . import paths

_IS_WINDOWS = os.name == 'nt'

DWORD = ctypes.c_ulong
BOOL = ctypes.c_int
HANDLE = ctypes.c_void_p

WTS_CURRENT_SERVER_HANDLE = 0
WTS_ACTIVE = 0
WTS_USER_NAME = 5
WTS_DOMAIN_NAME = 7

TOKEN_QUERY = 0x0008
TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_DUPLICATE = 0x0002
TOKEN_ASSIGN_PRIMARY = 0x0001
MAXIMUM_ALLOWED = 0x02000000
SECURITY_IMPERSONATION = 2
TOKEN_PRIMARY = 1
TOKEN_SESSION_ID = 12
SE_PRIVILEGE_ENABLED = 0x00000002
ERROR_SUCCESS = 0
ERROR_ALREADY_EXISTS = 183
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0
WIN_BUILTIN_ADMINISTRATORS = 26


class WTS_SESSION_INFOW(ctypes.Structure):
    _fields_ = [('SessionId', DWORD), ('pWinStationName', wintypes.LPWSTR),
                ('State', DWORD)]


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [('Sid', ctypes.c_void_p), ('Attributes', DWORD)]


class TOKEN_USER(ctypes.Structure):
    _fields_ = [('User', SID_AND_ATTRIBUTES)]


class LUID(ctypes.Structure):
    _fields_ = [('LowPart', DWORD), ('HighPart', ctypes.c_long)]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [('Luid', LUID), ('Attributes', DWORD)]


class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [('PrivilegeCount', DWORD), ('Privileges', LUID_AND_ATTRIBUTES * 1)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ('cb', DWORD), ('lpReserved', wintypes.LPWSTR), ('lpDesktop', wintypes.LPWSTR),
        ('lpTitle', wintypes.LPWSTR), ('dwX', DWORD), ('dwY', DWORD),
        ('dwXSize', DWORD), ('dwYSize', DWORD), ('dwXCountChars', DWORD),
        ('dwYCountChars', DWORD), ('dwFillAttribute', DWORD), ('dwFlags', DWORD),
        ('wShowWindow', ctypes.c_ushort), ('cbReserved2', ctypes.c_ushort),
        ('lpReserved2', ctypes.POINTER(ctypes.c_ubyte)),
        ('hStdInput', HANDLE), ('hStdOutput', HANDLE), ('hStdError', HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [('hProcess', HANDLE), ('hThread', HANDLE),
                ('dwProcessId', DWORD), ('dwThreadId', DWORD)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ('dwSize', DWORD), ('cntUsage', DWORD), ('th32ProcessID', DWORD),
        ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', DWORD),
        ('cntThreads', DWORD), ('th32ParentProcessID', DWORD),
        ('pcPriClassBase', ctypes.c_long), ('dwFlags', DWORD),
        ('szExeFile', wintypes.WCHAR * 260),
    ]


advapi32 = None
kernel32 = None
wtsapi32 = None
if _IS_WINDOWS:
    advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    wtsapi32 = ctypes.WinDLL('wtsapi32', use_last_error=True)

    wtsapi32.WTSEnumerateSessionsW.argtypes = [HANDLE, DWORD, DWORD,
                                               ctypes.POINTER(ctypes.c_void_p),
                                               ctypes.POINTER(DWORD)]
    wtsapi32.WTSEnumerateSessionsW.restype = BOOL
    wtsapi32.WTSQuerySessionInformationW.argtypes = [HANDLE, DWORD, ctypes.c_int,
                                                     ctypes.POINTER(ctypes.c_void_p),
                                                     ctypes.POINTER(DWORD)]
    wtsapi32.WTSQuerySessionInformationW.restype = BOOL
    wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]
    wtsapi32.WTSQueryUserToken.argtypes = [DWORD, ctypes.POINTER(HANDLE)]
    wtsapi32.WTSQueryUserToken.restype = BOOL

    advapi32.OpenProcessToken.argtypes = [HANDLE, DWORD, ctypes.POINTER(HANDLE)]
    advapi32.OpenProcessToken.restype = BOOL
    advapi32.GetTokenInformation.argtypes = [HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             DWORD, ctypes.POINTER(DWORD)]
    advapi32.GetTokenInformation.restype = BOOL
    advapi32.CheckTokenMembership.argtypes = [HANDLE, ctypes.c_void_p,
                                              ctypes.POINTER(BOOL)]
    advapi32.CheckTokenMembership.restype = BOOL
    advapi32.CreateWellKnownSid.argtypes = [ctypes.c_int, ctypes.c_void_p,
                                            ctypes.c_void_p, ctypes.POINTER(DWORD)]
    advapi32.CreateWellKnownSid.restype = BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                                ctypes.POINTER(ctypes.c_void_p)]
    advapi32.ConvertSidToStringSidW.restype = BOOL
    advapi32.DuplicateTokenEx.argtypes = [HANDLE, DWORD, ctypes.c_void_p,
                                          ctypes.c_int, ctypes.c_int,
                                          ctypes.POINTER(HANDLE)]
    advapi32.DuplicateTokenEx.restype = BOOL
    advapi32.SetTokenInformation.argtypes = [HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             DWORD]
    advapi32.SetTokenInformation.restype = BOOL
    advapi32.CreateProcessAsUserW.argtypes = [
        HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
        ctypes.c_void_p, BOOL, DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION),
    ]
    advapi32.CreateProcessAsUserW.restype = BOOL
    advapi32.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                                               ctypes.POINTER(LUID)]
    advapi32.LookupPrivilegeValueW.restype = BOOL
    advapi32.AdjustTokenPrivileges.argtypes = [HANDLE, BOOL,
                                               ctypes.POINTER(TOKEN_PRIVILEGES),
                                               DWORD, ctypes.c_void_p, ctypes.c_void_p]
    advapi32.AdjustTokenPrivileges.restype = BOOL

    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = HANDLE
    kernel32.CloseHandle.argtypes = [HANDLE]
    kernel32.CloseHandle.restype = BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.ProcessIdToSessionId.argtypes = [DWORD, ctypes.POINTER(DWORD)]
    kernel32.ProcessIdToSessionId.restype = BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = [DWORD, DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = HANDLE
    kernel32.Process32FirstW.argtypes = [HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = BOOL
    kernel32.Process32NextW.argtypes = [HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = BOOL
    kernel32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
    kernel32.OpenProcess.restype = HANDLE
    kernel32.TerminateProcess.argtypes = [HANDLE, ctypes.c_uint]
    kernel32.TerminateProcess.restype = BOOL
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = HANDLE
    kernel32.OpenMutexW.argtypes = [DWORD, BOOL, wintypes.LPCWSTR]
    kernel32.OpenMutexW.restype = HANDLE


def _close(h) -> None:
    try:
        if h:
            kernel32.CloseHandle(h)
    except Exception:
        pass


def _query_string(session: int, info_class: int) -> str:
    if not _IS_WINDOWS:
        return ''
    buf = ctypes.c_void_p()
    n = DWORD()
    try:
        if not wtsapi32.WTSQuerySessionInformationW(
                WTS_CURRENT_SERVER_HANDLE, session, info_class,
                ctypes.byref(buf), ctypes.byref(n)):
            return ''
        return ctypes.wstring_at(buf.value) if buf and buf.value else ''
    except Exception:
        return ''
    finally:
        if buf:
            try:
                wtsapi32.WTSFreeMemory(buf)
            except Exception:
                pass


def _token_sid(token) -> str:
    if not _IS_WINDOWS or not token:
        return ''
    try:
        n = DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(n))
        if not n.value:
            return ''
        buf = ctypes.create_string_buffer(n.value)
        if not advapi32.GetTokenInformation(token, 1, buf, n.value, ctypes.byref(n)):
            return ''
        tu = ctypes.cast(buf, ctypes.POINTER(TOKEN_USER)).contents
        s = ctypes.c_void_p()
        if not advapi32.ConvertSidToStringSidW(tu.User.Sid, ctypes.byref(s)):
            return ''
        try:
            return ctypes.wstring_at(s.value)
        finally:
            kernel32.LocalFree(s)
    except Exception:
        return ''


def _token_is_admin(token) -> bool:
    if not _IS_WINDOWS or not token:
        return False
    try:
        # UAC 过滤后的管理员令牌里 Administrators 是 deny-only，
        # CheckTokenMembership 可能返回 False；先看 ElevationType。
        et = ctypes.c_int(0)
        n = DWORD()
        if advapi32.GetTokenInformation(token, 18, ctypes.byref(et),
                                        ctypes.sizeof(et), ctypes.byref(n)):
            if et.value in (2, 3):      # TokenElevationTypeLimited / Full
                return True
        n = DWORD(0)
        advapi32.CreateWellKnownSid(WIN_BUILTIN_ADMINISTRATORS, None, None,
                                    ctypes.byref(n))
        if not n.value:
            return False
        sid = ctypes.create_string_buffer(n.value)
        if not advapi32.CreateWellKnownSid(WIN_BUILTIN_ADMINISTRATORS, None,
                                            sid, ctypes.byref(n)):
            return False
        member = BOOL(0)
        return bool(advapi32.CheckTokenMembership(token, sid, ctypes.byref(member))
                    and member.value)
    except Exception:
        return False


def list_active_sessions() -> list[dict]:
    """返回活动会话：[{id, user, domain, sid, is_admin}]。"""
    if not _IS_WINDOWS:
        return []
    _enable_privilege('SeTcbPrivilege')
    out: list[dict] = []
    p = ctypes.c_void_p()
    count = DWORD()
    try:
        if not wtsapi32.WTSEnumerateSessionsW(
                WTS_CURRENT_SERVER_HANDLE, 0, 1, ctypes.byref(p), ctypes.byref(count)):
            return []
        arr = ctypes.cast(p, ctypes.POINTER(WTS_SESSION_INFOW))
        for i in range(count.value):
            s = arr[i]
            if s.State != WTS_ACTIVE:
                continue
            user = _query_string(s.SessionId, WTS_USER_NAME)
            if not user:
                continue
            domain = _query_string(s.SessionId, WTS_DOMAIN_NAME)
            sid = ''
            is_admin = False
            tok = HANDLE()
            try:
                if wtsapi32.WTSQueryUserToken(s.SessionId, ctypes.byref(tok)) and tok:
                    sid = _token_sid(tok)
                    is_admin = _token_is_admin(tok)
            finally:
                _close(tok)
            out.append({'id': int(s.SessionId), 'user': user, 'domain': domain,
                        'sid': sid, 'is_admin': bool(is_admin)})
    except Exception as e:
        config.log(f'[wts] 枚举会话失败: {e}')
    finally:
        if p:
            try:
                wtsapi32.WTSFreeMemory(p)
            except Exception:
                pass
    return out


def _enable_privilege(name: str) -> bool:
    if not _IS_WINDOWS:
        return False
    tok = HANDLE()
    try:
        if not advapi32.OpenProcessToken(
                kernel32.GetCurrentProcess(),
                TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, ctypes.byref(tok)):
            return False
        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            return False
        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
        advapi32.AdjustTokenPrivileges(tok, False, ctypes.byref(tp), 0, None, None)
        return ctypes.get_last_error() == ERROR_SUCCESS
    except Exception:
        return False
    finally:
        _close(tok)


def _enable_tcb() -> bool:
    return _enable_privilege('SeTcbPrivilege')


def launch_agent(session: int, data: Path | None = None) -> bool:
    """以 SYSTEM 身份在指定用户会话的默认桌面启动代理。"""
    if not _IS_WINDOWS:
        return False
    data = data or paths.data_dir()
    exe = paths.installed_exe()
    _enable_privilege('SeTcbPrivilege')
    _enable_privilege('SeAssignPrimaryTokenPrivilege')
    _enable_privilege('SeIncreaseQuotaPrivilege')
    proc_tok = HANDLE()
    dup = HANDLE()
    try:
        if not advapi32.OpenProcessToken(
                kernel32.GetCurrentProcess(),
                TOKEN_DUPLICATE | TOKEN_QUERY | TOKEN_ASSIGN_PRIMARY,
                ctypes.byref(proc_tok)):
            config.log('[wts] OpenProcessToken 失败')
            return False
        if not advapi32.DuplicateTokenEx(
                proc_tok, MAXIMUM_ALLOWED, None, SECURITY_IMPERSONATION,
                TOKEN_PRIMARY, ctypes.byref(dup)):
            config.log('[wts] DuplicateTokenEx 失败')
            return False
        sid = DWORD(int(session))
        if not advapi32.SetTokenInformation(
                dup, TOKEN_SESSION_ID, ctypes.byref(sid), ctypes.sizeof(DWORD)):
            config.log(f'[wts] SetTokenInformation(session={session}) 失败: '
                       f'{ctypes.get_last_error()}')
            return False

        cmd = f'"{exe}" --agent --session {int(session)} --data-dir "{data}"'
        cmd_buf = ctypes.create_unicode_buffer(cmd)
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(STARTUPINFOW)
        si.lpDesktop = 'winsta0\\default'
        si.dwFlags = STARTF_USESHOWWINDOW
        si.wShowWindow = SW_HIDE
        pi = PROCESS_INFORMATION()
        ok = advapi32.CreateProcessAsUserW(
            dup, None, cmd_buf, None, None, False, 0, None,
            str(Path(exe).parent), ctypes.byref(si), ctypes.byref(pi))
        if not ok:
            config.log(f'[wts] CreateProcessAsUser(session={session}) 失败: '
                       f'{ctypes.get_last_error()}')
            return False
        config.log(f'[wts] 已在会话 {session} 启动代理 pid={pi.dwProcessId}')
        return True
    except Exception as e:
        config.log(f'[wts] 启动代理异常: {e}')
        return False
    finally:
        _close(dup)
        _close(proc_tok)


def agent_mutex_name(session: int) -> str:
    # 代理在用户会话内运行，Local\ 即可；守护进程用心跳文件判断存活
    return f'Local\\PCTimeAgent_{int(session)}'


def acquire_agent_mutex(session: int):
    """同一会话只允许一个代理；返回需要保持到进程结束的句柄。"""
    if not _IS_WINDOWS:
        return True
    try:
        h = kernel32.CreateMutexW(None, False, agent_mutex_name(session))
        if not h:
            return None
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(h)
            return None
        return h
    except Exception:
        return True


def release_agent_mutex(h) -> None:
    if _IS_WINDOWS and h and h is not True:
        _close(h)


def heartbeat_paths(data: Path, session: int) -> tuple[Path, Path]:
    rt = Path(data) / 'runtime'
    rt.mkdir(parents=True, exist_ok=True)
    return rt / f'agent_{int(session)}.pid', rt / f'agent_{int(session)}.hb'


def write_heartbeat(data: Path, session: int) -> None:
    pidf, hbf = heartbeat_paths(data, session)
    try:
        pidf.write_text(str(os.getpid()), encoding='utf-8')
        hbf.write_text(str(time.time()), encoding='utf-8')
    except Exception:
        pass


def clear_heartbeat(data: Path, session: int) -> None:
    for p in heartbeat_paths(data, session):
        try:
            p.unlink()
        except OSError:
            pass


def heartbeat_age(data: Path, session: int) -> float | None:
    _pidf, hbf = heartbeat_paths(data, session)
    try:
        return max(0.0, time.time() - hbf.stat().st_mtime)
    except OSError:
        return None


def read_agent_pid(data: Path, session: int) -> int:
    pidf, _hbf = heartbeat_paths(data, session)
    try:
        return int(pidf.read_text(encoding='utf-8').strip())
    except Exception:
        return 0


def _processes() -> list[tuple[int, int, str]]:
    if not _IS_WINDOWS:
        return []
    out = []
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return out
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if kernel32.Process32FirstW(snap, ctypes.byref(pe)):
            while True:
                sid = DWORD()
                if kernel32.ProcessIdToSessionId(pe.th32ProcessID, ctypes.byref(sid)):
                    out.append((int(pe.th32ProcessID), int(sid.value),
                                str(pe.szExeFile)))
                if not kernel32.Process32NextW(snap, ctypes.byref(pe)):
                    break
    finally:
        _close(snap)
    return out


def kill_pid(pid: int) -> bool:
    if not _IS_WINDOWS or not pid:
        return False
    h = kernel32.OpenProcess(PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION,
                             False, int(pid))
    if not h:
        return False
    try:
        return bool(kernel32.TerminateProcess(h, 1))
    finally:
        _close(h)


def kill_agent_pid(pid: int, session: int, exe_name: str) -> bool:
    """只结束 PID/会话/进程名都匹配的代理，避免 PID 复用误杀。"""
    if not _IS_WINDOWS or not pid:
        return False
    for p, sid, name in _processes():
        if p == int(pid) and sid == int(session) and name.lower() == exe_name.lower():
            return kill_pid(p)
    return False


def kill_agents(data: Path, session: int | None = None) -> None:
    """结束用户会话中的代理（保留 session 0 的服务进程）。"""
    if not _IS_WINDOWS:
        return
    exe_name = paths.installed_exe().name.lower()
    self_pid = os.getpid()
    for pid, sid, name in _processes():
        if pid == self_pid or sid == 0:
            continue
        if session is not None and sid != int(session):
            continue
        if name.lower() != exe_name:
            continue
        if kill_pid(pid):
            config.log(f'[wts] 已结束代理 pid={pid} session={sid}')
    if session is not None:
        clear_heartbeat(data, session)


def is_agent_healthy(data: Path, session: int, max_age: float = 20.0) -> bool:
    age = heartbeat_age(data, session)
    return age is not None and age <= max_age
