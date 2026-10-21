# -*- coding: utf-8 -*-
"""服务模式的安装路径、数据路径和服务名。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from core import config

GUARD_SERVICE = 'PCTimeGuard'
WEB_SERVICE = 'PCTimeWeb'
GUARD_DISPLAY = 'PC Time Guard'
WEB_DISPLAY = 'PC Time Control Web'
FIREWALL_RULE = 'PCTimeControlLite'
HKLM_ROOT = r'Software\PCTimeControlLite'


def program_files() -> Path:
    return Path(os.environ.get('ProgramFiles') or os.environ.get('ProgramW6432')
                or r'C:\Program Files')


def program_data() -> Path:
    return Path(os.environ.get('ProgramData') or r'C:\ProgramData')


def install_dir() -> Path:
    return program_files() / config.APP_NAME


def data_dir() -> Path:
    override = os.environ.get('PCTL_DATA_DIR')
    if override:
        return Path(override)
    return program_data() / config.APP_NAME


def installed_exe() -> Path:
    name = Path(sys.executable).name if getattr(sys, 'frozen', False) else 'PCTimeControlLite.exe'
    return install_dir() / name


def default_data_dir() -> Path:
    """服务模式未显式传 --data-dir 时的数据目录。"""
    return data_dir()


def service_bin_path(mode: str, data: Path | None = None) -> str:
    """构造 sc create 的 binPath 值（含引号）。"""
    exe = installed_exe()
    data = data or data_dir()
    return f'"{exe}" --{mode} --data-dir "{data}"'


def agent_bin_path(session: int, data: Path | None = None) -> str:
    exe = Path(sys.executable)
    data = data or data_dir()
    return f'"{exe}" --agent --session {int(session)} --data-dir "{data}"'


def icon_path() -> Path | None:
    mei = getattr(sys, '_MEIPASS', '')
    candidates = []
    if mei:
        candidates.append(Path(mei) / 'app.ico')
    candidates.append(installed_exe().parent / 'app.ico')
    candidates.append(Path(__file__).resolve().parent.parent / 'app.ico')
    for p in candidates:
        if p.exists():
            return p
    return None
