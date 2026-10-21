# -*- coding: utf-8 -*-
"""本地控制面板与首次运行设置窗口。

快捷键唤出的面板提供：状态、开始/暂停、锁定/解锁、打开手机页、
快捷键/端口/托盘图标设置、退出。首次运行必须完成设置才能使用。

作者：野怼怼；https://www.bd7iwd.com
许可：MIT
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from core import config, store
from ui import win32

FONT_CN = 'Microsoft YaHei UI'


def _center(win: tk.Toplevel, w: int, h: int) -> None:
    px, py, pw, ph = win32.primary_rect()
    x = px + max(0, (pw - w) // 2)
    y = py + max(0, (ph - h) // 3)
    try:
        win.geometry(f'{w}x{h}+{x}+{y}')
    except Exception:
        pass


class SetupWindow(tk.Toplevel):
    """首次运行强制设置：管理密码、解锁密码、每日额度。"""

    W, H = 430, 500

    def __init__(self, root: tk.Tk, on_done, on_exit):
        super().__init__(root)
        self.on_done = on_done
        self.on_exit = on_exit
        self.title('首次设置 - 儿童电脑使用时长控制')
        self.resizable(False, False)
        self.attributes('-topmost', True)
        self.protocol('WM_DELETE_WINDOW', lambda: None)
        self.configure(bg='#f2f5fa')
        _center(self, self.W, self.H)

        wrap = tk.Frame(self, bg='#f2f5fa')
        wrap.pack(fill='both', expand=True, padx=24, pady=18)

        tk.Label(wrap, text='👋 首次使用', bg='#f2f5fa', fg='#16202e',
                 font=(FONT_CN, 18, 'bold'), anchor='w').pack(fill='x')
        tk.Label(wrap, text='必须先设置密码，程序才能启动管控。\n请记牢，忘记后只能删除数据重置。',
                 bg='#f2f5fa', fg='#7c8a9e', font=(FONT_CN, 10),
                 justify='left', anchor='w').pack(fill='x', pady=(4, 14))

        self.admin = self._field(wrap, '管理密码（登录手机管理页，≥6 位）', True)
        self.unlock = self._field(wrap, '解锁密码（锁屏后恢复电脑，≥4 位）', True)

        tk.Label(wrap, text='每天允许使用多久（分钟）', bg='#f2f5fa', fg='#7c8a9e',
                 font=(FONT_CN, 10), anchor='w').pack(fill='x', pady=(8, 2))
        self.quota = tk.Spinbox(wrap, from_=1, to=1440, width=10, font=(FONT_CN, 12))
        self.quota.delete(0, 'end')
        self.quota.insert(0, '120')
        self.quota.pack(anchor='w', pady=(0, 16), ipady=4)

        self.err = tk.Label(wrap, text='', bg='#f2f5fa', fg='#d1373a',
                            font=(FONT_CN, 10), anchor='w', wraplength=380,
                            justify='left')
        self.err.pack(fill='x', pady=(0, 8))

        btns = tk.Frame(wrap, bg='#f2f5fa')
        btns.pack(fill='x', side='bottom')
        ttk.Button(btns, text='退出程序', command=self._exit).pack(side='left')
        ttk.Button(btns, text='完成设置', command=self._submit).pack(side='right')

        self.admin.focus_force()
        self.bind('<Return>', lambda e: self._submit())
        self.grab_set()

    def _field(self, parent, label: str, secret: bool) -> ttk.Entry:
        tk.Label(parent, text=label, bg='#f2f5fa', fg='#7c8a9e',
                 font=(FONT_CN, 10), anchor='w').pack(fill='x', pady=(8, 2))
        e = ttk.Entry(parent, show='*' if secret else '', font=(FONT_CN, 12))
        e.pack(fill='x', ipady=5)
        return e

    def _submit(self) -> None:
        admin = self.admin.get().strip()
        unlock = self.unlock.get().strip()
        try:
            quota = int(self.quota.get())
        except ValueError:
            quota = 0
        if len(admin) < 6:
            self.err.configure(text='管理密码至少 6 位。')
            self.admin.focus_set()
            return
        if len(unlock) < 4:
            self.err.configure(text='解锁密码至少 4 位。')
            self.unlock.focus_set()
            return
        if not (1 <= quota <= 1440):
            self.err.configure(text='每日额度需在 1–1440 分钟之间。')
            self.quota.focus_set()
            return
        store.update_settings(admin_hash=store.hash_pwd(admin),
                              unlock_hash=store.hash_pwd(unlock),
                              quota_minutes=quota,
                              enabled=0, locked=0, lock_reason=None)
        store.add_event('setup', f'本地首次设置完成，每日额度 {quota} 分钟')
        config.log('[setup] 本地首次设置完成')
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        self.on_done()

    def _exit(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        self.on_exit()


class ControlPanel(tk.Toplevel):
    """快捷键唤出的本地控制面板。"""

    W, H = 460, 660

    def __init__(self, root: tk.Tk, app):
        super().__init__(root)
        self.app = app
        self.service_mode = bool(getattr(app, 'service_mode', False))
        self._auto_hide_id = None
        self.withdraw()
        self.title('管控面板 - 儿童电脑使用时长控制')
        self.resizable(True, True)
        self.protocol('WM_DELETE_WINDOW', self.hide)
        self.attributes('-topmost', True)
        _center(self, self.W, self.H)

        wrap = tk.Frame(self, bg='#f2f5fa')
        wrap.pack(fill='both', expand=True, padx=20, pady=16)

        tk.Label(wrap, text='🖥️ 儿童电脑使用时长控制', bg='#f2f5fa', fg='#16202e',
                 font=(FONT_CN, 15, 'bold'), anchor='w').pack(fill='x')
        self.state_lbl = tk.Label(wrap, text='', bg='#f2f5fa', fg='#2f6fed',
                                  font=(FONT_CN, 11), anchor='w', justify='left')
        self.state_lbl.pack(fill='x', pady=(6, 2))
        self.time_lbl = tk.Label(wrap, text='', bg='#f2f5fa', fg='#16202e',
                                 font=(FONT_CN, 20, 'bold'), anchor='w')
        self.time_lbl.pack(fill='x', pady=(0, 10))

        row = tk.Frame(wrap, bg='#f2f5fa')
        row.pack(fill='x', pady=(0, 10))
        self.btn_main = ttk.Button(row, text='开始管控', command=self._toggle_main)
        self.btn_main.pack(side='left', fill='x', expand=True, padx=(0, 5))
        self.btn_lock = ttk.Button(row, text='立即锁定', command=self._toggle_lock)
        self.btn_lock.pack(side='left', fill='x', expand=True, padx=(5, 0))

        ttk.Button(wrap, text='打开手机管理页', command=self.app.open_web).pack(
            fill='x', pady=(0, 12))

        box = ttk.LabelFrame(wrap, text=' 本机设置 ')
        box.pack(fill='x', pady=(0, 12))

        tk.Label(box, text='每天允许使用多久（分钟，1–1440）', bg='#f2f5fa',
                 fg='#7c8a9e', font=(FONT_CN, 9), anchor='w').pack(
            fill='x', padx=10, pady=(8, 2))
        qrow = tk.Frame(box, bg='#f2f5fa')
        qrow.pack(fill='x', padx=10)
        self.quota = ttk.Entry(qrow, width=8)
        self.quota.pack(side='left', ipady=4)
        ttk.Button(qrow, text='保存', command=self._save_quota, width=6).pack(
            side='left', padx=(6, 0))
        self.quota_hint = tk.Label(qrow, text='修改后立即生效', bg='#f2f5fa',
                                   fg='#7c8a9e', font=(FONT_CN, 9), anchor='w')
        self.quota_hint.pack(side='left', padx=(10, 0), fill='x', expand=True)

        tk.Label(box, text='唤出面板快捷键（例：Ctrl+Alt+M）', bg='#f2f5fa',
                 fg='#7c8a9e', font=(FONT_CN, 9), anchor='w').pack(
            fill='x', padx=10, pady=(8, 2))
        hkrow = tk.Frame(box, bg='#f2f5fa')
        hkrow.pack(fill='x', padx=10)
        self.hotkey = ttk.Entry(hkrow)
        self.hotkey.pack(side='left', fill='x', expand=True, ipady=4)
        ttk.Button(hkrow, text='保存', command=self._save_hotkey, width=6).pack(
            side='left', padx=(6, 0))

        tk.Label(box, text='手机管理页监听端口（1024–65535）', bg='#f2f5fa',
                 fg='#7c8a9e', font=(FONT_CN, 9), anchor='w').pack(
            fill='x', padx=10, pady=(12, 2))
        prow = tk.Frame(box, bg='#f2f5fa')
        prow.pack(fill='x', padx=10)
        self.port = ttk.Entry(prow, width=10)
        self.port.pack(side='left', ipady=4)
        ttk.Button(prow, text='应用', command=self._apply_port, width=6).pack(
            side='left', padx=(6, 0))
        self.url_lbl = tk.Label(prow, text='', bg='#f2f5fa', fg='#2f6fed',
                                font=(FONT_CN, 9), anchor='w')
        self.url_lbl.pack(side='left', padx=(10, 0), fill='x', expand=True)

        self.tray_var = tk.IntVar(value=0)
        ttk.Checkbutton(box, text='隐藏系统托盘图标（仍可用快捷键唤出）',
                        variable=self.tray_var,
                        command=self._toggle_tray).pack(
            anchor='w', padx=10, pady=(12, 10))

        ttk.Button(wrap,
                   text=('停止保护服务（需要解锁密码）' if self.service_mode
                         else '退出程序（需要解锁密码）'),
                   command=self._exit).pack(fill='x', side='bottom')

        self.bind('<Escape>', lambda e: self.hide())

    # ------------------------------------------------------------ 显示/刷新

    def show(self) -> None:
        self._sync_fields()
        self.refresh()
        self.deiconify()
        self.lift()
        self.focus_force()
        try:
            if self._auto_hide_id is not None:
                self.after_cancel(self._auto_hide_id)
            # 家长离开后自动收起，避免孩子接着使用未锁定的面板
            self._auto_hide_id = self.after(180000, self.hide)
        except Exception:
            self._auto_hide_id = None

    def hide(self) -> None:
        try:
            if self._auto_hide_id is not None:
                self.after_cancel(self._auto_hide_id)
        except Exception:
            pass
        self._auto_hide_id = None
        self.withdraw()

    def _sync_fields(self) -> None:
        st = self.app.snapshot()
        self.quota.delete(0, 'end')
        self.quota.insert(0, str(st.get('quota_minutes') or 120))
        self.hotkey.delete(0, 'end')
        self.hotkey.insert(0, st.get('hotkey') or config.DEFAULT_HOTKEY)
        self.port.delete(0, 'end')
        self.port.insert(0, str(st.get('port') or config.DEFAULT_PORT))
        self.tray_var.set(1 if st.get('tray_hidden') else 0)

    def refresh(self) -> None:
        try:
            st = self.app.snapshot()
        except Exception:
            return
        if st.get('locked'):
            self.state_lbl.configure(text='状态：已锁定（蓝屏中）', fg='#d1373a')
        elif not st.get('enabled'):
            self.state_lbl.configure(text='状态：待机中（不计时、不锁屏）', fg='#7c8a9e')
        else:
            self.state_lbl.configure(text='状态：计时中', fg='#18a058')

        def dur(sec):
            sec = max(0, int(sec))
            hh, mm, ss = sec // 3600, (sec % 3600) // 60, sec % 60
            if hh:
                return f'{hh}小时{mm}分'
            if mm:
                return f'{mm}分{ss}秒'
            return f'{ss}秒'

        self.time_lbl.configure(
            text=f"今日剩余 {dur(st.get('remaining', 0))}")
        self.btn_main.configure(
            text='暂停管控' if st.get('enabled') else '开始管控')
        self.btn_lock.configure(
            text='解除锁定' if st.get('locked') else '立即锁定')
        self.url_lbl.configure(text=str(st.get('url') or ''))
        self.tray_var.set(1 if st.get('tray_hidden') else 0)

    # ------------------------------------------------------------ 动作

    def _toggle_main(self) -> None:
        if self.app.snapshot().get('enabled'):
            self.app.panel_pause()
        else:
            self.app.panel_start()
        self.refresh()

    def _toggle_lock(self) -> None:
        if self.app.snapshot().get('locked'):
            self.app.panel_unlock()
        else:
            self.app.panel_lock()
        self.refresh()

    def _save_quota(self) -> None:
        ok, msg = self.app.save_quota(self.quota.get())
        if ok:
            try:
                self.quota.delete(0, 'end')
                self.quota.insert(0, msg)
            except Exception:
                pass
            messagebox.showinfo('每日额度', f'已设为 {msg} 分钟。', parent=self)
        else:
            messagebox.showwarning('每日额度', msg, parent=self)

    def _save_hotkey(self) -> None:
        ok, msg = self.app.save_hotkey(self.hotkey.get())
        if ok:
            self.hotkey.delete(0, 'end')
            self.hotkey.insert(0, msg)
            messagebox.showinfo('快捷键', '已保存，1 秒内生效。', parent=self)
        else:
            messagebox.showwarning('快捷键', msg, parent=self)

    def _apply_port(self) -> None:
        ok, msg = self.app.apply_port(self.port.get())
        if ok:
            messagebox.showinfo('端口', msg, parent=self)
            self._sync_fields()
        else:
            messagebox.showwarning('端口', msg, parent=self)

    def _toggle_tray(self) -> None:
        hidden = bool(self.tray_var.get())
        ok, msg = self.app.set_tray_hidden(hidden)
        if not ok:
            messagebox.showwarning('托盘图标', msg, parent=self)
            self.tray_var.set(0 if hidden else 1)
        else:
            self.refresh()

    def _exit(self) -> None:
        self.app.request_exit(self)
