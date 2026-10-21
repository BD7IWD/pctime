# -*- coding: utf-8 -*-
"""Program Files / ProgramData 的 ACL 设置与修复。

使用 icacls + 内置 SID，避免依赖系统语言：
  *S-1-5-18      SYSTEM
  *S-1-5-32-544  Administrators
  *S-1-5-19      LOCAL SERVICE
  *S-1-5-32-545  Users（不授予）

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from core import config

_IS_WINDOWS = os.name == 'nt'
CREATE_NO_WINDOW = 0x08000000

SID_SYSTEM = '*S-1-5-18'
SID_ADMINS = '*S-1-5-32-544'
SID_LOCAL_SERVICE = '*S-1-5-19'
SID_USERS = '*S-1-5-32-545'


def program_acl_args(path: Path) -> list[str]:
    """程序目录：SYSTEM/管理员完全控制，LocalService 只读执行。"""
    return [
        'icacls', str(path),
        '/inheritance:r',
        '/grant:r',
        f'{SID_SYSTEM}:(OI)(CI)F',
        f'{SID_ADMINS}:(OI)(CI)F',
        f'{SID_LOCAL_SERVICE}:(OI)(CI)RX',
        # 普通用户只读执行：这样标准账户也能触发 UAC 运行卸载器；
        # 但不能写入/删除程序文件。
        f'{SID_USERS}:(OI)(CI)RX',
        '/T', '/C',
    ]


def data_acl_args(path: Path) -> list[str]:
    """数据目录：SYSTEM/管理员/LocalService 完全控制，普通用户无权限。"""
    return [
        'icacls', str(path),
        '/inheritance:r',
        '/grant:r',
        f'{SID_SYSTEM}:(OI)(CI)F',
        f'{SID_ADMINS}:(OI)(CI)F',
        f'{SID_LOCAL_SERVICE}:(OI)(CI)F',
        '/T', '/C',
    ]


def reset_acl_args(path: Path) -> list[str]:
    return ['icacls', str(path), '/reset', '/T', '/C']


def _run(args: list[str], log=None) -> bool:
    if not _IS_WINDOWS:
        return True
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW, timeout=60)
        if p.returncode != 0 and log:
            log(f'[acl] 失败 ({p.returncode}): {" ".join(args)} :: '
                f'{(p.stdout or "")[-300:]} {(p.stderr or "")[-300:]}')
        return p.returncode == 0
    except Exception as e:
        if log:
            log(f'[acl] 异常: {e}')
        return False


def apply_all(install: Path, data: Path, log=None) -> bool:
    if not _IS_WINDOWS:
        return True
    ok = True
    try:
        install.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        data.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    ok = _run(program_acl_args(install), log) and ok
    ok = _run(data_acl_args(data), log) and ok
    return ok


def reset_all(install: Path, data: Path, log=None) -> None:
    if not _IS_WINDOWS:
        return
    _run(reset_acl_args(install), log)
    _run(reset_acl_args(data), log)


def log_default(msg: str) -> None:
    config.log(msg)
