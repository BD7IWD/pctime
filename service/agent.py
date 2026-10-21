# -*- coding: utf-8 -*-
"""用户会话代理（SYSTEM）：托盘、热键、本地面板、锁屏、静音、键盘钩子。

代理不计数、不跑 HTTP；它每 500ms 读数据库：
  - locked=1 时进入假蓝屏/静音/冻结键盘；
  - locked=0 时恢复；
  - 处理 Web/面板留下的 toast 指令。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import queue
import sys
import time
import tkinter as tk
from tkinter import messagebox, simpledialog

from core import config, netinfo, store
from core import hotkey as hotkeylib
from core.audio import AudioMuter
from core.guard import KeyboardGuard
from ui import win32
from ui.lock_screen import LockManager
from ui.panel import ControlPanel, SetupWindow
from ui.toast import ToastManager
from . import paths, scm, wts

DEFAULT_HOTKEY = config.DEFAULT_HOTKEY

MENU_STATUS = 1
MENU_URL = 2
MENU_PANEL = 3
MENU_EXIT = 7


def _arg(name: str, default: str = '') -> str:
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(name + '='):
            return a.split('=', 1)[1]
    return default


def _fmt(sec: int) -> str:
    sec = max(0, int(sec))
    hh, mm, ss = sec // 3600, (sec % 3600) // 60, sec % 60
    if hh:
        return f'{hh}小时{mm}分'
    if mm:
        return f'{mm}分{ss}秒'
    return f'{ss}秒'


class AgentApp:
    def __init__(self, root: tk.Tk, session: int):
        self.root = root
        self.session = int(session)
        self.service_mode = True
        self.data = config.DATA_DIR
        self.events: queue.Queue = queue.Queue()
        self.settings = store.get_settings()
        self._cfg_ver = self.settings.get('updated_at', '')
        self._started = False
        self._setup_window = None
        self._active_hotkey = ''
        self._processed_hotkey = self.settings.get('hotkey') or DEFAULT_HOTKEY
        self._tray_hidden = int(self.settings.get('tray_hidden') or 0)
        self._last_hb = 0.0
        self._panel_prompting = False
        self._panel_fails: list[float] = []
        self._panel_block_until = 0.0

        self.guard = KeyboardGuard()
        self.guard.start()
        self.audio = AudioMuter()
        try:
            self.audio.recover()
        except Exception as e:
            config.log(f'[agent] 恢复静音失败: {e}')
        self.locker = LockManager(root, self.guard, self.audio,
                                  on_local_unlocked=self._on_local_unlocked)
        self.toasts = ToastManager(root)
        self.tray = win32.TrayIcon(self.events, icon_path=paths.icon_path())
        self.hotkey = win32.HotkeyManager(self.events)
        self.panel = ControlPanel(root, self)

        self._poll_id = root.after(120, self._poll_events)

    # ------------------------------------------------------------ 启动

    def start(self) -> None:
        config.log(f'[agent] 启动 session={self.session} data={self.data}')
        self._heartbeat()
        if store.is_initialized():
            self._start_ui()
        else:
            self._show_setup()
        self.root.after(500, self._poll)

    def _heartbeat(self) -> None:
        now = time.time()
        if now - self._last_hb >= 2.0:
            self._last_hb = now
            wts.write_heartbeat(self.data, self.session)

    def _show_setup(self) -> None:
        if self._setup_window is not None:
            return
        self._setup_window = SetupWindow(self.root, self._start_ui,
                                         self._setup_exit)

    def _setup_exit(self) -> None:
        config.log('[agent] 本地设置被关闭；退出代理，由守护服务重新拉起')
        wts.clear_heartbeat(self.data, self.session)
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass

    def _start_ui(self) -> None:
        if self._started:
            return
        self._started = True
        if self._setup_window is not None:
            try:
                self._setup_window.destroy()
            except Exception:
                pass
            self._setup_window = None
        self.settings = store.get_settings()
        self._cfg_ver = self.settings.get('updated_at', '')
        self._tray_hidden = int(self.settings.get('tray_hidden') or 0)

        tray_ok = self.tray.start(visible=not self._tray_hidden)
        if not tray_ok:
            config.log('[agent] 托盘启动失败，快捷键与网页仍可用')

        want_hk = self.settings.get('hotkey') or DEFAULT_HOTKEY
        hk_ok = self._register_hotkey(want_hk)
        if not hk_ok and want_hk != DEFAULT_HOTKEY:
            hk_ok = self._register_hotkey(DEFAULT_HOTKEY)
            if hk_ok:
                store.update_settings(hotkey=DEFAULT_HOTKEY)
        if not hk_ok:
            config.log(f'[agent] 快捷键 {want_hk} 注册失败')
            if self._tray_hidden:
                self._tray_hidden = 0
                store.update_settings(tray_hidden=0)
                self.tray.set_visible(True)
            self.toasts.show(f'快捷键 {want_hk} 注册失败，请在控制面板里换一个。', 10, 'warn')
        self.settings = store.get_settings()
        self._processed_hotkey = self.settings.get('hotkey') or DEFAULT_HOTKEY

        self._sync_lock(self.settings)
        self.update_ui()
        url = netinfo.access_urls(int(config.load_cfg().get('port', config.DEFAULT_PORT)))[0]
        if self.tray.visible:
            self.tray.notify('管控服务已启动', f'手机访问 {url}', 10000)
        else:
            self.toasts.show(f'管控服务已启动；手机访问 {url}', 8)

    # ------------------------------------------------------------ 轮询

    def _poll(self) -> None:
        try:
            self._heartbeat()
            if not self._started:
                if store.is_initialized():
                    self._start_ui()
                else:
                    self.root.after(500, self._poll)
                    return
            s = store.get_settings()
            ver = s.get('updated_at', '')
            if ver != self._cfg_ver:
                self._cfg_ver = ver
                self.settings = s
                config.log(f'[agent] 设置更新 locked={s["locked"]} enabled={s["enabled"]}')
            self.settings = s
            self._sync_prefs(s)
            self._sync_lock(s)
            for cmd in store.take_commands({'toast'}):
                text = str((cmd.get('payload') or {}).get('text') or '').strip()
                if text:
                    self.toasts.show(text, 10, 'info')
                    try:
                        self.tray.notify('📢 家长提醒', text, 8000)
                    except Exception:
                        pass
            self.update_ui()
        except Exception as e:
            config.log(f'[agent] poll 异常: {e}')
        try:
            self.root.after(500, self._poll)
        except Exception:
            pass

    def _sync_lock(self, s: dict) -> None:
        locked = int(s.get('locked') or 0) == 1
        if locked and not self.locker.is_locked:
            self.locker.lock(s.get('lock_style', 'bsod'),
                             s.get('bsod_code') or 'CRITICAL_PROCESS_DIED')
            config.log('[agent] 已进入锁定画面')
        elif not locked and self.locker.is_locked:
            self.locker.unlock()
            config.log('[agent] 已解除锁定')

    # ------------------------------------------------------------ 热键/托盘

    def _register_hotkey(self, text: str) -> bool:
        ok, canon = hotkeylib.normalize_hotkey(text)
        if not ok:
            return False
        old = self._active_hotkey
        if self.hotkey.start(canon):
            self._active_hotkey = canon
            return True
        if old and old != canon:
            self.hotkey.start(old)
        elif not old:
            fallback = DEFAULT_HOTKEY
            if fallback != canon and self.hotkey.start(fallback):
                self._active_hotkey = fallback
        return False

    def _sync_prefs(self, s: dict) -> None:
        want_hk = s.get('hotkey') or DEFAULT_HOTKEY
        if want_hk != self._processed_hotkey:
            self._processed_hotkey = want_hk
            if not self._register_hotkey(want_hk):
                old = self._active_hotkey or DEFAULT_HOTKEY
                config.log(f'[agent] 快捷键 {want_hk} 注册失败，回退 {old}')
                if old != want_hk:
                    store.update_settings(hotkey=old)
                self._processed_hotkey = old
                if self._tray_hidden:
                    self._tray_hidden = 0
                    store.update_settings(tray_hidden=0)
                    self.tray.set_visible(True)
                self.toasts.show(f'快捷键 {want_hk} 不可用，已保留 {old}。', 8, 'warn')
            else:
                self._processed_hotkey = self._active_hotkey or want_hk

        hidden = 1 if int(s.get('tray_hidden') or 0) else 0
        if hidden != self._tray_hidden:
            self._apply_tray_hidden(hidden)

    def _apply_tray_hidden(self, hidden: int) -> None:
        self._tray_hidden = int(hidden)
        if hidden:
            if not self._active_hotkey:
                self._tray_hidden = 0
                store.update_settings(tray_hidden=0)
                self.tray.set_visible(True)
                self.toasts.show('请先设置可用的全局快捷键，再隐藏托盘。', 8, 'warn')
                return
            if not self.tray.set_visible(False):
                self._tray_hidden = 0
                store.update_settings(tray_hidden=0)
                self.toasts.show('托盘图标隐藏失败，已保持显示。', 8, 'warn')
                return
            self.toasts.show(f'托盘已隐藏，按 {self._active_hotkey} 可唤出面板。', 8)
        else:
            self.tray.set_visible(True)
        self.update_ui()

    def save_hotkey(self, text: str) -> tuple[bool, str]:
        ok, canon = hotkeylib.normalize_hotkey(text)
        if not ok:
            return False, canon
        if not self._register_hotkey(canon):
            return False, f'快捷键 {canon} 注册失败（可能被占用）。'
        store.update_settings(hotkey=canon)
        self._processed_hotkey = canon
        store.add_event('hotkey_changed', f'快捷键改为 {canon}')
        return True, canon

    def set_tray_hidden(self, hidden: bool) -> tuple[bool, str]:
        hidden = 1 if hidden else 0
        if hidden and not self._active_hotkey:
            return False, '请先设置可用的全局快捷键，否则隐藏托盘后将无法本地操作。'
        if hidden:
            if not self.tray.set_visible(False):
                return False, '托盘图标隐藏失败。'
        else:
            self.tray.set_visible(True)
        self._tray_hidden = hidden
        store.update_settings(tray_hidden=hidden)
        return True, ''

    # ------------------------------------------------------------ 面板动作

    def snapshot(self) -> dict:
        s = store.get_settings()
        quota = max(60, int(s['quota_minutes']) * 60)
        used = store.get_used()
        port = int(config.load_cfg().get('port', config.DEFAULT_PORT))
        urls = netinfo.access_urls(port)
        return {
            'port': port,
            'url': urls[0] if urls else '',
            'hotkey': self._active_hotkey or s.get('hotkey') or DEFAULT_HOTKEY,
            'tray_hidden': int(self._tray_hidden),
            'enabled': int(s['enabled']) == 1,
            'locked': self.locker.is_locked or int(s['locked']) == 1,
            'remaining': max(0, quota - used),
            'quota': quota,
            'quota_minutes': max(1, int(s.get('quota_minutes') or 120)),
            'used': used,
        }

    def save_quota(self, minutes) -> tuple[bool, str]:
        try:
            q = int(str(minutes).strip())
        except (TypeError, ValueError):
            return False, '请输入 1–1440 之间的整数分钟。'
        if not (1 <= q <= 1440):
            return False, '每日额度需在 1–1440 分钟之间。'
        store.update_settings(quota_minutes=q)
        store.add_event('quota_changed', f'每日额度改为 {q} 分钟')
        self._reload()
        self.update_ui()
        return True, str(q)

    def _reload(self) -> dict:
        self.settings = store.get_settings()
        return self.settings

    def panel_start(self) -> None:
        store.reset_used()
        store.update_settings(enabled=1, locked=0, lock_reason=None)
        store.add_event('parent_start', '本地面板点了「开始管控」')
        self._sync_lock(self._reload())
        self.update_ui()

    def panel_pause(self) -> None:
        store.update_settings(enabled=0, locked=0, lock_reason=None)
        store.add_event('parent_pause', '本地面板点了「暂停管控」')
        self._sync_lock(self._reload())
        self.update_ui()

    def panel_lock(self) -> None:
        store.update_settings(locked=1, lock_reason='manual')
        store.add_event('parent_lock', '本地面板点了「立即锁定」')
        self._sync_lock(self._reload())
        self.update_ui()

    def panel_unlock(self) -> None:
        store.reset_used()
        store.update_settings(locked=0, lock_reason=None)
        store.bump_unlock()
        store.add_event('unlocked', '本地面板解除锁定，今日用时已清零')
        self._sync_lock(self._reload())
        self.update_ui()

    def apply_port(self, port) -> tuple[bool, str]:
        try:
            p = int(port)
        except (TypeError, ValueError):
            return False, '端口必须是数字。'
        current = int(config.load_cfg().get('port', config.DEFAULT_PORT))
        if p == current:
            return True, f'端口未变化（{p}）。'
        ok, msg = config.validate_port(p)
        if not ok:
            return False, msg
        store.push_command('set_port', {'port': p})
        store.add_event('port_change_requested', f'本地面板请求端口改为 {p}')
        return True, f'已请求切换到 {p}，约 1 秒内生效。'

    def open_web(self) -> None:
        port = int(config.load_cfg().get('port', config.DEFAULT_PORT))
        url = netinfo.access_urls(port)[0]
        messagebox.showinfo('手机访问', f'请用手机访问：\n{url}', parent=self.panel)

    def request_exit(self, parent) -> bool:
        s = store.get_settings()
        if s.get('unlock_hash'):
            pwd = simpledialog.askstring('停止保护服务', '请输入解锁密码：',
                                         show='*', parent=parent)
            if pwd is None:
                return False
            if not store.verify_pwd(pwd, s['unlock_hash']):
                messagebox.showerror('密码错误', '密码不正确。', parent=parent)
                return False
        if not messagebox.askyesno(
                '停止保护服务',
                '将停止守护/网页服务；重启电脑后会自动恢复。\n'
                '彻底卸载请用管理员运行安装目录下的 exe --uninstall。\n\n继续吗？',
                parent=parent):
            return False
        config.log('[agent] 家长请求停止保护服务')
        wts.clear_heartbeat(self.data, self.session)
        try:
            scm.stop(paths.WEB_SERVICE)
        except Exception:
            pass
        try:
            scm.stop(paths.GUARD_SERVICE)
        except Exception:
            pass
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass
        return True

    def _on_local_unlocked(self) -> None:
        self.update_ui()

    # ------------------------------------------------------------ 托盘/事件

    def _status_text(self) -> str:
        s = self.settings
        if self.locker.is_locked:
            return '状态：已锁定（蓝屏中）'
        if int(s['enabled']) != 1:
            return '状态：待机（未开始计时）'
        quota = max(60, int(s['quota_minutes']) * 60)
        left = max(0, quota - store.get_used())
        return f'状态：计时中 · 剩余 {_fmt(left)}'

    def _menu_items(self) -> list[dict]:
        urls = netinfo.access_urls(int(config.load_cfg().get('port', config.DEFAULT_PORT)))
        locked = self.locker.is_locked
        items = [
            {'id': MENU_STATUS, 'label': self._status_text(), 'enabled': False},
            {'id': MENU_URL, 'label': f'手机访问：{urls[0]}', 'enabled': False},
        ]
        if not locked:
            items += [
                {'sep': True},
                {'id': MENU_PANEL, 'label': '打开控制面板（需密码）', 'enabled': True},
            ]
        items += [
            {'sep': True},
            {'id': MENU_EXIT, 'label': '停止保护服务（需要解锁密码）', 'enabled': True},
        ]
        return items

    def show_panel(self) -> None:
        """本地面板需要管理/解锁密码，防止孩子用默认快捷键暂停管控。"""
        if self.locker.is_locked or self._panel_prompting:
            return
        self._panel_prompting = True
        try:
            now = time.time()
            if now < self._panel_block_until:
                messagebox.showwarning('密码尝试过多', '请稍后再试。', parent=None)
                return
            s = store.get_settings()
            if s.get('admin_hash'):
                pwd = simpledialog.askstring('家长验证', '请输入管理密码：',
                                             show='*', parent=None)
                if pwd is None:
                    return
                if not store.verify_pwd(pwd, s['admin_hash']):
                    self._panel_fails = [x for x in self._panel_fails
                                         if now - x <= 300] + [now]
                    if len(self._panel_fails) >= 5:
                        self._panel_fails.clear()
                        self._panel_block_until = now + 60
                        messagebox.showerror('密码错误', '尝试次数过多，请 1 分钟后再试。',
                                             parent=None)
                    else:
                        messagebox.showerror('密码错误', '管理密码不正确。', parent=None)
                    return
                self._panel_fails.clear()
            self.panel.show()
        finally:
            self._panel_prompting = False

    def _handle_tray(self, cmd: int) -> None:
        if cmd == MENU_PANEL:
            self.show_panel()
        elif cmd == MENU_EXIT:
            self.request_exit(None)

    def _poll_events(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                try:
                    if self.root.grab_current() is not None:
                        continue
                except Exception:
                    pass
                if kind in ('hotkey', 'tray_open'):
                    self.show_panel()
                elif kind == 'tray':
                    self._handle_tray(int(ev[1]))
        except queue.Empty:
            pass
        except Exception as e:
            config.log(f'[agent] 事件异常: {e}')
        try:
            self._poll_id = self.root.after(120, self._poll_events)
        except Exception:
            pass

    def update_ui(self) -> None:
        try:
            self.tray.set_tooltip(f'{config.APP_TITLE} — {self._status_text()}')
            self.tray.set_menu(self._menu_items())
        except Exception:
            pass
        try:
            if self.panel.winfo_exists() and self.panel.winfo_viewable():
                self.panel.refresh()
        except Exception:
            pass


def run_agent() -> int:
    session = int(_arg('--session', '0') or 0)
    mutex = wts.acquire_agent_mutex(session)
    if mutex is None:
        config.log(f'[agent] 会话 {session} 已有代理在运行')
        return 0
    win32.set_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    root.title(config.APP_TITLE)

    def _tk_error(exc, val, tb):
        config.log(f'[agent] Tk 异常 {getattr(exc, "__name__", exc)}: {val}')

    root.report_callback_exception = _tk_error
    app = AgentApp(root, session)
    root.after(0, app.start)
    try:
        root.mainloop()
    finally:
        wts.clear_heartbeat(config.DATA_DIR, session)
        wts.release_agent_mutex(mutex)
    return 0
