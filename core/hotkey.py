# -*- coding: utf-8 -*-
"""全局快捷键字符串解析与规范化（纯逻辑，不依赖 Win32）。

支持格式：Ctrl+Alt+M、Ctrl+Shift+F12、Win+Alt+1 …
修饰键：Ctrl / Alt / Shift / Win（大小写不敏感，Ctrl 与 Control 等价）
主键：A–Z、0–9、F1–F12

示例：
    ok, text = normalize_hotkey('ctrl+alt+m')
    mods, vk = parse_hotkey(text)

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

# Win32 RegisterHotKey 修饰键位（数值与 Windows API 一致，便于直接使用）
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

#: 必须至少包含其中之一，避免占用普通按键
REQUIRED_MODS = MOD_CONTROL | MOD_ALT | MOD_WIN

_MOD_ALIASES = {
    'ctrl': MOD_CONTROL,
    'control': MOD_CONTROL,
    'alt': MOD_ALT,
    'shift': MOD_SHIFT,
    'win': MOD_WIN,
    'meta': MOD_WIN,
    'super': MOD_WIN,
}

_MOD_ORDER = (
    (MOD_CONTROL, 'Ctrl'),
    (MOD_ALT, 'Alt'),
    (MOD_SHIFT, 'Shift'),
    (MOD_WIN, 'Win'),
)

#: 主键名 -> 虚拟键码
_KEYS: dict[str, int] = {}
for _c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
    _KEYS[_c] = ord(_c)
for _d in '0123456789':
    _KEYS[_d] = ord(_d)
for _n in range(1, 13):
    _KEYS[f'F{_n}'] = 0x70 + _n - 1

_VK_NAMES = {v: k for k, v in _KEYS.items()}


def parse_hotkey(text: str) -> tuple[int, int] | None:
    """解析快捷键字符串，返回 (mods, vk)；非法输入返回 None。"""
    raw = str(text or '').strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split('+')]
    if len(parts) < 2 or any(not p for p in parts):
        return None

    mods = 0
    for p in parts[:-1]:
        bit = _MOD_ALIASES.get(p.lower())
        if bit is None or mods & bit:
            return None
        mods |= bit

    key = parts[-1].upper()
    vk = _KEYS.get(key)
    if vk is None:
        return None
    if not (mods & REQUIRED_MODS):
        return None
    return mods, vk


def format_hotkey(mods: int, vk: int) -> str:
    """把 (mods, vk) 格式化成规范字符串；无法识别时返回空串。"""
    names = [name for bit, name in _MOD_ORDER if int(mods) & bit]
    key = _VK_NAMES.get(int(vk))
    if not names or not key:
        return ''
    return '+'.join(names + [key])


def normalize_hotkey(text: str) -> tuple[bool, str]:
    """校验并规范化快捷键。

    返回 (True, 规范字符串) 或 (False, 错误说明)。
    """
    parsed = parse_hotkey(text)
    if parsed is None:
        return False, ('快捷键格式无效。示例：Ctrl+Alt+M；'
                       '主键支持 A–Z、0–9、F1–F12，且至少包含 Ctrl/Alt/Win 之一。')
    return True, format_hotkey(*parsed)
