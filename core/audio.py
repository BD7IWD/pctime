# -*- coding: utf-8 -*-
"""锁屏静音：通过 Windows Core Audio（IAudioEndpointVolume）静音默认播放设备。

设计要点：
  - 不用第三方库，直接 ctypes 调 COM；
  - 静音前保存原有静音状态，解锁/退出时恢复；
  - 状态写入数据目录的 audio.state，进程被强杀后下次启动自动恢复；
  - 测试或调试可设 PCTL_DISABLE_AUDIO=1 完全禁用；
  - 任何失败都只记日志，不影响锁屏本身。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import ctypes
import json
import os
import time
import uuid
from pathlib import Path

from . import config

CLSID_MMDeviceEnumerator = uuid.UUID('{BCDE0395-E52F-467C-8E3D-C4579291692E}')
IID_IMMDeviceEnumerator = uuid.UUID('{A95664D2-9614-4F35-A746-DE8DB63617E6}')
IID_IAudioEndpointVolume = uuid.UUID('{5CDF2C82-841E-4546-9722-0CF74078229A}')

CLSCTX_ALL = 23
E_RENDER = 0          # dataFlow: eRender
E_CONSOLE = 0         # role: eConsole

#: IAudioEndpointVolume vtable 索引
_VT_RELEASE = 2
_VT_SET_MUTE = 14
_VT_GET_MUTE = 15

#: IMMDeviceEnumerator vtable 索引
_VT_GET_DEFAULT_ENDPOINT = 4

#: IMMDevice vtable 索引
_VT_ACTIVATE = 3


def _enabled() -> bool:
    return os.environ.get('PCTL_DISABLE_AUDIO', '').strip() not in ('1', 'true', 'yes')


def _is_windows() -> bool:
    return os.name == 'nt'


class _GUID(ctypes.Structure):
    _fields_ = [
        ('Data1', ctypes.c_ulong),
        ('Data2', ctypes.c_ushort),
        ('Data3', ctypes.c_ushort),
        ('Data4', ctypes.c_ubyte * 8),
    ]


_WINFUNCTYPE = getattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE)


def _guid(u: uuid.UUID) -> _GUID:
    b = u.bytes_le
    return _GUID(
        int.from_bytes(b[0:4], 'little'),
        int.from_bytes(b[4:6], 'little'),
        int.from_bytes(b[6:8], 'little'),
        (ctypes.c_ubyte * 8)(*b[8:16]),
    )


class WindowsCoreAudio:
    """默认播放设备的静音读写。每次调用重新获取端点，设备切换也能跟上。"""

    def __init__(self) -> None:
        if not _is_windows():
            raise OSError('Core Audio 仅支持 Windows')
        self._ctypes = ctypes
        self._ole32 = ctypes.WinDLL('ole32', use_last_error=True)
        self._ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self._ole32.CoInitializeEx.restype = ctypes.c_long
        self._ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
        ]
        self._ole32.CoCreateInstance.restype = ctypes.c_long

        # 已初始化过（S_FALSE/RPC_E_CHANGED_MODE）也能继续用
        hr = self._ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
        if hr not in (0, 1) and hr != -2147417850:  # RPC_E_CHANGED_MODE
            config.log(f'[audio] CoInitializeEx 失败 hr={hr}')

    # ---------------------------------------------------------------- COM 工具

    @staticmethod
    def _vt(ptr, index: int, restype, argtypes):
        vtbl = ctypes.cast(
            ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        proto = _WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        return proto(vtbl[index])

    def _call(self, ptr, index: int, restype, argtypes, *args):
        return self._vt(ptr, index, restype, argtypes)(ptr, *args)

    def _release(self, ptr) -> None:
        try:
            self._call(ptr, _VT_RELEASE, ctypes.c_ulong, [])
        except Exception:
            pass

    def _endpoint(self):
        """返回 (volume_ptr, enumerator_ptr, device_ptr)；失败抛异常。"""
        enum = ctypes.c_void_p()
        hr = self._ole32.CoCreateInstance(
            ctypes.byref(_guid(CLSID_MMDeviceEnumerator)), None, CLSCTX_ALL,
            ctypes.byref(_guid(IID_IMMDeviceEnumerator)), ctypes.byref(enum))
        if hr < 0 or not enum:
            raise OSError(f'CoCreateInstance(MMDeviceEnumerator) hr={hr}')

        dev = ctypes.c_void_p()
        try:
            hr = self._call(enum, _VT_GET_DEFAULT_ENDPOINT, ctypes.c_long,
                            [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
                            E_RENDER, E_CONSOLE, ctypes.byref(dev))
            if hr < 0 or not dev:
                raise OSError(f'GetDefaultAudioEndpoint hr={hr}')

            vol = ctypes.c_void_p()
            try:
                hr = self._call(dev, _VT_ACTIVATE, ctypes.c_long,
                                [ctypes.POINTER(_GUID), ctypes.c_ulong, ctypes.c_void_p,
                                 ctypes.POINTER(ctypes.c_void_p)],
                                ctypes.byref(_guid(IID_IAudioEndpointVolume)),
                                CLSCTX_ALL, None, ctypes.byref(vol))
                if hr < 0 or not vol:
                    raise OSError(f'Activate(IAudioEndpointVolume) hr={hr}')
            except Exception:
                self._release(dev)
                raise
        except Exception:
            self._release(enum)
            raise
        return vol, enum, dev

    # ---------------------------------------------------------------- 公共接口

    def get_mute(self) -> bool | None:
        vol = enum = dev = None
        try:
            vol, enum, dev = self._endpoint()
            value = ctypes.c_int(0)
            hr = self._call(vol, _VT_GET_MUTE, ctypes.c_long,
                            [ctypes.POINTER(ctypes.c_int)], ctypes.byref(value))
            if hr < 0:
                raise OSError(f'GetMute hr={hr}')
            return bool(value.value)
        except Exception as e:
            config.log(f'[audio] 读取静音状态失败: {e}')
            return None
        finally:
            for p in (vol, dev, enum):
                if p:
                    self._release(p)

    def set_mute(self, mute: bool) -> bool:
        vol = enum = dev = None
        try:
            vol, enum, dev = self._endpoint()
            hr = self._call(vol, _VT_SET_MUTE, ctypes.c_long,
                            [ctypes.c_int, ctypes.c_void_p], 1 if mute else 0, None)
            if hr < 0:
                raise OSError(f'SetMute hr={hr}')
            return True
        except Exception as e:
            config.log(f'[audio] 设置静音失败: {e}')
            return False
        finally:
            for p in (vol, dev, enum):
                if p:
                    self._release(p)


def default_backend():
    if not _enabled() or not _is_windows():
        return None
    try:
        return WindowsCoreAudio()
    except Exception as e:
        config.log(f'[audio] 初始化 Core Audio 失败: {e}')
        return None


class AudioMuter:
    """静音状态机。backend 可注入假实现用于测试。"""

    def __init__(self, backend=None, state_path: Path | None = None,
                 enabled: bool | None = None):
        self.enabled = _enabled() if enabled is None else bool(enabled)
        self.backend = backend if backend is not None else (default_backend() if self.enabled else None)
        self.state_path = Path(state_path) if state_path else (config.DATA_DIR / 'audio.state')
        # 内存兜底：数据目录被删/状态文件写失败时，解锁仍能恢复声音
        self._mem_muted = False
        self._mem_prev = False

    # ------------------------------------------------------------ 状态文件

    def _read(self) -> dict:
        try:
            d = json.loads(self.state_path.read_text(encoding='utf-8'))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _write(self, d: dict) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(self.state_path.suffix + f'.{os.getpid()}.tmp')
            tmp.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
            os.replace(tmp, self.state_path)
        except Exception as e:
            config.log(f'[audio] 写入状态失败: {e}')

    # ------------------------------------------------------------ 公共接口

    def mute(self) -> bool:
        """锁屏时调用：保存原状态并静音。返回是否成功静音。"""
        if not self.enabled or self.backend is None:
            return False
        if self._mem_muted:
            return True
        state = self._read()
        if state.get('muted'):
            self._mem_muted = True
            self._mem_prev = bool(state.get('prev_mute', False))
            return True
        prev = self.backend.get_mute()
        if prev is None:
            return False
        if not self.backend.set_mute(True):
            return False
        self._mem_muted = True
        self._mem_prev = bool(prev)
        self._write({'muted': True, 'prev_mute': bool(prev),
                     'at': time.strftime('%Y-%m-%d %H:%M:%S')})
        config.log(f'[audio] 已静音（原状态={prev}）')
        return True

    def restore(self) -> None:
        """解锁/退出时调用：恢复锁屏前的静音状态。"""
        state = self._read()
        if state.get('muted'):
            prev = bool(state.get('prev_mute', False))
        elif self._mem_muted:
            prev = bool(self._mem_prev)
        else:
            return
        if not self.enabled or self.backend is None:
            # 音频被禁用（如测试）时不要丢掉状态文件，留给下次真实运行恢复
            config.log('[audio] 音频已禁用，保留状态文件')
            return
        if self.backend.set_mute(prev):
            config.log(f'[audio] 已恢复静音状态={prev}')
        else:
            config.log('[audio] 恢复静音状态失败，保留状态文件以便下次重试')
            return
        self._mem_muted = False
        self._write({'muted': False, 'prev_mute': None})

    def recover(self) -> None:
        """启动时调用：清理上次被强杀遗留的静音。"""
        state = self._read()
        if state.get('muted'):
            config.log('[audio] 检测到上次遗留的静音，正在恢复')
            self.restore()
